from unittest.mock import AsyncMock

import pytest

from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis import tasks as analysis_tasks


def test_normalize_error_summary_removes_placeholders():
    summary = "(No logs found matching the query)\n... (truncated)\n... (更多日志请登录平台查看)"
    assert analysis_tasks._normalize_error_summary(summary) == ""


@pytest.mark.asyncio
async def test_send_error_log_notification_skips_placeholder_only(monkeypatch):
    aggregator_should_send = AsyncMock(return_value=(True, 0))
    notify_error_logs = AsyncMock()

    monkeypatch.setattr(
        "logmind.domain.alert.aggregator.alert_aggregator.should_send",
        aggregator_should_send,
    )
    monkeypatch.setattr(
        "logmind.domain.alert.channels.webhook.notify_error_logs",
        notify_error_logs,
    )

    ctx = PipelineContext(
        tenant_id="t1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="Test Biz",
        processed_logs="... (truncated)\n... (更多日志请登录平台查看)",
        log_count=10,
    )

    await analysis_tasks._send_error_log_notification(ctx, webhook_url="")

    aggregator_should_send.assert_not_awaited()
    notify_error_logs.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_error_log_notification_uses_normalized_summary(monkeypatch):
    aggregator_should_send = AsyncMock(return_value=(True, 0))
    notify_error_logs = AsyncMock()

    monkeypatch.setattr(
        "logmind.domain.alert.aggregator.alert_aggregator.should_send",
        aggregator_should_send,
    )
    monkeypatch.setattr(
        "logmind.domain.alert.channels.webhook.notify_error_logs",
        notify_error_logs,
    )

    ctx = PipelineContext(
        tenant_id="t1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="Test Biz",
        processed_logs="[ERROR] Database timeout\n... (truncated)",
        log_count=1,
        language="java",
    )

    await analysis_tasks._send_error_log_notification(ctx, webhook_url="")

    aggregator_should_send.assert_awaited_once()
    notify_error_logs.assert_awaited_once()
    assert aggregator_should_send.await_args.kwargs["alert_summary"] == "[ERROR] Database timeout"
    assert notify_error_logs.await_args.kwargs["error_summary"] == "[ERROR] Database timeout"
