from __future__ import annotations

from pathlib import Path
import pandas as pd

KEY = ["trade_date", "industry_name"]


def normalize_key(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out["industry_name"] = out["industry_name"].astype(str).str.strip()
    return out.dropna(subset=["trade_date", "industry_name"])


def merge_upsert(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
    key: list[str] | None = None,
) -> pd.DataFrame:
    key = key or KEY
    if existing is None or existing.empty:
        out = incoming.copy()
    elif incoming is None or incoming.empty:
        out = existing.copy()
    else:
        out = pd.concat([existing, incoming], ignore_index=True, sort=False)

    out = normalize_key(out)
    out = (
        out.sort_values(["trade_date", "industry_name", "ingested_at"])
        .drop_duplicates(key, keep="last")
        .sort_values(["trade_date", "industry_name"])
        .reset_index(drop=True)
    )
    return out


def read_parquet_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def upsert_parquet(
    incoming: pd.DataFrame,
    path: Path,
    key: list[str] | None = None,
) -> pd.DataFrame:
    existing = read_parquet_if_exists(path)
    merged = merge_upsert(existing, incoming, key=key)
    write_parquet_atomic(merged, path)
    return merged


def save_daily_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
