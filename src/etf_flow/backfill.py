from __future__ import annotations

import argparse
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from industry_flow.config import data_sources, resolve_akshare_api
from industry_flow.storage import read_parquet_if_exists, write_parquet_atomic
from .daily import (
    ETF_KEY,
    OUTPUT_COLUMNS,
    _daily_file,
    _history_file,
    _normalize_shares,
    _upsert_history,
    _write_csv_atomic,
)


_TRADE_CALENDAR_API = data_sources["trade_calendar"]["api"]
_ETF_SOURCES = data_sources["etf"]


def _dates_before(end_date: dt.date) -> list[dt.date]:
    calendar = resolve_akshare_api(_TRADE_CALENDAR_API)()
    if "trade_date" not in calendar:
        raise RuntimeError("交易日历缺少 trade_date 列")
    dates = pd.to_datetime(calendar["trade_date"], errors="coerce").dropna().dt.date
    return sorted({date for date in dates if date <= end_date})


def _fetch_shares(dates: list[dt.date]) -> dict[dt.date, pd.DataFrame]:
    snapshots: dict[dt.date, pd.DataFrame] = {}

    # Shenzhen provides the full ETF share history for the requested date range
    # in one exchange workbook.
    share_sources = _ETF_SOURCES["shares"]
    start_arg, end_arg = dates[0].strftime("%Y%m%d"), dates[-1].strftime("%Y%m%d")
    szse_raw = resolve_akshare_api(share_sources["szse_api"])(
        start_date=start_arg,
        end_date=end_arg,
        symbol=share_sources["szse_symbol"],
    )
    if not {"日期", "基金代码", "基金简称", "基金份额"}.issubset(szse_raw.columns):
        raise RuntimeError(f"深交所历史份额返回列不完整：{szse_raw.columns.tolist()}")
    szse_raw = szse_raw.copy()
    szse_raw["_date"] = pd.to_datetime(szse_raw["日期"], errors="coerce").dt.date

    for index, target_date in enumerate(dates, 1):
        raw = resolve_akshare_api(share_sources["sse_api"])(
            date=target_date.strftime("%Y%m%d")
        )
        if "统计日期" not in raw.columns:
            raise RuntimeError(f"上交所 {target_date} 响应缺少统计日期")
        returned_dates = pd.to_datetime(raw["统计日期"], errors="coerce").dropna().dt.date.unique()
        if list(returned_dates) != [target_date]:
            raise RuntimeError(f"上交所返回日期 {list(returned_dates)}，预期 {target_date}")
        sse = _normalize_shares(raw, "SSE", target_date)

        szse_day = szse_raw.loc[szse_raw["_date"].eq(target_date)]
        if szse_day.empty:
            raise RuntimeError(f"深交所没有返回 {target_date} 的 ETF 份额")
        szse = _normalize_shares(szse_day, "SZSE", target_date)

        snapshot = pd.concat([sse, szse], ignore_index=True)
        if snapshot.duplicated(["exchange", "code"]).any():
            raise RuntimeError(f"{target_date} 存在重复交易所/代码")
        snapshots[target_date] = snapshot
        print(
            f"[etf-backfill] shares {index}/{len(dates)} {target_date}: "
            f"SSE={len(sse)}, SZSE={len(szse)}, total={len(snapshot)}",
            flush=True,
        )
    return snapshots


def _fetch_one_price(exchange: str, code: str) -> tuple[str, str, pd.DataFrame | None, str | None]:
    symbol = ("sh" if exchange == "SSE" else "sz") + code
    history_api = _ETF_SOURCES["prices"]["history_api"]
    for attempt in range(1, 4):
        try:
            raw = resolve_akshare_api(history_api)(symbol=symbol)
            if raw.empty or not {"date", "close"}.issubset(raw.columns):
                raise RuntimeError("Sina 日线为空或缺少 date/close")
            price = raw[["date", "close"]].copy()
            price["trade_date"] = pd.to_datetime(price["date"], errors="coerce").dt.date
            price["close"] = pd.to_numeric(price["close"], errors="coerce")
            price = price.dropna(subset=["trade_date", "close"])
            price = price.loc[price["close"].gt(0), ["trade_date", "close"]]
            return exchange, code, price, None
        except Exception as exc:
            if attempt == 3:
                return exchange, code, None, f"{type(exc).__name__}: {exc}"
            time.sleep(0.3 * attempt)
    return exchange, code, None, "unknown price error"


