import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_csharp_error_filter_never_uses_mixed_filetype_as_error(monkeypatch):
    from logmind.domain.log import service

    monkeypatch.setattr(
        "logmind.domain.log.error_signals.get_all_error_signals",
        lambda _business_line_id="": _async_value([]),
    )

    predicate = await service.build_severity_filter(
        "error",
        business_line_id="biz-1",
        language="csharp",
    )
    serialized = json.dumps(predicate)

    assert "sys.log.txt" not in serialized
    assert "application.log" not in serialized
    assert "error.log" in serialized
    assert "[ERR]" in serialized
    assert "Unhandled exception" in serialized


@pytest.mark.asyncio
async def test_all_csharp_levels_have_message_markers(monkeypatch):
    from logmind.domain.log import service

    monkeypatch.setattr(
        "logmind.domain.log.error_signals.get_all_error_signals",
        lambda _business_line_id="": _async_value([]),
    )

    expectations = {
        "error": "[ERR]",
        "warning": "[WRN]",
        "info": "[INF]",
        "debug": "[DBG]",
    }
    for severity, marker in expectations.items():
        predicate = await service.build_severity_filter(severity, language="csharp")
        assert marker in json.dumps(predicate)


@pytest.mark.asyncio
async def test_log_search_requests_exact_total_hits(monkeypatch):
    from logmind.domain.log.schemas import LogQueryRequest
    from logmind.domain.log.service import LogService

    fake_es = AsyncMock()
    fake_es.search.return_value = {
        "hits": {"total": {"value": 15023}, "hits": []},
        "took": 1,
    }
    monkeypatch.setattr("logmind.core.elasticsearch.get_es_client", lambda: fake_es)

    result = await LogService().search_logs(LogQueryRequest(
        index_pattern="csharp-*",
        time_from=datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc),
        time_to=datetime(2026, 6, 28, 11, 0, tzinfo=timezone.utc),
        size=10,
    ))

    assert fake_es.search.await_args.kwargs["body"]["track_total_hits"] is True
    assert result.total == 15023


async def _async_value(value):
    return value


def test_event_budget_keeps_latest_complete_evidence():
    from logmind.domain.analysis.stages.log_preprocess import _fit_complete_events

    events = [
        "old-header\nold-stack",
        "middle-" + ("x" * 200),
        "recent-error\nInnerException: database timeout",
    ]

    rendered = _fit_complete_events(events, 100)

    assert "old-header\nold-stack" in rendered
    assert "recent-error\nInnerException: database timeout" in rendered
    assert "middle events omitted" in rendered
