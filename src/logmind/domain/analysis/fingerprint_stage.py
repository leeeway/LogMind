"""
Error Fingerprint Deduplication Stage

Filters out previously-analyzed error patterns using Redis-backed fingerprint cache.
Prevents redundant AI inference on repeated errors within a configurable TTL window.

Fingerprint rules:
  - Java/C# exceptions: {biz_id}:{ExceptionClass}:{first_line_hash}
  - Generic errors:      {biz_id}:{message_hash}
"""

import hashlib
import re

from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)

# Exception class name pattern (Java + C#)
_EXCEPTION_CLASS_RE = re.compile(r"([\w.]+(?:Exception|Error|Throwable|Fault))")

# Redis key prefix
_FP_PREFIX = "logmind:fingerprint:"


def _generate_fingerprint(business_line_id: str, message: str) -> str:
    """
    Generate a fingerprint for an error log message.

    Strategy:
      - If message contains a recognizable exception class name,
        use {ExceptionClass}:{first_line_truncated} as the key body.
      - Otherwise, use a hash of the first 200 characters.
    """
    if not message:
        return ""

    first_line = message.split("\n")[0][:200]
    exc_match = _EXCEPTION_CLASS_RE.search(first_line)

    if exc_match:
        exc_class = exc_match.group(1)
        # Use exception class + hash of first line for uniqueness
        line_hash = hashlib.md5(first_line.encode()).hexdigest()[:12]
        body = f"{exc_class}:{line_hash}"
    else:
        # Generic: hash of first 200 chars
        body = hashlib.md5(first_line.encode()).hexdigest()[:16]

    return f"{_FP_PREFIX}{business_line_id}:{body}"


class ErrorFingerprintStage(PipelineStage):
    """
    Pipeline stage: filter out previously-analyzed error patterns.

    Runs between LogPreprocessStage and PromptBuildStage.
    Non-critical — if Redis is unavailable, all logs pass through.
    """

    name = "error_fingerprint"
    is_critical = False  # Don't break pipeline if Redis fails

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        settings = get_settings()

        if not settings.analysis_fingerprint_enabled:
            logger.info("fingerprint_disabled", task_id=ctx.task_id)
            return ctx

        if not ctx.processed_logs or ctx.processed_logs.startswith("(No logs"):
            return ctx

        ttl_seconds = settings.analysis_fingerprint_ttl_hours * 3600

        try:
            from logmind.core.redis import get_redis_client
            r = get_redis_client()

            # Split processed logs into individual entries
            log_lines = ctx.processed_logs.split("\n")

            # Generate fingerprints for all lines
            fingerprints = {}
            for line in log_lines:
                if not line.strip():
                    continue
                fp = _generate_fingerprint(ctx.business_line_id, line)
                if fp:
                    fingerprints[fp] = line

            if not fingerprints:
                return ctx

            # Batch check which fingerprints already exist in Redis
            pipe = r.pipeline()
            fp_keys = list(fingerprints.keys())
            for key in fp_keys:
                pipe.exists(key)
            results = await pipe.execute()

            # Separate new vs seen
            new_lines = []
            seen_count = 0
            new_fp_keys = []

            for key, exists in zip(fp_keys, results):
                if exists:
                    seen_count += 1
                else:
                    new_lines.append(fingerprints[key])
                    new_fp_keys.append(key)

            # Store new fingerprints in Redis with TTL
            if new_fp_keys:
                pipe = r.pipeline()
                for key in new_fp_keys:
                    pipe.setex(key, ttl_seconds, "1")
                await pipe.execute()

            # Update context
            original_count = len([l for l in log_lines if l.strip()])
            ctx.processed_logs = "\n".join(new_lines)

            # Mark if all logs were filtered (no new errors)
            ctx.log_metadata["fingerprint_original"] = original_count
            ctx.log_metadata["fingerprint_filtered"] = seen_count
            ctx.log_metadata["fingerprint_new"] = len(new_lines)

            logger.info(
                "fingerprint_completed",
                original=original_count,
                filtered=seen_count,
                new=len(new_lines),
                task_id=ctx.task_id,
            )

            # If ALL logs were seen before, set a flag so pipeline can skip AI
            if not new_lines:
                ctx.processed_logs = ""
                logger.info("fingerprint_all_seen", task_id=ctx.task_id)

        except Exception as e:
            # Non-critical: if Redis fails, let all logs through
            logger.warning("fingerprint_redis_error", error=str(e), task_id=ctx.task_id)

        return ctx
