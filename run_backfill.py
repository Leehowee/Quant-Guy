import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from industry_flow.backfill import run_backfill

if __name__ == "__main__":
    run_backfill()
