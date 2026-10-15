from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
from PIL import Image
from unittest.mock import patch

from etf_flow.trade_review import build_candidate_summary, build_trade_review
from industry_flow.digest import build_ths_period_digest
from industry_flow.industry_report_image import render_industry_report_png
from industry_flow.market_snapshot import (
    build_market_index_backfill,
    build_market_report_summary,
    build_market_snapshot,
    capture_market_snapshot,
    save_market_index_backfill,
)


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

    def test_build_market_snapshot_retains_index_when_breadth_is_unavailable(self):
        target = date(2026, 10, 15)
        dates = [target - timedelta(days=19 - index) for index in range(20)]
        index = pd.DataFrame({"date": [day.isoformat() for day in dates], "close": range(100, 120)})
        snapshot = build_market_snapshot(target, pd.DataFrame(), index).iloc[0]
        self.assertAlmostEqual(snapshot["index_sma20"], 109.5)
        self.assertTrue(pd.isna(snapshot["advancer_ratio"]))
        self.assertIn("宽度缺失", snapshot["date_confidence"])

    def test_capture_keeps_index_when_breadth_endpoint_fails(self):
        target = date(2026, 10, 15)
        dates = [target - timedelta(days=19 - index) for index in range(20)]
        index = pd.DataFrame({"date": [day.isoformat() for day in dates], "close": range(100, 120)})
        endpoints = {
            "stock_zh_a_spot_em": lambda: (_ for _ in ()).throw(RuntimeError("breadth unavailable")),
            "stock_zh_index_daily_em": lambda **_: index,
            "stock_zh_index_daily": lambda **_: index,
        }
        with (
            patch("industry_flow.config.resolve_akshare_api", side_effect=lambda name: endpoints[name]),
            patch("industry_flow.market_snapshot.fetch_tencent_a_share_spot", side_effect=RuntimeError("fallback unavailable")),
        ):
            snapshot = capture_market_snapshot(target).iloc[0]
        self.assertTrue(pd.isna(snapshot["advancer_ratio"]))
        self.assertIn("market_breadth_unavailable", snapshot["source"])
        self.assertEqual(snapshot["trade_date"], target.isoformat())

    def test_capture_uses_tencent_breadth_when_eastmoney_fails(self):
        target = date(2026, 10, 15)
        dates = [target - timedelta(days=19 - index) for index in range(20)]
        index = pd.DataFrame({"date": [day.isoformat() for day in dates], "close": range(100, 120)})
        endpoints = {
            "stock_zh_a_spot_em": lambda: (_ for _ in ()).throw(RuntimeError("Eastmoney unavailable")),
            "stock_zh_index_daily_em": lambda **_: index,
            "stock_zh_index_daily": lambda **_: index,
        }
        tencent = pd.DataFrame({"code": ["sh600001", "sz000001", "sh600002"], "zdf": [1.2, 0, -0.5]})
        with (
            patch("industry_flow.config.resolve_akshare_api", side_effect=lambda name: endpoints[name]),
            patch("industry_flow.market_snapshot.fetch_tencent_a_share_spot", return_value=tencent) as fallback,
        ):
            snapshot = capture_market_snapshot(target).iloc[0]

        fallback.assert_called_once()
        self.assertAlmostEqual(snapshot["advancer_ratio"], 1 / 3)
        self.assertEqual(snapshot["breadth_count"], 3)
        self.assertIn("Tencent_stock_zh_a_spot_tx_direct", snapshot["source"])

    def test_market_summary_reports_regime_breadth_and_index_trend(self):
        target = date(2026, 10, 15)
        snapshot = pd.DataFrame([{
            "trade_date": target.isoformat(),
            "index_close": 4100.0,
            "index_sma20": 4000.0,
            "advancer_ratio": 0.6,
            "breadth_count": 5000,
            "captured_at": "2026-10-15T18:00:00",
            "date_confidence": "盘后采集日期推定",
        }])
        summary = build_market_report_summary(snapshot, target)
        self.assertIn("较强", summary["status"])
        self.assertIn("60%", summary["status"])
        self.assertIn("60.0%", summary["breadth"])
        self.assertIn("高于20日均线", summary["trend"])

    def test_market_summary_fails_closed_for_stale_or_missing_snapshot(self):
        target = date(2026, 10, 15)
        stale = pd.DataFrame([{
            "trade_date": "2026-10-14",
            "index_close": 4100.0,
            "index_sma20": 4000.0,
            "advancer_ratio": 0.6,
        }])
        stale_summary = build_market_report_summary(stale, target)
        missing_summary = build_market_report_summary(pd.DataFrame(), target)
        self.assertIn("数据不足", stale_summary["status"])
        self.assertIn("不匹配", stale_summary["note"])
        self.assertIn("数据不足", missing_summary["status"])

    def test_partial_market_summary_keeps_known_index_trend_visible(self):
        target = date(2026, 10, 15)
        snapshot = pd.DataFrame([{
            "trade_date": target.isoformat(),
            "index_close": 3900.0,
            "index_sma20": 4000.0,
            "advancer_ratio": pd.NA,
        }])
        summary = build_market_report_summary(snapshot, target)
        self.assertIn("数据不足", summary["status"])
        self.assertIn("无法获取", summary["breadth"])
        self.assertIn("未高于20日均线", summary["trend"])
        self.assertIn("4,000.00", summary["trend"])

    def test_digest_includes_market_filter_instead_of_only_collecting_it(self):
        target = date(2026, 10, 15)
        features = pd.DataFrame([{
            "trade_date": target.isoformat(),
            "period": "即时",
            "industry_name": "银行",
            "net_amount_yi": 1.0,
            "change_pct": 0.5,
            "period_change_pct": 0.5,
        }])
        market = pd.DataFrame([{
            "trade_date": target.isoformat(),
            "index_close": 4100.0,
            "index_sma20": 4000.0,
            "advancer_ratio": 0.6,
            "breadth_count": 5000,
            "captured_at": "2026-10-15T18:00:00",
            "date_confidence": "盘后采集日期推定",
        }])
        digest = build_ths_period_digest(
            features,
            market_snapshot=market,
            trade_date=target,
        )
        self.assertIn("短线市场环境过滤", digest)
        self.assertIn("A股上涨比例：60.0%", digest)
        self.assertIn("上证指数：4,100.00", digest)

    def test_industry_image_reserves_a_visible_market_context_panel(self):
        target = date(2026, 10, 15)
        features = pd.DataFrame([{
            "trade_date": target.isoformat(),
            "period": "即时",
            "industry_name": "银行",
            "net_amount_yi": 1.0,
            "change_pct": 0.5,
            "period_change_pct": 0.5,
        }])
        market = pd.DataFrame([{
            "trade_date": target.isoformat(),
            "index_close": 4100.0,
            "index_sma20": 4000.0,
            "advancer_ratio": 0.6,
            "breadth_count": 5000,
            "captured_at": "2026-10-15T18:00:00",
            "date_confidence": "盘后采集日期推定",
        }])
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            output = Path(directory) / "industry.png"
            render_industry_report_png(
                features,
                target,
                output,
                market_snapshot=market,
            )
            with Image.open(output) as image:
                self.assertEqual(image.width, 1200)
                self.assertEqual(image.height, 314 + 342 * 5 + 122)

    def test_rejects_wrong_index_date_instead_of_using_stale_data(self):
        target = date(2026, 10, 15)
        dates = [target - timedelta(days=20 - index) for index in range(20)]
        index = pd.DataFrame({"date": [day.isoformat() for day in dates], "close": range(100, 120)})
        with self.assertRaisesRegex(RuntimeError, "complete 20-session"):
            build_market_snapshot(target, pd.DataFrame({"涨跌幅": [1.0]}), index)

    def test_index_backfill_keeps_breadth_unknown_and_preserves_captured_breadth(self):
        dates = [date(2026, 8, 1) + timedelta(days=index) for index in range(45)]
        history = pd.DataFrame({"date": dates, "close": [100 + index for index in range(45)]})
        target = dates[-1]
        backfill = build_market_index_backfill(history, target, sessions=20)
        self.assertEqual(len(backfill), 20)
        self.assertEqual(backfill.iloc[-1]["trade_date"], target.isoformat())
        self.assertTrue(pd.isna(backfill.iloc[-1]["advancer_ratio"]))
        self.assertAlmostEqual(backfill.iloc[-1]["index_sma20"], sum(range(125, 145)) / 20)

        with TemporaryDirectory(dir=Path.cwd()) as directory:
            processed = Path(directory)
            existing = pd.DataFrame([{
                "trade_date": target.isoformat(),
                "index_close": 1.0,
                "index_sma20": 1.0,
                "advancer_ratio": 0.62,
                "breadth_count": 5000,
                "captured_at": "2026-09-14T18:00:00",
                "date_confidence": "盘后实采",
                "source": "market snapshot",
            }])
            existing.to_csv(processed / "market_regime_history.csv", index=False)
            save_market_index_backfill(backfill.tail(1), processed)
            saved = pd.read_csv(processed / "market_regime_history.csv")

        self.assertAlmostEqual(saved.iloc[0]["advancer_ratio"], 0.62)
        self.assertEqual(saved.iloc[0]["date_confidence"], "盘后实采")
        self.assertAlmostEqual(saved.iloc[0]["index_close"], 144.0)


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

    def test_candidate_summary_exposes_filter_counts_when_market_blocks_entries(self):
        review = pd.DataFrame([{
            "qualified_candidate": False,
            "name": "沪深300ETF",
            "flow_pass_5d": True,
            "trend_pass_20d": True,
            "relative_strength_pass_10d": False,
            "market_regime": "数据不足",
        }])
        summary = build_candidate_summary(review)
        self.assertIn("5日资金通过 1 只", summary)
        self.assertIn("20日趋势通过 1 只", summary)
        self.assertIn("市场过滤为“数据不足”", summary)
        self.assertIn("近10日跑赢上证", summary)

    def test_absent_market_snapshot_fails_closed(self):
        result, summary = build_trade_review(self.etf, self.market.iloc[:-1], self.target, self.calendar)
        self.assertTrue(result.empty)
        self.assertIn("数据不足", summary)

    def test_incomplete_etf_history_cannot_become_candidate(self):
        result, _ = build_trade_review(self.etf.iloc[:-1], self.market, self.target, self.calendar)
        self.assertFalse(bool(result.iloc[0]["qualified_candidate"]))

    def test_index_history_without_market_breadth_still_fails_closed(self):
        market = self.market.copy()
        market.loc[market["trade_date"].eq(self.target.isoformat()), "advancer_ratio"] = pd.NA
        result, summary = build_trade_review(self.etf, market, self.target, self.calendar)
        self.assertFalse(bool(result.iloc[0]["qualified_candidate"]))
        self.assertEqual(result.iloc[0]["market_regime"], "数据不足")
        self.assertIn("数据不足", summary)
        self.assertIn("上涨家数占比缺失", summary)
        self.assertIn("上证", summary)


if __name__ == "__main__":
    unittest.main()
