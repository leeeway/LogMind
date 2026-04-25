"""
Tests for Change-Point Detection Stage

Tests the pure detection functions (no ES dependency) and the
stage-level execution with mocked ES responses.
"""

import json
import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from logmind.domain.analysis.stages.change_point import (
    ChangePoint,
    ChangePointDetectionStage,
    classify_trend,
    detect_change_points,
    MIN_BUCKETS_FOR_ANALYSIS,
    ROLLING_WINDOW_MINUTES,
    MIN_STD,
)


# ── detect_change_points Tests ───────────────────────────

class TestDetectChangePoints:
    """Tests for the pure Z-Score change-point detection function."""

    def test_stable_series_no_change_points(self):
        """Flat series should produce no change-points."""
        counts = [10] * 60  # 60 minutes at 10/min
        timestamps = [f"2026-04-25T10:{i:02d}:00Z" for i in range(60)]
        result = detect_change_points(counts, timestamps)
        assert result == []

    def test_spike_detected(self):
        """A sudden spike should be detected as a change-point."""
        counts = [5] * 40 + [200] + [5] * 19
        timestamps = [f"2026-04-25T10:{i:02d}:00Z" for i in range(60)]
        result = detect_change_points(counts, timestamps, threshold=3.0)
        assert len(result) >= 1
        # The spike is at index 40
        spike_cp = result[0]
        assert spike_cp.bucket_count == 200
        assert spike_cp.z_score > 3.0
        assert spike_cp.before_rate < 10

    def test_gradual_increase_no_spike(self):
        """Slowly increasing series should not trigger sharp spike detection."""
        # Linear increase: 1, 2, 3, ..., 60
        counts = list(range(1, 61))
        timestamps = [f"2026-04-25T10:{i:02d}:00Z" for i in range(60)]
        result = detect_change_points(counts, timestamps, threshold=3.0)
        # Gradual increase shouldn't produce z > 3 spikes
        assert len(result) == 0

    def test_multiple_spikes(self):
        """Multiple spikes at different times should all be detected."""
        counts = [5] * 35 + [100] + [5] * 10 + [150] + [5] * 13
        timestamps = [f"2026-04-25T{10 + i // 60}:{i % 60:02d}:00Z" for i in range(60)]
        result = detect_change_points(counts, timestamps, threshold=3.0)
        assert len(result) >= 2

    def test_insufficient_data_returns_empty(self):
        """With too few data points, detection should return empty."""
        counts = [10] * 5
        timestamps = [f"2026-04-25T10:{i:02d}:00Z" for i in range(5)]
        result = detect_change_points(counts, timestamps)
        assert result == []

    def test_all_zeros_no_crash(self):
        """All-zero series should not crash (MIN_STD prevents div/0)."""
        counts = [0] * 60
        timestamps = [f"2026-04-25T10:{i:02d}:00Z" for i in range(60)]
        result = detect_change_points(counts, timestamps)
        assert result == []

    def test_threshold_sensitivity(self):
        """Lower threshold should detect more change-points."""
        counts = [5] * 35 + [30] + [5] * 24
        timestamps = [f"2026-04-25T10:{i:02d}:00Z" for i in range(60)]

        result_high = detect_change_points(counts, timestamps, threshold=5.0)
        result_low = detect_change_points(counts, timestamps, threshold=2.0)
        assert len(result_low) >= len(result_high)

    def test_mismatched_lengths_raises(self):
        """counts and timestamps with different lengths should raise."""
        counts = [10] * 40
        timestamps = [f"T{i}" for i in range(30)]
        with pytest.raises(ValueError, match="same length"):
            detect_change_points(counts, timestamps)

    def test_change_point_to_dict(self):
        """ChangePoint.to_dict should produce correct structure."""
        cp = ChangePoint(
            timestamp="2026-04-25T10:40:00Z",
            before_rate=5.3,
            after_rate=150.7,
            z_score=12.456789,
            bucket_count=200,
        )
        d = cp.to_dict()
        assert d["timestamp"] == "2026-04-25T10:40:00Z"
        assert d["before_rate"] == 5.3
        assert d["after_rate"] == 150.7
        assert d["z_score"] == 12.46  # rounded to 2
        assert d["bucket_count"] == 200

    def test_custom_rolling_window(self):
        """Custom rolling window should affect detection."""
        counts = [5] * 15 + [50] + [5] * 44
        timestamps = [f"2026-04-25T10:{i:02d}:00Z" for i in range(60)]

        # With window=10, we have enough trailing data
        result_short = detect_change_points(counts, timestamps, rolling_window=10)
        # With window=50, spike is within the window
        result_long = detect_change_points(counts, timestamps, rolling_window=50)
        assert len(result_short) >= len(result_long)

    def test_spike_at_end_of_series(self):
        """Spike at the very end of the series should be detected."""
        counts = [5] * 59 + [500]
        timestamps = [f"2026-04-25T10:{i:02d}:00Z" for i in range(60)]
        result = detect_change_points(counts, timestamps, threshold=3.0)
        assert len(result) >= 1
        assert result[-1].bucket_count == 500


# ── classify_trend Tests ─────────────────────────────────

