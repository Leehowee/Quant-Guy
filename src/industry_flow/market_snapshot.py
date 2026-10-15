"""Capture a dated post-close market-regime observation through AKShare."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from .market_breadth import fetch_tencent_a_share_spot
from .trading_rules import assess_market


def build_market_report_summary(
    snapshot: pd.DataFrame | None,
    trade_date: dt.date,
    unavailable_reason: str | None = None,
) -> dict[str, str]:
    """Format a same-day market snapshot for the industry text and image reports."""
    unavailable = {
        "status": "环境：数据不足 | ETF候选观察：暂停",
        "breadth": "A股上涨比例：无法获取",
        "trend": "上证指数20日趋势：无法获取",
        "note": unavailable_reason or "未取得与日报日期匹配的完整市场快照。",
    }
    if snapshot is None or snapshot.empty:
        return unavailable

    row = snapshot.iloc[0]
    snapshot_date = pd.to_datetime(row.get("trade_date"), errors="coerce")
    if pd.isna(snapshot_date) or snapshot_date.date() != trade_date:
        unavailable["note"] = unavailable_reason or (
            f"市场快照日期与日报不匹配（期望 {trade_date.isoformat()}）。"
        )
        return unavailable

    index_close = pd.to_numeric(row.get("index_close"), errors="coerce")
    index_sma20 = pd.to_numeric(row.get("index_sma20"), errors="coerce")
    advancer_ratio = pd.to_numeric(row.get("advancer_ratio"), errors="coerce")
    assessment = assess_market(index_close, index_sma20, advancer_ratio)
    breadth_count = pd.to_numeric(row.get("breadth_count"), errors="coerce")
    breadth_count_text = (
        f"（样本 {int(breadth_count):,} 只）" if pd.notna(breadth_count) else ""
    )
    breadth = (
        f"A股上涨比例：{advancer_ratio:.1%}{breadth_count_text}"
        if pd.notna(advancer_ratio) and 0 <= advancer_ratio <= 1
        else "A股上涨比例：无法获取"
    )
    if pd.notna(index_close) and pd.notna(index_sma20) and index_close > 0 and index_sma20 > 0:
        relation = "高于" if index_close > index_sma20 else "未高于"
        trend = f"上证指数：{index_close:,.2f}，{relation}20日均线 {index_sma20:,.2f}"
    else:
        trend = "上证指数20日趋势：无法获取"
    captured_at = row.get("captured_at")
    confidence = row.get("date_confidence")
    footnotes = []
    if pd.notna(captured_at):
        footnotes.append(f"采集时间 {captured_at}")
    if pd.notna(confidence):
        footnotes.append(str(confidence))
    note = "；".join(footnotes) or "日期按盘后任务日推定。"

    status = (
        f"环境：{assessment.regime.value} | 策略风险敞口上限：{assessment.exposure_cap_pct:.0%}"
        if assessment.regime.value != "数据不足"
        else "环境：数据不足 | ETF候选观察：暂停 | 策略风险敞口上限：0%"
    )
    return {"status": status, "breadth": breadth, "trend": trend, "note": note}


def build_market_snapshot(
    trade_date: dt.date,
    breadth: pd.DataFrame,
    index_history: pd.DataFrame,
    *,
    index_source: str = "stock_zh_index_daily_em",
    breadth_source: str = "stock_zh_a_spot_em",
) -> pd.DataFrame:
    """Build one market row, retaining index evidence when breadth is unavailable."""
    breadth_column = next(
        (column for column in ("涨跌幅", "zdf") if column in breadth.columns),
        None,
    )
    changes = (
        pd.to_numeric(breadth[breadth_column], errors="coerce").dropna()
        if breadth_column is not None
        else pd.Series(dtype=float)
    )
    required_index = {"date", "close"}
    if not required_index.issubset(index_history.columns):
        raise RuntimeError(f"Index history is missing columns: {sorted(required_index.difference(index_history.columns))}")
    index = index_history[["date", "close"]].copy()
    index["date"] = pd.to_datetime(index["date"], errors="coerce").dt.date
    index["close"] = pd.to_numeric(index["close"], errors="coerce")
    index = index.dropna(subset=["date", "close"]).drop_duplicates("date", keep="last")
    index = index[index["date"] <= trade_date].sort_values("date").tail(20)
    if len(index) < 20 or index.iloc[-1]["date"] != trade_date:
        raise RuntimeError(f"Index history has no complete 20-session window ending on {trade_date}")
    close = float(index.iloc[-1]["close"])
    moving_average = float(index["close"].mean())
    if close <= 0 or moving_average <= 0:
        raise RuntimeError("Index close or moving average must be positive")
    return pd.DataFrame.from_records([{
        "trade_date": trade_date.isoformat(),
        "index_close": close,
        "index_sma20": moving_average,
        "advancer_ratio": float((changes > 0).mean()) if not changes.empty else pd.NA,
        "breadth_count": int(changes.size) if not changes.empty else pd.NA,
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "date_confidence": "盘后采集日期推定" if not changes.empty else "指数日线日期匹配；市场宽度缺失",
        "source": (
            f"{breadth_source}+{index_source}"
            if not changes.empty
            else f"{index_source}; market_breadth_unavailable"
        ),
    }])


def build_market_index_backfill(
    index_history: pd.DataFrame,
    end_date: dt.date,
    *,
    sessions: int = 40,
) -> pd.DataFrame:
    """Build dated index trend rows without inventing historical market breadth."""
    if sessions < 20:
        raise ValueError("指数回溯至少需要20个交易日")
    required = {"date", "close"}
    if not required.issubset(index_history.columns):
        raise RuntimeError(
            f"Index history is missing columns: {sorted(required.difference(index_history.columns))}"
        )
    index = index_history[["date", "close"]].copy()
    index["date"] = pd.to_datetime(index["date"], errors="coerce").dt.date
    index["close"] = pd.to_numeric(index["close"], errors="coerce")
    index = index.dropna(subset=["date", "close"])
    index = index.loc[index["close"].gt(0) & index["date"].le(end_date)]
    index = index.drop_duplicates("date", keep="last").sort_values("date")
    index["index_sma20"] = index["close"].rolling(20, min_periods=20).mean()
    index = index.dropna(subset=["index_sma20"]).tail(sessions)
    if index.empty or index.iloc[-1]["date"] != end_date:
        raise RuntimeError(f"Index history has no valid close on {end_date}")
    captured_at = dt.datetime.now().isoformat(timespec="seconds")
    return pd.DataFrame.from_records([
        {
            "trade_date": row.date.isoformat(),
            "index_close": float(row.close),
            "index_sma20": float(row.index_sma20),
            "advancer_ratio": pd.NA,
            "breadth_count": pd.NA,
            "captured_at": captured_at,
            "date_confidence": "历史指数日线；市场宽度未回溯",
            "source": "AKShare_stock_zh_index_daily_sina",
        }
        for row in index.itertuples(index=False)
    ])


def capture_market_snapshot(trade_date: dt.date) -> pd.DataFrame:
    """Fetch market evidence independently so one source failure preserves the other."""
    from .config import data_sources, resolve_akshare_api

    source = data_sources.get("market", {})
    errors: list[str] = []
    breadth = pd.DataFrame()
    breadth_source = ""
    try:
        breadth_api = resolve_akshare_api(source.get("breadth_api", "stock_zh_a_spot_em"))
        breadth = breadth_api()
        if _market_breadth_changes(breadth).empty:
            raise RuntimeError("no valid涨跌幅 rows")
        breadth_source = f"AKShare_{source.get('breadth_api', 'stock_zh_a_spot_em')}"
    except Exception as exc:
        errors.append(f"market breadth {type(exc).__name__}")
        breadth = pd.DataFrame()

    if breadth.empty and source.get("breadth_fallback", "tencent") == "tencent":
        try:
            breadth = fetch_tencent_a_share_spot(
                use_environment_proxy=bool(
                    source.get("tencent_use_environment_proxy", False)
                ),
                max_workers=int(source.get("tencent_workers", 5)),
            )
            if _market_breadth_changes(breadth).empty:
                raise RuntimeError("no valid Tencent zdf rows")
            breadth_source = "Tencent_stock_zh_a_spot_tx_direct"
        except Exception as exc:
            errors.append(f"market breadth Tencent {type(exc).__name__}")
            breadth = pd.DataFrame()

    index_history = pd.DataFrame()
    index_api_used = ""
    index_api_names = (
        source.get("index_api", "stock_zh_index_daily_em"),
        source.get("index_history_api", "stock_zh_index_daily"),
    )
    for api_name in dict.fromkeys(index_api_names):
        try:
            index_api = resolve_akshare_api(api_name)
            symbol = (
                source.get("index_history_symbol", "sh000001")
                if api_name == source.get("index_history_api")
                else source.get("index_symbol", "sh000001")
            )
            index_history = index_api(symbol=symbol)
            if not index_history.empty:
                index_api_used = api_name
                break
        except Exception as exc:
            errors.append(f"index {api_name} {type(exc).__name__}")
    if index_history.empty:
        raise RuntimeError("market index history unavailable: " + "; ".join(errors))
    return build_market_snapshot(
        trade_date,
        breadth,
        index_history,
        index_source=index_api_used,
        breadth_source=breadth_source or "market_breadth_unavailable",
    )


def _market_breadth_changes(breadth: pd.DataFrame) -> pd.Series:
    """Return valid per-stock change values from supported spot schemas."""
    if breadth is None or breadth.empty:
        return pd.Series(dtype=float)
    column = next(
        (name for name in ("涨跌幅", "zdf") if name in breadth.columns),
        None,
    )
    if column is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(breadth[column], errors="coerce").dropna()


def save_market_snapshot(snapshot: pd.DataFrame, processed_dir: Path | None = None) -> Path:
    if snapshot.empty or "trade_date" not in snapshot.columns:
        raise ValueError("不能保存空的市场环境记录")
    if processed_dir is None:
        from .config import settings

        processed_dir = settings.processed_dir
    directory = processed_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "market_regime_history.csv"
    return _upsert_market_history(snapshot, path)


def save_market_index_backfill(
    backfill: pd.DataFrame,
    processed_dir: Path | None = None,
) -> Path:
    """Save historical index data while preserving any observed breadth fields."""
    if backfill.empty or "trade_date" not in backfill.columns:
        raise ValueError("不能保存空的指数历史回溯")
    if processed_dir is None:
        from .config import settings

        processed_dir = settings.processed_dir
    processed_dir.mkdir(parents=True, exist_ok=True)
    return _upsert_market_history(
        backfill,
        processed_dir / "market_regime_history.csv",
        preserve_existing_market_fields=True,
    )


def _upsert_market_history(
    incoming: pd.DataFrame,
    path: Path,
    *,
    preserve_existing_market_fields: bool = False,
) -> Path:
    incoming = incoming.copy()
    incoming["trade_date"] = pd.to_datetime(incoming["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    incoming = incoming.dropna(subset=["trade_date"]).drop_duplicates("trade_date", keep="last")
    if path.exists():
        existing = pd.read_csv(path, dtype={"trade_date": str})
        existing["trade_date"] = pd.to_datetime(existing["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        existing = existing.dropna(subset=["trade_date"]).drop_duplicates("trade_date", keep="last")
        combined = incoming.set_index("trade_date").combine_first(existing.set_index("trade_date"))
        # Preserve actual captured breadth and its provenance over partial index-only rows.
        if preserve_existing_market_fields:
            for column in ("advancer_ratio", "breadth_count", "captured_at", "date_confidence"):
                if column in existing.columns:
                    combined[column] = existing.set_index("trade_date")[column].combine_first(
                        incoming.set_index("trade_date")[column]
                        if column in incoming.columns
                        else pd.Series(dtype=object)
                    )
        if preserve_existing_market_fields and "source" in combined.columns:
            old_source = existing.set_index("trade_date").get("source")
            new_source = incoming.set_index("trade_date").get("source")
            if old_source is not None and new_source is not None:
                both = old_source.notna() & new_source.notna()
                combined.loc[both, "source"] = (
                    old_source.loc[both].astype(str) + "; " + new_source.loc[both].astype(str)
                )
        combined = combined.reset_index()
    else:
        combined = incoming
    combined = combined.drop_duplicates("trade_date", keep="last").sort_values("trade_date")
    temporary = path.with_suffix(".csv.tmp")
    combined.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)
    return path
