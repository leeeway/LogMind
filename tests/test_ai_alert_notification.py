import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from logmind.domain.analysis.pipeline import PipelineContext


def test_ai_alert_location_summary_omits_duplicate_cause(monkeypatch):
    from logmind.domain.analysis import tasks

    monkeypatch.setattr(
        "logmind.core.config.get_settings",
        lambda: SimpleNamespace(public_app_url=""),
    )

    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-duplicate-cause",
        business_line_id="biz-1",
        business_line_name="Core Service",
        log_count=91,
    )
    alert_content = (
        "建议进一步按更宽的关键词和上下游链路排查："
        "检索同时间段的 WARN/ERROR 全量日志与异常堆栈。"
    )

    content = tasks._build_alert_location_summary(
        ctx,
        {"severity": "warning", "content": alert_content},
        priority_label="🟡 [P1|40.7分]",
        issue_label="",
        reason="P1: warning 级别错误，正常通知",
    )

    assert f"问题: {alert_content}" in content
    assert "疑似原因:" not in content
    assert content.count(alert_content) == 1


def test_ai_alert_location_summary_omits_duplicate_ai_finding_evidence(monkeypatch):
    from logmind.domain.analysis import tasks

    monkeypatch.setattr(
        "logmind.core.config.get_settings",
        lambda: SimpleNamespace(public_app_url=""),
    )

    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-duplicate-evidence",
        business_line_id="biz-1",
        business_line_name="Core Service",
        log_count=62,
    )
    alert_content = (
        "在分析窗口内捕获到同一类 ERROR/异常堆栈："
        "cn.gydev.lib.exception.RedisLockException: 操作过快，请稍后重试，"
        "发生于 ExternalAuthController.login 登录接口的分布式锁/互斥保护逻辑中。"
    )

    content = tasks._build_alert_location_summary(
        ctx,
        {
            "result_type": "root_cause",
            "severity": "warning",
            "content": alert_content,
        },
        priority_label="🟡 [P1|50.2分]",
        issue_label="",
        reason="P1: warning 级别错误，正常通知",
    )

    assert f"问题: {alert_content}" in content
    assert "AI 分析发现:" not in content
    assert content.count(alert_content) == 1


def test_ai_alert_location_summary_omits_truncated_duplicate_ai_finding_evidence(monkeypatch):
    from logmind.domain.analysis import tasks

    monkeypatch.setattr(
        "logmind.core.config.get_settings",
        lambda: SimpleNamespace(public_app_url="https://logmind.example.com"),
    )

    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-truncated-duplicate-evidence",
        business_line_id="biz-1",
        business_line_name="validation2",
        log_count=109,
    )
    alert_content = (
        "在给定时间窗内仅发现 1 条 ERROR，主要模式为 Tomcat 在生成验证码图片并写回响应时发生 "
        "java.io.IOException: Broken pipe ，伴随 ClientAbortException 。同窗内其余日志均为验证码校验失败/通过的 WARN，"
        "未见数据库、核心业务写入或数据一致性相关的致命异常。该错误更像是客户端在服务端响应生成完成前主动断开连接，"
        "常见于浏览器刷新、页面跳转、网络中断或爬虫/探测流量提前关闭连接。"
    )

    content = tasks._build_alert_location_summary(
        ctx,
        {
            "result_type": "summary",
            "severity": "warning",
            "content": alert_content,
        },
        priority_label="🟡 [P1|56.8分]",
        issue_label="",
        reason="🟡 P1: warning 级别错误，正常通知",
    )

    assert "问题: 在给定时间窗内仅发现 1 条 ERROR" in content
    assert "AI 分析发现:" not in content
    assert "Broken pipe" in content
    assert content.count("Broken pipe") == 1


