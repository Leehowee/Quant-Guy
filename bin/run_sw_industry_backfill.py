from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industry_flow.sw_industry_backfill import backfill_recent_sw_industry_flow


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill recent Shanghai/Shenzhen A-share flow grouped by Shenwan L2"
    )
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--end-date", help="latest trade date YYYY-MM-DD")
    parser.add_argument(
        "--force",
        action="store_true",
        help="refetch and replace the selected recent sessions",
    )
    args = parser.parse_args()
    end_date = dt.date.fromisoformat(args.end_date) if args.end_date else None
    result = backfill_recent_sw_industry_flow(
        sessions=args.sessions,
        end_date=end_date,
        force=args.force,
    )
    if result.empty:
        print("[sw-backfill] no missing Shenwan L2 sessions")
        return
    dates = pd.to_datetime(result["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    print(
        f"[sw-backfill] saved {result['trade_date'].nunique()} sessions "
        f"{dates.min()}..{dates.max()}"
    )


if __name__ == "__main__":
    main()
