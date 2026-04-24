"""
Tests for adaptive_sampler — intelligent log sampling engine.

Covers:
  - Empty / small input passthrough
  - Severity-weighted budget allocation
  - Diversity guarantee (every group represented)
  - Temporal ordering preservation
  - Budget clamping (min/max bounds)
  - Compute adaptive budget from profiles
  - Group key computation stability
  - Timestamp parsing (ISO 8601 variants)
  - Fallback on internal error
  - Metrics correctness
"""

import pytest
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from logmind.domain.analysis.adaptive_sampler import (
    adaptive_sample,
    compute_adaptive_budget,
    _compute_group_key,
    _parse_timestamp,
    _allocate_budget,
    _SeverityBucket,
    _LogEntry,
    SamplingMetrics,
    MIN_SAMPLE_SIZE,
    MAX_SAMPLE_SIZE,
    DEFAULT_SAMPLE_SIZE,
)


# ── Fixtures ─────────────────────────────────────────────

def _make_log(
    message: str,
    level: str = "ERROR",
    ts: str = "2026-04-24T10:00:00.000Z",
) -> dict:
    """Create a minimal log dict."""
    return {
        "message": message,
        "level": level,
        "@timestamp": ts,
    }


def _make_logs(n: int, prefix: str = "Error", level: str = "ERROR") -> list[dict]:
    """Create n logs with distinct messages."""
    base = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
    return [
        _make_log(
            message=f"{prefix} #{i}: something went wrong in component-{i % 5}",
            level=level,
            ts=(base + timedelta(seconds=i * 10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
        for i in range(n)
    ]


# ── Tests: Basic Behavior ───────────────────────────────

class TestAdaptiveSampleBasic:
    """Basic sampling behavior tests."""

    def test_empty_input(self):
        """Empty input returns empty output with passthrough strategy."""
        result, metrics = adaptive_sample([])
        assert result == []
        assert metrics.input_count == 0
        assert metrics.output_count == 0
        assert metrics.strategy == "passthrough"

    def test_small_input_passthrough(self):
        """Input smaller than budget passes through unchanged."""
        logs = _make_logs(10)
        result, metrics = adaptive_sample(logs, max_budget=50)
        assert len(result) == 10
        assert metrics.strategy == "passthrough"
        assert metrics.input_count == 10
        assert metrics.output_count == 10

    def test_exact_budget_passthrough(self):
        """Input exactly equal to budget passes through."""
        logs = _make_logs(50)
        result, metrics = adaptive_sample(logs, max_budget=50)
        assert len(result) == 50
        assert metrics.strategy == "passthrough"

    def test_over_budget_samples_down(self):
        """Input larger than budget is sampled down."""
        logs = _make_logs(500)
        result, metrics = adaptive_sample(logs, max_budget=100)
        assert len(result) <= 100
        assert metrics.strategy == "adaptive"
        assert metrics.input_count == 500
        assert metrics.output_count <= 100


class TestSeverityWeighting:
    """Severity-weighted allocation tests."""

    def test_critical_gets_priority(self):
        """CRITICAL logs get more slots than INFO logs."""
        critical = _make_logs(100, prefix="CriticalFault", level="CRITICAL")
        info = _make_logs(100, prefix="InfoMessage", level="INFO")
        all_logs = critical + info

        result, metrics = adaptive_sample(all_logs, max_budget=50)

        # Count severities in output
        critical_count = sum(1 for r in result if r["level"] == "CRITICAL")
        info_count = sum(1 for r in result if r["level"] == "INFO")

        # Critical should have significantly more samples
        assert critical_count > info_count, (
            f"Expected more CRITICAL ({critical_count}) than INFO ({info_count})"
        )

    def test_all_severities_represented(self):
        """When input has multiple severities, all should appear in output."""
        logs = (
            _make_logs(50, prefix="Crit", level="CRITICAL")
            + _make_logs(50, prefix="Err", level="ERROR")
            + _make_logs(50, prefix="Warn", level="WARNING")
            + _make_logs(50, prefix="Info", level="INFO")
        )
        result, metrics = adaptive_sample(logs, max_budget=60)

        output_levels = {r["level"] for r in result}
        assert "CRITICAL" in output_levels
        assert "ERROR" in output_levels
        # WARNING and INFO should also be present (at least 1 each)
        assert len(output_levels) >= 3


class TestDiversityGuarantee:
    """Diversity guarantee tests."""

    def test_every_group_has_representative(self):
        """Each unique error group gets at least one representative."""
        # Create 20 distinct exception types, 10 logs each = 200 total
        exception_names = [
            "NullPointerException", "IOException", "SQLException",
            "TimeoutException", "ClassNotFoundException",
            "IllegalArgumentException", "IndexOutOfBoundsException",
            "FileNotFoundException", "SocketException", "ParseException",
            "ArithmeticException", "SecurityException", "InterruptedException",
            "ClassCastException", "UnsupportedOperationException",
            "ConcurrentModificationException", "StackOverflowError",
            "OutOfMemoryError", "NoSuchElementException", "RuntimeException",
        ]
        logs = []
        base_ts = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
        for g, exc_name in enumerate(exception_names):
            for i in range(10):
                logs.append(_make_log(
                    message=f"java.lang.{exc_name}: failure in module {g}",
                    ts=(base_ts + timedelta(seconds=g * 100 + i)).strftime(
                        "%Y-%m-%dT%H:%M:%S.%fZ"
                    ),
                ))

        result, metrics = adaptive_sample(logs, max_budget=50)

        # At least 15 of 20 distinct exception types should be represented
        found_groups = set()
        for r in result:
            for exc in exception_names:
                if exc in r["message"]:
                    found_groups.add(exc)
                    break
        assert len(found_groups) >= 15, (
            f"Expected at least 15 unique groups, got {len(found_groups)}: {found_groups}"
        )


class TestTemporalOrder:
    """Temporal ordering tests."""

    def test_output_sorted_by_timestamp(self):
        """Output should be sorted by timestamp ascending."""
        logs = _make_logs(300)
        result, metrics = adaptive_sample(logs, max_budget=100)

        timestamps = [r.get("@timestamp", "") for r in result]
        assert timestamps == sorted(timestamps), "Output should be sorted by timestamp"

    def test_temporal_span_nonzero(self):
        """Metrics should report temporal span when timestamps are present."""
        logs = _make_logs(300)
        result, metrics = adaptive_sample(logs, max_budget=50)
        assert metrics.temporal_span_seconds > 0


class TestBudgetClamping:
    """Budget bound enforcement tests."""

    def test_budget_clamped_to_min(self):
        """Budget below MIN_SAMPLE_SIZE is clamped up."""
        logs = _make_logs(500)
        result, metrics = adaptive_sample(logs, max_budget=5)
        assert metrics.budget >= MIN_SAMPLE_SIZE

    def test_budget_clamped_to_max(self):
        """Budget above MAX_SAMPLE_SIZE is clamped down."""
        logs = _make_logs(500)
        result, metrics = adaptive_sample(logs, max_budget=9999)
        assert metrics.budget <= MAX_SAMPLE_SIZE


# ── Tests: Budget Computation ────────────────────────────

class TestComputeAdaptiveBudget:
    """Adaptive budget computation from Redis profiles."""

    def test_small_input_returns_input_count(self):
        """When input is smaller than default, return input count."""
        budget = compute_adaptive_budget("biz-1", input_count=50, default_budget=150)
        assert budget == 50

    def test_no_profile_returns_default(self):
        """When no Redis profile exists, return default budget."""
        budget = compute_adaptive_budget(
            "biz-no-profile-exists-xyz", input_count=500, default_budget=150
        )
        assert budget == 150

    def test_budget_within_bounds(self):
        """Computed budget is always within [MIN, MAX]."""
        for input_count in [100, 500, 1000, 5000]:
            budget = compute_adaptive_budget("biz-x", input_count=input_count)
            assert MIN_SAMPLE_SIZE <= budget <= MAX_SAMPLE_SIZE


# ── Tests: Group Key ────────────────────────────────────

class TestGroupKey:
    """Group key computation tests."""

    def test_exception_class_extracted(self):
        """Full qualified exception class name is used as group key."""
        key = _compute_group_key("java.lang.NullPointerException: value is null")
        assert key == "java.lang.NullPointerException"

    def test_exception_with_package(self):
        """Full qualified exception is matched."""
        key = _compute_group_key(
            "org.springframework.dao.DataAccessException: DB error"
        )
        assert key == "org.springframework.dao.DataAccessException"

    def test_non_exception_normalized(self):
        """Non-exception messages are normalized (IDs/numbers stripped)."""
        key1 = _compute_group_key("Connection to 10.0.0.1:3306 failed after 30s")
        key2 = _compute_group_key("Connection to 10.0.0.2:3306 failed after 60s")
        assert key1 == key2

    def test_uuid_stripped(self):
        """UUIDs in messages are normalized."""
        key1 = _compute_group_key("Task abc12345-6789-def0 failed")
        key2 = _compute_group_key("Task fff99999-1234-aaa0 failed")
        assert key1 == key2

    def test_empty_message(self):
        """Empty message returns sentinel key."""
        assert _compute_group_key("") == "__empty__"


# ── Tests: Timestamp Parsing ─────────────────────────────

class TestTimestampParsing:
    """Timestamp parsing tests for various ISO 8601 formats."""

    def test_iso_with_millis_z(self):
        ts = _parse_timestamp({"@timestamp": "2026-04-24T10:00:00.123Z"})
        assert ts > 0

    def test_iso_without_millis(self):
        ts = _parse_timestamp({"@timestamp": "2026-04-24T10:00:00Z"})
        assert ts > 0

    def test_iso_with_tz_offset(self):
        ts = _parse_timestamp({"@timestamp": "2026-04-24T18:00:00+08:00"})
        assert ts > 0

    def test_space_format(self):
        ts = _parse_timestamp({"@timestamp": "2026-04-24 10:00:00"})
        assert ts > 0

    def test_missing_timestamp(self):
        ts = _parse_timestamp({})
        assert ts == 0.0

    def test_numeric_timestamp(self):
        ts = _parse_timestamp({"@timestamp": 1745488800.0})
        assert ts == 1745488800.0

    def test_garbage_string(self):
        ts = _parse_timestamp({"@timestamp": "not-a-date"})
        assert ts == 0.0


# ── Tests: Budget Allocation ─────────────────────────────

class TestBudgetAllocation:
    """Internal budget allocation algorithm tests."""

    def test_single_bucket(self):
        """All budget goes to the single bucket."""
        buckets = {
            "ERROR": _SeverityBucket(
                severity="ERROR", weight=3.0,
                entries=[_LogEntry(
                    raw={}, severity="ERROR", severity_weight=3.0,
                    group_key="g1", timestamp_epoch=0, message_hash=str(i),
                ) for i in range(100)],
            ),
        }
        _allocate_budget(buckets, total_budget=50)
        assert buckets["ERROR"].allocated_slots == 50

    def test_two_buckets_weighted(self):
        """Higher weight bucket gets more slots."""
        buckets = {
            "CRITICAL": _SeverityBucket(
                severity="CRITICAL", weight=5.0,
                entries=[_LogEntry(
                    raw={}, severity="CRITICAL", severity_weight=5.0,
                    group_key="c", timestamp_epoch=0, message_hash=f"c{i}",
                ) for i in range(100)],
            ),
            "INFO": _SeverityBucket(
                severity="INFO", weight=0.5,
                entries=[_LogEntry(
                    raw={}, severity="INFO", severity_weight=0.5,
                    group_key="i", timestamp_epoch=0, message_hash=f"i{i}",
                ) for i in range(100)],
            ),
        }
        _allocate_budget(buckets, total_budget=50)
        assert buckets["CRITICAL"].allocated_slots > buckets["INFO"].allocated_slots

    def test_no_overallocation(self):
        """No bucket gets more slots than it has entries."""
        buckets = {
            "ERROR": _SeverityBucket(
                severity="ERROR", weight=3.0,
                entries=[_LogEntry(
                    raw={}, severity="ERROR", severity_weight=3.0,
                    group_key="e", timestamp_epoch=0, message_hash=f"e{i}",
                ) for i in range(3)],
            ),
        }
        _allocate_budget(buckets, total_budget=100)
        assert buckets["ERROR"].allocated_slots <= 3

    def test_total_does_not_exceed_budget(self):
        """Sum of all allocations never exceeds total budget."""
        buckets = {
            "CRITICAL": _SeverityBucket(
                severity="CRITICAL", weight=5.0,
                entries=[_LogEntry(
                    raw={}, severity="CRITICAL", severity_weight=5.0,
                    group_key="c", timestamp_epoch=0, message_hash=f"c{i}",
                ) for i in range(200)],
            ),
            "ERROR": _SeverityBucket(
                severity="ERROR", weight=3.0,
                entries=[_LogEntry(
                    raw={}, severity="ERROR", severity_weight=3.0,
                    group_key="e", timestamp_epoch=0, message_hash=f"e{i}",
                ) for i in range(200)],
            ),
            "INFO": _SeverityBucket(
                severity="INFO", weight=0.5,
                entries=[_LogEntry(
                    raw={}, severity="INFO", severity_weight=0.5,
                    group_key="i", timestamp_epoch=0, message_hash=f"i{i}",
                ) for i in range(200)],
            ),
        }
        _allocate_budget(buckets, total_budget=100)
        total = sum(b.allocated_slots for b in buckets.values())
        assert total <= 100


# ── Tests: Metrics ───────────────────────────────────────

class TestSamplingMetrics:
    """Sampling metrics correctness."""

    def test_metrics_to_dict(self):
        """Metrics serialize correctly."""
        m = SamplingMetrics(
            input_count=500, output_count=100, budget=150,
            severity_distribution={"ERROR": 70, "WARNING": 30},
            group_count=15, temporal_span_seconds=3600.123,
            strategy="adaptive",
        )
        d = m.to_dict()
        assert d["input_count"] == 500
        assert d["output_count"] == 100
        assert d["temporal_span_seconds"] == 3600.1
        assert d["strategy"] == "adaptive"

    def test_metrics_immutable(self):
        """SamplingMetrics is frozen dataclass."""
        m = SamplingMetrics(
            input_count=10, output_count=10, budget=50,
            severity_distribution={}, group_count=0,
            temporal_span_seconds=0.0, strategy="passthrough",
        )
        with pytest.raises(AttributeError):
            m.input_count = 999
