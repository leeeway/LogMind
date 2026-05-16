from datetime import datetime, timezone

from logmind.domain.dashboard.efficiency_router import _duration_minutes


def test_duration_minutes_handles_mixed_timezone_awareness():
    start = datetime(2026, 1, 1, 0, 0)
    end = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)

    assert _duration_minutes(start, end) == 10.0


def test_duration_minutes_clamps_negative_values():
    start = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    assert _duration_minutes(start, end) == 0.0
