"""
Tenant Domain — Celery Tasks

Periodic ES index auto-discovery task.
"""

from logmind.core.async_task import run_async
from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(name="logmind.domain.tenant.tasks.discover_business_lines")
def discover_business_lines():
    """
    Periodic task — scan ES for new master-* indices.

    Runs via Celery Beat. Discovers indices for all active tenants
    and writes them to the discovered_index table as pending items.
    """
    from logmind.core.config import get_settings

    settings = get_settings()
    if not settings.auto_discover_enabled:
        logger.debug("auto_discover_disabled")
        return

    logger.info("auto_discover_started")
    run_async(_discover_all_tenants())


async def _discover_all_tenants():
    """Run discovery for all active tenants."""
    from sqlalchemy import select

    from logmind.core.database import get_db_context
    from logmind.domain.tenant.discovery import discover_indices
    from logmind.domain.tenant.models import Tenant

    async with get_db_context() as session:
        result = await session.execute(
            select(Tenant.id).where(Tenant.is_active == True)
        )
        tenant_ids = [row[0] for row in result.all()]

    total_new = 0
    for tid in tenant_ids:
        try:
            stats = await discover_indices(tid)
            total_new += stats.get("new", 0)
        except Exception as e:
            logger.error("auto_discover_tenant_failed", tenant_id=tid, error=str(e))

    logger.info("auto_discover_completed", tenants=len(tenant_ids), new_total=total_new)
