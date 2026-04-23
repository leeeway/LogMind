"""
Unit Tests for Priority Decision Engine

Tests the multi-dimensional scoring engine that calculates
alert priority (P0/P1/P2) and determines notification actions.
"""

import pytest
from unittest.mock import patch
from datetime import datetime

from logmind.domain.analysis.priority_engine import (
    PriorityDecisionEngine,
    PriorityFactors,
    _P0_THRESHOLD,
    _P1_THRESHOLD,
)


@pytest.fixture
def engine():
    return PriorityDecisionEngine()


class TestScoreCalculation:
    """Tests for priority score calculation."""

    def test_critical_severity_high_score(self, engine):
        factors = PriorityFactors(
            ai_severity="critical",
            confidence=0.95,
            current_error_count=100,
            baseline_error_count=10,
            business_weight=8,
            is_core_path=True,
        )
        decision = engine.decide(factors)
        assert decision.priority == "P0"
        assert decision.score >= _P0_THRESHOLD

    def test_info_severity_low_score(self, engine):
        factors = PriorityFactors(
            ai_severity="info",
            confidence=0.5,
            current_error_count=5,
            baseline_error_count=10,
            business_weight=3,
        )
        decision = engine.decide(factors)
        assert decision.priority == "P2"
        assert decision.score < _P1_THRESHOLD

    def test_warning_with_high_frequency_escalates(self, engine):
        factors = PriorityFactors(
            ai_severity="warning",
            confidence=0.8,
            current_error_count=500,
            baseline_error_count=10,  # 50x spike
            business_weight=7,
            is_core_path=True,
        )
        decision = engine.decide(factors)
        # High frequency anomaly should push into P0/P1
        assert decision.priority in ("P0", "P1")

    def test_score_capped_at_100(self, engine):
        factors = PriorityFactors(
            ai_severity="critical",
            confidence=1.0,
            current_error_count=10000,
            baseline_error_count=1,
            business_weight=10,
            is_core_path=True,
            has_stack_traces=True,
            unique_error_types=5,
            log_count=1000,
        )
        decision = engine.decide(factors)
        assert decision.score <= 100.0

    def test_score_floored_at_0(self, engine):
        factors = PriorityFactors(
            ai_severity="info",
            confidence=0.1,
            current_error_count=0,
            baseline_error_count=100,
            business_weight=1,
            historical_adjustment=-15,
        )
        decision = engine.decide(factors)
        assert decision.score >= 0.0

    def test_stack_traces_bonus(self, engine):
        base = PriorityFactors(ai_severity="critical", business_weight=5)
        with_stacks = PriorityFactors(ai_severity="critical", business_weight=5, has_stack_traces=True)
        d1 = engine.decide(base)
        d2 = engine.decide(with_stacks)
        assert d2.score > d1.score

    def test_multiple_error_types_bonus(self, engine):
        base = PriorityFactors(ai_severity="warning", business_weight=5, unique_error_types=1)
        multi = PriorityFactors(ai_severity="warning", business_weight=5, unique_error_types=5)
        d1 = engine.decide(base)
        d2 = engine.decide(multi)
        assert d2.score > d1.score


class TestPriorityMapping:
    """Tests for score-to-priority mapping."""

    def test_p0_boundary(self, engine):
        assert engine._score_to_priority(_P0_THRESHOLD) == "P0"
        assert engine._score_to_priority(_P0_THRESHOLD - 0.1) == "P1"

    def test_p1_boundary(self, engine):
        assert engine._score_to_priority(_P1_THRESHOLD) == "P1"
        assert engine._score_to_priority(_P1_THRESHOLD - 0.1) == "P2"

    def test_p2_low_score(self, engine):
        assert engine._score_to_priority(0) == "P2"
        assert engine._score_to_priority(10) == "P2"


