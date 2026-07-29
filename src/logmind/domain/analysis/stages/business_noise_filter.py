"""
Business Noise Filter Stage — Intelligent Non-Fault Log Elimination

Inserted after LogQualityFilterStage in the pipeline, this stage identifies
ERROR-level logs that represent normal business flow (user input errors,
rate limiting, business validation failures) and removes them from
the processing pipeline before expensive AI analysis.

Three-layer matching:
  1. Static patterns (curated, always available)
  2. Per-BusinessLine custom patterns (DB-stored, operator-configurable)
  3. AI-learned noise rules (ES-stored, auto-populated)

Safety: Lines containing real fault indicators (stack traces, 5xx, OOM,
connection errors, etc.) are NEVER filtered, even if they also match
a noise pattern. See business_noise.has_fault_protection().

Non-critical: if this stage fails, all logs pass through unchanged.
"""

import json

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)


class BusinessNoiseFilterStage(PipelineStage):
    """Filter business noise logs before AI analysis."""

    name = "business_noise_filter"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.processed_logs or not ctx.raw_logs:
            return ctx

        if ctx.full_log_analysis:
            ctx.log_metadata["business_noise_filter_skipped"] = "full_log_analysis"
            return ctx

        from logmind.domain.log.business_noise import (
            classify_line,
            load_learned_noise_rules,
        )

        # Load per-business-line custom noise patterns from DB
        custom_patterns = await self._load_custom_patterns(ctx.business_line_id)

        # Load AI-learned noise rules from ES
        try:
            learned_rules = await load_learned_noise_rules(ctx.business_line_id)
        except Exception as e:
            logger.warning("learned_noise_load_failed", error=str(e))
            learned_rules = []

        # Classify each line
        kept_lines = []
        noise_lines = []
        noise_categories: dict[str, int] = {}
        noise_reasons: list[str] = []

        for line in ctx.processed_logs.split("\n"):
            stripped = line.strip()
            if not stripped:
                kept_lines.append(line)
                continue

            is_noise, matched_rule = classify_line(
                stripped, custom_patterns=custom_patterns, learned_rules=learned_rules
            )

            if is_noise and matched_rule:
                noise_lines.append(line)
                category = matched_rule.get("category", "unknown")
                noise_categories[category] = noise_categories.get(category, 0) + 1
                reason = matched_rule.get("reason", "")
                if reason and reason not in noise_reasons:
                    noise_reasons.append(reason)
            else:
                kept_lines.append(line)

        noise_count = len(noise_lines)

        if noise_count > 0:
            logger.info(
                "business_noise_filter_applied",
                task_id=ctx.task_id,
                business_line=ctx.business_line_name,
                noise_count=noise_count,
                kept_count=len(kept_lines),
                categories=noise_categories,
            )

            ctx.processed_logs = "\n".join(kept_lines)
            ctx.log_metadata["business_noise_filtered"] = noise_count
            ctx.log_metadata["business_noise_categories"] = noise_categories
            ctx.log_metadata["business_noise_reasons"] = noise_reasons[:5]

            # If all logs were noise, mark log_count = 0 so pipeline skips analysis
            if not kept_lines or all(l.strip() == "" for l in kept_lines):
                ctx.processed_logs = ""
                ctx.log_count = 0
                logger.info(
                    "business_noise_filter_all_removed",
                    task_id=ctx.task_id,
                    business_line=ctx.business_line_name,
                    noise_count=noise_count,
                    categories=noise_categories,
                    reason="All logs classified as business noise (non-fault)",
                )
        else:
            logger.debug(
                "business_noise_filter_no_match",
                task_id=ctx.task_id,
            )

        return ctx

    @staticmethod
    async def _load_custom_patterns(business_line_id: str) -> list[dict]:
        """Load per-BusinessLine custom noise patterns from DB."""
        try:
            from logmind.core.database import get_db_context
            from logmind.domain.tenant.models import BusinessLine

            async with get_db_context() as session:
                biz = await session.get(BusinessLine, business_line_id)
                if biz and biz.noise_patterns:
                    patterns = json.loads(biz.noise_patterns)
                    if isinstance(patterns, list):
                        return patterns
        except Exception as e:
            logger.warning(
                "custom_noise_patterns_load_failed",
                biz_id=business_line_id,
                error=str(e),
            )
        return []
