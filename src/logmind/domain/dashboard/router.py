"""
Dashboard Domain — API Router

Aggregation endpoints for the Dashboard UI.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.domain.analysis.models import AnalysisResult, LogAnalysisTask
from logmind.domain.alert.models import AlertHistory
from logmind.domain.tenant.models import BusinessLine
from logmind.shared.base_schema import BaseSchema

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class DashboardOverview(BaseSchema):
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    total_alerts: int
    total_tokens_used: int
    total_business_lines: int
    recent_tasks: list[dict]
    severity_distribution: list[dict]


@router.get("/overview", response_model=DashboardOverview)
async def get_dashboard_overview(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=90),
):
    """Get dashboard overview statistics for current tenant."""
    tenant_id = user.tenant_id
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Task counts
    total_tasks = await _count_tasks(session, tenant_id, since)
    completed = await _count_tasks(session, tenant_id, since, status="completed")
    failed = await _count_tasks(session, tenant_id, since, status="failed")

    # Total tokens
    token_stmt = (
        select(func.coalesce(func.sum(LogAnalysisTask.token_usage), 0))
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
        )
    )
    token_result = await session.execute(token_stmt)
    total_tokens = token_result.scalar_one()

    # Alert count
    alert_stmt = (
        select(func.count())
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == tenant_id,
            AlertHistory.fired_at >= since,
        )
    )
    alert_result = await session.execute(alert_stmt)
    total_alerts = alert_result.scalar_one()

    # Business lines
    biz_stmt = (
        select(func.count())
        .select_from(BusinessLine)
        .where(
            BusinessLine.tenant_id == tenant_id,
            BusinessLine.is_active == True,
        )
    )
    biz_result = await session.execute(biz_stmt)
    total_biz = biz_result.scalar_one()

    # Recent tasks
    recent_stmt = (
        select(LogAnalysisTask)
        .where(LogAnalysisTask.tenant_id == tenant_id)
        .order_by(LogAnalysisTask.created_at.desc())
        .limit(10)
    )
    recent_result = await session.execute(recent_stmt)
    recent_tasks = [
        {
            "id": t.id,
            "status": t.status,
            "task_type": t.task_type,
            "log_count": t.log_count,
            "token_usage": t.token_usage,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in recent_result.scalars().all()
    ]

    # Severity distribution from results
    severity_stmt = (
        select(
            AnalysisResult.severity,
            func.count().label("count"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
        )
        .group_by(AnalysisResult.severity)
    )
    sev_result = await session.execute(severity_stmt)
    severity_dist = [
        {"severity": row.severity, "count": row.count}
        for row in sev_result.all()
    ]

    return DashboardOverview(
        total_tasks=total_tasks,
        completed_tasks=completed,
        failed_tasks=failed,
        total_alerts=total_alerts,
        total_tokens_used=total_tokens,
        total_business_lines=total_biz,
        recent_tasks=recent_tasks,
        severity_distribution=severity_dist,
    )


async def _count_tasks(
    session: AsyncSession,
    tenant_id: str,
    since: datetime,
    status: str | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
        )
    )
    if status:
        stmt = stmt.where(LogAnalysisTask.status == status)
    result = await session.execute(stmt)
    return result.scalar_one()
