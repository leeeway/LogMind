"""
Analysis Domain — Celery Async Tasks

Handles:
- Async log analysis execution
- Scheduled log patrol (cost-controlled)
- Old task cleanup
"""

import asyncio
from datetime import datetime, timedelta, timezone

from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="logmind.domain.analysis.tasks.run_analysis_task",
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def run_analysis_task(self, task_id: str):
    """
    Async Celery task: Execute a log analysis pipeline.
    """
    logger.info("celery_task_started", task_id=task_id)
    asyncio.run(_execute_analysis(task_id))


async def _execute_analysis(task_id: str):
    """Run the analysis pipeline for a given task."""
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.analysis.pipeline import (
        AIInferenceStage,
        AlertEvalStage,
        AnalysisPipeline,
        LogFetchStage,
        LogPreprocessStage,
        PersistStage,
        PipelineContext,
        PromptBuildStage,
        RAGRetrieveStage,
        ResultParseStage,
    )
    from logmind.domain.log.service import log_service
    from logmind.domain.prompt.engine import prompt_engine
    from logmind.domain.prompt.models import PromptTemplate
    from logmind.domain.provider.manager import provider_manager
    from logmind.domain.tenant.models import BusinessLine
    from logmind.shared.base_repository import BaseRepository

    prompt_repo = BaseRepository(PromptTemplate)

    async with get_db_context() as session:
        # 1. Load task
        task = await session.get(LogAnalysisTask, task_id)
        if not task:
            logger.error("task_not_found", task_id=task_id)
            return

        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        await session.flush()

        # 2. Load business line
        biz = await session.get(BusinessLine, task.business_line_id)
        if not biz:
            task.status = "failed"
            task.error_message = "Business line not found"
            task.completed_at = datetime.now(timezone.utc)
            await session.flush()
            return

    # 3. Build pipeline
    pipeline = AnalysisPipeline(stages=[
        LogFetchStage(log_service),
        LogPreprocessStage(),
        RAGRetrieveStage(),
        PromptBuildStage(prompt_engine, prompt_repo),
        AIInferenceStage(provider_manager),
        ResultParseStage(),
        AlertEvalStage(),
        PersistStage(),
    ])

    # 4. Build context
    import json
    query_params = {}
    try:
        query_params = json.loads(task.query_params)
    except Exception:
        pass

    ctx = PipelineContext(
        tenant_id=task.tenant_id,
        task_id=task_id,
        business_line_id=task.business_line_id,
        business_line_name=biz.name,
        es_index_pattern=biz.es_index_pattern,
        severity_threshold=biz.severity_threshold,
        language=biz.language,
        time_from=task.time_from,
        time_to=task.time_to,
        query=query_params.get("query", ""),
        extra_filters=query_params.get("extra_filters", {}),
        provider_config_id=task.provider_config_id or "",
        prompt_template_id=task.prompt_template_id or "",
    )

    # 5. Execute pipeline
    try:
        ctx = await pipeline.run(ctx)

        async with get_db_context() as session:
            task = await session.get(LogAnalysisTask, task_id)
            task.status = "completed"
            task.log_count = ctx.log_count
            task.token_usage = ctx.token_usage.total_tokens if ctx.token_usage else 0
            task.provider_config_id = ctx.provider_config_id
            task.prompt_template_id = ctx.prompt_template_id
            task.completed_at = datetime.now(timezone.utc)
            if ctx.errors:
                task.error_message = "; ".join(ctx.errors)
            await session.flush()

        # 6. Fire alert notifications if needed
        if ctx.alerts_fired:
            await _send_alert_notifications(ctx)

    except Exception as e:
        logger.error("pipeline_failed", task_id=task_id, error=str(e))
        async with get_db_context() as session:
            task = await session.get(LogAnalysisTask, task_id)
            task.status = "failed"
            task.error_message = str(e)
            task.completed_at = datetime.now(timezone.utc)
            await session.flush()


async def _send_alert_notifications(ctx):
    """Send alert notifications for critical findings."""
    from logmind.domain.alert.channels.wechat import send_wechat_alert

    for alert in ctx.alerts_fired:
        try:
            await send_wechat_alert(
                business_line=ctx.business_line_name,
                severity=alert.get("severity", "warning"),
                content=alert.get("content", ""),
                task_id=ctx.task_id,
            )
        except Exception as e:
            logger.error("alert_notification_failed", error=str(e))


@celery_app.task(name="logmind.domain.analysis.tasks.cleanup_old_tasks")
def cleanup_old_tasks():
    """Clean up analysis tasks older than 30 days."""
    asyncio.run(_cleanup_old_tasks())


async def _cleanup_old_tasks():
    from sqlalchemy import delete

    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import AnalysisResult, LogAnalysisTask

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    async with get_db_context() as session:
        # Delete old results first (FK constraint)
        await session.execute(
            delete(AnalysisResult).where(AnalysisResult.created_at < cutoff)
        )
        await session.execute(
            delete(LogAnalysisTask).where(LogAnalysisTask.created_at < cutoff)
        )
        logger.info("old_tasks_cleaned", cutoff=cutoff.isoformat())
