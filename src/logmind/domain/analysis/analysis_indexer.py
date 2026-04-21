"""
Analysis Indexer — Auto-index AI Analysis Conclusions as Vectors (Phase 3)

After each AI analysis completes, this Celery task:
  1. Takes the error signature and analysis conclusions
  2. Embeds the error signature into a vector
  3. Stores it in the `logmind-analysis-vectors` ES index

This creates a "memory" of past analyses. Future errors with similar
signatures will be matched by SemanticDedupStage, allowing the system
to skip LLM calls and reuse historical conclusions.
"""

import asyncio
import json

from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


async def _async_index_analysis(
    task_id: str,
    business_line_id: str,
    error_signature: str,
    analysis_content: str,
    severity: str,
):
    """Index an analysis result into the vector store for future dedup."""
    from logmind.core.config import get_settings
    from logmind.domain.analysis.semantic_dedup import cached_embed
    from logmind.domain.log.service import log_service
    from datetime import datetime, timedelta, timezone

    settings = get_settings()

    try:
        # 1. Embed the error signature
        vector = await cached_embed(
            text=error_signature,
            redis_url=settings.redis_url,
            cache_ttl=settings.analysis_embedding_cache_ttl_seconds,
        )
        if vector is None:
            logger.warning("analysis_index_embed_failed", task_id=task_id)
            return

        # 2. Build the document
        now = datetime.now(timezone.utc)
        ttl_hours = settings.analysis_semantic_dedup_ttl_hours
        expire_at = now + timedelta(hours=ttl_hours)

        doc = {
            "business_line_id": business_line_id,
            "error_signature": error_signature,
            "analysis_content": analysis_content[:3000],  # Cap content size
            "severity": severity,
            "task_id": task_id,
            "embedding": vector,
            "created_at": now.isoformat(),
            "ttl_expire_at": expire_at.isoformat(),
            # Known Issue Library fields
            "status": "open",
            "hit_count": 1,
            "first_seen": now.isoformat(),
            "last_seen": now.isoformat(),
            "resolved_at": None,
            "feedback_quality": None,
        }

        # 3. Insert into ES
        success = await log_service.insert_analysis_vector(doc)

        if success:
            logger.info(
                "analysis_indexed_for_dedup",
                task_id=task_id,
                ttl_hours=ttl_hours,
                sig_preview=error_signature[:80],
            )
        else:
            logger.warning("analysis_index_insert_failed", task_id=task_id)

    except Exception as e:
        logger.error("analysis_index_error", task_id=task_id, error=str(e))
    finally:
        from logmind.core.elasticsearch import close_celery_es_client
        await close_celery_es_client()


@celery_app.task(
    name="logmind.domain.analysis.tasks.index_analysis_result",
    queue="analysis",
    ignore_result=True,
)
def index_analysis_result(
    task_id: str,
    business_line_id: str,
    error_signature: str,
    analysis_content: str,
    severity: str = "warning",
):
    """
    Celery task: Index analysis result for semantic dedup.

    Called asynchronously after a successful AI analysis to store
    the error signature + conclusion in the vector index.
    """
    logger.info("analysis_index_task_started", task_id=task_id)
    asyncio.run(
        _async_index_analysis(
            task_id=task_id,
            business_line_id=business_line_id,
            error_signature=error_signature,
            analysis_content=analysis_content,
            severity=severity,
        )
    )
