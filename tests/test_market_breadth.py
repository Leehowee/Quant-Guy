from __future__ import annotations

import unittest
from unittest.mock import Mock

import requests

from industry_flow.market_breadth import fetch_tencent_a_share_spot


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class TencentBreadthTests(unittest.TestCase):
    def test_fetches_every_page_retries_and_disables_environment_proxy(self):
        total = 450
        failures = {200: 1}
        calls = []
        sessions = []

        class Session:
            trust_env = True

            def get(self, _url, *, params, timeout):
                offset = int(params["offset"])
                calls.append((offset, timeout))
                if failures.get(offset, 0):
                    failures[offset] -= 1
                    raise requests.ConnectionError("temporary failure")
                rows = [
                    {"code": f"sh{600000 + index}", "zdf": str(index % 5 - 2)}
                    for index in range(offset, min(offset + 200, total))
                ]
                return _Response({
                    "code": 0,
                    "data": {"total": total, "rank_list": rows},
                })

        def session_factory():
            session = Session()
            sessions.append(session)
            return session

        result = fetch_tencent_a_share_spot(
            max_workers=1,
            retries=1,
            timeout=(1, 2),
            session_factory=session_factory,
            retry_sleep=0,
        )

        self.assertEqual(len(result), total)
        self.assertEqual(len(set(result["code"])), total)
        self.assertEqual(sum(offset == 200 for offset, _ in calls), 2)
        self.assertTrue(all(not session.trust_env for session in sessions))

    def test_rejects_incomplete_board_instead_of_reporting_partial_breadth(self):
        def session_factory():
            session = Mock()
            session.get.return_value = _Response({
                "code": 0,
                "data": {
                    "total": 1000,
                    "rank_list": [{"code": f"sh{index}", "zdf": 1} for index in range(200)],
                },
            })
            return session

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            fetch_tencent_a_share_spot(
                max_workers=1,
                retries=0,
                session_factory=session_factory,
            )


if __name__ == "__main__":
    unittest.main()
