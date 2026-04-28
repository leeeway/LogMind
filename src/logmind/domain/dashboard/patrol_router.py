"""
Patrol Status — Proactive anomaly detection status API.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, func, case

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.tenant.models import BusinessLine
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
biz_repo = BaseRepository(BusinessLine)


class ServiceStatus(BaseModel):
    service_id: str
    service_name: str
    status: str  # normal | warning | critical
    error_count_1h: int
    error_rate_change: float  # vs previous hour
    last_error: str


class PatrolResponse(BaseModel):
    patrol_status: str  # scanning | anomaly_detected | all_clear
    anomaly_count: int
    services: list[ServiceStatus]
    last_scan: str


@router.get("/patrol-status", response_model=PatrolResponse)
async def get_patrol_status(
    session: DBSession,
    user: CurrentUser,
):
    """
    Get real-time patrol/scan status for all services.
    """
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    two_hours_ago = now - timedelta(hours=2)

    biz_lines = await biz_repo.get_all(session, tenant_id=user.tenant_id, limit=100)
    biz_map = {b.id: b.name for b in biz_lines}

    # Current hour errors per service
    curr_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.sum(case((AnalysisResult.severity == "critical", 1), else_=0)).label("errors"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= one_hour_ago,
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    curr_result = await session.execute(curr_stmt)
    curr_map = {row[0]: int(row[1] or 0) for row in curr_result.all()}

    # Previous hour errors
    prev_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.sum(case((AnalysisResult.severity == "critical", 1), else_=0)).label("errors"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= two_hours_ago,
            AnalysisResult.created_at < one_hour_ago,
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    prev_result = await session.execute(prev_stmt)
    prev_map = {row[0]: int(row[1] or 0) for row in prev_result.all()}

    # Build service statuses
    all_ids = set(list(biz_map.keys()) + list(curr_map.keys()))
    services: list[ServiceStatus] = []
    anomaly_count = 0

    for sid in all_ids:
        curr = curr_map.get(sid, 0)
        prev = prev_map.get(sid, 0)
        change = ((curr - prev) / max(prev, 1)) * 100 if prev > 0 else (100 if curr > 0 else 0)

        if curr >= 5:
            status = "critical"
            anomaly_count += 1
        elif curr >= 2 or change > 100:
            status = "warning"
            anomaly_count += 1
        else:
            status = "normal"

        services.append(ServiceStatus(
            service_id=sid,
            service_name=biz_map.get(sid, sid[:8] if sid else "unknown"),
            status=status,
            error_count_1h=curr,
            error_rate_change=round(change, 1),
            last_error="",
        ))

    # Sort: critical first, then warning, then normal
    order = {"critical": 0, "warning": 1, "normal": 2}
    services.sort(key=lambda s: (order.get(s.status, 2), -s.error_count_1h))

    patrol_status = (
        "anomaly_detected" if anomaly_count > 0
        else "all_clear"
    )

    return PatrolResponse(
        patrol_status=patrol_status,
        anomaly_count=anomaly_count,
        services=services,
        last_scan=now.isoformat(),
    )
