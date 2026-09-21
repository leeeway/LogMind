import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from logmind.domain.analysis.delivery import deliver, notification_summary, snapshot
from logmind.domain.analysis.pipeline import PipelineContext


@pytest.fixture
def delivery_env(monkeypatch):
    ctx = PipelineContext(
        tenant_id="tenant",
        task_id="task",
        business_line_id="biz",
        log_count=1,
        alerts_fired=[
            {
                "severity": "warning",
                "content": "GetUser 抛出 NullReferenceException",
                "alertable": True,
            }
        ],
        priority_decision={"priority": "P1", "should_notify": True},
        log_metadata={"fingerprint_keys": ["incident-key"]},
    )
    task = SimpleNamespace(
        tenant_id="tenant",
        business_line_id="biz",
        query_params=json.dumps(
            {"delivery": {"state": "pending", "sent": [], "context": snapshot(ctx)}}
        ),
        stage_metrics="[]",
        status="notification_pending",
        error_message=None,
    )
    biz = SimpleNamespace(
        tenant_id="tenant",
        is_active=True,
        ai_enabled=True,
        webhook_url="",
        night_policy="all",
        night_hours="22:00-08:00",
        min_notify_priority="P1",
        business_weight=5,
        is_core_path=True,
        estimated_dau=100,
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, object_id, **kwargs):
            return task if object_id == "task" else biz

    monkeypatch.setattr("logmind.core.database.get_db_context", Session)
    monkeypatch.setattr(
        "logmind.domain.analysis.fingerprint_stage.persisted_delivery_records",
        AsyncMock(return_value={}),
    )
    memory = {}

    async def get(key):
        return memory.get(key)

    async def setex(key, ttl, value):
        memory[key] = value

    redis = SimpleNamespace(
        get=get, setex=setex, set=AsyncMock(return_value=True), eval=AsyncMock(), expire=AsyncMock()
    )
    monkeypatch.setattr("logmind.core.redis.get_redis_client", lambda: redis)

    async def decide(self, context):
        return context

    monkeypatch.setattr(
        "logmind.domain.analysis.stages.priority_decision.PriorityDecisionStage.execute", decide
    )
    return task, biz, redis, ctx


@pytest.mark.asyncio
async def test_failed_send_remains_pending_and_retries_without_ai(delivery_env, monkeypatch):
    task, _, _, _ = delivery_env
    calls = []

    async def send(ctx, webhook, task_id):
        calls.append(ctx.alerts_fired[0]["content"])
        ctx.log_metadata["delivery_succeeded"] = len(calls) > 1

    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    assert json.loads(task.query_params)["delivery"]["state"] == "failed"
    assert task.status == "notification_pending"
    await deliver("task")
    assert json.loads(task.query_params)["delivery"]["state"] == "sent"
    assert task.status == "completed"
    await deliver("task")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_partial_delivery_only_retries_unsent_alert(delivery_env, monkeypatch):
    task, _, _, ctx = delivery_env
    ctx.alerts_fired.append({"severity": "warning", "content": "second", "alertable": True})
    task.query_params = json.dumps(
        {"delivery": {"state": "failed", "sent": [0], "context": snapshot(ctx)}}
    )
    calls = []

    async def send(ctx, webhook, task_id):
        content = ctx.alerts_fired[0]["content"]
        calls.append(content)
        ctx.log_metadata["delivery_succeeded"] = len(calls) > 1

    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    assert json.loads(task.query_params)["delivery"]["sent"] == [0]
    await deliver("task")
    assert calls == ["second", "second"]


@pytest.mark.asyncio
async def test_disabled_business_line_cannot_send_pending_notification(delivery_env, monkeypatch):
    task, biz, _, _ = delivery_env
    biz.is_active = False
    send = AsyncMock()
    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    assert json.loads(task.query_params)["delivery"]["state"] == "suppressed"
    send.assert_not_called()


@pytest.mark.asyncio
async def test_delivery_lease_contention_keeps_pending(delivery_env, monkeypatch):
    task, _, redis, _ = delivery_env
    redis.set.return_value = False
    send = AsyncMock()
    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    assert json.loads(task.query_params)["delivery"]["state"] == "pending"
    send.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_checkpoint_never_sends(delivery_env, monkeypatch):
    task, _, _, _ = delivery_env
    data = json.loads(task.query_params)
    data["delivery"]["state"] = "shadow"
    task.query_params = json.dumps(data)
    send = AsyncMock()
    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    send.assert_not_called()


@pytest.mark.asyncio
async def test_night_delay_then_day_retry(delivery_env, monkeypatch):
    task, _, _, _ = delivery_env
    day = False

    async def decide(self, ctx):
        ctx.priority_decision.update(should_notify=day, delay_until_morning=not day)
        return ctx

    monkeypatch.setattr(
        "logmind.domain.analysis.stages.priority_decision.PriorityDecisionStage.execute", decide
    )

    async def send(ctx, webhook, task_id):
        ctx.log_metadata["delivery_succeeded"] = True

    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    assert json.loads(task.query_params)["delivery"]["state"] == "deferred"
    day = True
    await deliver("task")
    assert json.loads(task.query_params)["delivery"]["state"] == "sent"


