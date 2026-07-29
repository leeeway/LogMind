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
    monkeypatch.setattr("logmind.core.elasticsearch.get_es_client", lambda: fake_es)

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
async def test_count_error_patterns_requests_error_only_stats(monkeypatch):
    from logmind.domain.analysis import agent_tools

    captured = {}

    async def fake_get_log_stats(index_pattern, time_from, time_to, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            total_logs=3,
            by_filetype=[],
            by_domain=[],
            time_histogram=[],
            by_level=[],
        )

    monkeypatch.setattr(agent_tools.log_service, "get_log_stats", fake_get_log_stats)

    result = await agent_tools._exec_count_error_patterns(
        {},
        "csharp-*",
        datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 28, 11, 0, tzinfo=timezone.utc),
        business_line_id="biz-1",
        language="csharp",
    )

    assert captured == {
        "severity": "error",
        "business_line_id": "biz-1",
        "language": "csharp",
    }
    assert '"error_count": 3' in result
    assert "total_logs" not in result


@pytest.mark.asyncio
async def test_get_log_context_keeps_all_levels_and_language(monkeypatch):
    from logmind.domain.analysis import agent_tools

    captured = {}

    async def fake_search_logs(request):
        captured["request"] = request
        return SimpleNamespace(total=0, logs=[])

    monkeypatch.setattr(agent_tools.log_service, "search_logs", fake_search_logs)

    await agent_tools._exec_get_log_context(
        {"timestamp": "2026-06-28T10:00:00Z"},
        "csharp-*",
        business_line_id="biz-1",
        language="csharp",
    )

    request = captured["request"]
    assert request.severity is None
    assert request.language == "csharp"
    assert request.business_line_id == "biz-1"


@pytest.mark.asyncio
async def test_service_health_uses_canonical_csharp_error_count(monkeypatch):
    from logmind.domain.analysis import agent_tools

    captured = {}
    fake_es = AsyncMock()

    async def fake_search(**kwargs):
        captured.update(kwargs)
        return {
            "aggregations": {
                "total": {"value": 10003},
                "by_level": {"buckets": []},
                "errors": {"doc_count": 3},
                "hourly": {
                    "buckets": [{
                        "key_as_string": "2026-06-28T10:00:00Z",
                        "errors": {"doc_count": 3},
                    }]
                },
            }
        }

    fake_es.search = fake_search
    monkeypatch.setattr("logmind.core.elasticsearch.get_es_client", lambda: fake_es)
    monkeypatch.setattr(
        "logmind.domain.log.error_signals.get_all_error_signals",
        AsyncMock(return_value=[]),
    )

    result = await agent_tools._exec_get_service_health(
        {"hours_back": 1},
        "csharp-*",
        None,
        datetime(2026, 6, 28, 11, 0, tzinfo=timezone.utc),
        business_line_id="biz-1",
        language="csharp",
    )

    query_text = str(captured["body"])
    assert "sys.log.txt" not in query_text
    assert "[ERR]" in query_text
    assert "错误数: 3" in result
    assert "错误率: 0.03%" in result


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


@pytest.mark.asyncio
async def test_list_available_indices_is_bounded_to_current_index_pattern(monkeypatch):
    from logmind.domain.analysis import agent_tools

    captured = {}

    async def fake_list_indices(pattern):
        captured["pattern"] = pattern
        return [
            SimpleNamespace(name="current-2026", docs_count=10, size="1kb"),
        ]

    monkeypatch.setattr(agent_tools.log_service, "list_indices", fake_list_indices)

    result = await agent_tools.execute_tool(
        tool_name="list_available_indices",
        arguments={"pattern": "*"},
        es_index_pattern="current-*",
        tenant_id="tenant-1",
        business_line_id="biz-1",
    )

    assert captured["pattern"] == "current-*"
    assert "current-2026" in result
