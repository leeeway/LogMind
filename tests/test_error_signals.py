"""
Unit Tests — Error Signals Module

Tests signal registry, deduplication, and combined output.
"""

from logmind.domain.log.error_signals import (
    ALL_STATIC_SIGNALS,
    INFRA_SIGNALS,
    BUSINESS_FAILURE_SIGNALS,
    ERROR_CODE_SIGNALS,
    EXCEPTION_SIGNALS,
    _static_set,
)


class TestStaticSignals:
    """Test the curated static signal lists."""

    def test_infra_signals_not_empty(self):
        assert len(INFRA_SIGNALS) > 10

    def test_business_signals_not_empty(self):
        assert len(BUSINESS_FAILURE_SIGNALS) > 5

    def test_error_code_signals(self):
        assert "errorCode=-" in ERROR_CODE_SIGNALS
        assert "error_code=-" in ERROR_CODE_SIGNALS

    def test_exception_signals(self):
        assert "NullPointerException" in EXCEPTION_SIGNALS
        assert "Caused by:" in EXCEPTION_SIGNALS
        assert "Traceback (most recent" in EXCEPTION_SIGNALS

    def test_all_static_is_union(self):
        """ALL_STATIC_SIGNALS should contain all sub-lists."""
        expected_len = (
            len(INFRA_SIGNALS)
            + len(BUSINESS_FAILURE_SIGNALS)
            + len(ERROR_CODE_SIGNALS)
            + len(EXCEPTION_SIGNALS)
        )
        assert len(ALL_STATIC_SIGNALS) == expected_len

    def test_no_empty_signals(self):
        """No signal should be empty or whitespace-only."""
        for s in ALL_STATIC_SIGNALS:
            assert s.strip(), f"Empty signal found: '{s}'"

    def test_no_duplicate_static_signals(self):
        """Static signals should not contain duplicates."""
        seen = set()
        duplicates = []
        for s in ALL_STATIC_SIGNALS:
            if s in seen:
                duplicates.append(s)
            seen.add(s)
        assert duplicates == [], f"Duplicate signals: {duplicates}"


class TestStaticSet:
    """Test the pre-computed set for fast lookups."""

    def test_set_matches_list(self):
        """The _static_set should contain all unique items from ALL_STATIC_SIGNALS."""
        assert len(_static_set) == len(set(ALL_STATIC_SIGNALS))

    def test_lookup_performance(self):
        """Key lookups should work correctly."""
        assert "NullPointerException" in _static_set
        assert "connection refused" in _static_set
        assert "请求失败" in _static_set
        assert "random_string_not_a_signal" not in _static_set


class TestSignalCoverage:
    """Test that key error patterns are covered."""

    def test_timeout_variants_covered(self):
        timeout_signals = [s for s in ALL_STATIC_SIGNALS if "timeout" in s.lower() or "timed out" in s.lower()]
        assert len(timeout_signals) >= 4, "Should cover multiple timeout patterns"

    def test_connection_failures_covered(self):
        conn_signals = [s for s in ALL_STATIC_SIGNALS if "connection" in s.lower() or "connect" in s.lower()]
        assert len(conn_signals) >= 3

    def test_oom_covered(self):
        assert "OutOfMemoryError" in _static_set
        assert "out of memory" in _static_set

    def test_chinese_business_failures(self):
        """Chinese business failure patterns should be present."""
        chinese = [s for s in ALL_STATIC_SIGNALS if any('\u4e00' <= c <= '\u9fff' for c in s)]
        assert len(chinese) >= 10, "Should have 10+ Chinese patterns"

    def test_negative_error_codes(self):
        """Negative error code patterns should end with '=-'."""
        for s in ERROR_CODE_SIGNALS:
            assert s.endswith("=-"), f"Error code signal should end with =-: {s}"

    def test_java_exceptions_covered(self):
        java_excs = [
            "NullPointerException",
            "StackOverflowError",
            "ClassNotFoundException",
            "IllegalStateException",
        ]
        for exc in java_excs:
            assert exc in _static_set, f"Missing Java exception: {exc}"

    def test_csharp_exceptions_covered(self):
        assert "NullReferenceException" in _static_set
        assert "DataIntegrityViolationException" in _static_set