class TestNotificationActions:
    """Tests for notification behavior based on priority and night policy."""

    def test_p0_always_notifies_daytime(self, engine):
        factors = PriorityFactors(ai_severity="critical", business_weight=10,
                                   confidence=0.9, is_core_path=True,
                                   current_error_count=100, baseline_error_count=5)
        with patch.object(engine, '_is_night_time', return_value=False):
            decision = engine.decide(factors)
        assert decision.actions.should_notify is True

    def test_p0_night_p0_only_wakes(self, engine):
        factors = PriorityFactors(ai_severity="critical", business_weight=10,
                                   confidence=0.9, is_core_path=True,
                                   current_error_count=100, baseline_error_count=5)
        with patch.object(engine, '_is_night_time', return_value=True):
            decision = engine.decide(factors, night_policy="p0_only")
        assert decision.actions.should_wake is True

    def test_p0_night_silent_delays(self, engine):
        factors = PriorityFactors(ai_severity="critical", business_weight=10,
                                   confidence=0.9, is_core_path=True,
                                   current_error_count=100, baseline_error_count=5)
        with patch.object(engine, '_is_night_time', return_value=True):
            decision = engine.decide(factors, night_policy="silent")
        assert decision.actions.delay_until_morning is True
        assert decision.actions.should_notify is False

    def test_p2_never_notifies(self, engine):
        factors = PriorityFactors(ai_severity="info", business_weight=2)
        decision = engine.decide(factors)
        assert decision.actions.should_notify is False
        assert decision.actions.include_in_digest is True

    def test_suppressed_overrides_all(self, engine):
        factors = PriorityFactors(
            ai_severity="critical", business_weight=10,
            confidence=0.9, is_core_path=True,
            current_error_count=100, baseline_error_count=5,
            is_suppressed=True, suppression_reason="Known noise pattern"
        )
        decision = engine.decide(factors)
        assert decision.actions.should_notify is False
        assert decision.actions.should_wake is False
        assert "自动抑制" in decision.actions.reason


class TestNightTimeDetection:
    """Tests for the night time window parser."""

    def test_cross_midnight_window(self, engine):
        # 22:00-08:00 crosses midnight
        with patch('logmind.domain.analysis.priority_engine.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 1, 23, 30)
            assert engine._is_night_time("22:00-08:00") is True

    def test_cross_midnight_early_morning(self, engine):
        with patch('logmind.domain.analysis.priority_engine.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 1, 6, 0)
            assert engine._is_night_time("22:00-08:00") is True

    def test_daytime_not_night(self, engine):
        with patch('logmind.domain.analysis.priority_engine.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 1, 14, 0)
            assert engine._is_night_time("22:00-08:00") is False

    def test_invalid_format_returns_false(self, engine):
        assert engine._is_night_time("invalid") is False
        assert engine._is_night_time("") is False


class TestSelfLearningIntegration:
    """Tests for the historical adjustment factor."""

    def test_positive_adjustment_increases_score(self, engine):
        base = PriorityFactors(ai_severity="warning", business_weight=5)
        adjusted = PriorityFactors(ai_severity="warning", business_weight=5,
                                    historical_adjustment=10.0)
        d1 = engine.decide(base)
        d2 = engine.decide(adjusted)
        assert d2.score > d1.score

    def test_negative_adjustment_decreases_score(self, engine):
        base = PriorityFactors(ai_severity="warning", business_weight=5)
        adjusted = PriorityFactors(ai_severity="warning", business_weight=5,
                                    historical_adjustment=-15.0)
        d1 = engine.decide(base)
        d2 = engine.decide(adjusted)
        assert d2.score < d1.score

    def test_adjustment_clamped(self, engine):
        """Historical adjustment should be clamped to [-15, +10]."""
        extreme = PriorityFactors(ai_severity="info", business_weight=5,
                                   historical_adjustment=-100.0)
        decision = engine.decide(extreme)
        # Score should not go below 0 (clamped)
        assert decision.score >= 0.0
