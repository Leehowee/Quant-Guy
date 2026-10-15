"""Build cautious, non-personalized context for the ETF report image."""

from __future__ import annotations

import csv
import datetime as dt
import math
from pathlib import Path

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
