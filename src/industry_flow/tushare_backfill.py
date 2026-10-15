"""One-off historical market breadth backfill using Tushare daily bars."""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
from pathlib import Path
import time
from typing import Any

import pandas as pd
import requests

from .config import settings
from .market_snapshot import save_market_snapshot


TUSHARE_API_URL = "https://api.tushare.pro"
TUSHARE_DAILY_FIELDS = "ts_code,trade_date,pct_chg"


def fetch_tushare_daily(
    trade_date: dt.date,
    *,
    token: str,
    session: Any | None = None,
    timeout: float = 30,
    retries: int = 2,
    retry_sleep: float = 1,
) -> pd.DataFrame:
    """Fetch the full-market unadjusted daily bars for one trading date."""
    if not token.strip():
        raise ValueError("Tushare token cannot be empty")
    if session is None:
        session = requests.Session()
    payload = {
        "api_name": "daily",
        "token": token,
        "params": {"trade_date": trade_date.strftime("%Y%m%d")},
        "fields": TUSHARE_DAILY_FIELDS,
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.post(TUSHARE_API_URL, json=payload, timeout=timeout)
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                # The response message contains the service error, never the submitted token.
                raise RuntimeError(
                    f"Tushare daily API error ({body.get('code')}): "
                    f"{str(body.get('msg', 'unknown error'))[:200]}"
                )
            data = body.get("data") or {}
            fields = data.get("fields") or []
            items = data.get("items") or []
            frame = pd.DataFrame.from_records(items, columns=fields)
            required = {"ts_code", "trade_date", "pct_chg"}
            if not required.issubset(frame.columns):
                raise RuntimeError("Tushare daily response lacks required fields")
            frame["trade_date"] = frame["trade_date"].astype(str)
            expected = trade_date.strftime("%Y%m%d")
            frame = frame.loc[frame["trade_date"].eq(expected)].copy()
            frame["pct_chg"] = pd.to_numeric(frame["pct_chg"], errors="coerce")
            frame = frame.dropna(subset=["ts_code", "pct_chg"])
            frame = frame.drop_duplicates("ts_code", keep="last")
            if frame.empty:
                raise RuntimeError(
                    f"Tushare returned no daily bars for {trade_date.isoformat()}"
                )
            return frame.reset_index(drop=True)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep * (attempt + 1))
    raise RuntimeError(
        f"Tushare daily request failed for {trade_date.isoformat()} "
        f"after {retries + 1} attempts"
    ) from last_error


def build_tushare_breadth_row(
    trade_date: dt.date,
    daily_bars: pd.DataFrame,
    *,
    minimum_rows: int = 1,
) -> pd.DataFrame:
    """Count advancing stocks from that date's valid Tushare daily bars."""
    required = {"ts_code", "trade_date", "pct_chg"}
    if not required.issubset(daily_bars.columns):
        raise RuntimeError(
            f"Tushare daily bars missing columns: "
            f"{sorted(required.difference(daily_bars.columns))}"
        )
    bars = daily_bars.copy()
    bars["trade_date"] = bars["trade_date"].astype(str).str.replace("-", "", regex=False)
    expected = trade_date.strftime("%Y%m%d")
    bars = bars.loc[bars["trade_date"].eq(expected)].copy()
    bars["pct_chg"] = pd.to_numeric(bars["pct_chg"], errors="coerce")
    bars = bars.dropna(subset=["ts_code", "pct_chg"]).drop_duplicates(
        "ts_code", keep="last"
    )
    if len(bars) < minimum_rows:
        raise RuntimeError(
            f"Tushare daily sample for {trade_date.isoformat()} is incomplete: "
            f"{len(bars)} rows, need at least {minimum_rows}"
        )
    return pd.DataFrame.from_records([{
        "trade_date": trade_date.isoformat(),
        "index_close": pd.NA,
        "index_sma20": pd.NA,
        "advancer_ratio": float(bars["pct_chg"].gt(0).mean()),
        "breadth_count": int(len(bars)),
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "date_confidence": "Tushare历史日线；停牌股票无日线且不计入分母",
        "source": "Tushare_daily_historical",
    }])


