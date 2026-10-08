import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from etf_flow.daily import run_etf_daily


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Save previous-trading-day ETF share and estimated-flow data"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="send the DingTalk digest; disabled by default",
    )
    parser.add_argument(
        "--force-send",
        action="store_true",
        help="send even if a digest for the target trading date was already sent",
    )
    parser.add_argument(
        "--allow-non-trading-day",
        action="store_true",
        help="allow a manual run on a holiday/weekend to fetch the most recent trading day",
    )
    args = parser.parse_args()
    run_etf_daily(
        send_dingtalk=args.send,
        force_send=args.force_send,
        allow_non_trading_day=args.allow_non_trading_day,
    )
