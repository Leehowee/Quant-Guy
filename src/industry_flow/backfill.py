from __future__ import annotations

import json
import os
import time
import pandas as pd

from .config import settings
from .features import build_features
from .source import (
    fetch_current_industries,
    fetch_industry_flow_history,
)
from .storage import (
    read_parquet_if_exists,
    upsert_parquet,
    write_parquet_atomic,
)

CHECKPOINT_EVERY = 5
MAX_CONSECUTIVE_FAILURES = 3
MAX_FAILURE_COOLDOWNS = 3
FAILURE_COOLDOWN_SECONDS = max(
    1, int(os.getenv("BACKFILL_FAILURE_COOLDOWN_SECONDS", "60"))
)
REQUEST_DELAY_SECONDS = max(
    0.0, float(os.getenv("BACKFILL_REQUEST_DELAY_SECONDS", "1.0"))
)


def _write_json_atomic(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def run_backfill() -> None:
    settings.ensure_dirs()

    industries = fetch_current_industries()
    print(f"[backfill] current industries: {len(industries)}")

    chunks = []
    pending_names = []
    failed = []
    consecutive_failures = 0
    cooldown_count = 0
    merged = None
    progress = {}
    if settings.backfill_progress_file.exists():
        try:
            progress = json.loads(
                settings.backfill_progress_file.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            progress = {}

    if set(progress.get("industry_universe", [])) == set(industries):
        completed = set(progress.get("completed_industries", []))
        print(f"[backfill] resuming: {len(completed)}/{len(industries)} done")
    else:
        # Recover work written by older runs that checkpointed Parquet before
        # the resumable manifest existed. Eastmoney currently returns 120
        # daily rows for each established industry when lmt=0.
        existing = read_parquet_if_exists(settings.history_file)
        completed = set()
        if not existing.empty:
            row_counts = existing.groupby("industry_name")["trade_date"].nunique()
            completed = {
                name
                for name, count in row_counts.items()
                if name in industries and count >= 120
            }
        progress = {
            "industry_universe": industries,
            "completed_industries": [],
        }

    def save_progress() -> None:
        progress["completed_industries"] = sorted(completed)
        _write_json_atomic(settings.backfill_progress_file, progress)

    if completed:
        save_progress()
        print(f"[backfill] recovered {len(completed)} industries from Parquet")

    def flush_checkpoint() -> None:
        nonlocal merged
        if chunks:
            pending = pd.concat(chunks, ignore_index=True, sort=False)
            merged = upsert_parquet(pending, settings.history_file)
            chunks.clear()
            print(
                f"[backfill] checkpoint saved: {len(pending)} new rows; "
                f"history now has {len(merged)} rows"
            )

        if not pending_names:
            return

        completed.update(pending_names)
        pending_names.clear()
        save_progress()

    for i, name in enumerate(industries, 1):
        if name in completed:
            continue

        try:
            df = fetch_industry_flow_history(name)
            if not df.empty:
                chunks.append(df)
            consecutive_failures = 0
            pending_names.append(name)

            print(
                f"[backfill] {i}/{len(industries)} "
                f"OK {name}: {len(df)} rows"
            )

        except Exception as exc:
            failed.append((name, str(exc)))
            consecutive_failures += 1
            print(
                f"[backfill] {i}/{len(industries)} "
                f"FAIL {name}: {exc}"
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                # Preserve successful work, then give a transient upstream
                # outage a chance to recover before continuing the universe.
                flush_checkpoint()
                cooldown_count += 1
                if cooldown_count > MAX_FAILURE_COOLDOWNS:
                    raise RuntimeError(
                        "Backfill stopped after repeated failure cooldowns. "
                        "Successful rows are checkpointed; fix connectivity "
                        "and rerun to retry unfinished industries."
                    ) from exc
                print(
                    f"[backfill] {MAX_CONSECUTIVE_FAILURES} consecutive "
                    f"failures; checkpoint saved, cooling down for "
                    f"{FAILURE_COOLDOWN_SECONDS}s "
                    f"({cooldown_count}/{MAX_FAILURE_COOLDOWNS})"
                )
                time.sleep(FAILURE_COOLDOWN_SECONDS)
                consecutive_failures = 0

        if i % CHECKPOINT_EVERY == 0:
            flush_checkpoint()

        # Keep requests below one per second by default to reduce upstream
        # throttling and connection resets during a long historical run.
        time.sleep(REQUEST_DELAY_SECONDS)

    flush_checkpoint()

    if merged is None:
        merged = read_parquet_if_exists(settings.history_file)

    if merged.empty:
        raise RuntimeError(
            "No historical industry fund-flow data was downloaded."
        )

    features = build_features(merged)

    write_parquet_atomic(
        features,
        settings.feature_file,
    )

    if failed:
        # Failed industries remain absent from completed_industries, so the
        # next run resumes them. Do not publish a completion marker for a
        # partial history universe.
        save_progress()
        print(
            f"[backfill] incomplete: {len(failed)} industry requests failed; "
            "partial features were written and progress was retained"
        )
        for name, err in failed:
            print(f"  - {name}: {err}")
        return

    _write_json_atomic(
        settings.backfill_complete_file,
        {
            "completed_at": pd.Timestamp.now().isoformat(),
            "industry_universe": industries,
            "industry_count": len(industries),
            "history_rows": len(merged),
        },
    )
    settings.backfill_progress_file.unlink(missing_ok=True)

    print(f"[backfill] history rows: {len(merged)}")
    print(f"[backfill] feature rows: {len(features)}")
    print(f"[backfill] failed industries: {len(failed)}")

    for name, err in failed:
        print(f"  - {name}: {err}")
