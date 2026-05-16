from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from logmind.domain.dashboard.efficiency_router import _calc_period_metrics, _duration_minutes


def test_duration_minutes_handles_mixed_timezone_awareness():
    start = datetime(2026, 1, 1, 0, 0)
    end = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)

    assert _duration_minutes(start, end) == 10.0


def test_duration_minutes_clamps_negative_values():
    start = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    assert _duration_minutes(start, end) == 0.0


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, batches):
        self._batches = list(batches)

    async def execute(self, _stmt):
        return _FakeResult(self._batches.pop(0))


@pytest.mark.asyncio
async def test_calc_period_metrics_uses_python_aggregation():
    fired_at = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    resolved_at = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    session = _FakeSession([
        [
            SimpleNamespace(
                status="resolved",
                severity="critical",
                priority="P0",
                message="db timeout",
                fired_at=fired_at,
                acked_at=fired_at,
                resolved_at=resolved_at,
            ),
            SimpleNamespace(
                status="fired",
                severity="warning",
                priority="P2",
                message="retry spike",
                fired_at=fired_at,
                acked_at=None,
                resolved_at=None,
            ),
        ],
        [
            SimpleNamespace(id="t1", status="completed", token_usage=1200),
            SimpleNamespace(id="t2", status="skipped_dedup", token_usage=0),
        ],
        [
            SimpleNamespace(feedback_score=1, severity="critical"),
            SimpleNamespace(feedback_score=None, severity="warning"),
        ],
    ])

    metrics = await _calc_period_metrics(session, "tenant-1", fired_at, resolved_at)

    assert metrics["total_alerts"] == 2
    assert metrics["p0_alerts"] == 1
    assert metrics["ack_rate"] == 50
    assert metrics["resolve_rate"] == 50
    assert metrics["avg_mttr_min"] == 20
    assert metrics["ai_usage_rate"] == 50
    assert metrics["automation_rate"] == 100
    assert metrics["finding_quality"] == 50