def test_ai_alert_location_summary_omits_empty_evidence_and_next_step_sections(monkeypatch):
    from logmind.domain.analysis import tasks

    monkeypatch.setattr(
        "logmind.core.config.get_settings",
        lambda: SimpleNamespace(public_app_url="https://logmind.example.com"),
    )

    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-no-actionable-sections",
        business_line_id="biz-1",
        business_line_name="qr.module",
        log_count=259,
    )
    alert_content = (
        "在给定时间窗内，日志以数据库访问成功的 DEBUG 记录为主，未直接出现 ERROR 级别异常堆栈；"
        "但错误率检测显示 2026-07-02T02:53:00Z 附近出现显著突增。"
    )

    content = tasks._build_alert_location_summary(
        ctx,
        {
            "result_type": "summary",
            "severity": "critical",
            "content": alert_content,
        },
        priority_label="🟡 [P1|57.7分]",
        issue_label="",
        reason="🟡 P1: critical 级别错误，正常通知",
    )

    assert f"问题: {alert_content}" in content
    assert "暂无结构化证据" not in content
    assert "打开分析详情，核对原始日志" not in content
    assert "证据:" not in content
    assert "下一步:" not in content
    assert "分析入口: https://logmind.example.com/analysis/task-no-actionable-sections" in content


def test_ai_alert_template_uses_compact_summary_heading():
    from logmind.domain.alert.channels.webhook import _build_ai_analysis_alert

    content = _build_ai_analysis_alert(
        business_line="游戏社区-中宣实名",
        domain="stage-nppa.gyyx.cn",
        branch="master",
        host_name="",
        language="java",
        severity="warning",
        content="问题: 数据库连接池等待超时\n分析入口: 任务 task-1",
        task_id="task-1",
        log_count=91,
    )

    assert "**摘要**:" in content
    assert "**AI 分析结论**:" not in content


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


@pytest.mark.asyncio
async def test_ai_alert_content_is_concise_location_summary_with_deep_link(monkeypatch):
    from logmind.domain.analysis import tasks

    monkeypatch.setattr(
        "logmind.core.config.get_settings",
        lambda: SimpleNamespace(public_app_url="https://logmind.example.com"),
    )

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
        task_id="task-locate-1",
        business_line_id="biz-1",
        business_line_name="Checkout",
        log_count=28,
    )
    ctx.priority_decision = {
        "priority": "P1",
        "score": 72.5,
        "reason": "错误率突增且命中核心链路",
    }
    ctx.alerts_fired = [{
        "result_type": "root_cause",
        "severity": "critical",
        "content": "Checkout 大量请求超时，疑似 Redis 连接池耗尽。" * 30,
        "confidence_score": 0.91,
        "structured_data": json.dumps({
            "root_cause": "Redis 连接池耗尽",
            "upstream_service": "RedisCluster",
            "change_points": [{
                "timestamp": "2026-07-01T06:10:00+00:00",
                "before_rate": 2,
                "after_rate": 90,
                "z_score": 7.2,
                "bucket_count": 90,
            }],
            "correlated_errors": [{
                "service_name": "RedisCluster",
                "direction": "upstream",
                "error_count": 12,
                "error_samples": ["ERR max number of clients reached"],
            }],
            "next_verifications": [
                "检查 Redis maxclients 和当前连接数",
                "回看 06:10 前后的发布/配置变更",
                "确认 Checkout 线程池是否堆积",
            ],
        }, ensure_ascii=False),
        "source_log_refs": json.dumps(["log-1", "log-2", "log-3", "log-4"]),
    }]

    await tasks._send_ai_alerts(ctx, webhook_url="", task_id=ctx.task_id)

    content = notify_ai_alert.await_args.kwargs["content"]
    assert "问题:" in content
    assert "疑似原因:" in content
    assert "证据:" in content
    assert "下一步:" in content
    assert "分析入口: https://logmind.example.com/analysis/task-locate-1" in content
    assert "RedisCluster" in content
    assert "Redis 连接池耗尽" in content
    assert "检查 Redis maxclients" in content
    assert "确认 Checkout 线程池是否堆积" not in content
    assert "log-4" not in content
    assert len(content) <= 1600


