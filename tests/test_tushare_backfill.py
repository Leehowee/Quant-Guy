from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from industry_flow.tushare_backfill import (
    backfill_missing_tushare_breadth,
    build_tushare_breadth_row,
    fetch_tushare_daily,
)


class _Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class TushareBackfillTests(unittest.TestCase):
    def test_fetch_tushare_daily_requests_only_required_historical_fields(self):
        session = Mock()
        session.post.return_value = _Response({
            "code": 0,
            "data": {
                "fields": ["ts_code", "trade_date", "pct_chg"],
                "items": [
                    ["000001.SZ", "20261014", 1.2],
                    ["600000.SH", "20261014", -0.8],
                ],
            },
        })

        result = fetch_tushare_daily(date(2026, 10, 14), token="secret", session=session)

        self.assertEqual(len(result), 2)
        self.assertEqual(result["trade_date"].unique().tolist(), ["20261014"])
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["api_name"], "daily")
        self.assertEqual(payload["params"], {"trade_date": "20261014"})
        self.assertEqual(payload["fields"], "ts_code,trade_date,pct_chg")

    def test_builds_historical_advancer_ratio_and_labels_suspension_scope(self):
        bars = pd.DataFrame({
            "ts_code": ["a", "b", "c", "d"],
            "trade_date": ["20261014"] * 4,
            "pct_chg": [2.0, 0.0, -1.0, 3.0],
        })

        row = build_tushare_breadth_row(date(2026, 10, 14), bars).iloc[0]

        self.assertAlmostEqual(row["advancer_ratio"], 0.5)
        self.assertEqual(row["breadth_count"], 4)
        self.assertIn("停牌股票", row["date_confidence"])
        self.assertEqual(row["source"], "Tushare_daily_historical")

    def test_rejects_undercovered_daily_sample(self):
        bars = pd.DataFrame({
            "ts_code": ["a", "b"],
            "trade_date": ["20261014"] * 2,
            "pct_chg": [2.0, -1.0],
        })
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            build_tushare_breadth_row(date(2026, 10, 14), bars, minimum_rows=3)

    def test_backfill_fills_only_missing_dates_and_preserves_index_history(self):
        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            processed = Path(temp_dir)
            pd.DataFrame([
                {
                    "trade_date": "2026-10-14",
                    "index_close": 100,
                    "index_sma20": 99,
                    "advancer_ratio": pd.NA,
                    "breadth_count": pd.NA,
                    "captured_at": "",
                    "date_confidence": "",
                    "source": "index history",
                },
                {
                    "trade_date": "2026-10-15",
                    "index_close": 101,
                    "index_sma20": 99.5,
                    "advancer_ratio": 0.6,
                    "breadth_count": 5576,
                    "captured_at": "2026-10-15T18:00:00",
                    "date_confidence": "verified live snapshot",
                    "source": "Tencent live",
                },
            ]).to_csv(processed / "market_regime_history.csv", index=False)
            bars = pd.DataFrame({
                "ts_code": [f"stock-{i}" for i in range(5000)],
                "trade_date": ["20261014"] * 5000,
                "pct_chg": [1.0] * 2500 + [-1.0] * 2500,
            })

            with patch(
                "industry_flow.tushare_backfill.fetch_tushare_daily",
                return_value=bars,
            ) as fetch:
                result = backfill_missing_tushare_breadth(
                    processed_dir=processed,
                    token="secret",
                    session=Mock(),
                    retry_sleep=0,
                )
            saved = pd.read_csv(processed / "market_regime_history.csv")

        fetch.assert_called_once_with(
            date(2026, 10, 14), token="secret", session=unittest.mock.ANY, retry_sleep=0
        )
        self.assertEqual(len(result), 1)
        old = saved.loc[saved["trade_date"].eq("2026-10-14")].iloc[0]
        latest = saved.loc[saved["trade_date"].eq("2026-10-15")].iloc[0]
        self.assertAlmostEqual(old["advancer_ratio"], 0.5)
        self.assertEqual(old["index_close"], 100)
        self.assertEqual(old["breadth_count"], 5000)
        self.assertAlmostEqual(latest["advancer_ratio"], 0.6)
        self.assertEqual(latest["source"], "Tencent live")


if __name__ == "__main__":
    unittest.main()
