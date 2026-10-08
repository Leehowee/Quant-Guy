import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industry_flow.config import settings
from industry_flow.digest import build_daily_digest

if __name__ == "__main__":
    if not settings.feature_file.exists():
        raise SystemExit(
            "feature file not found; "
            "run backfill or daily first."
        )

    df = pd.read_parquet(
        settings.feature_file
    )

    print(
        build_daily_digest(
            df,
            top_n=settings.digest_top_n,
        )
    )