def test_one_notification_for_anomaly_and_root_cause_of_same_task():
    text = "message_info_tb.content 无法写入四字节字符，MessageInfoDao.insertMessageInfo 执行失败"
    findings = [
        {"result_type": "anomaly", "content": text, "severity": "critical"},
        {"result_type": "root_cause", "content": text, "severity": "critical"},
    ]
    result = notification_summary(findings)
    assert len(result) == 1
    assert result[0]["result_type"] == "root_cause"
    assert result[0]["content"] == text


def test_distinct_findings_keep_highest_priority_and_indicate_more():
    result = notification_summary(
        [
            {"content": "慢请求", "severity": "warning"},
            {"content": "数据库写入失败", "severity": "critical"},
        ]
    )
    assert len(result) == 1
    assert result[0]["severity"] == "critical"
    assert "另有 1 项" in result[0]["content"]


@pytest.mark.asyncio
async def test_old_unsent_two_finding_checkpoint_sends_once(delivery_env, monkeypatch):
    task, _, _, ctx = delivery_env
    ctx.alerts_fired.append({**ctx.alerts_fired[0], "result_type": "root_cause"})
    task.query_params = json.dumps(
        {"delivery": {"state": "pending", "sent": [], "context": snapshot(ctx)}}
    )
    calls = []

    async def send(context, webhook, task_id):
        calls.append(context.alerts_fired)
        context.log_metadata["delivery_succeeded"] = True

    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    await deliver("task")
    assert len(calls) == 1
    assert json.loads(task.query_params)["delivery"]["state"] == "sent"


@pytest.mark.asyncio
async def test_cache_write_failure_keeps_durable_delivery_and_suppresses_retry(
    delivery_env, monkeypatch
):
    from logmind.domain.analysis.fingerprint_stage import delivered_unchanged

    task, _, redis, ctx = delivery_env
    redis.setex = AsyncMock(side_effect=ConnectionError("cache unavailable"))

    async def send(context, webhook, task_id):
        context.log_metadata["delivery_succeeded"] = True

    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    checkpoint = json.loads(task.query_params)["delivery"]
    assert checkpoint["state"] == "sent"
    records = checkpoint["context"]["log_metadata"]["delivered_records"]
    assert records["incident-key"]["task_id"] == "task"
    monkeypatch.setattr(
        "logmind.domain.analysis.fingerprint_stage.persisted_delivery_records",
        AsyncMock(return_value=records),
    )
    ctx.task_id = "next-task"
    assert await delivered_unchanged(ctx)


@pytest.mark.asyncio
async def test_durable_lookup_failure_does_not_resend(delivery_env, monkeypatch):
    task, _, _, _ = delivery_env
    monkeypatch.setattr(
        "logmind.domain.analysis.fingerprint_stage.persisted_delivery_records",
        AsyncMock(side_effect=ConnectionError("db unavailable")),
    )
    send = AsyncMock()
    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    with pytest.raises(ConnectionError):
        await deliver("task")
    send.assert_not_called()
    assert json.loads(task.query_params)["delivery"]["state"] == "pending"


@pytest.mark.asyncio
async def test_context_is_reloaded_after_lock_acquisition(delivery_env, monkeypatch):
    task, _, redis, _ = delivery_env

    async def acquire(*args, **kwargs):
        data = json.loads(task.query_params)
        data["delivery"]["state"] = "sent"
        task.query_params = json.dumps(data)
        return True

    redis.set = acquire
    send = AsyncMock()
    monkeypatch.setattr("logmind.domain.analysis.tasks._send_ai_alerts", send)
    await deliver("task")
    send.assert_not_called()


@pytest.mark.asyncio
async def test_durable_lookup_ignores_unsent_checkpoints(monkeypatch):
    from logmind.domain.analysis.fingerprint_stage import persisted_delivery_records

    rows = []
    for state in ("pending", "failed", "shadow", "sent", "duplicate"):
        rows.append(
            json.dumps(
                {
                    "delivery": {
                        "state": state,
                        "context": {"log_metadata": {"delivered_records": {state: {"count": 1}}}},
                    }
                }
            )
        )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalars(self, query):
            params = query.compile().params
            assert "tenant-scope" in params.values()
            assert "biz-scope" in params.values()
            assert "completed" in params.values()
            return SimpleNamespace(all=lambda: rows)

    monkeypatch.setattr("logmind.core.database.get_db_context", Session)
    ctx = PipelineContext(tenant_id="tenant-scope", business_line_id="biz-scope", task_id="new")
    assert set(await persisted_delivery_records(ctx)) == {"sent", "duplicate"}
