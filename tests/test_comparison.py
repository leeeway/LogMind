"""
Tests for Analysis Comparison (Diff) Feature

Tests the core comparison logic: normalization, matching, and diff categorization.
"""

import pytest

from logmind.domain.analysis.comparison import (
    ComparisonResult,
    ErrorEntry,
    compare_analyses,
    normalize_signature,
)


# ── normalize_signature Tests ────────────────────────────

class TestNormalizeSignature:
    """Tests for content normalization used in error matching."""

    def test_strips_timestamps(self):
        sig = normalize_signature("Error at 2026-04-25T10:30:00Z in UserService")
        assert "2026-04-25" not in sig
        assert "userservice" in sig

    def test_strips_uuids(self):
        sig = normalize_signature("Task a1b2c3d4-e5f6-7890-abcd-ef1234567890 failed")
        assert "a1b2c3d4" not in sig
        assert "failed" in sig

    def test_strips_hex_addresses(self):
        sig = normalize_signature("Null pointer at 0x7fff5fbff8c0")
        assert "0x7fff" not in sig

    def test_strips_long_numbers(self):
        sig = normalize_signature("Connection refused for server 192345678")
        assert "192345678" not in sig

    def test_strips_port_numbers(self):
        sig = normalize_signature("MySQL:3306 connection timeout")
        assert ":3306" not in sig

    def test_collapses_whitespace(self):
        sig = normalize_signature("error   at   line    42")
        assert "  " not in sig

    def test_truncates_at_120(self):
        long_content = "x" * 300
        sig = normalize_signature(long_content)
        assert len(sig) <= 120

    def test_lowercases(self):
        sig = normalize_signature("NullPointerException in UserService.processOrder")
        assert sig == sig.lower()

    def test_empty_input(self):
        assert normalize_signature("") == ""
        assert normalize_signature("   ") == ""

    def test_same_error_different_timestamps_match(self):
        """Same error at different times should produce the same signature."""
        sig_a = normalize_signature(
            "2026-04-25T10:00:00Z NullPointerException in UserService.getUser"
        )
        sig_b = normalize_signature(
            "2026-04-25T14:30:00Z NullPointerException in UserService.getUser"
        )
        assert sig_a == sig_b

    def test_same_error_different_ids_match(self):
        """Same error with different UUIDs should produce the same signature."""
        sig_a = normalize_signature(
            "Task a1111111-2222-3333-4444-555555555555: Redis timeout"
        )
        sig_b = normalize_signature(
            "Task b1111111-2222-3333-4444-666666666666: Redis timeout"
        )
        assert sig_a == sig_b


# ── compare_analyses Tests ───────────────────────────────

