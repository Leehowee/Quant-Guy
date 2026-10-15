"""Conservative, explainable filters for a short-horizon ETF review.

These rules generate a review state only. They never place or recommend an
automatic order; callers must provide dated market data and account risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import math
from numbers import Real
from typing import Mapping, Sequence


class MarketRegime(str, Enum):
    GREEN = "较强"
    YELLOW = "分化"
    RED = "较弱"
    UNKNOWN = "数据不足"


class RiskMode(str, Enum):
    NORMAL = "正常"
    HALF_RISK = "风险减半"
    PAUSE = "暂停开仓"
    REDUCE = "减仓复核"


@dataclass(frozen=True)
class MarketAssessment:
    regime: MarketRegime
    exposure_cap_pct: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateAssessment:
    qualified: bool
    flow_days_positive: int
    flow_to_estimated_value_pct: float | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PositionSize:
    shares: int
    value: float
    maximum_loss_budget: float


def assess_market(
    index_close: float | None,
    index_sma20: float | None,
    advancer_ratio: float | None,
) -> MarketAssessment:
    """Classify market conditions; missing evidence always blocks new risk."""
    if not _finite(index_close) or not _finite(index_sma20) or not _finite(advancer_ratio):
        return MarketAssessment(
            MarketRegime.UNKNOWN,
            0.0,
            ("指数收盘、20日均线或上涨家数比例缺失",),
        )
    if index_close <= 0 or index_sma20 <= 0 or not 0 <= advancer_ratio <= 1:
        return MarketAssessment(MarketRegime.UNKNOWN, 0.0, ("市场数据超出有效范围",))

    trend_ok = index_close > index_sma20
    breadth_ok = advancer_ratio >= 0.5
    if trend_ok and breadth_ok:
        return MarketAssessment(
            MarketRegime.GREEN,
            0.60,
            ("指数高于20日均线", "上涨家数占比不低于50%"),
        )
    if trend_ok or breadth_ok:
        reasons = (
            "指数高于20日均线" if trend_ok else "指数未高于20日均线",
            "上涨家数占比不低于50%" if breadth_ok else "上涨家数占比低于50%",
        )
        return MarketAssessment(MarketRegime.YELLOW, 0.30, reasons)
    return MarketAssessment(
        MarketRegime.RED,
        0.0,
        ("指数未高于20日均线", "上涨家数占比低于50%"),
    )


def assess_etf_candidate(
    trading_sessions: Sequence[date | datetime | str],
    observations: Sequence[Mapping[str, object]],
    *,
    close_sma20: float | None,
    excess_return_10d_pct: float | None,
) -> CandidateAssessment:
    """Require five complete sessions, persistent flow and price confirmation."""
    reasons: list[str] = []
    if len(trading_sessions) < 20:
        return CandidateAssessment(False, 0, None, ("不足20个交易日，无法计算均线",))

    expected = [_date_key(value) for value in trading_sessions[-5:]]
    by_date: dict[str, Mapping[str, object]] = {}
    for observation in observations:
        key = _date_key(observation.get("trade_date"))
        if key in by_date:
            return CandidateAssessment(False, 0, None, (f"{key}存在重复记录",))
        by_date[key] = observation

    missing = [session for session in expected if session not in by_date]
    if missing:
        return CandidateAssessment(
            False,
            0,
            None,
            (f"近5个交易日数据不完整：缺少{', '.join(missing)}",),
        )

    recent = [by_date[session] for session in expected]
    flows = [_number(row.get("estimated_flow")) for row in recent]
    if any(value is None for value in flows):
        return CandidateAssessment(False, 0, None, ("近5日估算净申购数据不完整",))
    positive_days = sum(value > 0 for value in flows if value is not None)
    total_flow = sum(value for value in flows if value is not None)
    latest_shares = _number(recent[-1].get("shares"))
    latest_close = _number(recent[-1].get("close"))
    if latest_shares is None or latest_close is None or latest_shares <= 0 or latest_close <= 0:
        return CandidateAssessment(False, positive_days, None, ("缺少有效份额或收盘价",))

    flow_to_estimated_value_pct = total_flow / (latest_shares * latest_close) * 100
    flow_ok = total_flow > 0 and positive_days >= 3
    trend_ok = _finite(close_sma20) and latest_close > close_sma20
    relative_ok = _finite(excess_return_10d_pct) and excess_return_10d_pct > 0
    if not flow_ok:
        reasons.append("近5日净申购未为正，或正申购少于3天")
    if not trend_ok:
        reasons.append("ETF收盘价未高于20日均线")
    if not relative_ok:
        reasons.append("近10日未跑赢市场基准")
    if not reasons:
        reasons.append("近5日申购持续，且价格趋势和相对强度确认；进入候选观察")
    return CandidateAssessment(flow_ok and trend_ok and relative_ok, positive_days, flow_to_estimated_value_pct, tuple(reasons))


def assess_risk_mode(drawdown_pct: float | None) -> RiskMode:
    """Translate peak-to-current account drawdown into predefined risk actions."""
    if not _finite(drawdown_pct) or drawdown_pct < 0:
        return RiskMode.PAUSE
    if drawdown_pct >= 8:
        return RiskMode.REDUCE
    if drawdown_pct >= 6:
        return RiskMode.PAUSE
    if drawdown_pct >= 4:
        return RiskMode.HALF_RISK
    return RiskMode.NORMAL


def position_exit_reason(
    *,
    entry_price: float,
    current_close: float,
    initial_stop: float,
    peak_close: float,
    atr: float | None,
    holding_sessions: int,
    max_holding_sessions: int = 10,
    stall_sessions: int = 3,
) -> str | None:
    """Return the first predefined exit condition without inferring missing prices."""
    required = (entry_price, current_close, initial_stop, peak_close)
    if any(not _finite(value) or value <= 0 for value in required):
        raise ValueError("入场、收盘、初始退出位和持仓最高价必须有效且大于0")
    if atr is not None and (not _finite(atr) or atr <= 0):
        raise ValueError("ATR必须大于0")
    if holding_sessions < 0 or max_holding_sessions <= 0 or stall_sessions <= 0:
        raise ValueError("持仓交易日参数无效")
    if current_close <= initial_stop:
        return "触及初始退出位"
    if atr is not None and current_close <= peak_close - 2 * atr:
        return "触发2倍ATR跟踪退出"
    if holding_sessions >= max_holding_sessions:
        return "达到最长持有期"
    if holding_sessions >= stall_sessions and current_close <= entry_price:
        return "持有期内未走强"
    return None


def size_etf_position(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    market: MarketAssessment,
    risk_mode: RiskMode,
    current_exposure: float = 0.0,
    same_theme_exposure: float = 0.0,
    open_risk: float = 0.0,
    lot_size: int = 100,
    round_trip_cost_pct: float = 0.20,
) -> PositionSize:
    """Return a lot-rounded upper bound from per-trade, group and gross limits."""
    values = (equity, entry_price, stop_price, current_exposure, same_theme_exposure, open_risk, round_trip_cost_pct)
    if any(not _finite(value) for value in values) or equity <= 0 or entry_price <= 0:
        raise ValueError("账户、价格和敞口参数必须是有效有限数值")
    if stop_price <= 0 or stop_price >= entry_price:
        raise ValueError("退出参考价必须高于0且低于入场价")
    if lot_size <= 0 or round_trip_cost_pct < 0 or min(current_exposure, same_theme_exposure, open_risk) < 0:
        raise ValueError("手数、费用和已有风险参数无效")
    if risk_mode in (RiskMode.PAUSE, RiskMode.REDUCE) or market.exposure_cap_pct <= 0:
        return PositionSize(0, 0.0, 0.0)

    per_trade_pct = 0.003 if risk_mode is RiskMode.NORMAL else 0.0015
    remaining_total_risk = max(0.0, equity * 0.012 - open_risk)
    loss_budget = min(equity * per_trade_pct, remaining_total_risk)
    loss_per_share = entry_price - stop_price + entry_price * round_trip_cost_pct / 100
    risk_limited_value = loss_budget / (loss_per_share / entry_price)
    exposure_limits = (
        equity * 0.15,
        max(0.0, equity * 0.20 - same_theme_exposure),
        max(0.0, equity * market.exposure_cap_pct - current_exposure),
    )
    value = min(risk_limited_value, *exposure_limits)
    shares = math.floor(value / (entry_price * lot_size)) * lot_size
    rounded_value = shares * entry_price
    return PositionSize(shares, rounded_value, loss_budget if shares else 0.0)


def _date_key(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(value)[:10]


def _number(value: object) -> float | None:
    if not _finite(value):
        return None
    return float(value)


def _finite(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))
