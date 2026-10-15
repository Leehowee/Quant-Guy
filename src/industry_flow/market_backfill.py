"""Backfill index trend history without fabricating historical breadth."""

from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from .config import data_sources, resolve_akshare_api, settings
from .market_snapshot import build_market_index_backfill, save_market_index_backfill
from .storage import read_parquet_if_exists


def backfill_market_index(*, end_date: dt.date | None = None, sessions: int = 40) -> pd.DataFrame:
    source = data_sources.get("market", {})
    api_name = source.get("index_history_api", "stock_zh_index_daily")
    symbol = source.get("index_history_symbol", "sh000001")
    history = resolve_akshare_api(api_name)(symbol=symbol)
    if end_date is None:
        etf_history = read_parquet_if_exists(settings.processed_dir / "etf_flow_history.parquet")
        if not etf_history.empty and "trade_date" in etf_history:
            dates = pd.to_datetime(etf_history["trade_date"], errors="coerce").dropna()
            end_date = dates.max().date()
        else:
            if "date" not in history.columns:
                raise RuntimeError("指数历史缺少 date 字段")
            dates = pd.to_datetime(history["date"], errors="coerce").dropna()
            if dates.empty:
                raise RuntimeError("ETF历史和指数历史都没有可用日期")
            end_date = dates.max().date()
    backfill = build_market_index_backfill(history, end_date, sessions=sessions)
    save_market_index_backfill(backfill, settings.processed_dir)
    return backfill


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Shanghai Composite index trend history")
    parser.add_argument("--sessions", type=int, default=40, help="number of dated rows to save")
    parser.add_argument("--end-date", help="last date YYYY-MM-DD; defaults to latest ETF history date")
    args = parser.parse_args()
    if args.end_date:
        end_date = dt.date.fromisoformat(args.end_date)
    else:
        end_date = None
    backfill = backfill_market_index(end_date=end_date, sessions=args.sessions)
    print(
        f"[market-backfill] saved {len(backfill)} index sessions "
        f"{backfill.iloc[0]['trade_date']}..{backfill.iloc[-1]['trade_date']}; "
        "historical market breadth remains unavailable and will not be inferred"
    )


if __name__ == "__main__":
    main()
