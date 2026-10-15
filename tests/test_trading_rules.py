from datetime import date, timedelta
import unittest

from industry_flow.holder_tracker import HoldingObservation, compare_public_holdings, is_stale
from industry_flow.trading_rules import (
    MarketRegime,
    RiskMode,
    assess_etf_candidate,
    assess_market,
    assess_risk_mode,
    position_exit_reason,
    size_etf_position,
)


class MarketRuleTests(unittest.TestCase):
    def test_market_green_requires_trend_and_breadth(self):
        result = assess_market(101, 100, 0.50)
        self.assertEqual(result.regime, MarketRegime.GREEN)
        self.assertEqual(result.exposure_cap_pct, 0.60)

    def test_one_market_condition_produces_reduced_cap(self):
        result = assess_market(101, 100, 0.49)
        self.assertEqual(result.regime, MarketRegime.YELLOW)
        self.assertEqual(result.exposure_cap_pct, 0.30)

    def test_two_failed_market_conditions_block_entries(self):
        self.assertEqual(assess_market(99, 100, 0.49).regime, MarketRegime.RED)

    def test_missing_market_evidence_fails_closed(self):
        result = assess_market(None, 100, 0.8)
        self.assertEqual(result.regime, MarketRegime.UNKNOWN)
        self.assertEqual(result.exposure_cap_pct, 0)


class CandidateRuleTests(unittest.TestCase):
    def setUp(self):
        self.sessions = [date(2026, 1, 1) + timedelta(days=index) for index in range(20)]
        self.observations = [
            {
                "trade_date": session.isoformat(),
                "estimated_flow": 100.0 if index in (15, 16, 18) else -10.0,
                "shares": 1000.0,
                "close": 2.0,
            }
            for index, session in enumerate(self.sessions)
        ]

    def assess(self, observations=None, *, sma=1.9, excess=1.0):
        return assess_etf_candidate(
            self.sessions,
            self.observations if observations is None else observations,
            close_sma20=sma,
            excess_return_10d_pct=excess,
        )

    def test_candidate_requires_positive_flow_days_and_price_confirmation(self):
        result = self.assess()
        self.assertTrue(result.qualified)
        self.assertEqual(result.flow_days_positive, 3)
        self.assertGreater(result.flow_to_estimated_value_pct, 0)

    def test_missing_trade_session_is_not_skipped(self):
        result = self.assess(self.observations[:-1])
        self.assertFalse(result.qualified)
        self.assertIn("数据不完整", result.reasons[0])

    def test_single_day_flow_pulse_does_not_qualify(self):
        rows = [dict(row, estimated_flow=-10.0) for row in self.observations]
        rows[-1]["estimated_flow"] = 500.0
        self.assertFalse(self.assess(rows).qualified)

    def test_negative_relative_strength_does_not_qualify(self):
        self.assertFalse(self.assess(excess=-0.1).qualified)

    def test_missing_long_price_history_does_not_qualify(self):
        self.assertFalse(assess_etf_candidate(self.sessions[:19], self.observations, close_sma20=2, excess_return_10d_pct=1).qualified)


class RiskRuleTests(unittest.TestCase):
    def test_drawdown_bands(self):
        self.assertEqual(assess_risk_mode(3.99), RiskMode.NORMAL)
        self.assertEqual(assess_risk_mode(4), RiskMode.HALF_RISK)
        self.assertEqual(assess_risk_mode(6), RiskMode.PAUSE)
        self.assertEqual(assess_risk_mode(8), RiskMode.REDUCE)
        self.assertEqual(assess_risk_mode(None), RiskMode.PAUSE)

    def test_position_size_respects_per_trade_risk_and_lot_size(self):
        market = assess_market(101, 100, 0.8)
        result = size_etf_position(equity=100_000, entry_price=2, stop_price=1.9, market=market, risk_mode=RiskMode.NORMAL)
        self.assertEqual(result.shares % 100, 0)
        self.assertLessEqual(result.value, 15_000)
        self.assertLessEqual(result.value * 0.052, result.maximum_loss_budget + 1e-8)

    def test_pause_mode_produces_no_position(self):
        market = assess_market(101, 100, 0.8)
        result = size_etf_position(equity=100_000, entry_price=2, stop_price=1.9, market=market, risk_mode=RiskMode.PAUSE)
        self.assertEqual(result.shares, 0)

    def test_exit_rules_prioritize_stop_then_trailing_time_and_stall(self):
        common = {"entry_price": 10, "initial_stop": 9, "peak_close": 11, "atr": 0.5}
        self.assertEqual(
            position_exit_reason(**common, current_close=8.9, holding_sessions=1),
            "触及初始退出位",
        )
        self.assertEqual(
            position_exit_reason(**common, current_close=9.9, holding_sessions=4),
            "触发2倍ATR跟踪退出",
        )
        self.assertEqual(
            position_exit_reason(**common, current_close=10.5, holding_sessions=10),
            "达到最长持有期",
        )
        self.assertEqual(
            position_exit_reason(**{**common, "peak_close": 10.5}, current_close=9.8, holding_sessions=3),
            "持有期内未走强",
        )


class HolderTrackerTests(unittest.TestCase):
    def observation(self, day, shares):
        return HoldingObservation("样本组合", "510300", day, day + timedelta(days=30), shares, None)

    def test_compare_uses_share_change_and_keeps_publication_age(self):
        previous = self.observation(date(2025, 12, 31), 100)
        current = self.observation(date(2026, 3, 31), 125)
        result = compare_public_holdings(previous, current, as_of=date(2026, 6, 1))
        self.assertEqual(result.share_change_pct, 25)
        self.assertEqual(result.publication_age_days, 32)

    def test_holding_period_staleness_is_explicit(self):
        current = self.observation(date(2026, 1, 1), 100)
        self.assertTrue(is_stale(current, as_of=date(2026, 6, 1)))
        self.assertFalse(is_stale(current, as_of=date(2026, 4, 1)))


if __name__ == "__main__":
    unittest.main()