@pytest.mark.asyncio
async def test_ai_alert_without_public_app_url_uses_task_id_not_fake_link(monkeypatch):
    from logmind.domain.analysis import tasks

    monkeypatch.setattr(
        "logmind.core.config.get_settings",
        lambda: SimpleNamespace(public_app_url=""),
    )

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
        task_id="task-no-link",
        business_line_id="biz-1",
        business_line_name="Checkout",
        log_count=3,
    )
    ctx.priority_decision = {"priority": "P2", "score": 35, "reason": "低优先级观察"}
    ctx.alerts_fired = [{
        "severity": "warning",
        "content": "数据库连接池等待超时",
        "confidence_score": 0.7,
    }]

    await tasks._send_ai_alerts(ctx, webhook_url="", task_id=ctx.task_id)

    content = notify_ai_alert.await_args.kwargs["content"]
    assert "分析入口: 任务 task-no-link" in content
    assert "http://" not in content
    assert "https://" not in content


@pytest.mark.asyncio
async def test_p0_auto_incident_initial_event_contains_location_summary(monkeypatch):
    from logmind.domain.alert.models import AlertHistory
    from logmind.domain.analysis import tasks
    from logmind.domain.incident import IncidentEvent

    monkeypatch.setattr(
        "logmind.core.config.get_settings",
        lambda: SimpleNamespace(public_app_url="https://logmind.example.com"),
    )

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

    added = []

    class FakeDbContext:
        async def __aenter__(self):
            session = AsyncMock()

            def add(record):
                if isinstance(record, AlertHistory):
                    record.id = "alert-1"
                added.append(record)

            session.add = add
            session.flush = AsyncMock()
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("logmind.core.database.get_db_context", lambda: FakeDbContext())

    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-p0",
        business_line_id="biz-1",
        business_line_name="Checkout",
        log_count=8,
    )
    ctx.priority_decision = {"priority": "P0", "score": 95, "reason": "回归问题命中 P0"}
    ctx.log_metadata["is_regression"] = True
    ctx.alerts_fired = [{
        "result_type": "root_cause",
        "severity": "critical",
        "content": "支付链路请求超时",
        "confidence_score": 0.9,
        "structured_data": json.dumps({
            "root_cause": "Redis 连接池耗尽",
            "upstream_service": "RedisCluster",
            "next_verifications": ["检查 Redis 连接数"],
        }, ensure_ascii=False),
    }]

    await tasks._send_ai_alerts(ctx, webhook_url="", task_id=ctx.task_id)

    event_contents = [record.content for record in added if isinstance(record, IncidentEvent)]
    assert event_contents
    assert "问题:" in event_contents[0]
    assert "疑似原因:" in event_contents[0]
    assert "下一步:" in event_contents[0]
    assert "https://logmind.example.com/analysis/task-p0" in event_contents[0]


def test_fallback_prompt_requires_location_fields():
    from logmind.domain.analysis.pipeline import PipelineContext
    from logmind.domain.analysis.stages.prompt_build import _fallback_system_prompt

    prompt = _fallback_system_prompt(PipelineContext(
        tenant_id="tenant-1",
        task_id="task-1",
        business_line_id="biz-1",
    ))

    assert "root_cause" in prompt
    assert "root_cause_candidates" in prompt
    assert "source_log_refs" in prompt
    assert "next_verifications" in prompt


@pytest.mark.asyncio
async def test_ai_off_error_notification_aggregation_uses_summary_signature(monkeypatch):
    from logmind.domain.analysis import tasks

    captured = {}

    async def fake_should_send(**kwargs):
        captured.update(kwargs)
        return True, 1

    monkeypatch.setattr(
        "logmind.domain.alert.aggregator.alert_aggregator.should_send",
        fake_should_send,
    )
    monkeypatch.setattr(
        "logmind.domain.alert.channels.webhook.notify_error_logs",
        AsyncMock(return_value=True),
    )

    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="Core Service",
        log_count=1,
    )
    ctx.processed_logs = "2026-06-28 ERROR NullPointerException at PaymentService"
    ctx.time_from = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    ctx.time_to = datetime(2026, 6, 28, 10, 5, tzinfo=timezone.utc)

    await tasks._send_error_log_notification(ctx, webhook_url="")

    assert captured["error_signature"]
    assert captured["error_signature"] != "error"
    assert "NullPointerException" in captured["error_signature"]
