from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from industry_flow.config import data_sources, resolve_akshare_api, settings
from industry_flow.dingtalk import (
    get_app_access_token,
    send_group_markdown,
    send_markdown,
    upload_image,
)
from industry_flow.openclaw import send_openclaw_message
from industry_flow.storage import read_parquet_if_exists, write_parquet_atomic
from .report_image import render_etf_report_png


ETF_KEY = ["trade_date", "exchange", "code"]
OUTPUT_COLUMNS = [
    "trade_date",
    "code",
    "name",
    "exchange",
    "shares",
    "shares_prev_date",
    "shares_prev",
    "share_change",
    "share_change_pct",
    "close",
    "price_date",
    "estimated_flow",
    "flow_rank",
    "shares_source",
    "price_source",
    "ingested_at",
]
_ETF_SOURCES = data_sources["etf"]
_TRADE_CALENDAR_API = data_sources["trade_calendar"]["api"]
BOND_ETF_NAME_KEYWORDS = tuple(_ETF_SOURCES["filters"]["bond_name_keywords"])


def _is_bond_etf(name: object) -> bool:
    """Classify bond ETFs by the fund names available in exchange share feeds."""
    normalized = str(name).strip()
    return any(keyword in normalized for keyword in BOND_ETF_NAME_KEYWORDS)


def previous_trade_date(run_date: dt.date, calendar: pd.DataFrame | None = None) -> dt.date:
    calendar = calendar if calendar is not None else resolve_akshare_api(_TRADE_CALENDAR_API)()
    if "trade_date" not in calendar:
        raise RuntimeError("Trade calendar has no trade_date column")
    dates = pd.to_datetime(calendar["trade_date"], errors="coerce").dropna().dt.date
    previous = [date for date in dates.unique() if date < run_date]
    if not previous:
        raise RuntimeError(f"No previous CN trading date before {run_date}")
    return max(previous)


def _normalize_shares(raw: pd.DataFrame, exchange: str, target_date: dt.date) -> pd.DataFrame:
    required = {"基金代码", "基金简称", "基金份额"}
    missing = required.difference(raw.columns)
    if missing:
        raise RuntimeError(f"{exchange} share data missing columns: {sorted(missing)}")
    df = raw.rename(
        columns={"基金代码": "code", "基金简称": "name", "基金份额": "shares"}
    ).copy()
    df["code"] = df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    df["name"] = df["name"].astype(str).str.strip()
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["exchange"] = exchange
    df["trade_date"] = pd.Timestamp(target_date)
    df["shares_source"] = f"AKShare_{exchange}"
    df = df.dropna(subset=["code", "shares"])
    df = df[df["shares"] > 0]
    if df.empty:
        raise RuntimeError(f"{exchange} returned no valid ETF share rows for {target_date}")
    if df["code"].duplicated().any():
        duplicates = df.loc[df["code"].duplicated(), "code"].head(5).tolist()
        raise RuntimeError(f"{exchange} has duplicate ETF codes: {duplicates}")
    return df[["trade_date", "code", "name", "exchange", "shares", "shares_source"]]


