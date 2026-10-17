"""Seed recent Shenwan L2 flow history so rolling windows are usable."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd

from .config import settings
from .storage import merge_upsert, read_parquet_if_exists, save_daily_csv, write_parquet_atomic
from .sw_industry_flow import (
    aggregate_sw_l2_daily_flow,
    fetch_open_trade_dates,
    fetch_tushare_daily_universe,
    fetch_tushare_moneyflow,
    refresh_sw_l2_membership_cache,
)


def backfill_recent_sw_industry_flow(
    *,
    sessions: int = 20,
    end_date: dt.date | None = None,
    force: bool = False,
    processed_dir: Path | None = None,
    raw_daily_dir: Path | None = None,
    token: str | None = None,
    request_sleep: float = 0.2,
) -> pd.DataFrame:
    """Backfill recent missing trade sessions without replacing other history."""
    if sessions < 1:
        raise ValueError("sessions must be positive")
    processed_dir = processed_dir or settings.processed_dir
    raw_daily_dir = raw_daily_dir or settings.raw_daily_dir
    processed_dir.mkdir(parents=True, exist_ok=True)
    dates = fetch_open_trade_dates(end_date or dt.date.today(), sessions, token=token)
    history_path = processed_dir / "industry_flow_sw_l2_history.parquet"
    membership_path = processed_dir / "sw_l2_membership_history.csv"
    history = read_parquet_if_exists(history_path)
    memberships = refresh_sw_l2_membership_cache(
        membership_path,
        token=token,
        force_full_history=force and not membership_path.exists(),
    )
    known_dates = set()
    if not history.empty and "trade_date" in history.columns:
        known_dates = set(
            pd.to_datetime(history["trade_date"], errors="coerce")
            .dropna()
            .dt.strftime("%Y%m%d")
        )

    saved: list[pd.DataFrame] = []
    for trade_date in dates:
        date_key = trade_date.strftime("%Y%m%d")
        if date_key in known_dates and not force:
            continue
        print(f"[sw-backfill] fetching {trade_date.isoformat()}")
        universe = fetch_tushare_daily_universe(trade_date, token=token)
        moneyflow = fetch_tushare_moneyflow(trade_date, token=token)
        daily = aggregate_sw_l2_daily_flow(
            trade_date,
            universe,
            moneyflow,
            memberships,
        )
        raw_path = raw_daily_dir / f"industry_flow_{date_key}_sw_l2_tushare.csv"
        save_daily_csv(daily, raw_path)
        history = merge_upsert(
            history,
            daily,
            key=["trade_date", "industry_code"],
        )
        write_parquet_atomic(history, history_path)
        saved.append(daily)
        print(
            f"[sw-backfill] saved {len(daily)} sectors; "
            f"stock-flow coverage={daily['market_coverage_pct'].iloc[0]:.1f}%"
        )
        if request_sleep > 0:
            time.sleep(request_sleep)
    if not saved:
        return pd.DataFrame()
    return pd.concat(saved, ignore_index=True)
