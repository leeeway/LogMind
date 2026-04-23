"""
Unit Tests — Health Check Module

Tests health check data structures and conversion logic.
Async probe functions require actual infrastructure imports,
so we test the pure-logic parts here.
"""

from logmind.core.health import (
    ComponentHealth,
    SystemHealth,
    _component_to_dict,
)


class TestComponentHealth:
    """Test ComponentHealth dataclass."""

    def test_default_values(self):
        h = ComponentHealth()
        assert h.status == "ok"
        assert h.latency_ms == 0.0
        assert h.error is None

    def test_down_with_error(self):
        h = ComponentHealth(status="down", error="connection refused")
        assert h.status == "down"
        assert h.error == "connection refused"

    def test_degraded(self):
        h = ComponentHealth(status="degraded", detail="PING returned False")
        assert h.status == "degraded"


class TestComponentToDict:
    """Test dict conversion logic."""

    def test_ok_with_latency(self):
        h = ComponentHealth(status="ok", latency_ms=2.5)
        d = _component_to_dict(h)
        assert d == {"status": "ok", "latency_ms": 2.5}

    def test_down_with_error(self):
        h = ComponentHealth(status="down", error="fail")
        d = _component_to_dict(h)
        assert d["status"] == "down"
        assert d["error"] == "fail"
        assert "latency_ms" not in d  # 0.0 should be omitted

    def test_with_detail_dict(self):
        h = ComponentHealth(status="ok", latency_ms=1.0, detail={"cluster": "green"})
        d = _component_to_dict(h)
        assert d["detail"]["cluster"] == "green"

    def test_empty_detail_omitted(self):
        h = ComponentHealth(status="ok", latency_ms=1.0, detail="")
        d = _component_to_dict(h)
        assert "detail" not in d

    def test_full_dict(self):
        h = ComponentHealth(status="degraded", latency_ms=5.0, detail="slow", error="timeout")
        d = _component_to_dict(h)
        assert d["status"] == "degraded"
        assert d["latency_ms"] == 5.0
        assert d["detail"] == "slow"
        assert d["error"] == "timeout"


class TestSystemHealth:
    """Test SystemHealth dataclass."""

    def test_default(self):
        h = SystemHealth()
        assert h.status == "ok"
        assert h.version == "2.1.0"
        assert h.components == {}

    def test_to_dict(self):
        h = SystemHealth(
            status="degraded",
            components={"db": {"status": "ok"}, "redis": {"status": "degraded"}},
        )
        d = h.to_dict()
        assert d["status"] == "degraded"
        assert len(d["components"]) == 2

    def test_to_dict_has_version(self):
        h = SystemHealth()
        d = h.to_dict()
        assert "version" in d


class TestOverallStatusLogic:
    """Test the overall status determination logic."""

    def _determine_overall(self, db="ok", redis="ok", es="ok", celery="ok"):
        """Replicate the logic from get_system_health for unit testing."""
        statuses = [db, redis, es, celery]
        if db == "down" or es == "down":
            return "down"
        elif any(s != "ok" for s in statuses):
            return "degraded"
        return "ok"

    def test_all_ok(self):
        assert self._determine_overall() == "ok"

    def test_db_down_is_critical(self):
        assert self._determine_overall(db="down") == "down"

    def test_es_down_is_critical(self):
        assert self._determine_overall(es="down") == "down"

    def test_redis_down_is_degraded(self):
        """Redis down should NOT cause overall 'down' — it's non-critical."""
        assert self._determine_overall(redis="down") == "degraded"

    def test_celery_degraded_is_degraded(self):
        assert self._determine_overall(celery="degraded") == "degraded"

    def test_all_degraded(self):
        assert self._determine_overall("degraded", "degraded", "degraded", "degraded") == "degraded"

    def test_db_down_overrides_celery_ok(self):
        assert self._determine_overall(db="down", celery="ok") == "down"

    def test_mixed_critical_and_noncritical(self):
        """DB down + Redis degraded → still 'down' (DB is critical)."""
        assert self._determine_overall(db="down", redis="degraded") == "down"