def _fetch_changed_prices(
    changed: pd.DataFrame,
    dates: list[dt.date],
) -> tuple[pd.DataFrame, list[str]]:
    history_api = _ETF_SOURCES["prices"]["history_api"]
    instruments = changed[["exchange", "code"]].drop_duplicates().sort_values(["exchange", "code"])
    tasks = [tuple(row) for row in instruments.itertuples(index=False, name=None)]
    if not tasks:
        return pd.DataFrame(columns=["trade_date", "exchange", "code", "close", "price_date", "price_source"]), []

    target_set = set(dates)
    frames: list[pd.DataFrame] = []
    failures: list[str] = []
    print(f"[etf-backfill] fetching historical prices for {len(tasks)} ETFs", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_fetch_one_price, exchange, code) for exchange, code in tasks]
        for done_count, future in enumerate(as_completed(futures), 1):
            exchange, code, history, error = future.result()
            if history is None:
                failures.append(f"{exchange}:{code} {error}")
            else:
                history = history.loc[history["trade_date"].isin(target_set)].copy()
                if not history.empty:
                    history["exchange"] = exchange
                    history["code"] = code
                    history["price_date"] = pd.to_datetime(history["trade_date"])
                    history["price_source"] = f"AKShare_{history_api}"
                    frames.append(history)
            if done_count % 50 == 0 or done_count == len(tasks):
                print(
                    f"[etf-backfill] prices {done_count}/{len(tasks)}; "
                    f"failures={len(failures)}",
                    flush=True,
                )

    if frames:
        prices = pd.concat(frames, ignore_index=True)
        prices["trade_date"] = pd.to_datetime(prices["trade_date"])
        prices = prices.drop_duplicates(["trade_date", "exchange", "code"], keep="last")
        return prices[["trade_date", "exchange", "code", "close", "price_date", "price_source"]], failures
    return pd.DataFrame(columns=["trade_date", "exchange", "code", "close", "price_date", "price_source"]), failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill ETF share-flow data for recent trading sessions")
    parser.add_argument("--sessions", type=int, default=20, help="number of trading dates to write")
    parser.add_argument("--end-date", help="last report date, YYYY-MM-DD; defaults to latest saved ETF date")
    args = parser.parse_args()
    if args.sessions < 1:
        raise SystemExit("--sessions must be positive")

    history = read_parquet_if_exists(_history_file())
    if args.end_date:
        end_date = dt.date.fromisoformat(args.end_date)
    elif not history.empty:
        end_date = pd.to_datetime(history["trade_date"], errors="coerce").max().date()
    else:
        raise RuntimeError("没有现有 ETF 历史日期；请显式传入 --end-date")

    calendar_dates = _dates_before(end_date)
    report_dates = calendar_dates[-args.sessions :]
    first_index = calendar_dates.index(report_dates[0])
    if first_index == 0:
        raise RuntimeError("交易日历没有报告区间之前的基准日")
    fetch_dates = calendar_dates[first_index - 1 :]
    if not report_dates or report_dates[-1] != end_date:
        raise RuntimeError(f"结束日 {end_date} 不在交易日历中")
    print(
        f"[etf-backfill] report dates={report_dates[0]}..{report_dates[-1]} "
        f"({len(report_dates)} sessions); baseline={fetch_dates[0]}",
        flush=True,
    )

    snapshots = _fetch_shares(fetch_dates)
    reports: list[pd.DataFrame] = []
    for target_date in report_dates:
        target = snapshots[target_date].copy()
        previous_date = calendar_dates[calendar_dates.index(target_date) - 1]
        previous = snapshots[previous_date][["exchange", "code", "shares"]].rename(
            columns={"shares": "shares_prev"}
        )
        previous["shares_prev_date"] = pd.Timestamp(previous_date)
        df = target.merge(previous, on=["exchange", "code"], how="left", validate="one_to_one")
        df["share_change"] = df["shares"] - df["shares_prev"]
        denominator = df["shares_prev"].where(df["shares_prev"].gt(0))
        df["share_change_pct"] = df["share_change"] / denominator * 100
        df["close"] = pd.NA
        df["price_date"] = pd.NaT
        df["price_source"] = pd.NA
        df["estimated_flow"] = pd.NA
        df["flow_rank"] = pd.NA
        df["ingested_at"] = pd.Timestamp.now()
        for column in OUTPUT_COLUMNS:
            if column not in df:
                df[column] = pd.NA
        reports.append(df[OUTPUT_COLUMNS])

    report_rows = pd.concat(reports, ignore_index=True)
    changed = report_rows.loc[report_rows["share_change"].notna() & report_rows["share_change"].ne(0)]
    prices, price_failures = _fetch_changed_prices(changed, report_dates)
    if not prices.empty:
        price_lookup = prices.set_index(["trade_date", "exchange", "code"])
        for df in reports:
            keys = pd.MultiIndex.from_arrays(
                [pd.to_datetime(df["trade_date"]), df["exchange"], df["code"]],
                names=["trade_date", "exchange", "code"],
            )
            aligned = price_lookup.reindex(keys)
            df["close"] = aligned["close"].array
            df["price_date"] = aligned["price_date"].array
            df["price_source"] = aligned["price_source"].array
    for df in reports:
        df["estimated_flow"] = df["share_change"] * pd.to_numeric(df["close"], errors="coerce")
        unchanged = df["share_change"].eq(0)
        df.loc[unchanged, "estimated_flow"] = 0.0
        priced = df["estimated_flow"].notna()
        df.loc[priced, "flow_rank"] = df.loc[priced, "estimated_flow"].rank(
            ascending=False, method="min"
        )
        df.sort_values(["exchange", "code"], inplace=True, ignore_index=True)

    # Store the previous trading session as a shares-only baseline in cumulative history,
    # while writing exactly the requested number of daily report files.
    baseline = snapshots[fetch_dates[0]].copy()
    baseline["shares_prev_date"] = pd.NaT
    baseline["shares_prev"] = pd.NA
    baseline["share_change"] = pd.NA
    baseline["share_change_pct"] = pd.NA
    baseline["close"] = pd.NA
    baseline["price_date"] = pd.NaT
    baseline["estimated_flow"] = pd.NA
    baseline["flow_rank"] = pd.NA
    baseline["price_source"] = pd.NA
    baseline["ingested_at"] = pd.Timestamp.now()
    baseline = baseline[OUTPUT_COLUMNS]
    incoming_parts = [part.dropna(axis=1, how="all") for part in [baseline, *reports]]
    incoming = pd.concat(incoming_parts, ignore_index=True)
    updated_history = _upsert_history(history, incoming)

    # Check the complete result before replacing any saved files.
    all_reports = pd.concat(reports, ignore_index=True)
    expected_rows = sum(len(snapshots[date]) for date in report_dates)
    if len(all_reports) != expected_rows:
        raise RuntimeError(f"输出行数 {len(all_reports)} 与快照行数 {expected_rows} 不符")
    if all_reports.duplicated(ETF_KEY).any():
        raise RuntimeError("报告区间内存在重复日期/交易所/代码")
    if all_reports["shares"].isna().any() or all_reports["shares"].le(0).any():
        raise RuntimeError("报告区间包含缺失或非正份额")
    if set(pd.to_datetime(all_reports["trade_date"]).dt.date.unique()) != set(report_dates):
        raise RuntimeError("写出日期与预期 20 个交易日不一致")

    for date, df in zip(report_dates, reports):
        _write_csv_atomic(df[OUTPUT_COLUMNS], _daily_file(date))
    write_parquet_atomic(updated_history, _history_file())

    changes = all_reports["share_change"].notna()
    nonzero = all_reports["share_change"].ne(0) & changes
    priced_nonzero = nonzero & all_reports["estimated_flow"].notna()
    print("[etf-backfill] saved daily files:", len(reports), flush=True)
    print(f"[etf-backfill] daily rows: {len(all_reports)}; history rows: {len(updated_history)}", flush=True)
    print(f"[etf-backfill] share comparisons: {int(changes.sum())}; nonzero changes: {int(nonzero.sum())}", flush=True)
    print(
        f"[etf-backfill] nonzero changes with estimated flow: {int(priced_nonzero.sum())}/{int(nonzero.sum())}; "
        f"price fetch failures: {len(price_failures)}",
        flush=True,
    )
    if price_failures:
        print("[etf-backfill] sample price failures: " + " | ".join(price_failures[:10]), flush=True)
    for date, df in zip(report_dates, reports):
        print(
            f"[etf-backfill] {date}: rows={len(df)}, changed={int(df['share_change'].fillna(0).ne(0).sum())}, "
            f"priced_changes={int((df['share_change'].ne(0) & df['estimated_flow'].notna()).sum())}",
            flush=True,
        )


if __name__ == "__main__":
    main()
