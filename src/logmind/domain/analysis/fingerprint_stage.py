"""Versioned incident fingerprints. Detection is never proof of delivery."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

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
        counts = {}
        v3_counts = {}
        aliases = {}
        from logmind.domain.log.events import event_key

        for event in split_events(ctx.processed_logs):
            key = _generate_fingerprint(f"{ctx.tenant_id}:{ctx.business_line_id}", event)
            v3 = f"logmind:fingerprint:v3:{ctx.tenant_id}:{ctx.business_line_id}:{event_key(event)}"
            occurrences = re.search(r"\[occurrences:(\d+)\]", event)
            count = int(occurrences.group(1)) if occurrences else 1
            counts[key] = counts.get(key, 0) + count
            v3_counts[v3] = v3_counts.get(v3, 0) + count
            aliases.setdefault(key, []).append(v3)
        enabled = get_settings().analysis_event_fingerprint_mode == "enabled"
        keys = list(v3_counts if enabled else counts)
        ctx.log_metadata["fingerprint_keys"] = keys
        ctx.log_metadata["fingerprint_counts"] = {**counts, **v3_counts}
        ctx.log_metadata["fingerprint_aliases"] = aliases
        ctx.log_metadata["fingerprint_shadow_keys"] = list(counts if enabled else v3_counts)
        ctx.log_metadata["fingerprint_mode"] = "enabled" if enabled else "shadow"
        ctx.log_metadata["fingerprint_collisions"] = sum(len(set(v)) > 1 for v in aliases.values())
        ctx.log_metadata["fingerprint_new"] = len(keys)
        # No cache/suppression here: AI may fail or priority may have escalated.
        return ctx


async def persisted_delivery_records(ctx: PipelineContext) -> dict:
    """Use existing tenant-scoped checkpoints as the durable source of truth."""
    from sqlalchemy import select

    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask

    cutoff = datetime.now(UTC) - timedelta(hours=get_settings().analysis_fingerprint_ttl_hours)
    records = {}
    async with get_db_context() as session:
        offset = 0
        while True:
            rows = (
                await session.scalars(
                    select(LogAnalysisTask.query_params)
                    .where(
                        LogAnalysisTask.tenant_id == ctx.tenant_id,
                        LogAnalysisTask.business_line_id == ctx.business_line_id,
                        LogAnalysisTask.id != ctx.task_id,
                        LogAnalysisTask.status == "completed",
                        LogAnalysisTask.updated_at >= cutoff,
                    )
                    .order_by(LogAnalysisTask.updated_at.desc())
                    .offset(offset)
                    .limit(100)
                )
            ).all()
            for raw in rows:
                try:
                    delivery = json.loads(raw or "{}").get("delivery", {})
                    if delivery.get("state") not in {"sent", "duplicate"}:
                        continue
                    metadata = delivery.get("context", {}).get("log_metadata", {})
                    # Old records remain intact, but only successful deliveries
                    # (not detection/analysis caches) may suppress a new event.
                    saved = metadata.get("delivered_records", {})
                    if not saved and delivery.get("state") == "sent":
                        saved = {
                            k: {
                                "priority": delivery["context"]
                                .get("priority_decision", {})
                                .get("priority", "P1"),
                                "count": metadata.get("fingerprint_counts", {}).get(k, 1),
                            }
                            for k in metadata.get("fingerprint_keys", [])
                        }
                    for key, value in saved.items():
                        records.setdefault(key, value)
                except (TypeError, ValueError, KeyError):
                    continue
            if len(rows) < 100:
                break
            offset += len(rows)
    return records


async def delivered_unchanged(ctx: PipelineContext) -> bool:
    if ctx.log_metadata.get("is_regression"):
        return False
    keys = ctx.log_metadata.get("fingerprint_keys", [])
    if not keys:
        return False
    from logmind.core.redis import get_redis_client

    try:
        redis = get_redis_client()
        states = {}
        lookup_keys = list(
            dict.fromkeys(keys + ctx.log_metadata.get("fingerprint_shadow_keys", []))
        )
        for key in lookup_keys:
            try:
                raw = await redis.get(key)
                if raw:
                    states[key] = json.loads(raw)
            except Exception:
                pass
        if len(states) != len(lookup_keys):
            durable = await persisted_delivery_records(ctx)
            for key in lookup_keys:
                if key not in states and key in durable:
                    states[key] = durable[key]
        priority = ctx.priority_decision.get("priority", "P1")
        rank = {"P0": 0, "P1": 1, "P2": 2}
        for key in keys:
            count = max(ctx.log_metadata.get("fingerprint_counts", {}).get(key, ctx.log_count), 1)
            state = states.get(key)
            if not state:
                ctx.log_metadata["dedup_reason"] = "no_successful_delivery"
                return False
            expected = set(ctx.log_metadata.get("fingerprint_aliases", {}).get(key, []))
            if expected and not expected.issubset(state.get("aliases", [])):
                ctx.log_metadata["dedup_reason"] = "new_semantic_identity"
                return False
            if rank.get(priority, 2) < rank.get(state.get("priority"), 2):
                return False
            if count >= max(state.get("count", 1) * 1.5, 2):
                return False
        # Continuous observations extend the incident, not a periodic resend.
        ctx.log_metadata["delivered_records"] = states
        ctx.log_metadata["dedup_reason"] = "successful_delivery_unchanged"
        for key in states:
            try:
                await redis.setex(
                    key,
                    get_settings().analysis_fingerprint_ttl_hours * 3600,
                    json.dumps(states[key]),
                )
            except Exception:
                pass
        return True
    except Exception as exc:
        logger.warning("fingerprint_delivery_read_failed", error=type(exc).__name__)
        # Database failure must not silently become permission to resend.
        raise


async def mark_delivered(ctx: PipelineContext) -> None:
    from logmind.core.redis import get_redis_client

    keys = list(
        dict.fromkeys(
            ctx.log_metadata.get("fingerprint_keys", [])
            + ctx.log_metadata.get("fingerprint_shadow_keys", [])
        )
    )
    records = {
        key: {
            "priority": ctx.priority_decision.get("priority", "P1"),
            "count": max(ctx.log_metadata.get("fingerprint_counts", {}).get(key, ctx.log_count), 1),
            "task_id": ctx.task_id,
            "aliases": ctx.log_metadata.get("fingerprint_aliases", {}).get(key, []),
        }
        for key in keys
    }
    ctx.log_metadata["delivered_records"] = records
    # Persist before best-effort caching. A worker/cache crash must not erase
    # evidence of successful delivery and permit another task to resend.
    from logmind.domain.analysis.delivery import save_checkpoint

    await save_checkpoint(ctx, "sent")
    try:
        redis = get_redis_client()
        for key, record in records.items():
            state = json.dumps(record)
            await redis.setex(key, get_settings().analysis_fingerprint_ttl_hours * 3600, state)
    except Exception as exc:
        logger.warning("fingerprint_delivery_write_failed", error=type(exc).__name__)
