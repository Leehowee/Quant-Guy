from datetime import date, timedelta
import unittest

import pandas as pd

from etf_flow.trade_review import build_trade_review
from industry_flow.market_snapshot import build_market_snapshot


class MarketSnapshotTests(unittest.TestCase):
    def test_builds_breadth_and_twenty_session_index_average(self):
        target = date(2026, 10, 15)
        dates = [target - timedelta(days=19 - index) for index in range(20)]
        index = pd.DataFrame({"date": [day.isoformat() for day in dates], "close": range(100, 120)})
        breadth = pd.DataFrame({"涨跌幅": [1.0, 0.0, -1.0, 2.0]})
        snapshot = build_market_snapshot(target, breadth, index).iloc[0]
        self.assertAlmostEqual(snapshot["advancer_ratio"], 0.5)
        self.assertAlmostEqual(snapshot["index_sma20"], 109.5)
        self.assertEqual(snapshot["breadth_count"], 4)

    def test_rejects_wrong_index_date_instead_of_using_stale_data(self):
        target = date(2026, 10, 15)
        dates = [target - timedelta(days=20 - index) for index in range(20)]
        index = pd.DataFrame({"date": [day.isoformat() for day in dates], "close": range(100, 120)})
        with self.assertRaisesRegex(RuntimeError, "complete 20-session"):
            build_market_snapshot(target, pd.DataFrame({"涨跌幅": [1.0]}), index)


class TradeReviewTests(unittest.TestCase):
    def setUp(self):
        self.sessions = [date(2026, 9, 1) + timedelta(days=index) for index in range(20)]
        self.target = self.sessions[-1]
        self.calendar = pd.DataFrame({"trade_date": [day.isoformat() for day in self.sessions]})
        self.etf = pd.DataFrame([
            {
                "trade_date": day.isoformat(),
                "exchange": "SSE",
                "code": "510300",
                "name": "沪深300ETF",
                "shares": 1_000_000,
                "close": 4.0 + index * 0.01,
                "estimated_flow": 100_000 if index in (15, 16, 18) else -10_000,
            }
            for index, day in enumerate(self.sessions)
        ])
        self.market = pd.DataFrame([
            {
                "trade_date": day.isoformat(),
                "index_close": 100.0 + index * 0.01,
                "index_sma20": 100.0,
                "advancer_ratio": 0.6,
            }
            for index, day in enumerate(self.sessions)
        ])

    def test_qualified_etf_requires_market_and_flow_confirmation(self):
        result, summary = build_trade_review(self.etf, self.market, self.target, self.calendar)
        self.assertTrue(bool(result.iloc[0]["qualified_candidate"]))
        self.assertIn("较强", summary)

    def test_absent_market_snapshot_fails_closed(self):
        result, summary = build_trade_review(self.etf, self.market.iloc[:-1], self.target, self.calendar)
        self.assertTrue(result.empty)
        self.assertIn("数据不足", summary)

    def test_incomplete_etf_history_cannot_become_candidate(self):
        result, _ = build_trade_review(self.etf.iloc[:-1], self.market, self.target, self.calendar)
        self.assertFalse(bool(result.iloc[0]["qualified_candidate"]))


if __name__ == "__main__":
    unittest.main()
