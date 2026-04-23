"""
Log Fetch Stage — Fetch logs from Elasticsearch

Stage 1 of the analysis pipeline.
"""

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)


class LogFetchStage(PipelineStage):
    """Fetch logs from Elasticsearch."""

    name = "log_fetch"

    def __init__(self, log_service):
        self.log_service = log_service

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        from logmind.domain.log.schemas import LogQueryRequest

        request = LogQueryRequest(
            index_pattern=ctx.es_index_pattern,
            time_from=ctx.time_from,
            time_to=ctx.time_to,
            query=ctx.query,
            severity=ctx.severity_threshold,
            language=ctx.language,
            extra_filters=ctx.extra_filters,
            size=5000,  # Expand ES window so diversity sampler can see older rare errors
        )
        result = await self.log_service.search_logs(request)
        ctx.raw_logs = [log.raw for log in result.logs]
        ctx.log_count = len(ctx.raw_logs)

        # Extract GYYX business context from first log entry
        if ctx.raw_logs:
            first_log = ctx.raw_logs[0]
            gy = first_log.get("gy", {})
            if isinstance(gy, dict):
                ctx.domain = ctx.domain or gy.get("domain", "")
                ctx.branch = ctx.branch or gy.get("branch", "")
            image = first_log.get("image", {})
            if isinstance(image, dict):
                ctx.image_version = ctx.image_version or image.get("version", "")
            host = first_log.get("host", {})
            if isinstance(host, dict):
                ctx.host_name = ctx.host_name or host.get("name", "")

        logger.info("log_fetch_completed", count=ctx.log_count, task_id=ctx.task_id)
        return ctx
