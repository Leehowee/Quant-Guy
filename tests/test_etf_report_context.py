import csv
import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
import pandas as pd

from etf_flow.report_context import (
    RISK_PANEL_LINES,
    build_holder_panel_lines,
    build_next_day_decision_rows,
    format_next_day_decision_markdown,
)
from etf_flow.report_image import render_etf_report_png


class EtfReportContextTests(unittest.TestCase):
    def test_decision_table_exposes_candidate_evidence_and_unknown_price_budget(self):
        review = pd.DataFrame([{
            "qualified_candidate": True,
            "name": "沪深300ETF",
            "code": "510300",
            "flow_positive_days_5d": 4,
            "flow_to_estimated_value_pct_5d": 0.8,
            "excess_return_10d_pct": 1.2,
            "market_regime": "分化",
        }])
        rows = build_next_day_decision_rows(
            review,
            "市场状态：分化；权益仓位上限 30%。",
            ["公开持仓观察名单：10 项。", "暂无已录入且已公开的持仓快照。"],
        )

        by_item = {row["item"]: row["answer"] for row in rows}
        self.assertIn("沪深300ETF(510300)", by_item["候选 ETF（最多5只）"])
        self.assertIn("4/5", by_item["候选 ETF（最多5只）"])
        self.assertIn("暂无已录入", by_item["主体证据"])
        self.assertIn("触发价数据不足", by_item["入场条件"])
        self.assertIn("具体价格不足不伪造", by_item["退出条件"])
        self.assertIn("人民币预算及买入份数不足以计算", by_item["风险预算"])

        message = format_next_day_decision_markdown(rows, dt.date(2026, 10, 16))
        self.assertIn("2026-10-16 交易决策表", message)
        self.assertIn("| 不交易原因 |", message)

    def test_decision_table_fails_closed_when_market_or_etf_history_is_missing(self):
        rows = build_next_day_decision_rows(
            pd.DataFrame(),
            "市场状态：数据不足；权益仓位上限 0%。",
            [],
        )

        by_item = {row["item"]: row["answer"] for row in rows}
        self.assertIn("数据不足", by_item["候选 ETF（最多5只）"])
        self.assertIn("暂停新开仓", by_item["不交易原因"])
        self.assertIn("证据不足", by_item["主体证据"])

    def test_holder_panel_explains_empty_manual_observation_file(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            config = Path(directory)
            with (config / "holder_watchlist.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["entity", "category"])
                writer.writeheader()
                writer.writerow({"entity": "样本公开组合", "category": "机构"})

            lines = build_holder_panel_lines(config, as_of=dt.date(2026, 5, 15))

        self.assertIn("公开持仓观察名单：1 项", lines[0])
        self.assertIn("样本公开组合", lines[1])
        self.assertIn("暂无已录入", lines[2])

    def test_holder_panel_compares_only_published_dated_observations(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            config = Path(directory)
            (config / "holder_watchlist.csv").write_text(
                "entity,category\n样本公开组合,机构\n", encoding="utf-8-sig"
            )
            observations = config / "holder_observations.csv"
            with observations.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["entity", "security", "period_end", "published_at", "shares", "float_pct"],
                )
                writer.writeheader()
                writer.writerow({
                    "entity": "样本公开组合", "security": "510300", "period_end": "2025-12-31",
                    "published_at": "2026-03-20", "shares": "100", "float_pct": "",
                })
                writer.writerow({
                    "entity": "样本公开组合", "security": "510300", "period_end": "2026-03-31",
                    "published_at": "2026-04-30", "shares": "120", "float_pct": "",
                })
                writer.writerow({
                    "entity": "未来披露", "security": "510300", "period_end": "2026-03-31",
                    "published_at": "2026-06-30", "shares": "999", "float_pct": "",
                })

            lines = build_holder_panel_lines(config, as_of=dt.date(2026, 5, 15))

        self.assertTrue(any("较上期 +20.0%" in line for line in lines))
        self.assertTrue(any("晚于分析日" in line for line in lines))
        self.assertFalse(any("未来披露" in line for line in lines))

    def test_etf_infographic_allocates_space_for_trade_and_account_context(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            output = Path(directory) / "etf.png"
            render_etf_report_png(
                summaries=[],
                trade_date=dt.date(2026, 10, 14),
                output_path=output,
                market_summary="市场状态：数据不足；权益仓位上限 0%。",
                candidate_summary="候选观察暂停：市场数据不足。",
                risk_lines=list(RISK_PANEL_LINES),
                holder_lines=["观察名单：10 项。", "暂无已录入公开持仓快照。"],
                decision_rows=[
                    {"item": "市场状态", "answer": "市场状态：数据不足；权益仓位上限 0%。"},
                    {"item": "不交易原因", "answer": "市场数据不足，暂停新开仓。"},
                ],
                decision_date=dt.date(2026, 10, 15),
            )
            with Image.open(output) as image:
                self.assertEqual(image.width, 1200)
                self.assertEqual(image.height, 245 + 372 + 970 + 132 + 18)


if __name__ == "__main__":
    unittest.main()
