"""
Cross-Service Correlation Stage — Auto-check upstream/downstream on failure

Stage 2.5 of the analysis pipeline (after LogPreprocessStage).

When a business line has errors, this stage:
  1. Reads the related_services config from PipelineContext
  2. For each upstream/downstream service, queries ES for errors
     in the same time window
  3. Formats correlated errors into ctx.correlated_errors
  4. The PromptBuildStage injects these into the AI prompt so the
     Agent can reason about cross-service root cause chains

This is a NON-CRITICAL stage — failure does not abort the pipeline.
"""

import json
from datetime import datetime, timedelta, timezone

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)

# Max related services to query (prevent fan-out explosion)
MAX_RELATED_SERVICES = 6
# Max errors to fetch per related service
MAX_ERRORS_PER_SERVICE = 10


class CrossServiceCorrelationStage(PipelineStage):
    """
    Query upstream/downstream services for correlated errors.

    Non-critical: failures are logged and silently skipped.
    """

    name = "cross_service_correlation"
    is_critical = False  # Never abort the pipeline for correlation failures

    def __init__(self, log_service):
        self.log_service = log_service

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        # Skip if no related services configured
        if not ctx.related_services:
            logger.info(
                "cross_service_skip_no_relations",
                task_id=ctx.task_id,
                business_line_id=ctx.business_line_id,
            )
            return ctx

        # Skip if primary service has no errors (nothing to correlate)
        if ctx.log_count == 0:
            return ctx

        upstream_ids = ctx.related_services.get("upstream", [])
        downstream_ids = ctx.related_services.get("downstream", [])
        all_related = upstream_ids + downstream_ids

        if not all_related:
            return ctx

        # Limit fan-out
        all_related = all_related[:MAX_RELATED_SERVICES]

        # Load related business line configs from DB
        from sqlalchemy import select
        from logmind.core.database import get_db_context
        from logmind.domain.tenant.models import BusinessLine

        related_configs: dict[str, dict] = {}
        async with get_db_context() as session:
            stmt = select(BusinessLine).where(
                BusinessLine.id.in_(all_related),
                BusinessLine.is_active == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            for biz in result.scalars().all():
                direction = "upstream" if biz.id in upstream_ids else "downstream"
                related_configs[biz.id] = {
                    "name": biz.name,
                    "es_index_pattern": biz.es_index_pattern,
                    "language": biz.language,
                    "direction": direction,
                }

        if not related_configs:
            logger.info(
                "cross_service_no_active_relations",
                task_id=ctx.task_id,
                related_ids=all_related,
            )
            return ctx

        # Query each related service for errors in the same time window
        # Expand window by ±5 minutes to catch propagation delays
        from logmind.domain.log.schemas import LogQueryRequest

        time_from = ctx.time_from
        time_to = ctx.time_to
        if time_from:
            time_from = time_from - timedelta(minutes=5)
        if time_to:
            time_to = time_to + timedelta(minutes=5)

        correlated: list[dict] = []
        for biz_id, config in related_configs.items():
            try:
                request = LogQueryRequest(
                    index_pattern=config["es_index_pattern"],
                    time_from=time_from,
                    time_to=time_to,
                    severity="error",
                    language=config["language"],
                    size=MAX_ERRORS_PER_SERVICE,
                )
                result = await self.log_service.search_logs(request)

                if result.logs:
                    # Extract key info from correlated errors
                    error_samples = []
                    for log in result.logs[:5]:
                        msg = log.raw.get("message", "")
                        if isinstance(msg, str):
                            error_samples.append(msg[:300])

                    correlated.append({
                        "service_name": config["name"],
                        "service_id": biz_id,
                        "direction": config["direction"],
                        "error_count": len(result.logs),
                        "error_samples": error_samples,
                    })

                    logger.info(
                        "cross_service_errors_found",
                        related_service=config["name"],
                        direction=config["direction"],
                        error_count=len(result.logs),
                        task_id=ctx.task_id,
                    )
            except Exception as e:
                logger.warning(
                    "cross_service_query_failed",
                    related_service=config["name"],
                    error=str(e),
                    task_id=ctx.task_id,
                )

        ctx.correlated_errors = correlated

        if correlated:
            logger.info(
                "cross_service_correlation_completed",
                task_id=ctx.task_id,
                correlated_services=len(correlated),
                total_correlated_errors=sum(c["error_count"] for c in correlated),
            )
        else:
            logger.info(
                "cross_service_no_correlated_errors",
                task_id=ctx.task_id,
                checked_services=len(related_configs),
            )

        return ctx
