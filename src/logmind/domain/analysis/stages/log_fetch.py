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
            business_line_id=ctx.business_line_id,
            extra_filters=ctx.extra_filters,
            size=10000,  # Maximum ES window; preprocessing samples with temporal diversity
        )
        result = await self.log_service.search_logs(request)
        ctx.raw_logs = [log.raw for log in result.logs]
        refs = ctx.log_metadata.get("patrol", {}).get("evidence_refs", [])
        if refs:
            # Pin rare trigger evidence even when the ordinary fetch hits 10k.
            from logmind.core.elasticsearch import get_es_client
            response = await get_es_client().mget(docs=[
                {"_index": ref["index"], "_id": ref["id"]} for ref in refs[:20]
            ])
            for doc in response.get("docs", []):
                if doc.get("found"):
                    ctx.raw_logs.append({**doc["_source"], "_es_index": doc["_index"], "_es_id": doc["_id"], "_trigger_evidence": True})
        unique = {}
        for i, raw in enumerate(ctx.raw_logs):
            key = (raw.get("_es_index"), raw.get("_es_id")) if raw.get("_es_id") else (None, i)
            unique[key] = raw
        ctx.raw_logs = list(unique.values())
        ctx.log_count = max(result.total, len(ctx.raw_logs))
        ctx.log_metadata["matched_count"] = ctx.log_count
        ctx.log_metadata["fetched_count"] = len(ctx.raw_logs)

        # Shared metadata policy; keep rolling-deployment versions per instance.
        from logmind.domain.log.events import metadata
        records = [metadata(log) for log in ctx.raw_logs]
        for attribute, key in (("domain", "domain"), ("branch", "branch"), ("host_name", "host_name")):
            values = list(dict.fromkeys(r[key] for r in records if r[key]))
            if not getattr(ctx, attribute) and len(values) == 1:
                setattr(ctx, attribute, values[0])
        versions = list(dict.fromkeys(r["image_version"] for r in records if r["image_version"]))
        ctx.image_version = versions[0] if len(versions) == 1 else ""
        instances = {}
        for item in records:
            key = (item["pod_name"], item["host_name"], item["image_version"])
            if key not in instances and len(instances) < 100:
                instances[key] = item
        ctx.log_metadata["instances"] = list(instances.values())
        ctx.log_metadata["image_versions"] = versions[:100]

        logger.info("log_fetch_completed", count=ctx.log_count, task_id=ctx.task_id)
        return ctx