class TestClassifyTrend:
    """Tests for trend classification."""

    def test_stable(self):
        counts = [10] * 60
        assert classify_trend(counts) == "stable"

    def test_increasing(self):
        """Last third significantly higher than first third → increasing."""
        counts = [5] * 20 + [10] * 20 + [20] * 20
        assert classify_trend(counts) == "increasing"

    def test_declining(self):
        """Last third significantly lower than first third → declining."""
        counts = [50] * 20 + [30] * 20 + [10] * 20
        assert classify_trend(counts) == "declining"

    def test_spike(self):
        """Peak dramatically higher than first-third average → spike."""
        counts = [5] * 50 + [500] + [5] * 9
        assert classify_trend(counts) == "spike"

    def test_too_short_returns_unknown(self):
        """Series shorter than 6 should return unknown."""
        assert classify_trend([1, 2, 3]) == "unknown"
        assert classify_trend([]) == "unknown"

    def test_all_zeros(self):
        """All-zero series should be stable."""
        counts = [0] * 60
        assert classify_trend(counts) == "stable"

    def test_spike_from_zero_baseline(self):
        """Spike from zero baseline should be detected."""
        counts = [0] * 50 + [50] + [0] * 9
        assert classify_trend(counts) == "spike"


# ── Stage-Level Tests ────────────────────────────────────

class TestChangePointDetectionStage:
    """Tests for the pipeline stage with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_disabled_config_skips(self):
        """When disabled in config, stage should be a no-op."""
        from logmind.domain.analysis.pipeline import PipelineContext

        mock_service = MagicMock()
        stage = ChangePointDetectionStage(mock_service)
        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )

        with patch("logmind.domain.analysis.stages.change_point.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(analysis_changepoint_enabled=False)
            result = await stage.execute(ctx)

        assert result.change_points == []
        assert result.error_rate_trend == "unknown"

    @pytest.mark.asyncio
    async def test_no_time_to_skips(self):
        """Without time_to, stage should be a no-op."""
        from logmind.domain.analysis.pipeline import PipelineContext

        mock_service = MagicMock()
        stage = ChangePointDetectionStage(mock_service)
        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.time_to = None

        with patch("logmind.domain.analysis.stages.change_point.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                analysis_changepoint_enabled=True,
                analysis_changepoint_threshold=3.0,
                analysis_changepoint_window_hours=4,
            )
            result = await stage.execute(ctx)

        assert result.change_points == []

    @pytest.mark.asyncio
    async def test_insufficient_buckets_skips(self):
        """With too few ES buckets, stage should skip gracefully."""
        from logmind.domain.analysis.pipeline import PipelineContext

        mock_service = MagicMock()
        stage = ChangePointDetectionStage(mock_service)
        stage._fetch_error_time_series = AsyncMock(return_value=[
            {"timestamp": "T1", "count": 5},
            {"timestamp": "T2", "count": 5},
        ])

        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.time_to = datetime.now(timezone.utc)

        with patch("logmind.domain.analysis.stages.change_point.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                analysis_changepoint_enabled=True,
                analysis_changepoint_threshold=3.0,
                analysis_changepoint_window_hours=4,
            )
            result = await stage.execute(ctx)

        assert result.change_points == []
        assert result.error_rate_trend == "unknown"

    @pytest.mark.asyncio
    async def test_spike_detected_end_to_end(self):
        """Full stage execution with a spike should populate context."""
        from logmind.domain.analysis.pipeline import PipelineContext

        # Build a time series with a spike at minute 40
        buckets = []
        for i in range(60):
            count = 5 if i != 40 else 300
            buckets.append({
                "timestamp": f"2026-04-25T10:{i:02d}:00Z",
                "count": count,
            })

        mock_service = MagicMock()
        stage = ChangePointDetectionStage(mock_service)
        stage._fetch_error_time_series = AsyncMock(return_value=buckets)

        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.time_to = datetime.now(timezone.utc)

        with patch("logmind.domain.analysis.stages.change_point.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                analysis_changepoint_enabled=True,
                analysis_changepoint_threshold=3.0,
                analysis_changepoint_window_hours=4,
            )
            result = await stage.execute(ctx)

        assert len(result.change_points) >= 1
        assert result.change_points[0]["bucket_count"] == 300
        assert result.error_rate_trend == "spike"

    @pytest.mark.asyncio
    async def test_es_failure_graceful(self):
        """ES query failure should not crash the pipeline."""
        from logmind.domain.analysis.pipeline import PipelineContext

        mock_service = MagicMock()
        stage = ChangePointDetectionStage(mock_service)
        stage._fetch_error_time_series = AsyncMock(side_effect=Exception("ES down"))

        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.time_to = datetime.now(timezone.utc)

        with patch("logmind.domain.analysis.stages.change_point.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                analysis_changepoint_enabled=True,
                analysis_changepoint_threshold=3.0,
                analysis_changepoint_window_hours=4,
            )
            result = await stage.execute(ctx)

        # Should not crash — graceful fallback
        assert result.change_points == []

    @pytest.mark.asyncio
    async def test_stable_series_trend(self):
        """Flat series should report stable trend."""
        from logmind.domain.analysis.pipeline import PipelineContext

        buckets = [
            {"timestamp": f"2026-04-25T10:{i:02d}:00Z", "count": 10}
            for i in range(60)
        ]

        mock_service = MagicMock()
        stage = ChangePointDetectionStage(mock_service)
        stage._fetch_error_time_series = AsyncMock(return_value=buckets)

        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.time_to = datetime.now(timezone.utc)

        with patch("logmind.domain.analysis.stages.change_point.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                analysis_changepoint_enabled=True,
                analysis_changepoint_threshold=3.0,
                analysis_changepoint_window_hours=4,
            )
            result = await stage.execute(ctx)

        assert result.change_points == []
        assert result.error_rate_trend == "stable"