def backfill_missing_tushare_breadth(
    *,
    end_date: dt.date | None = None,
    limit: int | None = None,
    processed_dir: Path | None = None,
    token: str | None = None,
    minimum_coverage: float = 0.80,
    session: Any | None = None,
    retry_sleep: float = 1,
) -> pd.DataFrame:
    """Fill only missing breadth dates in market_regime_history.csv.

    Existing real-time breadth rows are retained.  For backfilled dates the
    sample floor is based on the latest observed live breadth count, so a
    partial Tushare response is rejected rather than stored as full-market data.
    """
    resolved_token = (token or os.getenv("TUSHARE_TOKEN", "")).strip()
    if not resolved_token:
        raise RuntimeError(
            "TUSHARE_TOKEN is not configured; add it to the local .env file "
            "and rerun this one-off backfill"
        )
    if not 0 < minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be in (0, 1]")
    if processed_dir is None:
        processed_dir = settings.processed_dir
    history_path = processed_dir / "market_regime_history.csv"
    if not history_path.exists():
        raise FileNotFoundError(f"Market history file not found: {history_path}")

    history = pd.read_csv(history_path, dtype={"trade_date": str})
    if "advancer_ratio" not in history.columns or "trade_date" not in history.columns:
        raise RuntimeError("Market history lacks trade_date or advancer_ratio")
    dates = pd.to_datetime(history["trade_date"], errors="coerce")
    ratios = pd.to_numeric(history["advancer_ratio"], errors="coerce")
    missing = history.loc[ratios.isna() & dates.notna(), "trade_date"].astype(str)
    if end_date is not None:
        missing = missing.loc[pd.to_datetime(missing).dt.date.le(end_date)]
    missing_dates = sorted({dt.date.fromisoformat(value[:10]) for value in missing})
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        missing_dates = missing_dates[-limit:]
    if not missing_dates:
        return pd.DataFrame()

    known_counts = pd.to_numeric(
        history.loc[ratios.notna(), "breadth_count"], errors="coerce"
    ).dropna()
    if known_counts.empty:
        raise RuntimeError(
            "Cannot validate Tushare coverage without one verified live breadth count"
        )
    minimum_rows = math.ceil(float(known_counts.max()) * minimum_coverage)
    saved: list[dict] = []
    for trade_date in missing_dates:
        bars = fetch_tushare_daily(
            trade_date,
            token=resolved_token,
            session=session,
            retry_sleep=retry_sleep,
        )
        row = build_tushare_breadth_row(
            trade_date,
            bars,
            minimum_rows=minimum_rows,
        )
        save_market_snapshot(row, processed_dir)
        saved.extend(row.to_dict(orient="records"))
        time.sleep(retry_sleep)
    return pd.DataFrame.from_records(saved)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill missing market breadth dates via Tushare daily bars"
    )
    parser.add_argument("--end-date", help="latest date YYYY-MM-DD; defaults to all missing dates")
    parser.add_argument("--limit", type=int, help="only fill the latest N missing dates")
    args = parser.parse_args()
    end_date = dt.date.fromisoformat(args.end_date) if args.end_date else None
    rows = backfill_missing_tushare_breadth(end_date=end_date, limit=args.limit)
    if rows.empty:
        print("[tushare-backfill] no missing market breadth dates")
        return
    print(
        f"[tushare-backfill] saved {len(rows)} dates "
        f"{rows.iloc[0]['trade_date']}..{rows.iloc[-1]['trade_date']}; "
        f"{rows.iloc[-1]['breadth_count']} daily bars on the last date; "
        "suspensions are excluded from the historical denominator"
    )


if __name__ == "__main__":
    main()
