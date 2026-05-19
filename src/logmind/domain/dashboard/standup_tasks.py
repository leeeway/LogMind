"""
Daily Standup scheduled tasks.
"""

from __future__ import annotations

from datetime import datetime, timezone

from logmind.core.async_task import run_async
from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(name="logmind.domain.dashboard.tasks.dispatch_daily_standup_auto_share")
def dispatch_daily_standup_auto_share():
    """Periodic dispatcher for tenant-level standup auto-share."""
    logger.info("daily_standup_auto_share_dispatcher_started")
    run_async(_dispatch_daily_standup_auto_share())


@celery_app.task(
    bind=True,
    name="logmind.domain.dashboard.tasks.share_daily_standup_for_tenant",
    max_retries=2,
    default_retry_delay=60,
)
def share_daily_standup_for_tenant(self, tenant_id: str):
    """Share daily standup for one tenant."""
    logger.info("daily_standup_auto_share_started", tenant_id=tenant_id)
    try:
        run_async(_share_daily_standup_for_tenant(tenant_id))
    except Exception as exc:
        logger.warning(
            "daily_standup_auto_share_retrying",
            tenant_id=tenant_id,
            retry=self.request.retries + 1,
            error=str(exc),
        )
        raise self.retry(exc=exc)


async def _dispatch_daily_standup_auto_share() -> None:
    from sqlalchemy import select

    from logmind.core.database import get_db_context
    from logmind.domain.dashboard.standup_service import is_daily_standup_due
    from logmind.domain.tenant.models import Tenant

    now_utc = datetime.now(timezone.utc)
    dispatched = 0
    skipped = 0

    async with get_db_context() as session:
        tenants = (await session.execute(
            select(Tenant).where(Tenant.is_active == True)  # noqa: E712
        )).scalars().all()

    for tenant in tenants:
        if not is_daily_standup_due(tenant.settings, now_utc=now_utc):
            skipped += 1
            continue
        share_daily_standup_for_tenant.delay(tenant.id)
        dispatched += 1

    logger.info(
        "daily_standup_auto_share_dispatcher_done",
        dispatched=dispatched,
        skipped=skipped,
        total_tenants=dispatched + skipped,
    )


async def _share_daily_standup_for_tenant(tenant_id: str) -> None:
    from logmind.core.database import get_db_context
    from logmind.domain.dashboard.standup_service import (
        auto_share_target_date,
        load_daily_standup_settings,
        mark_auto_share_complete,
        release_auto_share_lock,
        share_standup_for_tenant,
        try_acquire_auto_share_lock,
    )
    from logmind.domain.tenant.models import Tenant

    target_date = auto_share_target_date()
    report_date = target_date.strftime("%Y-%m-%d")

    async with get_db_context() as session:
        tenant = await session.get(Tenant, tenant_id)
        if not tenant or not tenant.is_active:
            logger.warning("daily_standup_auto_share_tenant_missing_or_inactive", tenant_id=tenant_id)
            return

        config = load_daily_standup_settings(tenant.settings)
        if not config["enabled"]:
            logger.info("daily_standup_auto_share_disabled", tenant_id=tenant_id)
            return

    if not await try_acquire_auto_share_lock(tenant_id, report_date):
        logger.info("daily_standup_auto_share_skipped_duplicate", tenant_id=tenant_id, date=report_date)
        return

    try:
        result = await share_standup_for_tenant(tenant_id, target_date=target_date)
        if result["sent_count"] > 0:
            await mark_auto_share_complete(tenant_id, result["date"])
        else:
            await release_auto_share_lock(tenant_id, report_date)
        logger.info(
            "daily_standup_auto_share_done",
            tenant_id=tenant_id,
            date=result["date"],
            sent_count=result["sent_count"],
        )
    except Exception:
        await release_auto_share_lock(tenant_id, report_date)
        raise
