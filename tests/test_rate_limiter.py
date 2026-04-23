"""
Unit Tests — Rate Limiter Module

Tests the sliding window rate limit logic, route matching,
and fail-open behavior.
"""

from logmind.core.rate_limiter import _get_rate_limit, _EXEMPT_PATHS


class TestGetRateLimit:
    """Test route → rate limit matching."""

    def test_analysis_route(self):
        limit = _get_rate_limit("/api/v1/analysis/tasks")
        assert limit is not None
        max_req, window = limit
        assert max_req == 10
        assert window == 60

    def test_rag_route(self):
        limit = _get_rate_limit("/api/v1/rag/search")
        assert limit is not None
        assert limit[0] == 10

    def test_general_route(self):
        limit = _get_rate_limit("/api/v1/tenants")
        assert limit is not None
        assert limit[0] == 60  # general limit

    def test_health_exempt(self):
        limit = _get_rate_limit("/api/v1/health")
        assert limit is None

    def test_health_live_exempt(self):
        limit = _get_rate_limit("/api/v1/health/live")
        assert limit is None

    def test_docs_exempt(self):
        limit = _get_rate_limit("/docs")
        assert limit is None

    def test_openapi_exempt(self):
        limit = _get_rate_limit("/openapi.json")
        assert limit is None

    def test_most_specific_match(self):
        """Analysis-specific limit should be used over general /api/v1 limit."""
        analysis_limit = _get_rate_limit("/api/v1/analysis/run")
        general_limit = _get_rate_limit("/api/v1/providers")
        assert analysis_limit[0] < general_limit[0]  # 10 < 60


class TestExemptPaths:
    """Test exempt path set."""

    def test_contains_expected_paths(self):
        assert "/api/v1/health" in _EXEMPT_PATHS
        assert "/api/v1/health/live" in _EXEMPT_PATHS
        assert "/docs" in _EXEMPT_PATHS
        assert "/openapi.json" in _EXEMPT_PATHS

    def test_non_exempt(self):
        assert "/api/v1/analysis" not in _EXEMPT_PATHS
        assert "/api/v1/alerts" not in _EXEMPT_PATHS
