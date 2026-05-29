from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from logmind.domain.dashboard import standup_generator


class _FakeResult:
    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def one_or_none(self):
        return self._value

    def scalar(self):
        return self._value

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self._execute_count = 0

    async def execute(self, stmt):
        self._execute_count += 1
        if self._execute_count == 1:
            compiled = str(stmt)
            assert "alert_history.acked_at IS NOT NULL" in compiled
            assert "alert_history.resolved_at IS NOT NULL" in compiled
        return self._results.pop(0)


class _DbContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_generate_standup_report_counts_acked_and_resolved_records(monkeypatch):
    session = _FakeSession(
        [
            _FakeResult(
                SimpleNamespace(
                    total=2,
                    p0=1,
                    p1=1,
                    acked=2,
                    resolved=1,
                )
            ),
            _FakeResult(1),
            _FakeResult(7),
            _FakeResult(SimpleNamespace(total=0, completed=0)),
            _FakeResult(rows=[]),
            _FakeResult(rows=[]),
        ]
    )

    monkeypatch.setattr(
        "logmind.core.database.get_db_context",
        lambda: _DbContext(session),
    )
    monkeypatch.setattr(
        standup_generator,
        "_generate_ai_summary",
        AsyncMock(return_value="standup summary"),
    )

    report = await standup_generator.generate_standup_report(
        "tenant-1",
        datetime(2026, 5, 20),
    )

    assert report["data"]["alerts"]["ack_rate_pct"] == 100
    assert report["data"]["alerts"]["resolve_rate_pct"] == 50
