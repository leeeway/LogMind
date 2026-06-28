from unittest.mock import AsyncMock

import pytest

from logmind.core.exceptions import AllProvidersFailedError, PipelineError
from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis import tasks as analysis_tasks


def test_normalize_error_summary_removes_placeholders():
    summary = "(No logs found matching the query)\n... (truncated)\n... (更多日志请登录平台查看)"
    assert analysis_tasks._normalize_error_summary(summary) == ""


def test_meaningful_error_summary_rejects_short_truncated_prefix():
    assert analysis_tasks._is_meaningful_error_summary("[2026-05-12T01:") is False


def test_meaningful_error_summary_accepts_real_log_line():
    assert analysis_tasks._is_meaningful_error_summary(
        "[2026-05-12T01:00:14Z] [ERROR] Database timeout while sending SMS"
    ) is True


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
async def test_send_error_log_notification_skips_truncated_prefix(monkeypatch):
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
        processed_logs="[2026-05-12T01:\n... (更多日志请登录平台查看)",
        log_count=111,
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


@pytest.mark.asyncio
async def test_send_error_log_notification_skips_success_business_noise(monkeypatch):
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
        business_line_name="社区计费系统-核心兑换服务",
        processed_logs=(
            "[INFO] WDGameCharge.charge - 问道兑换元宝[订单=R260529150936039315920247652]"
            "结果ProcessResult[description='Account successfully charged', errorCode=0]\n"
            "[INFO] ChangeService.changeGameNew - 调用游戏接口发元宝[changeGameNew]"
            "游戏兑换结果ResultBean(success=true, message=, error=, data=成功)"
        ),
        log_count=2,
        language="java",
    )

    await analysis_tasks._send_error_log_notification(ctx, webhook_url="")

    aggregator_should_send.assert_not_awaited()
    notify_error_logs.assert_not_awaited()


def test_should_send_plain_error_fallback_for_provider_failure():
    ctx = PipelineContext(
        tenant_id="t1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="Test Biz",
        processed_logs="[ERROR] Database timeout while sending SMS",
        log_count=3,
    )

    exc = AllProvidersFailedError("t1", errors=["subapi: Server disconnected without sending a response"])

    assert analysis_tasks._should_send_plain_error_fallback(ctx, exc) is True


def test_should_not_send_plain_error_fallback_without_meaningful_logs():
    ctx = PipelineContext(
        tenant_id="t1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="Test Biz",
        processed_logs="... (truncated)",
        log_count=3,
    )

    exc = PipelineError("ai_inference", RuntimeError("All providers failed"))

    assert analysis_tasks._should_send_plain_error_fallback(ctx, exc) is False


@pytest.mark.asyncio
async def test_maybe_send_plain_error_fallback_dispatches_notification(monkeypatch):
    send_error_log_notification = AsyncMock()
    monkeypatch.setattr(
        "logmind.domain.analysis.tasks._send_error_log_notification",
        send_error_log_notification,
    )

    ctx = PipelineContext(
        tenant_id="t1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="Test Biz",
        processed_logs="[ERROR] Database timeout while sending SMS",
        log_count=2,
    )

    sent = await analysis_tasks._maybe_send_plain_error_fallback(
        ctx,
        PipelineError("ai_inference", RuntimeError("All providers failed")),
        webhook_url="",
    )

    assert sent is True
    send_error_log_notification.assert_awaited_once_with(ctx, "")
