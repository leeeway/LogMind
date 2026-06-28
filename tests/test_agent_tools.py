from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_get_alerts_filters_to_current_tenant_and_business_line(monkeypatch):
    from logmind.domain.analysis import agent_tools

    captured = {}

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class FakeSession:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return FakeResult()

    class FakeDbContext:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "logmind.core.database.get_db_context",
        lambda: FakeDbContext(),
    )

    result = await agent_tools._exec_get_alerts(
        {},
        tenant_id="tenant-1",
        business_line_id="biz-1",
    )

    compiled = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
    assert "alert_history.tenant_id = 'tenant-1'" in compiled
    assert "log_analysis_task.business_line_id = 'biz-1'" in compiled
    assert "无告警记录" in result


@pytest.mark.asyncio
async def test_trace_error_chain_uses_bounded_indices_and_masks_messages(monkeypatch):
    from logmind.domain.analysis import agent_tools

    captured = {}
    fake_es = AsyncMock()

    async def fake_search(**kwargs):
        captured.update(kwargs)
        return {
            "hits": {
                "hits": [
                    {
                        "_index": "current-2026",
                        "_source": {
                            "@timestamp": "2026-06-28T10:00:00Z",
                            "level": "ERROR",
                            "message": "Login failed password=supersecret",
                        },
                    }
                ]
            }
        }

    fake_es.search = fake_search
    monkeypatch.setattr("logmind.core.elasticsearch.get_es_client", AsyncMock(return_value=fake_es))

    result = await agent_tools._exec_trace_error_chain(
        {"error_keyword": "Login failed", "minutes_back": 30},
        index_pattern="current-*",
        default_to=datetime(2026, 6, 28, 10, 5, tzinfo=timezone.utc),
        related_index_patterns=["upstream-*", "downstream-*"],
    )

    assert captured["index"] == "current-*,upstream-*,downstream-*"
    assert captured["index"] != "*"
    assert "supersecret" not in result
    assert "password=" in result


@pytest.mark.asyncio
async def test_search_cross_service_logs_only_uses_configured_related_indices(monkeypatch):
    from logmind.domain.analysis import agent_tools

    captured = {}

    async def fake_search_logs(request):
        captured["index_pattern"] = request.index_pattern
        return SimpleNamespace(
            logs=[
                SimpleNamespace(
                    domain="upstream",
                    timestamp="2026-06-28T10:00:00Z",
                    level="ERROR",
                    message="Connection timeout token=abc123456",
                )
            ]
        )

    monkeypatch.setattr(agent_tools.log_service, "search_logs", fake_search_logs)
    monkeypatch.setattr(
        agent_tools.log_service,
        "list_indices",
        AsyncMock(side_effect=AssertionError("list_indices must not be used")),
    )

    result = await agent_tools._exec_search_cross_service_logs(
        {"keyword": "timeout", "minutes_back": 30},
        current_index_pattern="current-*",
        related_index_patterns=["upstream-*", "downstream-*"],
    )

    assert captured["index_pattern"] == "upstream-*,downstream-*"
    assert "current-*" not in captured["index_pattern"]
    assert "abc123456" not in result
