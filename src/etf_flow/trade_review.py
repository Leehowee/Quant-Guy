"""Create a non-executing ETF candidate review from saved dated history."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from industry_flow.trading_rules import MarketRegime, assess_etf_candidate, assess_market


def build_trade_review(
    etf_history: pd.DataFrame,
    market_history: pd.DataFrame,
    target_date: dt.date,
    calendar: pd.DataFrame,
) -> tuple[pd.DataFrame, str]:
    """Evaluate every ETF on the same 20-session window and require market data."""
    required_etf = {"trade_date", "exchange", "code", "name", "shares", "close", "estimated_flow"}
    if not required_etf.issubset(etf_history.columns):
        raise RuntimeError(f"ETF history is missing columns: {sorted(required_etf.difference(etf_history.columns))}")
    required_market = {"trade_date", "index_close", "index_sma20", "advancer_ratio"}
    if not required_market.issubset(market_history.columns):
        raise RuntimeError(f"Market history is missing columns: {sorted(required_market.difference(market_history.columns))}")

    sessions = pd.to_datetime(calendar["trade_date"], errors="coerce").dropna().dt.date
    sessions = sorted({session for session in sessions if session <= target_date})
    if len(sessions) < 20 or sessions[-1] != target_date:
        market = assess_market(None, None, None)
        return pd.DataFrame(), _market_message(market, "交易日历不足20日或目标日不是交易日")
    sessions_20 = sessions[-20:]
    sessions_11 = sessions[-11:]

    market = market_history.copy()
    market["_date"] = pd.to_datetime(market["trade_date"], errors="coerce").dt.date
    market = market.drop_duplicates("_date", keep="last").set_index("_date")
    if target_date not in market.index:
        assessment = assess_market(None, None, None)
        return pd.DataFrame(), _market_message(assessment, f"缺少{target_date}市场快照")
    current = market.loc[target_date]
    assessment = assess_market(
        _numeric(current.get("index_close")),
        _numeric(current.get("index_sma20")),
        _numeric(current.get("advancer_ratio")),
    )
    base_dates = sessions_11
    market_returns: dict[dt.date, float] = {}
    for session in base_dates:
        if session in market.index and _numeric(market.loc[session].get("index_close")):
            market_returns[session] = float(market.loc[session]["index_close"])
    benchmark_return: float | None = None
    if len(base_dates) == 11 and all(session in market_returns for session in base_dates):
        benchmark_return = (market_returns[target_date] / market_returns[base_dates[0]] - 1) * 100

    history = etf_history.copy()
    history["_date"] = pd.to_datetime(history["trade_date"], errors="coerce").dt.date
    history = history[history["_date"].isin(sessions_20)]
    records: list[dict[str, object]] = []
    for (exchange, code), group in history.groupby(["exchange", "code"], sort=True):
        by_date = group.drop_duplicates("_date", keep="last").set_index("_date").sort_index()
        dates_20 = sessions_20
        closes = [_numeric(by_date.loc[day].get("close")) if day in by_date.index else None for day in dates_20]
        close_sma20 = sum(closes) / 20 if all(value is not None for value in closes) else None
        etf_return: float | None = None
        if all(day in by_date.index for day in base_dates):
            base_close = _numeric(by_date.loc[base_dates[0]].get("close"))
            final_close = _numeric(by_date.loc[target_date].get("close")) if target_date in by_date.index else None
            if base_close and final_close:
                etf_return = (final_close / base_close - 1) * 100
        excess_return = etf_return - benchmark_return if etf_return is not None and benchmark_return is not None else None
        observations = [
            {
                "trade_date": day.isoformat(),
                "estimated_flow": by_date.loc[day].get("estimated_flow"),
                "shares": by_date.loc[day].get("shares"),
                "close": by_date.loc[day].get("close"),
            }
            for day in dates_20 if day in by_date.index
        ]
        candidate = assess_etf_candidate(
            sessions_20,
            observations,
            close_sma20=close_sma20,
            excess_return_10d_pct=excess_return,
        )
        name_values = group["name"].dropna()
        name = str(name_values.iloc[-1]) if not name_values.empty else str(code)
        qualified = candidate.qualified and assessment.regime in (MarketRegime.GREEN, MarketRegime.YELLOW)
        reasons = list(candidate.reasons)
        if assessment.regime is MarketRegime.RED:
            reasons.append("市场偏弱，暂停新开仓")
        elif assessment.regime is MarketRegime.UNKNOWN:
            reasons.append("市场环境数据不足，不能开仓")
        records.append({
            "trade_date": target_date.isoformat(),
            "exchange": exchange,
            "code": code,
            "name": name,
            "market_regime": assessment.regime.value,
            "market_exposure_cap_pct": assessment.exposure_cap_pct * 100,
            "flow_positive_days_5d": candidate.flow_days_positive,
            "flow_to_estimated_value_pct_5d": candidate.flow_to_estimated_value_pct,
            "etf_return_10d_pct": etf_return,
            "benchmark_return_10d_pct": benchmark_return,
            "excess_return_10d_pct": excess_return,
            "qualified_candidate": qualified,
            "decision": "候选观察" if qualified else "不交易",
            "reason": "；".join(reasons),
        })
    result = pd.DataFrame.from_records(records)
    if not result.empty:
        result = result.sort_values(["qualified_candidate", "flow_to_estimated_value_pct_5d"], ascending=[False, False]).reset_index(drop=True)
    return result, _market_message(assessment)


def save_trade_review(review: pd.DataFrame, target_date: dt.date, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / f"trade_review_{target_date:%Y%m%d}.csv"
    temporary = path.with_suffix(".csv.tmp")
    review.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)
    return path


def _market_message(assessment, detail: str | None = None) -> str:
    status = f"市场状态：{assessment.regime.value}；权益仓位上限 {assessment.exposure_cap_pct:.0%}"
    if detail:
        status += f"（{detail}）"
    return status


def _numeric(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None
