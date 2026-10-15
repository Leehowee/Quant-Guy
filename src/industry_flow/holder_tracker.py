"""Helpers for dated, public-account holding observations.

Holdings are snapshots published with a delay. Falling out of a top-ten table
is not evidence of a full sale, so missing observations are never zero-filled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class HoldingObservation:
    entity: str
    security: str
    period_end: date
    published_at: date
    shares: float
    float_pct: float | None = None


@dataclass(frozen=True)
class HoldingChange:
    entity: str
    security: str
    previous_period_end: date
    current_period_end: date
    previous_shares: float
    current_shares: float
    share_change_pct: float | None
    publication_age_days: int


def compare_public_holdings(
    previous: HoldingObservation,
    current: HoldingObservation,
    *,
    as_of: date,
) -> HoldingChange:
    if (previous.entity, previous.security) != (current.entity, current.security):
        raise ValueError("只能比较同一披露账户和同一证券")
    if current.period_end <= previous.period_end:
        raise ValueError("当前持仓截止日必须晚于上一期")
    if current.published_at > as_of:
        raise ValueError("当前持仓公告日晚于分析日期，存在前视数据")
    if min(previous.shares, current.shares) < 0:
        raise ValueError("持股数量不能为负数")
    change_pct = (
        (current.shares / previous.shares - 1) * 100
        if previous.shares > 0
        else None
    )
    return HoldingChange(
        entity=current.entity,
        security=current.security,
        previous_period_end=previous.period_end,
        current_period_end=current.period_end,
        previous_shares=previous.shares,
        current_shares=current.shares,
        share_change_pct=change_pct,
        publication_age_days=max(0, (as_of - current.published_at).days),
    )


def is_stale(observation: HoldingObservation, *, as_of: date, max_age_days: int = 120) -> bool:
    if max_age_days < 0:
        raise ValueError("max_age_days不能为负数")
    return (as_of - observation.period_end).days > max_age_days