class TestCompareAnalyses:
    """Tests for the core comparison function."""

    def test_identical_results(self):
        """Two identical result sets should show no changes."""
        results = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Redis connection timeout", "confidence_score": 0.8},
        ]
        diff = compare_analyses(results, results)
        assert len(diff.new_errors) == 0
        assert len(diff.resolved_errors) == 0
        assert len(diff.worsened) == 0
        assert len(diff.improved) == 0
        assert diff.unchanged == 1

    def test_new_error_detected(self):
        """Error in B but not A should appear as new."""
        results_a = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Redis connection timeout", "confidence_score": 0.8},
        ]
        results_b = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Redis connection timeout", "confidence_score": 0.8},
            {"result_type": "root_cause", "severity": "critical",
             "content": "MySQL deadlock on user_orders table", "confidence_score": 0.9},
        ]
        diff = compare_analyses(results_a, results_b)
        assert len(diff.new_errors) == 1
        assert "mysql deadlock" in diff.new_errors[0]["content"].lower()

    def test_resolved_error_detected(self):
        """Error in A but not B should appear as resolved."""
        results_a = [
            {"result_type": "anomaly", "severity": "critical",
             "content": "Out of memory in payment service", "confidence_score": 0.9},
            {"result_type": "anomaly", "severity": "warning",
             "content": "Slow query on user_profiles", "confidence_score": 0.7},
        ]
        results_b = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Slow query on user_profiles", "confidence_score": 0.7},
        ]
        diff = compare_analyses(results_a, results_b)
        assert len(diff.resolved_errors) == 1
        assert "out of memory" in diff.resolved_errors[0]["content"].lower()

    def test_severity_upgrade_worsened(self):
        """Same error with higher severity in B → worsened."""
        results_a = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Redis connection timeout", "confidence_score": 0.8},
        ]
        results_b = [
            {"result_type": "anomaly", "severity": "critical",
             "content": "Redis connection timeout", "confidence_score": 0.8},
        ]
        diff = compare_analyses(results_a, results_b)
        assert len(diff.worsened) == 1
        assert diff.worsened[0]["change"] == "severity_upgrade"
        assert diff.worsened[0]["previous_severity"] == "warning"

    def test_severity_downgrade_improved(self):
        """Same error with lower severity in B → improved."""
        results_a = [
            {"result_type": "anomaly", "severity": "critical",
             "content": "Database connection pool exhausted", "confidence_score": 0.9},
        ]
        results_b = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Database connection pool exhausted", "confidence_score": 0.9},
        ]
        diff = compare_analyses(results_a, results_b)
        assert len(diff.improved) == 1
        assert diff.improved[0]["change"] == "severity_downgrade"

    def test_confidence_increase_worsened(self):
        """Same error with significantly higher confidence → worsened."""
        results_a = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Intermittent DNS resolution failure", "confidence_score": 0.3},
        ]
        results_b = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Intermittent DNS resolution failure", "confidence_score": 0.9},
        ]
        diff = compare_analyses(results_a, results_b)
        assert len(diff.worsened) == 1
        assert diff.worsened[0]["change"] == "confidence_increase"

    def test_empty_results_a(self):
        """All errors in B are new when A is empty."""
        results_b = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "New error pattern", "confidence_score": 0.8},
        ]
        diff = compare_analyses([], results_b)
        assert len(diff.new_errors) == 1
        assert len(diff.resolved_errors) == 0

    def test_empty_results_b(self):
        """All errors in A are resolved when B is empty."""
        results_a = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Old error pattern", "confidence_score": 0.8},
        ]
        diff = compare_analyses(results_a, [])
        assert len(diff.new_errors) == 0
        assert len(diff.resolved_errors) == 1

    def test_both_empty(self):
        """Two empty result sets → no changes."""
        diff = compare_analyses([], [])
        assert diff.summary == "两次分析结果完全一致"

    def test_summary_generated(self):
        """Summary should describe the changes."""
        results_a = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Old error", "confidence_score": 0.8},
        ]
        results_b = [
            {"result_type": "anomaly", "severity": "critical",
             "content": "Brand new error", "confidence_score": 0.9},
        ]
        diff = compare_analyses(results_a, results_b)
        assert "新增" in diff.summary
        assert "修复" in diff.summary

    def test_complex_scenario(self):
        """Multi-error scenario with new + resolved + unchanged."""
        results_a = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Redis timeout on getUser", "confidence_score": 0.7},
            {"result_type": "anomaly", "severity": "critical",
             "content": "OOM killed in worker-3", "confidence_score": 0.9},
            {"result_type": "summary", "severity": "info",
             "content": "Stable operation overall", "confidence_score": 0.5},
        ]
        results_b = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "Redis timeout on getUser", "confidence_score": 0.7},
            {"result_type": "anomaly", "severity": "critical",
             "content": "MySQL deadlock on orders", "confidence_score": 0.95},
            {"result_type": "summary", "severity": "info",
             "content": "Two critical issues detected", "confidence_score": 0.8},
        ]
        diff = compare_analyses(results_a, results_b)
        # OOM resolved, MySQL deadlock new, Redis unchanged, summaries differ
        assert len(diff.new_errors) >= 1
        assert len(diff.resolved_errors) >= 1

    def test_to_dict(self):
        """ComparisonResult.to_dict should include all fields."""
        diff = compare_analyses([], [], task_a_id="a1", task_b_id="b2",
                                task_a_time="10:00", task_b_time="11:00")
        d = diff.to_dict()
        assert d["task_a_id"] == "a1"
        assert d["task_b_id"] == "b2"
        assert "new_errors" in d
        assert "resolved_errors" in d
        assert "worsened" in d
        assert "improved" in d
        assert "unchanged" in d
        assert "summary" in d

    def test_content_truncated_in_output(self):
        """Long content should be truncated to 500 chars in to_dict."""
        results = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "x" * 1000, "confidence_score": 0.8},
        ]
        diff = compare_analyses([], results)
        assert len(diff.new_errors[0]["content"]) <= 500

    def test_timestamp_invariant_matching(self):
        """Same error at different times should match correctly."""
        results_a = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "2026-04-25T10:00:00Z NullPointerException at UserService.getUser:42",
             "confidence_score": 0.8},
        ]
        results_b = [
            {"result_type": "anomaly", "severity": "warning",
             "content": "2026-04-25T14:30:00Z NullPointerException at UserService.getUser:42",
             "confidence_score": 0.8},
        ]
        diff = compare_analyses(results_a, results_b)
        assert diff.unchanged == 1
        assert len(diff.new_errors) == 0
