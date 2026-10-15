"""Build cautious, non-personalized context for the ETF report image."""

from __future__ import annotations

import csv
import datetime as dt
import math
from pathlib import Path

import pandas as pd

from industry_flow.holder_tracker import (
    HoldingObservation,
    compare_public_holdings,
    is_stale,
)


RISK_PANEL_LINES = (
    "账户净值、回撤和持仓未接入：不计算个性化仓位股数。",
    "单笔风险上限 0.3%；全部持仓计划风险上限 1.2%。",
    "单只 ETF ≤15%，同主题合计 ≤20%，总敞口遵守市场上限。",
    "回撤 4% 风险减半；6% 暂停开仓；8% 减仓复核；10% 为容忍目标。",
    "退出复核：初始止损、2倍 ATR 跟踪、3日未走强或持有满10日。",
)


def build_holder_panel_lines(config_dir: Path, *, as_of: dt.date) -> list[str]:
    """Summarize configured public-account coverage and dated observations."""
    watchlist_path = config_dir / "holder_watchlist.csv"
    observation_path = config_dir / "holder_observations.csv"
    names: list[str] = []
    try:
        with watchlist_path.open("r", encoding="utf-8-sig", newline="") as stream:
            names = [
                row.get("entity", "").strip()
                for row in csv.DictReader(stream)
                if row.get("entity", "").strip()
            ]
    except OSError:
        pass

    lines = [
        f"公开持仓观察名单：{len(names)} 项；披露滞后，不代表实时账户交易。",
    ]
    if names:
        lines.append("、".join(names))

    observations: dict[tuple[str, str], list[HoldingObservation]] = {}
    invalid_rows = 0
    if observation_path.exists():
        try:
            with observation_path.open("r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    try:
                        observation = HoldingObservation(
                            entity=row["entity"].strip(),
                            security=row["security"].strip(),
                            period_end=dt.date.fromisoformat(row["period_end"].strip()),
                            published_at=dt.date.fromisoformat(row["published_at"].strip()),
                            shares=float(row["shares"]),
                            float_pct=(
                                float(row["float_pct"])
                                if row.get("float_pct", "").strip()
                                else None
                            ),
                        )
                        if (
                            observation.entity
                            and observation.security
                            and observation.period_end <= as_of
                            and observation.published_at <= as_of
                            and math.isfinite(observation.shares)
                            and observation.shares >= 0
                        ):
                            key = (observation.entity, observation.security)
                            observations.setdefault(key, []).append(observation)
                        else:
                            invalid_rows += 1
                    except (KeyError, TypeError, ValueError):
                        invalid_rows += 1
        except OSError:
            invalid_rows += 1

    if not observations:
        lines.append("暂无已录入且已公开的持仓快照；可按模板核验录入后比较披露期。")
    else:
        lines.append(f"已录入 {sum(map(len, observations.values()))} 条公开持仓快照：")
        displayed = 0
        for (entity, security), entries in sorted(observations.items()):
            entries.sort(key=lambda item: (item.period_end, item.published_at))
            current = entries[-1]
            stale_note = "；较旧" if is_stale(current, as_of=as_of) else ""
            change_note = "首次记录"
            if len(entries) >= 2:
                try:
                    change = compare_public_holdings(entries[-2], current, as_of=as_of)
                    change_note = (
                        f"较上期 {change.share_change_pct:+.1f}%"
                        if change.share_change_pct is not None
                        else "上期为零，比例不可计算"
                    )
                except ValueError:
                    change_note = "披露期不可比较"
            lines.append(
                f"{entity} / {security}：{current.period_end:%Y-%m-%d}，"
                f"{change_note}{stale_note}。"
            )
            displayed += 1
            if displayed >= 3:
                break
    if invalid_rows:
        lines.append(f"另有 {invalid_rows} 条记录日期/数值无效或晚于分析日，未纳入。")
    return lines


def build_next_day_decision_rows(
    review: pd.DataFrame,
    market_summary: str,
    holder_lines: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build an evidence-bounded plan for the next session, never an order."""
    holder_lines = holder_lines or []
    if review.empty:
        qualified = review
        regime = "数据不足"
        candidate_text = "无；市场或 ETF 历史数据不足，按规则不生成候选。"
        no_trade = "市场或 ETF 必要数据不足；暂停新开仓，等待完整数据。"
    else:
        if "qualified_candidate" in review:
            qualified = review.loc[
                review["qualified_candidate"].fillna(False).astype(bool)
            ].head(5)
        else:
            qualified = review.iloc[0:0]
        regime = str(review.iloc[0].get("market_regime", "数据不足"))
        if not qualified.empty:
            candidate_parts = []
            for item in qualified.itertuples(index=False):
                name = str(getattr(item, "name", "ETF"))
                code = str(getattr(item, "code", "")).zfill(6)
                flow_days = _display_int(getattr(item, "flow_positive_days_5d", None))
                flow_pct = _display_pct(
                    getattr(item, "flow_to_estimated_value_pct_5d", None)
                )
                excess_pct = _display_pct(getattr(item, "excess_return_10d_pct", None))
                candidate_parts.append(
                    f"{name}({code})：5日正申购 {flow_days}/5，资金强度 {flow_pct}，"
                    f"10日超额 {excess_pct}"
                )
            candidate_text = "；".join(candidate_parts)
            no_trade = (
                "若市场转弱、任一资金/趋势过滤失效、当日行情数据缺失或价格偏离计划时放弃；"
                "溢价率/事件风险尚未接入，相关风险不明时不交易、不追涨。"
            )
        else:
            flow_count = _pass_count(review, "flow_pass_5d")
            trend_count = _pass_count(review, "trend_pass_20d")
            relative_count = _pass_count(review, "relative_strength_pass_10d")
            candidate_text = (
                f"无合格候选（复核 {len(review)} 只；5日资金通过 {flow_count} 只，"
                f"20日趋势通过 {trend_count} 只，10日相对强度通过 {relative_count} 只）。"
            )
            if regime == "较弱":
                no_trade = (
                    "市场偏弱、权益仓位上限为 0%；暂停新开仓。溢价率/事件风险尚未接入，"
                    "如风险不明或异常则不交易。"
                )
            elif regime == "数据不足":
                no_trade = (
                    "市场证据不完整；暂停新开仓，等待数据补齐。溢价率/事件风险也未接入，"
                    "不据此假定风险正常。"
                )
            else:
                no_trade = (
                    "没有 ETF 通过全部过滤；不为凑候选而降低条件。溢价率/事件风险未接入，"
                    "风险不明时不交易。"
                )

    subject_evidence = (
        "按已公开且截至分析日可见的持仓快照核验；披露滞后，不代表实时买卖。"
    )
    if any("暂无已录入" in line for line in holder_lines):
        subject_evidence += (
            " 当前暂无已录入的公开持仓快照；具体持仓人、截止日和披露日未知，"
            "不能作为买入依据。"
        )
    elif not holder_lines:
        subject_evidence += " 持仓观察数据不可用，证据不足。"

    return [
        {"item": "市场状态", "answer": market_summary},
        {"item": "候选 ETF（最多5只）", "answer": candidate_text},
        {"item": "主体证据", "answer": subject_evidence},
        {
            "item": "入场条件",
            "answer": (
                "观察条件：近5日净申购为正且至少3日为正、收盘高于20日均线、"
                "近10日跑赢上证；盘中触发价数据不足（未接入可靠高低价/盘口），不预填价格、不追价。"
            ),
        },
        {
            "item": "退出条件",
            "answer": (
                "初始止损价须结合实际入场价和 ATR；当前缺少可核验的 ATR/账户持仓成本，"
                "具体价格不足不伪造。3个交易日未走强退出，持有满10个交易日退出；"
                "有有效 ATR 时按 2×ATR 跟踪退出。"
            ),
        },
        {
            "item": "风险预算",
            "answer": (
                "单笔计划风险≤账户净值 0.3%，全部持仓计划风险≤1.2%；"
                "账户净值、当前持仓和回撤未接入，人民币预算及买入份数不足以计算。"
            ),
        },
        {"item": "不交易原因", "answer": no_trade},
    ]


def format_next_day_decision_markdown(
    rows: list[dict[str, str]], decision_date: dt.date
) -> str:
    """Format the decision rows for the DingTalk text accompanying the image."""
    lines = [
        f"### {decision_date:%Y-%m-%d} 交易决策表（仅作观察，不自动下单）",
        "| 栏目 | 每天要回答的问题 |",
        "|---|---|",
    ]
    for row in rows:
        item = row["item"].replace("|", "\\|")
        answer = row["answer"].replace("|", "\\|")
        lines.append(f"| {item} | {answer} |")
    return "\n".join(lines)


def _pass_count(review: pd.DataFrame, column: str) -> int:
    if column not in review:
        return 0
    return int(review[column].fillna(False).astype(bool).sum())


def _display_int(value: object) -> str:
    try:
        number = float(value)
        return str(int(number)) if math.isfinite(number) else "不足"
    except (TypeError, ValueError):
        return "不足"


def _display_pct(value: object) -> str:
    try:
        number = float(value)
        return f"{number:+.2f}%" if math.isfinite(number) else "不足"
    except (TypeError, ValueError):
        return "不足"
