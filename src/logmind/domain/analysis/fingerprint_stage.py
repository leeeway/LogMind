"""Versioned incident fingerprints. Detection is never proof of delivery."""

import hashlib
import json
import re

from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage
from logmind.domain.log.csharp import normalized_fault, parse_dotnet

logger = get_logger(__name__)
_FP_PREFIX = "logmind:fingerprint:v2:"


def _generate_fingerprint(business_line_id: str, message: str) -> str:
    if not message:
        return ""
    event = parse_dotnet(message)
    digest = hashlib.sha256(normalized_fault(message).encode()).hexdigest()[:16]
    exception = event.exceptions[0] + ":" if event.exceptions else ""
    return f"{_FP_PREFIX}{business_line_id}:{exception}{digest}"


def split_events(text: str) -> list[str]:
    # Stack lines belong to their event, never fingerprint each frame separately.
    return [
        s
        for s in re.split(
            r"\n(?=\[[^\n\]]+\]\s+\[(?:ERROR|WARN|FATAL|CRITICAL|UNKNOWN|INFO)\])", text
        )
        if s.strip()
    ]


class ErrorFingerprintStage(PipelineStage):
    name = "error_fingerprint"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.full_log_analysis or not get_settings().analysis_fingerprint_enabled:
            return ctx
        keys = list(
            dict.fromkeys(
                _generate_fingerprint(f"{ctx.tenant_id}:{ctx.business_line_id}", event)
                for event in split_events(ctx.processed_logs)
            )
        )
        ctx.log_metadata["fingerprint_keys"] = keys
        ctx.log_metadata["fingerprint_new"] = len(keys)
        # No cache/suppression here: AI may fail or priority may have escalated.
        return ctx


async def delivered_unchanged(ctx: PipelineContext) -> bool:
    if ctx.log_metadata.get("is_regression"):
        return False
    keys = ctx.log_metadata.get("fingerprint_keys", [])
    if not keys:
        return False
    from logmind.core.redis import get_redis_client

    try:
        redis = get_redis_client()
        priority = ctx.priority_decision.get("priority", "P1")
        rank = {"P0": 0, "P1": 1, "P2": 2}
        count = max(ctx.log_metadata.get("matched_count", ctx.log_count), 1)
        for key in keys:
            raw = await redis.get(key)
            if not raw:
                return False
            state = json.loads(raw)
            if rank.get(priority, 2) < rank.get(state.get("priority"), 2):
                return False
            if count >= max(state.get("count", 1) * 1.5, 2):
                return False
        # Continuous observations extend the incident, not a periodic resend.
        for key in keys:
            await redis.expire(key, get_settings().analysis_fingerprint_ttl_hours * 3600)
        return True
    except Exception as exc:
        logger.warning("fingerprint_delivery_read_failed", error=type(exc).__name__)
        return False


async def mark_delivered(ctx: PipelineContext) -> None:
    from logmind.core.redis import get_redis_client

    try:
        redis = get_redis_client()
        state = json.dumps(
            {
                "priority": ctx.priority_decision.get("priority", "P1"),
                "count": max(ctx.log_metadata.get("matched_count", ctx.log_count), 1),
                "task_id": ctx.task_id,
            }
        )
        for key in ctx.log_metadata.get("fingerprint_keys", []):
            await redis.setex(key, get_settings().analysis_fingerprint_ttl_hours * 3600, state)
    except Exception as exc:
        logger.warning("fingerprint_delivery_write_failed", error=type(exc).__name__)