def fetch_shares(target_date: dt.date) -> pd.DataFrame:
    date_arg = target_date.strftime("%Y%m%d")
    errors: list[str] = []
    frames: list[pd.DataFrame] = []
    share_sources = _ETF_SOURCES["shares"]

    try:
        # This is SSE's ETF scale feed. Trading currency funds are published
        # under a separate SSE scale endpoint and are outside this universe.
        sse = _request_with_retries(
            "SSE ETF shares",
            lambda: resolve_akshare_api(share_sources["sse_api"])(date=date_arg),
        )
        if "统计日期" not in sse.columns:
            raise RuntimeError("SSE response has no 统计日期")
        sse_dates = pd.to_datetime(sse["统计日期"], errors="coerce").dropna().dt.date.unique()
        if len(sse_dates) != 1 or sse_dates[0] != target_date:
            raise RuntimeError(f"SSE returned dates {list(sse_dates)}, expected {target_date}")
        # AKShare normalizes SSE's source value (万份) to shares already.
        frames.append(_normalize_shares(sse, "SSE", target_date))
    except Exception as exc:
        errors.append(f"SSE: {type(exc).__name__}: {exc}")

    try:
        szse = _request_with_retries(
            "SZSE ETF shares",
            lambda: resolve_akshare_api(share_sources["szse_api"])(
                start_date=date_arg,
                end_date=date_arg,
                symbol=share_sources["szse_symbol"],
            ),
        )
        if "日期" not in szse.columns:
            raise RuntimeError("SZSE response has no 日期")
        szse_dates = pd.to_datetime(szse["日期"], errors="coerce").dropna().dt.date.unique()
        if len(szse_dates) != 1 or szse_dates[0] != target_date:
            raise RuntimeError(f"SZSE returned dates {list(szse_dates)}, expected {target_date}")
        # SZSE's 基金份额 field is already in shares; do not scale it.
        frames.append(_normalize_shares(szse, "SZSE", target_date))
    except Exception as exc:
        errors.append(f"SZSE: {type(exc).__name__}: {exc}")

    if errors:
        raise RuntimeError("Incomplete ETF market snapshot; " + " | ".join(errors))

    current = pd.concat(frames, ignore_index=True)
    if current.duplicated(["exchange", "code"]).any():
        raise RuntimeError("Duplicate (exchange, code) rows in combined ETF snapshot")
    return current


