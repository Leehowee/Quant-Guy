"""Resilient direct Tencent source for the all-A-share market breadth snapshot."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import threading
import time
from typing import Callable

import pandas as pd
import requests


TENCENT_A_SHARE_RANK_URL = (
    "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
)


def fetch_tencent_a_share_spot(
    *,
    page_size: int = 200,
    max_workers: int = 5,
    retries: int = 2,
    timeout: tuple[float, float] = (10, 20),
    use_environment_proxy: bool = False,
    session_factory: Callable[[], requests.Session] | None = None,
    retry_sleep: float = 0.5,
) -> pd.DataFrame:
    """Fetch the full Tencent A-share quote board and return its raw fields.

    Tencent caps the board endpoint at 200 rows per request. Pages are fetched
    concurrently with bounded retries; environment proxies are disabled by
    default because a dead local proxy can otherwise block this fallback too.
    """
    if page_size != 200:
        raise ValueError("Tencent quote endpoint requires page_size=200")
    if max_workers < 1 or retries < 0:
        raise ValueError("max_workers must be positive and retries cannot be negative")
    if session_factory is None:
        session_factory = requests.Session

    thread_state = threading.local()

    def get_session() -> requests.Session:
        session = getattr(thread_state, "session", None)
        if session is None:
            session = session_factory()
            session.trust_env = use_environment_proxy
            thread_state.session = session
        return session

    def fetch_page(offset: int) -> tuple[int, list[dict], int]:
        params = {
            "_appver": "11.17.0",
            "board_code": "aStock",
            "sort_type": "price",
            "direct": "down",
            "offset": str(offset),
            "count": str(page_size),
        }
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = get_session().get(
                    TENCENT_A_SHARE_RANK_URL,
                    params=params,
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data", {})
                rows = data.get("rank_list")
                total = int(data.get("total", 0))
                if payload.get("code") != 0 or not isinstance(rows, list):
                    raise RuntimeError(
                        f"Tencent quote page rejected (offset={offset}): "
                        f"{payload.get('msg', 'invalid response')}"
                    )
                return offset, rows, total
            except Exception as exc:  # retry transport errors and malformed pages alike
                last_error = exc
                if attempt < retries:
                    time.sleep(retry_sleep * (attempt + 1))
        raise RuntimeError(
            f"Tencent quote page failed (offset={offset}, attempts={retries + 1})"
        ) from last_error

    first_offset, first_rows, total = fetch_page(0)
    if first_offset != 0 or total <= 0 or not first_rows:
        raise RuntimeError("Tencent quote endpoint returned an empty all-A-share board")

    offsets = range(page_size, total, page_size)
    page_rows: list[dict] = list(first_rows)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_page, offset) for offset in offsets]
        for future in as_completed(futures):
            _, rows, _ = future.result()
            page_rows.extend(rows)

    result = pd.DataFrame.from_records(page_rows)
    if "code" not in result.columns or "zdf" not in result.columns:
        raise RuntimeError("Tencent quote response is missing code or zdf fields")
    result = result.dropna(subset=["code"]).drop_duplicates("code", keep="last")
    minimum_coverage = math.ceil(total * 0.98)
    if len(result) < minimum_coverage:
        raise RuntimeError(
            f"Tencent quote board incomplete: received {len(result)} of {total} "
            f"unique stocks (need at least {minimum_coverage})"
        )
    return result.reset_index(drop=True)
