from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
from PIL import Image

from industry_flow.digest import build_sw_industry_flow_digest
from industry_flow.industry_report_image import render_industry_report_png
from industry_flow.sw_industry_flow import (
    aggregate_sw_l2_daily_flow,
    build_sw_period_snapshot,
    fetch_tushare_moneyflow,
    select_sw_l2_memberships_asof,
)


class _FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class _FakeSession:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.body)


class ShenwanIndustryFlowTests(unittest.TestCase):
    def setUp(self):
        self.trade_date = date(2026, 10, 16)
        self.universe = pd.DataFrame({
            "ts_code": ["600001.SH", "600002.SH", "000001.SZ", "000002.SZ"],
            "trade_date": ["20261016"] * 4,
        })
        self.moneyflow = pd.DataFrame({
            "ts_code": ["600001.SH", "600002.SH", "000001.SZ", "000002.SZ"],
            "trade_date": ["20261016"] * 4,
            "net_mf_amount": [10000, -5000, 2500, 0],
        })
        self.memberships = pd.DataFrame([
            # A stale open-ended interval plus a newer assignment: as-of must
            # use the most recent effective L2 classification.
            {"ts_code": "600001.SH", "l2_code": "801010.SI", "l2_name": "旧行业", "in_date": "20100101", "out_date": ""},
            {"ts_code": "600001.SH", "l2_code": "801020.SI", "l2_name": "银行", "in_date": "20261015", "out_date": ""},
            {"ts_code": "600002.SH", "l2_code": "801020.SI", "l2_name": "银行", "in_date": "20100101", "out_date": ""},
            {"ts_code": "000001.SZ", "l2_code": "801030.SI", "l2_name": "电子", "in_date": "20100101", "out_date": ""},
            {"ts_code": "000002.SZ", "l2_code": "801030.SI", "l2_name": "电子", "in_date": "20100101", "out_date": ""},
        ])

    def test_selects_effective_l2_assignment_for_trade_date(self):
        selected = select_sw_l2_memberships_asof(self.memberships, self.trade_date)
        code = selected.loc[selected["ts_code"].eq("600001.SH"), "l2_code"].iloc[0]
        self.assertEqual(code, "801020.SI")

    def test_aggregates_all_share_flow_into_shenwan_l2_and_converts_units(self):
        result = aggregate_sw_l2_daily_flow(
            self.trade_date,
            self.universe,
            self.moneyflow,
            self.memberships,
        ).set_index("industry_name")

        self.assertAlmostEqual(result.loc["银行", "net_amount_yi"], 0.5)
        self.assertAlmostEqual(result.loc["电子", "net_amount_yi"], 0.25)
        self.assertEqual(result.loc["银行", "active_stock_count"], 2)
        self.assertEqual(result.loc["银行", "flow_stock_count"], 2)
        self.assertEqual(result.loc["银行", "coverage_pct"], 100)
        self.assertEqual(result["market_coverage_pct"].iloc[0], 100)
        self.assertEqual(result["mapping_coverage_pct"].iloc[0], 100)
        self.assertTrue(result["data_sufficient"].all())

    def test_rejects_partial_market_moneyflow_coverage(self):
        partial = self.moneyflow.iloc[:3].copy()
        with self.assertRaisesRegex(RuntimeError, "coverage is insufficient"):
            aggregate_sw_l2_daily_flow(
                self.trade_date,
                self.universe,
                partial,
                self.memberships,
                minimum_coverage=0.95,
            )

    def test_rejects_unmapped_market_coverage(self):
        memberships = self.memberships.loc[
            self.memberships["ts_code"].ne("000002.SZ")
        ]
        with self.assertRaisesRegex(RuntimeError, "mapping coverage is insufficient"):
            aggregate_sw_l2_daily_flow(
                self.trade_date,
                self.universe,
                self.moneyflow,
                memberships,
                minimum_coverage=0.95,
            )

    def test_rejects_conflicting_active_l2_memberships(self):
        conflicting = pd.concat([
            self.memberships,
            pd.DataFrame([{
                "ts_code": "600002.SH",
                "l2_code": "801099.SI",
                "l2_name": "冲突行业",
                "in_date": "20100101",
                "out_date": "",
            }]),
        ], ignore_index=True)
        with self.assertRaisesRegex(RuntimeError, "Conflicting active"):
            select_sw_l2_memberships_asof(conflicting, self.trade_date)

    def test_builds_only_complete_rolling_windows(self):
        sessions = [date(2026, 10, 12 + index) for index in range(5)]
        daily = []
        for index, session in enumerate(sessions):
            daily.extend([
                {
                    "trade_date": session,
                    "industry_code": "801020.SI",
                    "industry_name": "银行",
                    "net_amount_yi": index + 1,
                    "coverage_pct": 100,
                    "market_coverage_pct": 99,
                    "active_stock_count": 10,
                    "flow_stock_count": 10,
                },
                {
                    "trade_date": session,
                    "industry_code": "801030.SI",
                    "industry_name": "电子",
                    "net_amount_yi": -1,
                    "coverage_pct": 96,
                    "market_coverage_pct": 99,
                    "active_stock_count": 5,
                    "flow_stock_count": 5,
                },
            ])
        snapshot = build_sw_period_snapshot(pd.DataFrame(daily), sessions)
        bank = snapshot.loc[
            snapshot["industry_name"].eq("银行")
            & snapshot["period"].eq("5日")
        ].iloc[0]
        self.assertEqual(bank["net_amount_yi"], 15)
        self.assertEqual(bank["trade_dates_count"], 5)
        self.assertNotIn("10日", set(snapshot["period"]))

    def test_missing_date_breaks_period_instead_of_treating_it_as_zero(self):
        sessions = [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)]
        history = pd.DataFrame([{
            "trade_date": session,
            "industry_code": "801020.SI",
            "industry_name": "银行",
            "net_amount_yi": 1,
            "coverage_pct": 100,
            "market_coverage_pct": 99,
            "active_stock_count": 10,
            "flow_stock_count": 10,
        } for session in (sessions[0], sessions[2])])
        snapshot = build_sw_period_snapshot(history, sessions)
        self.assertEqual(set(snapshot["period"]), {"1日"})

    def test_fetch_moneyflow_requests_daily_net_flow_and_checks_date(self):
        session = _FakeSession({
            "code": 0,
            "data": {
                "fields": ["ts_code", "trade_date", "net_mf_amount"],
                "items": [["600001.SH", "20261016", 123.5]],
            },
        })
        frame = fetch_tushare_moneyflow(
            self.trade_date,
            token="test-secret",
            session=session,
            retries=0,
        )
        self.assertEqual(frame.iloc[0]["net_mf_amount"], 123.5)
        payload = session.calls[0][1]["json"]
        self.assertEqual(payload["api_name"], "moneyflow")
        self.assertEqual(payload["params"]["trade_date"], "20261016")
        self.assertIn("net_mf_amount", payload["fields"])

    def test_digest_states_source_scope_and_coverage(self):
        feature = pd.DataFrame([{
            "trade_date": self.trade_date,
            "period": "1日",
            "industry_code": "801020.SI",
            "industry_name": "银行",
            "net_amount_yi": 1.2,
            "coverage_pct": 98.0,
            "market_coverage_pct": 98.5,
            "mapping_coverage_pct": 99.0,
            "flow_stock_count": 120,
            "source": "tushare_moneyflow_sw_l2",
        }])
        digest = build_sw_industry_flow_digest(feature, trade_date=self.trade_date)
        self.assertIn("申万二级行业", digest)
        self.assertIn("沪深A股", digest)
        self.assertIn("北交所未纳入", digest)
        self.assertIn("全市场个股资金流覆盖 98.5%", digest)
        self.assertIn("1日累计", digest)

    def test_sw_image_renders_rolling_windows_and_source_scope(self):
        feature = pd.DataFrame([{
            "trade_date": self.trade_date,
            "period": "1日",
            "industry_code": "801020.SI",
            "industry_name": "银行",
            "net_amount_yi": 1.2,
            "coverage_pct": 98.0,
            "market_coverage_pct": 98.5,
            "mapping_coverage_pct": 99.0,
            "active_stock_count": 124,
            "flow_stock_count": 120,
            "source": "tushare_moneyflow_sw_l2",
        }])
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            output = Path(directory) / "industry.png"
            render_industry_report_png(feature, self.trade_date, output)
            with Image.open(output) as image:
                self.assertEqual(image.size, (1200, 314 + 342 * 5 + 122))


if __name__ == "__main__":
    unittest.main()