def _request_with_retries(
    source: str,
    request: Callable[[], pd.DataFrame],
) -> pd.DataFrame:
    attempts = max(1, settings.request_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return request()
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = max(0.0, settings.request_retry_sleep)
            print(
                f"[etf] {source} request failed ({attempt}/{attempts}); "
                f"retrying in {delay:g}s: {type(exc).__name__}: {exc}"
            )
            time.sleep(delay)
    raise RuntimeError(
        f"{source} request failed after {attempts} attempts: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


def fetch_closing_prices(
    target_date: dt.date,
    run_date: dt.date,
    universe: pd.DataFrame,
) -> pd.DataFrame:
    """Use the live ETF quote as the target day's closing price after market close."""
    now = dt.datetime.now()
    empty = pd.DataFrame(
        columns=["exchange", "code", "close", "price_date", "price_source"]
    )
    if run_date != dt.date.today() or target_date != run_date:
        print("[etf] price skipped: live quotes are only valid for today's target date")
        return empty
    if now.time() < dt.time(15, 5):
        print("[etf] price skipped: market close is not confirmed yet (before 15:05)")
        return empty

    try:
        primary_api = _ETF_SOURCES["prices"]["primary_api"]
        spot = resolve_akshare_api(primary_api)()
        required = {"代码", "最新价"}
        if not required.issubset(spot.columns):
            raise RuntimeError(f"ETF spot response missing columns: {sorted(required.difference(spot.columns))}")
        prices = spot.rename(columns={"代码": "code", "最新价": "close"})[["code", "close"]].copy()
        prices["code"] = prices["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
        prices = prices.dropna(subset=["close"])
        prices = prices[prices["close"] > 0].drop_duplicates("code", keep="last")
        code_exchange = universe[["code", "exchange"]].drop_duplicates()
        if code_exchange["code"].duplicated().any():
            raise RuntimeError("Cannot map quote codes to exchanges uniquely")
        prices = prices.merge(code_exchange, on="code", how="inner")
        prices["price_date"] = pd.Timestamp(target_date)
        prices["price_source"] = f"AKShare_{primary_api}_after_close"
        return prices[["exchange", "code", "close", "price_date", "price_source"]]
    except Exception as exc:
        fallback = _ETF_SOURCES["prices"]
        print(
            f"[etf] {fallback['primary_provider']} price fetch failed; "
            f"trying {fallback['fallback_provider']}: {type(exc).__name__}: {exc}"
        )

    try:
        fallback = _ETF_SOURCES["prices"]
        fallback_api = fallback["fallback_api"]
        spot = resolve_akshare_api(fallback_api)(symbol=fallback["fallback_symbol"])
        required = {"代码", "最新价"}
        if not required.issubset(spot.columns):
            raise RuntimeError(f"Sina ETF quote response missing columns: {sorted(required.difference(spot.columns))}")
        prices = spot.rename(columns={"代码": "source_code", "最新价": "close"})[["source_code", "close"]].copy()
        prices["source_code"] = prices["source_code"].astype(str).str.lower()
        prices["code"] = prices["source_code"].str[-6:]
        prices["exchange"] = prices["source_code"].str[:2].map({"sh": "SSE", "sz": "SZSE"})
        prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
        prices = prices.dropna(subset=["close", "exchange"])
        prices = prices[prices["close"] > 0]
        prices = prices.drop_duplicates(["exchange", "code"], keep="last")
        prices = prices.merge(
            universe[["exchange", "code"]].drop_duplicates(),
            on=["exchange", "code"],
            how="inner",
        )
        prices["price_date"] = pd.Timestamp(target_date)
        prices["price_source"] = f"AKShare_{fallback_api}_after_close"
        return prices[["exchange", "code", "close", "price_date", "price_source"]]
    except Exception as exc:
        print(f"[etf] Sina ETF price fetch failed; share data will still be saved: {type(exc).__name__}: {exc}")
        return empty


def _history_file() -> Path:
    return settings.processed_dir / "etf_flow_history.parquet"


def _daily_file(trade_date: dt.date) -> Path:
    return settings.raw_daily_dir / f"etf_flow_{trade_date:%Y%m%d}.csv"


def _read_previous(
    history: pd.DataFrame,
    target_date: dt.date,
    calendar: pd.DataFrame,
) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame(columns=["exchange", "code", "shares_prev", "shares_prev_date"])
    history = history.copy()
    history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce")
    expected_previous_date = previous_trade_date(target_date, calendar)
    previous = history.loc[
        history["trade_date"].dt.date.eq(expected_previous_date),
        ["exchange", "code", "shares"],
    ].copy()
    if previous.empty:
        return pd.DataFrame(columns=["exchange", "code", "shares_prev", "shares_prev_date"])
    previous = previous.rename(columns={"shares": "shares_prev"})
    previous["shares_prev_date"] = pd.Timestamp(expected_previous_date)
    return previous


def _close_for_date(
    history: pd.DataFrame,
    exchange: str,
    code: str,
    target_date: dt.date,
    cache: dict[tuple[str, str, dt.date], float],
) -> float | None:
    key = (exchange, code, target_date)
    if key in cache:
        return cache[key] if pd.notna(cache[key]) else None

    if {"trade_date", "exchange", "code", "close"}.issubset(history.columns):
        match = history.loc[
            history["trade_date"].dt.date.eq(target_date)
            & history["exchange"].eq(exchange)
            & history["code"].astype(str).str.zfill(6).eq(code),
            "close",
        ]
    else:
        match = pd.Series(dtype=float)
    match = pd.to_numeric(match, errors="coerce").dropna()
    if not match.empty and match.iloc[-1] > 0:
        cache[key] = float(match.iloc[-1])
        return cache[key]
    cache[key] = float("nan")
    return None


def _append_period_summary(
    lines: list[str],
    history: pd.DataFrame,
    trading_dates: list[dt.date],
    window_size: int,
    price_cache: dict[tuple[str, str, dt.date], float],
    summaries: list[dict[str, object]],
) -> None:
    label = "今日" if window_size == 1 else f"{window_size}日"
    unavailable = "数据不足"

    def add_unavailable(status: str) -> None:
        lines.append(f"| {label} | {status} | {status} |")
        summaries.append(
            {"period": label, "inflow": [], "outflow": [], "status": status}
        )

    required = {
        "trade_date", "exchange", "code", "name", "shares", "shares_prev",
        "share_change", "estimated_flow", "close",
    }
    if history.empty or not required.issubset(history.columns):
        add_unavailable(unavailable)
        return
    if len(trading_dates) < window_size + 1:
        add_unavailable("历史不足")
        return

    window_dates = trading_dates[-window_size:]
    base_date = trading_dates[-window_size - 1]
    period = history.loc[history["trade_date"].dt.date.isin(window_dates)].copy()
    available_dates = set(period["trade_date"].dt.date.unique())
    missing_dates = [date for date in window_dates if date not in available_dates]
    if missing_dates:
        add_unavailable("历史不足")
        return
    missing_exchanges = [
        date
        for date, rows in period.groupby(period["trade_date"].dt.date)
        if not {"SSE", "SZSE"}.issubset(set(rows["exchange"].unique()))
    ]
    if missing_exchanges:
        add_unavailable("历史不足")
        return

    keys = ["exchange", "code"]
    bond_keys = period.loc[period["name"].map(_is_bond_etf), keys].drop_duplicates()
    if not bond_keys.empty:
        period = period.merge(
            bond_keys.assign(_excluded_bond=True),
            on=keys,
            how="left",
        )
        period = period.loc[period["_excluded_bond"].ne(True)].drop(
            columns="_excluded_bond"
        )

    comparable = period["share_change"].notna()
    comparable_rows = period.loc[comparable].copy()
    if comparable_rows.empty:
        add_unavailable("暂无有效数据")
        return
    missing_flow_rows = comparable_rows.loc[comparable_rows["estimated_flow"].isna()]
    missing_price_count = missing_flow_rows[keys].drop_duplicates().shape[0]
    flow_rows = comparable_rows.loc[comparable_rows["estimated_flow"].notna()].copy()
    if flow_rows.empty:
        add_unavailable("没有可估算净额")
        return

    ordered = comparable_rows.sort_values("trade_date")
    ordered_flow = flow_rows.sort_values("trade_date")
    aggregated = ordered_flow.groupby(keys, as_index=False).agg(
        name=("name", "last"),
        estimated_flow=("estimated_flow", "sum"),
    )
    start_rows = ordered.drop_duplicates(keys, keep="first")[
        keys + ["shares_prev", "shares_prev_date"]
    ].rename(
        columns={
            "shares_prev": "shares_start",
            "shares_prev_date": "shares_start_date",
        }
    )
    end_rows = ordered.drop_duplicates(keys, keep="last")[
        keys + ["shares", "trade_date"]
    ].rename(
        columns={
            "shares": "shares_end",
            "trade_date": "shares_end_date",
        }
    )
    observed_days = ordered.groupby(keys, as_index=False).agg(
        observed_days=("trade_date", "nunique")
    )
    share_period = (
        start_rows.merge(end_rows, on=keys, how="inner")
        .merge(observed_days, on=keys, how="left")
    )
    share_period["shares_start_date"] = pd.to_datetime(
        share_period["shares_start_date"], errors="coerce"
    ).dt.date
    share_period["shares_end_date"] = pd.to_datetime(
        share_period["shares_end_date"], errors="coerce"
    ).dt.date
    strength_valid = (
        share_period["observed_days"].eq(window_size)
        & share_period["shares_start_date"].eq(base_date)
        & share_period["shares_end_date"].eq(window_dates[-1])
        & share_period["shares_start"].gt(0)
    )
    share_period["strength_pct"] = (
        (share_period["shares_end"] - share_period["shares_start"])
        / share_period["shares_start"]
        * 100
    ).where(strength_valid)
    aggregated = aggregated.merge(
        share_period[keys + ["strength_pct"]],
        on=keys,
        how="left",
    )
    inflow_candidates = aggregated.loc[aggregated["estimated_flow"] > 0].nlargest(5, "estimated_flow")
    outflow_candidates = aggregated.loc[aggregated["estimated_flow"] < 0].nsmallest(5, "estimated_flow")
    candidates = pd.concat([inflow_candidates, outflow_candidates]).drop_duplicates(keys)
    returns: dict[tuple[str, str], float | None] = {}
    for row in candidates.itertuples(index=False):
        code = str(row.code).zfill(6)
        base_close = _close_for_date(history, row.exchange, code, base_date, price_cache)
        end_close = _close_for_date(history, row.exchange, code, window_dates[-1], price_cache)
        returns[(row.exchange, code)] = (
            (end_close / base_close - 1) * 100
            if base_close is not None and end_close is not None
            else None
        )
    aggregated["change_pct"] = [
        returns.get((row.exchange, str(row.code).zfill(6)))
        for row in aggregated.itertuples(index=False)
    ]
    inflow = aggregated.loc[aggregated["estimated_flow"] > 0].nlargest(5, "estimated_flow")
    outflow = aggregated.loc[aggregated["estimated_flow"] < 0].nsmallest(5, "estimated_flow")

    def serialize(rows: pd.DataFrame) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for row in rows.itertuples(index=False):
            items.append(
                {
                    "code": str(row.code).zfill(6),
                    "name": str(row.name),
                    "flow_yi": float(row.estimated_flow / 1e8),
                    "change_pct": (
                        float(row.change_pct) if pd.notna(row.change_pct) else None
                    ),
                    "strength_pct": (
                        float(row.strength_pct) if pd.notna(row.strength_pct) else None
                    ),
                }
            )
        return items

    summaries.append(
        {
            "period": label,
            "inflow": serialize(inflow),
            "outflow": serialize(outflow),
            "status": None,
            "note": (
                f"{missing_price_count}只ETF存在缺价，净额按有价格的日期累计"
                if missing_price_count
                else None
            ),
        }
    )

    def render(rows: pd.DataFrame) -> str:
        if rows.empty:
            return "暂无有效数据"
        entries = []
        for row in rows.itertuples(index=False):
            pct = f"{row.change_pct:+.2f}%" if pd.notna(row.change_pct) else "NA"
            strength = (
                f"{row.strength_pct:+.2f}%"
                if pd.notna(row.strength_pct)
                else "NA"
            )
            entries.append(
                f"{row.code} {row.name} {row.estimated_flow / 1e8:+.2f} / "
                f"{pct} / {strength}"
            )
        return "<br>".join(entries)

    display_label = (
        f"{label}（{missing_price_count}只ETF存在缺价）"
        if missing_price_count
        else label
    )
    lines.append(f"| {display_label} | {render(inflow)} | {render(outflow)} |")


def _build_message(
    df: pd.DataFrame,
    trade_date: dt.date,
    history: pd.DataFrame | None = None,
    calendar: pd.DataFrame | None = None,
    summaries: list[dict[str, object]] | None = None,
) -> str:
    summaries = summaries if summaries is not None else []
    history = history if history is not None else read_parquet_if_exists(_history_file())
    if not df.empty:
        history = _upsert_history(history, df)
    if not history.empty:
        history = history.copy()
        history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce")
        if "shares_prev_date" in history:
            history["shares_prev_date"] = pd.to_datetime(
                history["shares_prev_date"], errors="coerce"
            )
        else:
            history["shares_prev_date"] = pd.NaT
        history["code"] = history["code"].astype(str).str.zfill(6)
        history = history.dropna(subset=["trade_date"])
    if calendar is None:
        calendar = resolve_akshare_api(_TRADE_CALENDAR_API)()

    lines = [
        f"# ETF资金流日报 {trade_date:%Y-%m-%d}",
        "已剔除债券类ETF及上交所货币ETF。净额单位：亿元；涨跌幅为区间价格涨跌幅；申购强度为份额净增率。",
        "",
        "| 周期 | 净流入前5（净额：亿元；涨跌幅：%；申购强度：%） | 净流出前5（净额：亿元；涨跌幅：%；申购强度：%） |",
        "|---|---|---|",
    ]
    price_cache: dict[tuple[str, str, dt.date], float] = {}
    if "trade_date" in calendar:
        calendar_dates = sorted(
            {
                date
                for date in pd.to_datetime(calendar["trade_date"], errors="coerce").dropna().dt.date
                if date <= trade_date
            }
        )
        for window_size in (1, 3, 5, 10, 20):
            _append_period_summary(
                lines, history, calendar_dates, window_size, price_cache, summaries
            )
    else:
        for window_size in (1, 3, 5, 10, 20):
            label = "今日" if window_size == 1 else f"{window_size}日"
            lines.append(f"| {label} | 交易日历不可用 | 交易日历不可用 |")
            summaries.append(
                {
                    "period": label,
                    "inflow": [],
                    "outflow": [],
                    "status": "交易日历不可用",
                }
            )
    return "\n".join(lines)


def run_etf_daily(
    run_date: dt.date | None = None,
    send_dingtalk: bool = False,
    force_send: bool = False,
    allow_non_trading_day: bool = False,
) -> pd.DataFrame | None:
    settings.ensure_dirs()
    run_date = run_date or dt.date.today()

    calendar = resolve_akshare_api(_TRADE_CALENDAR_API)()
    calendar_dates = set(
        pd.to_datetime(calendar["trade_date"], errors="coerce").dropna().dt.date
    )
    if run_date not in calendar_dates and not allow_non_trading_day:
        print(f"[etf] {run_date} is not a CN trading day; skip")
        return None
    target_date = run_date
    print(f"[etf] run_date={run_date}; target_trade_date={target_date}")

    current = fetch_shares(target_date)
    print(
        "[etf] fetched rows: "
        + ", ".join(f"{exchange}={count}" for exchange, count in current.groupby("exchange").size().items())
    )

    history = read_parquet_if_exists(_history_file())
    previous = _read_previous(history, target_date, calendar)
    df = current.merge(previous, on=["exchange", "code"], how="left")
    df["share_change"] = df["shares"] - df["shares_prev"]
    denominator = df["shares_prev"].where(df["shares_prev"] > 0)
    df["share_change_pct"] = df["share_change"] / denominator * 100

    prices = fetch_closing_prices(target_date, run_date, current)
    if not history.empty and {"trade_date", "exchange", "code", "close"}.issubset(
        history.columns
    ):
        history_dates = pd.to_datetime(history["trade_date"], errors="coerce").dt.date
        cached_prices = history.loc[
            history_dates.eq(target_date) & pd.to_numeric(history["close"], errors="coerce").gt(0),
            [column for column in ("exchange", "code", "close", "price_date", "price_source") if column in history.columns],
        ].copy()
        if not cached_prices.empty:
            prices = pd.concat([prices, cached_prices], ignore_index=True, sort=False)
            prices = prices.drop_duplicates(["exchange", "code"], keep="first")
            print(f"[etf] reused cached target-date prices: {len(cached_prices)}")
    df = df.merge(prices, on=["exchange", "code"], how="left")
    print(f"[etf] prices matched: {int(df['close'].notna().sum())}/{len(df)}")
    df["estimated_flow"] = df["share_change"] * df["close"]
    df["flow_rank"] = pd.NA
    priced = df["estimated_flow"].notna()
    df.loc[priced, "flow_rank"] = df.loc[priced, "estimated_flow"].rank(
        ascending=False, method="min"
    )
    df["ingested_at"] = pd.Timestamp.now()
    for column in OUTPUT_COLUMNS:
        if column not in df:
            df[column] = pd.NA
    df = df[OUTPUT_COLUMNS].sort_values(["exchange", "code"]).reset_index(drop=True)

    daily_path = _daily_file(target_date)
    _write_csv_atomic(df, daily_path)
    updated_history = _upsert_history(history, df)
    write_parquet_atomic(updated_history, _history_file())
    print(f"[etf] daily file saved: {daily_path}")
    print(f"[etf] history saved: {_history_file()}")

    if previous.empty:
        print("[etf] no earlier trading-day snapshot; saved baseline only, no DingTalk message")
        return df

    summaries: list[dict[str, object]] = []
    message = _build_message(
        df,
        target_date,
        history=updated_history,
        calendar=calendar,
        summaries=summaries,
    )
    print(message)
    if send_dingtalk:
        if settings.dingtalk_app_configured and not settings.dingtalk_app_ready:
            raise RuntimeError(
                "Incomplete DingTalk app configuration; set DINGTALK_CLIENT_ID, "
                "DINGTALK_CLIENT_SECRET, and DINGTALK_OPEN_CONVERSATION_ID "
                "(DINGTALK_ROBOT_CODE is optional when it matches Client ID)."
            )
        if settings.openclaw_configured and not settings.openclaw_ready:
            raise RuntimeError(
                "OpenClaw delivery requires both OPENCLAW_CHANNEL and OPENCLAW_TARGET."
            )

        title = f"ETF资金流日报 {target_date:%Y-%m-%d}"
        image_path = settings.reports_dir / f"etf_flow_{target_date:%Y%m%d}.png"
        image_ready = False

        def ensure_image() -> None:
            nonlocal image_ready
            if not image_ready:
                render_etf_report_png(summaries, target_date, image_path)
                image_ready = True

        dingtalk_configured = bool(
            settings.dingtalk_app_ready or settings.dingtalk_webhook
        )
        if dingtalk_configured:
            marker = settings.processed_dir / f"dingtalk_etf_sent_{target_date:%Y%m%d}.txt"
            if marker.exists() and not force_send:
                print(f"[etf] DingTalk already sent for {target_date}; skip duplicate")
            elif settings.dingtalk_app_ready:
                ensure_image()
                access_token = get_app_access_token(
                    settings.dingtalk_client_id,
                    settings.dingtalk_client_secret,
                )
                media_id = upload_image(access_token, image_path)
                image_message = f"![{title}]({media_id})"
                try:
                    send_group_markdown(
                        access_token,
                        settings.dingtalk_robot_code or settings.dingtalk_client_id,
                        settings.dingtalk_open_conversation_id,
                        title,
                        image_message,
                    )
                    print("[etf] infographic sent via app robot")
                except RuntimeError as exc:
                    if (
                        "code=invalid.openConversationId" not in str(exc)
                        or not settings.dingtalk_webhook
                    ):
                        raise
                    if (
                        settings.dingtalk_keyword
                        and settings.dingtalk_keyword not in image_message
                    ):
                        image_message = (
                            f"{image_message}\n{settings.dingtalk_keyword}"
                        )
                    send_markdown(settings.dingtalk_webhook, title, image_message)
                    print("[etf] infographic sent via configured group webhook")
                print(f"[etf] infographic saved: {image_path}")
                marker.write_text(
                    f"sent_at={dt.datetime.now().isoformat(timespec='seconds')}\n",
                    encoding="utf-8",
                )
            else:
                dingtalk_message = message
                if (
                    settings.dingtalk_keyword
                    and settings.dingtalk_keyword not in dingtalk_message
                ):
                    dingtalk_message = f"{dingtalk_message}\n{settings.dingtalk_keyword}"
                send_markdown(settings.dingtalk_webhook, title, dingtalk_message)
                marker.write_text(
                    f"sent_at={dt.datetime.now().isoformat(timespec='seconds')}\n",
                    encoding="utf-8",
                )
                print("[etf] report sent via DingTalk webhook")

        if settings.openclaw_ready:
            marker = settings.processed_dir / f"openclaw_etf_sent_{target_date:%Y%m%d}.txt"
            if marker.exists() and not force_send:
                print(f"[etf] OpenClaw already sent for {target_date}; skip duplicate")
            else:
                media_path = None
                if settings.openclaw_send_image:
                    ensure_image()
                    media_path = image_path
                send_openclaw_message(message, media_path=media_path)
                marker.write_text(
                    f"sent_at={dt.datetime.now().isoformat(timespec='seconds')}\n",
                    encoding="utf-8",
                )
                print(
                    f"[etf] report sent via OpenClaw channel "
                    f"{settings.openclaw_channel}"
                )

        if not dingtalk_configured and not settings.openclaw_ready:
            print("[etf] no DingTalk or OpenClaw route configured; skip send")
    return df


def _write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(temp_path, index=False, encoding="utf-8-sig")
    temp_path.replace(path)


def _upsert_history(history: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        combined = incoming.copy()
    else:
        # Drop columns that are entirely null in one input before concat; they
        # are re-created below and this avoids pandas' empty-column dtype warning.
        left = history.dropna(axis=1, how="all")
        right = incoming.dropna(axis=1, how="all")
        combined = pd.concat([left, right], ignore_index=True, sort=False)
    for column in OUTPUT_COLUMNS:
        if column not in combined:
            combined[column] = pd.NA
    combined["trade_date"] = pd.to_datetime(combined["trade_date"], errors="coerce")
    combined["code"] = combined["code"].astype(str).str.zfill(6)
    combined["exchange"] = combined["exchange"].astype(str)
    combined = combined.dropna(subset=ETF_KEY)
    if "ingested_at" in combined:
        combined["ingested_at"] = pd.to_datetime(combined["ingested_at"], errors="coerce")
        combined = combined.sort_values("ingested_at")
    combined = combined.drop_duplicates(ETF_KEY, keep="last")
    return combined.sort_values(ETF_KEY).reset_index(drop=True)
