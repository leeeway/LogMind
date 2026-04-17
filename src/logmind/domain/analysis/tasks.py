"""
Analysis Domain — Celery Async Tasks

Handles:
- Async log analysis execution (with AI toggle)
- AI-off mode: fetch + preprocess + direct webhook notification
- AI-on mode: full 8-stage pipeline
- Scheduled log patrol (cost-controlled)
- Old task cleanup
"""

import asyncio
import json
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
    """
    Run the analysis pipeline for a given task.

    Two modes based on BusinessLine.ai_enabled:
      - ai_enabled=True:  Full 8-stage pipeline (Fetch → AI → Alert → Persist)
      - ai_enabled=False: Lightweight mode (Fetch → Preprocess → Webhook)
    """
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.analysis.pipeline import (
        AlertEvalStage,
        AnalysisPipeline,
        LogFetchStage,
        LogPreprocessStage,
        PersistStage,
        PipelineContext,
        PromptBuildStage,
        ResultParseStage,
    )
    from logmind.domain.analysis.agent_stage import AgentInferenceStage
    from logmind.domain.analysis.fingerprint_stage import ErrorFingerprintStage
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

        # Snapshot business line config
        ai_enabled = biz.ai_enabled
        webhook_url = biz.webhook_url or ""
        biz_name = biz.name
        biz_language = biz.language

    # 3. Build pipeline — dynamically based on ai_enabled
    if ai_enabled:
        # Full AI pipeline with fingerprint dedup
        stages = [
            LogFetchStage(log_service),
            LogPreprocessStage(),
            ErrorFingerprintStage(),
            PromptBuildStage(prompt_engine, prompt_repo),
            AgentInferenceStage(provider_manager),
            ResultParseStage(),
            AlertEvalStage(),
            PersistStage(),
        ]
    else:
        # Lightweight: only fetch and preprocess (no AI, no persist)
        stages = [
            LogFetchStage(log_service),
            LogPreprocessStage(),
        ]

    pipeline = AnalysisPipeline(stages=stages)

    # 4. Build context
    query_params = {}
    try:
        query_params = json.loads(task.query_params)
    except Exception:
        pass

    ctx = PipelineContext(
        tenant_id=task.tenant_id,
        task_id=task_id,
        business_line_id=task.business_line_id,
        business_line_name=biz_name,
        es_index_pattern=biz.es_index_pattern,
        severity_threshold=biz.severity_threshold,
        language=biz_language,
        time_from=task.time_from,
        time_to=task.time_to,
        query=query_params.get("query", ""),
        extra_filters=query_params.get("extra_filters", {}),
        provider_config_id=task.provider_config_id or "",
        prompt_template_id=task.prompt_template_id or "",
    )

    # 5. Execute pipeline
    from logmind.core.elasticsearch import close_celery_es_client
    try:
        ctx = await pipeline.run(ctx)

        # Check if fingerprint stage filtered out ALL logs (no new errors)
        fingerprint_new = ctx.log_metadata.get("fingerprint_new")
        if ai_enabled and fingerprint_new is not None and fingerprint_new == 0:
            # All errors were previously analyzed — skip notification
            logger.info(
                "task_skipped_all_fingerprinted",
                task_id=task_id,
                filtered=ctx.log_metadata.get("fingerprint_filtered", 0),
            )
            async with get_db_context() as session:
                task = await session.get(LogAnalysisTask, task_id)
                task.status = "completed"
                task.log_count = ctx.log_count
                task.token_usage = 0
                task.completed_at = datetime.now(timezone.utc)
                task.error_message = (
                    f"跳过分析: 全部 {ctx.log_metadata.get('fingerprint_filtered', 0)} 条错误"
                    f"已在近期分析过（指纹去重）"
                )
                await session.flush()
            return  # No notification needed

        if ai_enabled:
            # ── AI mode: update task + send AI alert ──────
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

            # Fire AI analysis alerts
            if ctx.alerts_fired:
                await _send_ai_alerts(ctx, webhook_url)
        else:
            # ── AI-off mode: send direct error notification ──
            async with get_db_context() as session:
                task = await session.get(LogAnalysisTask, task_id)
                task.status = "completed"
                task.log_count = ctx.log_count
                task.token_usage = 0  # No AI used
                task.completed_at = datetime.now(timezone.utc)
                await session.flush()

            # Send direct webhook notification if errors found
            if ctx.log_count > 0:
                await _send_error_log_notification(ctx, webhook_url)

    except Exception as e:
        logger.error("pipeline_failed", task_id=task_id, error=str(e))

        async with get_db_context() as session:
            task = await session.get(LogAnalysisTask, task_id)
            task.status = "failed"
            task.error_message = str(e)
            task.completed_at = datetime.now(timezone.utc)
            await session.flush()

        # If AI was enabled but failed, send pipeline error notification
        # and also send raw error log summary as fallback
        if ai_enabled:
            await _send_pipeline_error_notification(ctx, str(e), webhook_url)
            # Fallback: if we have preprocessed logs, send them directly
            if ctx.processed_logs and ctx.log_count > 0:
                await _send_error_log_notification(ctx, webhook_url)
    finally:
        await close_celery_es_client()


async def _send_ai_alerts(ctx, webhook_url: str):
    """Send AI analysis alert notifications for critical findings."""
    from logmind.domain.alert.channels.webhook import notify_ai_alert

    for alert in ctx.alerts_fired:
        try:
            await notify_ai_alert(
                business_line=ctx.business_line_name,
                domain=ctx.domain,
                branch=ctx.branch,
                host_name=ctx.host_name,
                language=ctx.language,
                severity=alert.get("severity", "warning"),
                content=alert.get("content", ""),
                task_id=ctx.task_id,
                log_count=ctx.log_count,
                webhook_url=webhook_url or None,
            )
        except Exception as e:
            logger.error("ai_alert_notification_failed", error=str(e))


async def _send_error_log_notification(ctx, webhook_url: str):
    """Send direct error log notification (AI disabled mode)."""
    from logmind.domain.alert.channels.webhook import notify_error_logs

    # Build a concise error summary from preprocessed logs
    error_summary = ctx.processed_logs
    if len(error_summary) > 1500:
        error_summary = error_summary[:1500] + "\n... (更多日志请登录平台查看)"

    time_range = f"{ctx.time_from} ~ {ctx.time_to}" if ctx.time_from else "未知"

    try:
        await notify_error_logs(
            business_line=ctx.business_line_name,
            domain=ctx.domain,
            branch=ctx.branch,
            host_name=ctx.host_name,
            language=ctx.language,
            log_count=ctx.log_count,
            error_summary=error_summary,
            time_range=time_range,
            webhook_url=webhook_url or None,
        )
    except Exception as e:
        logger.error("error_log_notification_failed", error=str(e))


async def _send_pipeline_error_notification(ctx, error_message: str, webhook_url: str):
    """Send pipeline/model error notification."""
    from logmind.domain.alert.channels.webhook import notify_pipeline_error

    try:
        await notify_pipeline_error(
            business_line=ctx.business_line_name,
            domain=ctx.domain,
            error_message=error_message,
            task_id=ctx.task_id,
            webhook_url=webhook_url or None,
        )
    except Exception as e:
        logger.error("pipeline_error_notification_failed", error=str(e))


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
