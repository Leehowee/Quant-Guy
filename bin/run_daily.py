import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industry_flow.daily import run_daily

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch industry fund-flow data and optionally send the daily digest"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="send the DingTalk report; disabled by default",
    )
    parser.add_argument(
        "--force-send",
        action="store_true",
        help="resend to DingTalk even if today's report was already sent",
    )
    args = parser.parse_args()
    if args.force_send and not args.send:
        parser.error("--force-send requires --send")
    run_daily(send_dingtalk=args.send, force_send=args.force_send)
