"""Durable notification checkpoint in existing task JSON; no schema migration.

Only sanitized findings are stored. Never serialize providers, credentials or
raw logs. A Redis lease serializes delivery for a tenant/business line.
"""

import json
import uuid
from datetime import UTC, datetime

from logmind.domain.analysis.pipeline import PipelineContext

_FIELDS = (
    "tenant_id",
    "task_id",
    "business_line_id",
    "business_line_name",
    "domain",
    "branch",
    "host_name",
    "language",
    "log_count",
    "alerts_fired",
    "priority_decision",
    "error_signature",
    "log_metadata",
    "analysis_results",
)


def snapshot(ctx):
    from logmind.domain.analysis.sensitive_masker import mask_sensitive

    data = {key: getattr(ctx, key) for key in _FIELDS}

    # Sanitize the values, not serialized JSON (masking JSON can corrupt it).
    def clean(value):
        if isinstance(value, str):
            return mask_sensitive(value)
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    data = clean(data)
    for key in ("time_from", "time_to"):
        data[key] = getattr(ctx, key).isoformat() if getattr(ctx, key) else None
    return data


def restore(data):
    data = dict(data)
    for key in ("time_from", "time_to"):
        data[key] = datetime.fromisoformat(data[key]) if data.get(key) else None
    return PipelineContext(**data)


async def save_checkpoint(ctx, state, sent=None):
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask

    async with get_db_context() as session:
        task = await session.get(LogAnalysisTask, ctx.task_id, with_for_update=True)
        params = json.loads(task.query_params or "{}")
        old = params.get("delivery", {})
        params["delivery"] = {
            "state": state,
            "context": snapshot(ctx),
            "sent": sent if sent is not None else old.get("sent", []),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        task.query_params = json.dumps(params, ensure_ascii=False)
        task.status = (
            "notification_pending" if state in {"pending", "failed", "deferred"} else "completed"
        )
        task.error_message = {
            "pending": "诊断成功，通知待发送",
            "failed": "诊断成功，通知失败待补发",
            "deferred": "诊断成功，按夜间策略延后通知",
            "shadow": "影子诊断：未发送企微",
            "duplicate": "同事件已送达且影响未扩大",
            "suppressed": "诊断成功，按业务线策略不通知",
            "sent": None,
        }.get(state)
        metrics = json.loads(task.stage_metrics or "[]")
        metrics = [m for m in metrics if m.get("stage") != "notification"]
        metrics.append(
            {
                "stage": "notification",
                "status": state,
                "duration_ms": 0,
                "error": task.error_message,
            }
        )
        task.stage_metrics = json.dumps(metrics, ensure_ascii=False)


async def deliver(task_id):
    from logmind.core.database import get_db_context
    from logmind.core.redis import get_redis_client
    from logmind.domain.analysis.fingerprint_stage import delivered_unchanged, mark_delivered
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.analysis.tasks import _send_ai_alerts
    from logmind.domain.tenant.models import BusinessLine

    async with get_db_context() as session:
        task = await session.get(LogAnalysisTask, task_id)
        if not task:
            return
        data = json.loads(task.query_params or "{}").get("delivery", {})
        if data.get("state") not in {"pending", "failed", "deferred"}:
            return
        ctx = restore(data["context"])
        biz = await session.get(BusinessLine, ctx.business_line_id)
        if not biz or biz.tenant_id != ctx.tenant_id or not biz.is_active or not biz.ai_enabled:
            await save_checkpoint(ctx, "suppressed")
            return
        webhook = biz.webhook_url or ""
        # Re-evaluate the current policy when sending, including delayed retries.
        from logmind.domain.analysis.stages.priority_decision import PriorityDecisionStage

        ctx.night_policy, ctx.night_hours = biz.night_policy, biz.night_hours
        ctx.min_notify_priority = biz.min_notify_priority
        ctx.business_weight, ctx.is_core_path = biz.business_weight, biz.is_core_path
        ctx.estimated_dau = biz.estimated_dau
    await PriorityDecisionStage().execute(ctx)
    if not ctx.priority_decision.get("should_notify"):
        await save_checkpoint(
            ctx, "deferred" if ctx.priority_decision.get("delay_until_morning") else "suppressed"
        )
        return
    redis = get_redis_client()
    key = f"logmind:delivery:v2:{ctx.tenant_id}:{ctx.business_line_id}"
    token = uuid.uuid4().hex
    if not await redis.set(key, token, nx=True, ex=360):
        return
    try:
        # Re-read after obtaining the lease; a competing worker may have sent it.
        async with get_db_context() as session:
            task = await session.get(LogAnalysisTask, task_id)
            data = json.loads(task.query_params or "{}").get("delivery", {})
        if data.get("state") not in {"pending", "failed", "deferred"}:
            return
        if await delivered_unchanged(ctx):
            await save_checkpoint(ctx, "duplicate")
            return
        sent = data.get("sent", [])
        alerts = list(ctx.alerts_fired)
        for i, alert in enumerate(alerts):
            if i in sent:
                continue
            if alert.get("alertable") is False or not str(alert.get("content", "")).strip():
                sent.append(i)
                continue
            ctx.log_metadata["delivery_managed"] = True
            ctx.log_metadata["delivery_succeeded"] = False
            ctx.alerts_fired = [alert]
            await _send_ai_alerts(ctx, webhook, task_id)
            ctx.alerts_fired = alerts
            if not ctx.log_metadata.pop("delivery_succeeded", False):
                await save_checkpoint(ctx, "failed", sent)
                return
            sent.append(i)
            await save_checkpoint(ctx, "pending", sent)
        await mark_delivered(ctx)
        await save_checkpoint(ctx, "sent", sent)
    finally:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) end return 0",
            1,
            key,
            token,
        )
