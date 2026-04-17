"""
Alert Domain — Celery Tasks

Scheduled log patrol — Fan-out architecture for multi-business-line parallelism.

Design:
  scheduled_log_patrol (Beat) → dispatches N independent patrol tasks
  patrol_single_business_line (Worker) → each runs independently

Benefits:
  - True parallel execution across Celery workers
  - Single business line failure doesn't block others
  - Scales linearly with worker count
"""

import asyncio
from datetime import datetime, timedelta, timezone

from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(name="logmind.domain.alert.tasks.scheduled_log_patrol")
def scheduled_log_patrol():
    """
    Scheduled log patrol dispatcher — runs periodically via Celery Beat.

    Fan-out pattern: queries all eligible business lines, then dispatches
    an independent patrol task for each one. This is fast (only DB reads)
    and ensures no single business line can block the entire patrol cycle.
    """
    logger.info("scheduled_patrol_dispatcher_started")
    asyncio.run(_dispatch_patrols())


async def _dispatch_patrols():
    """
    Query all active business lines, check cooldown, and dispatch
    individual patrol tasks for eligible ones.
    """
    from sqlalchemy import select

    from logmind.core.config import get_settings
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.tenant.models import BusinessLine

    settings = get_settings()
    now = datetime.now(timezone.utc)
    cooldown = timedelta(minutes=settings.analysis_cooldown_minutes)

    dispatched = 0
    skipped = 0

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
                logger.debug("patrol_cooldown_skip", business_line=biz.name)
                skipped += 1
                continue

            # Dispatch independent patrol task for this business line
            patrol_single_business_line.delay(biz.id)
            dispatched += 1
            logger.info("patrol_dispatched", business_line=biz.name, biz_id=biz.id)

    logger.info(
        "scheduled_patrol_dispatcher_done",
        dispatched=dispatched,
        skipped_cooldown=skipped,
        total_business_lines=dispatched + skipped,
    )


@celery_app.task(
    name="logmind.domain.alert.tasks.patrol_single_business_line",
    max_retries=1,
    default_retry_delay=30,
)
def patrol_single_business_line(business_line_id: str):
    """
    Independent patrol task for a single business line.

    Creates an analysis task and dispatches it. Runs in its own
    Celery worker slot, so failures are isolated.
    """
    logger.info("patrol_single_started", biz_id=business_line_id)
    asyncio.run(_patrol_single(business_line_id))


async def _patrol_single(business_line_id: str):
    """Execute patrol for one business line: create task → dispatch analysis."""
    from logmind.core.config import get_settings
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.analysis.tasks import run_analysis_task
    from logmind.domain.tenant.models import BusinessLine

    settings = get_settings()
    now = datetime.now(timezone.utc)

    async with get_db_context() as session:
        biz = await session.get(BusinessLine, business_line_id)
        if not biz or not biz.is_active:
            logger.warning("patrol_biz_not_found_or_inactive", biz_id=business_line_id)
            return

        # Create patrol task
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

        # Dispatch analysis
        run_analysis_task.delay(task.id)
        logger.info(
            "patrol_task_created",
            business_line=biz.name,
            task_id=task.id,
        )
