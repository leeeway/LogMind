"""
Alert Domain — Celery Tasks

Scheduled log patrol — only analyzes logs at ERROR/CRITICAL severity
to control AI costs (per user requirement).
"""

import asyncio
from datetime import datetime, timedelta, timezone

from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(name="logmind.domain.alert.tasks.scheduled_log_patrol")
def scheduled_log_patrol():
    """
    Scheduled log patrol — runs periodically via Celery Beat.

    Cost control measures:
    - Only analyzes ERROR/CRITICAL severity logs
    - Respects cooldown between analyses per business line
    - Skips if no error logs found
    """
    logger.info("scheduled_patrol_started")
    asyncio.run(_run_patrol())


async def _run_patrol():
    """Execute patrol for all active business lines across all tenants."""
    from sqlalchemy import select

    from logmind.core.config import get_settings
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.analysis.tasks import run_analysis_task
    from logmind.domain.tenant.models import BusinessLine

    settings = get_settings()
    now = datetime.now(timezone.utc)
    cooldown = timedelta(minutes=settings.analysis_cooldown_minutes)

    async with get_db_context() as session:
        # Get all active business lines
        stmt = select(BusinessLine).where(BusinessLine.is_active == True)
        result = await session.execute(stmt)
        business_lines = result.scalars().all()

        for biz in business_lines:
            # Check cooldown — skip if recently analyzed
            last_task_stmt = (
                select(LogAnalysisTask)
                .where(
                    LogAnalysisTask.business_line_id == biz.id,
                    LogAnalysisTask.task_type == "scheduled",
                    LogAnalysisTask.created_at > (now - cooldown),
                )
                .limit(1)
            )
            last_result = await session.execute(last_task_stmt)
            if last_result.scalar_one_or_none():
                logger.info("patrol_cooldown_skip", business_line=biz.name)
                continue

            # Create patrol task — severity defaults to business line threshold
            task = LogAnalysisTask(
                tenant_id=biz.tenant_id,
                business_line_id=biz.id,
                task_type="scheduled",
                status="pending",
                time_from=now - timedelta(minutes=settings.analysis_cooldown_minutes),
                time_to=now,
                query_params="{}",
            )
            session.add(task)
            await session.flush()

            # Dispatch
            run_analysis_task.delay(task.id)
            logger.info(
                "patrol_task_created",
                business_line=biz.name,
                task_id=task.id,
            )
