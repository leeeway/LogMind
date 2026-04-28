"""
SLA Monitoring — Dashboard API

Calculates SLA metrics (availability, MTTR, error budget) from existing data.
"""

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import AnalysisResult, LogAnalysisTask
from logmind.domain.alert.models import AlertHistory
from logmind.domain.tenant.models import BusinessLine
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
task_repo = BaseRepository(LogAnalysisTask)
result_repo = BaseRepository(AnalysisResult)
alert_repo = BaseRepository(AlertHistory)
biz_repo = BaseRepository(BusinessLine)


class SLAMetric(BaseModel):
    business_line_id: str
    business_line_name: str
    availability_pct: float  # e.g. 99.95
    mttr_minutes: float  # Mean Time To Resolve
    total_incidents: int
    resolved_incidents: int
    error_budget_consumed_pct: float
    sla_target: float  # e.g. 99.9


class SLAResponse(BaseModel):
    overall_availability: float
    overall_mttr_minutes: float
    total_incidents: int
    total_resolved: int
    by_business_line: list[SLAMetric]


@router.get("/sla", response_model=SLAResponse)
async def get_sla_metrics(
    session: DBSession,
    user: CurrentUser,
    days: int = 7,
):
    """
    Calculate SLA metrics from alert history.

    Availability = (1 - incident_hours / total_hours) * 100
    MTTR = avg(resolved_at - fired_at) for resolved alerts
    Error Budget = consumed % of (100% - SLA target%)
    """
    from datetime import timedelta
    from sqlalchemy import select, func

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    total_hours = days * 24

    # Get all business lines for tenant
    biz_lines = await biz_repo.get_all(
        session, tenant_id=user.tenant_id, filters={"is_active": True}
    )

    # Get alert history for the time window
    stmt = (
        select(AlertHistory)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
        )
    )
    result = await session.execute(stmt)
    all_alerts = list(result.scalars().all())

    # Group alerts by business line
    alerts_by_biz: dict[str, list] = {}
    for alert in all_alerts:
        biz_id = alert.business_line_id or "unknown"
        alerts_by_biz.setdefault(biz_id, []).append(alert)

    metrics = []
    total_incidents = 0
    total_resolved = 0
    total_mttr_sum = 0.0
    total_mttr_count = 0

    for biz in biz_lines:
        alerts = alerts_by_biz.get(biz.id, [])
        incidents = len(alerts)
        resolved = sum(1 for a in alerts if a.status == "resolved")

        # Calculate MTTR for resolved alerts
        mttr_values = []
        for a in alerts:
            if a.resolved_at and a.fired_at:
                delta = (a.resolved_at - a.fired_at).total_seconds() / 60
                mttr_values.append(delta)

        mttr = sum(mttr_values) / len(mttr_values) if mttr_values else 0

        # Estimate incident hours (simplified: each critical = 1h, warning = 0.25h)
        incident_hours = sum(
            1.0 if a.severity == "critical" else 0.25
            for a in alerts
        )
        availability = max(0, (1 - incident_hours / total_hours) * 100) if total_hours > 0 else 100

        sla_target = 99.9
        error_budget_total = (100 - sla_target) / 100 * total_hours * 60  # in minutes
        error_budget_consumed = (incident_hours * 60 / error_budget_total * 100) if error_budget_total > 0 else 0

        metrics.append(SLAMetric(
            business_line_id=biz.id,
            business_line_name=biz.name,
            availability_pct=round(availability, 3),
            mttr_minutes=round(mttr, 1),
            total_incidents=incidents,
            resolved_incidents=resolved,
            error_budget_consumed_pct=round(min(error_budget_consumed, 100), 1),
            sla_target=sla_target,
        ))

        total_incidents += incidents
        total_resolved += resolved
        total_mttr_sum += sum(mttr_values)
        total_mttr_count += len(mttr_values)

    overall_mttr = total_mttr_sum / total_mttr_count if total_mttr_count > 0 else 0
    overall_incident_hours = sum(
        1.0 if a.severity == "critical" else 0.25 for a in all_alerts
    )
    overall_availability = max(0, (1 - overall_incident_hours / total_hours) * 100) if total_hours > 0 else 100

    return SLAResponse(
        overall_availability=round(overall_availability, 3),
        overall_mttr_minutes=round(overall_mttr, 1),
        total_incidents=total_incidents,
        total_resolved=total_resolved,
        by_business_line=metrics,
    )
