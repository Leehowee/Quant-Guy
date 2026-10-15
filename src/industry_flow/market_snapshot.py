"""Capture a dated post-close market-regime observation through AKShare."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd



def build_market_snapshot(
    trade_date: dt.date,
    breadth: pd.DataFrame,
    index_history: pd.DataFrame,
) -> pd.DataFrame:
    """Build one market row and reject stale or incomplete source dates."""
    if "涨跌幅" not in breadth.columns:
        raise RuntimeError("Market breadth source has no 涨跌幅 field")
    changes = pd.to_numeric(breadth["涨跌幅"], errors="coerce").dropna()
    if changes.empty:
        raise RuntimeError("Market breadth source has no valid stock changes")
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
        "advancer_ratio": float((changes > 0).mean()),
        "breadth_count": int(changes.size),
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "date_confidence": "盘后采集日期推定",
        "source": "AKShare_stock_zh_a_spot_em+stock_zh_index_daily_em",
    }])


def capture_market_snapshot(trade_date: dt.date) -> pd.DataFrame:
    """Fetch breadth and index history, returning a single validated row."""
    from .config import data_sources, resolve_akshare_api

    source = data_sources.get("market", {})
    breadth_api = resolve_akshare_api(source.get("breadth_api", "stock_zh_a_spot_em"))
    index_api = resolve_akshare_api(source.get("index_api", "stock_zh_index_daily_em"))
    breadth = breadth_api()
    index_history = index_api(symbol=source.get("index_symbol", "sh000001"))
    return build_market_snapshot(trade_date, breadth, index_history)


def save_market_snapshot(snapshot: pd.DataFrame, processed_dir: Path | None = None) -> Path:
    if snapshot.empty or "trade_date" not in snapshot.columns:
        raise ValueError("不能保存空的市场环境记录")
    if processed_dir is None:
        from .config import settings

        processed_dir = settings.processed_dir
    directory = processed_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "market_regime_history.csv"
    if path.exists():
        existing = pd.read_csv(path)
        snapshot = pd.concat([existing, snapshot], ignore_index=True, sort=False)
    snapshot = snapshot.drop_duplicates("trade_date", keep="last").sort_values("trade_date")
    temporary = path.with_suffix(".csv.tmp")
    snapshot.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)
    return path
