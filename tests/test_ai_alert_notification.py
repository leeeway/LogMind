import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from logmind.domain.analysis.pipeline import PipelineContext


@pytest.mark.asyncio
async def test_ai_alert_content_includes_priority_reason_and_limited_log_refs(monkeypatch):
    from logmind.domain.analysis import tasks

    notify_ai_alert = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "logmind.domain.alert.channels.webhook.notify_ai_alert",
        notify_ai_alert,
    )
    monkeypatch.setattr(
        "logmind.domain.alert.aggregator.alert_aggregator.should_send",
        AsyncMock(return_value=(True, 1)),
    )
    monkeypatch.setattr(
        "logmind.domain.alert.storm_detector.alert_storm_detector.check_storm",
        lambda **_kwargs: SimpleNamespace(should_suppress=False, storm_summary=""),
    )

    class FakeDbContext:
        async def __aenter__(self):
            session = AsyncMock()
            session.add = lambda _record: None
            session.flush = AsyncMock()
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("logmind.core.database.get_db_context", lambda: FakeDbContext())

    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="Core Service",
        log_count=10,
    )
    ctx.priority_decision = {
        "priority": "P1",
        "score": 55.0,
        "reason": "P1: warning 级别错误，正常通知",
    }
    ctx.alerts_fired = [
        {
            "severity": "warning",
            "content": "数据库连接池耗尽",
            "source_log_refs": json.dumps(["log-1", "log-2", "log-3", "log-4"]),
        }
    ]

    await tasks._send_ai_alerts(ctx, webhook_url="", task_id=ctx.task_id)

    content = notify_ai_alert.await_args.kwargs["content"]
    assert "通知原因: P1: warning 级别错误，正常通知" in content
    assert "日志引用: log-1, log-2, log-3" in content
    assert "log-4" not in content
