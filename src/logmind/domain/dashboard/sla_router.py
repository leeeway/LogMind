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
    # AlertHistory has no business_line_id — join through analysis_task_id
    stmt = (
        select(
            AlertHistory,
            LogAnalysisTask.business_line_id.label("biz_line_id"),
        )
        .outerjoin(
            LogAnalysisTask,
            AlertHistory.analysis_task_id == LogAnalysisTask.id,
        )
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
        )
    )
    result = await session.execute(stmt)
    all_rows = list(result.all())
    all_alerts = [row[0] for row in all_rows]

    # Group alerts by business line (resolved via task join)
    alerts_by_biz: dict[str, list] = {}
    for alert, biz_line_id in all_rows:
        biz_id = biz_line_id or "unknown"
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


# ── Capacity Prediction ──────────────────────────────────


class PredictionItem(BaseModel):
    business_line_id: str
    business_line_name: str
    current_error_rate: float  # errors/hour (last 24h)
    trend_slope: float  # errors/hour change per day
    trend_direction: str  # rising / falling / stable
    budget_exhaustion_eta_hours: float | None  # hours until error budget runs out
    burn_rate: float  # current burn rate multiplier (1x = normal)
    prediction_confidence: float  # 0-1
    suggestion: str


class CapacityPredictionResponse(BaseModel):
    predictions: list[PredictionItem]
    high_risk_count: int


@router.get("/capacity-prediction", response_model=CapacityPredictionResponse)
async def get_capacity_prediction(
    session: DBSession,
    user: CurrentUser,
    lookback_days: int = 7,
):
    """
    Predict error budget exhaustion using linear regression on historical error rates.

    Uses pure-Python least-squares regression (no numpy needed).
    """
    from datetime import timedelta
    from sqlalchemy import select, func, cast, Date

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=lookback_days)

    biz_lines = await biz_repo.get_all(
        session, tenant_id=user.tenant_id, filters={"is_active": True}
    )

    # Get daily alert counts per business line
    # AlertHistory has no business_line_id — join through analysis_task_id
    stmt = (
        select(
            LogAnalysisTask.business_line_id,
            cast(AlertHistory.fired_at, Date).label("day"),
            func.count().label("cnt"),
        )
        .outerjoin(
            LogAnalysisTask,
            AlertHistory.analysis_task_id == LogAnalysisTask.id,
        )
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
        )
        .group_by(LogAnalysisTask.business_line_id, "day")
        .order_by("day")
    )
    result = await session.execute(stmt)
    daily_data = list(result.all())

    # Group by business line
    biz_daily: dict[str, list[tuple[int, int]]] = {}
    for row in daily_data:
        biz_id = row.business_line_id or "unknown"
        day_offset = (row.day - since.date()).days
        biz_daily.setdefault(biz_id, []).append((day_offset, row.cnt))

    predictions = []
    high_risk = 0

    for biz in biz_lines:
        points = biz_daily.get(biz.id, [])

        if len(points) < 2:
            # Not enough data
            predictions.append(PredictionItem(
                business_line_id=biz.id,
                business_line_name=biz.name,
                current_error_rate=points[0][1] / 24 if points else 0,
                trend_slope=0,
                trend_direction="stable",
                budget_exhaustion_eta_hours=None,
                burn_rate=1.0,
                prediction_confidence=0.1,
                suggestion="数据不足，至少需要 2 天历史数据进行预测",
            ))
            continue

        # Pure-Python least-squares linear regression: y = a + b*x
        n = len(points)
        sum_x = sum(p[0] for p in points)
        sum_y = sum(p[1] for p in points)
        sum_xy = sum(p[0] * p[1] for p in points)
        sum_x2 = sum(p[0] ** 2 for p in points)

        denom = n * sum_x2 - sum_x ** 2
        if denom == 0:
            slope = 0
            intercept = sum_y / n
        else:
            slope = (n * sum_xy - sum_x * sum_y) / denom
            intercept = (sum_y - slope * sum_x) / n

        # R² for confidence
        mean_y = sum_y / n
        ss_tot = sum((p[1] - mean_y) ** 2 for p in points) + 0.001
        ss_res = sum((p[1] - (intercept + slope * p[0])) ** 2 for p in points)
        r_squared = max(0, 1 - ss_res / ss_tot)

        # Current rate (last day average, errors/hour)
        last_day_count = points[-1][1] if points else 0
        current_rate = last_day_count / 24

        # Trend direction
        if slope > 0.5:
            direction = "rising"
        elif slope < -0.5:
            direction = "falling"
        else:
            direction = "stable"

        # Error budget: SLA 99.9% → 0.1% budget = 0.024h/day downtime equivalent
        # Map errors to downtime: ~10 errors ≈ 0.1h impact (simplified)
        sla_target = 99.9
        daily_budget_minutes = (100 - sla_target) / 100 * 24 * 60  # 1.44 min/day
        daily_error_budget = daily_budget_minutes * 10  # ~14.4 "errors equivalent" per day

        # Burn rate: how fast are we burning the budget
        burn_rate = last_day_count / daily_error_budget if daily_error_budget > 0 else 0

        # Predict when budget runs out using slope
        eta_hours = None
        if slope > 0 and current_rate > 0:
            # At current trajectory, how many days until daily errors > budget
            days_until_exhaust = max(0, (daily_error_budget - last_day_count) / slope) if slope > 0 else None
            if days_until_exhaust is not None:
                eta_hours = round(days_until_exhaust * 24, 1)
                if eta_hours <= 0:
                    eta_hours = 0  # already exceeded

        # Generate suggestion
        if burn_rate > 2:
            suggestion = f"🔴 高风险: 当前消耗速率 {burn_rate:.1f}x，建议立即排查 top 错误源"
            high_risk += 1
        elif burn_rate > 1:
            suggestion = f"🟡 警告: 错误预算消耗速率 {burn_rate:.1f}x，建议关注错误趋势"
        elif direction == "rising":
            suggestion = f"📈 上升趋势: 每天新增 {slope:.1f} 个错误，建议提前关注"
        elif direction == "falling":
            suggestion = "✅ 下降趋势: 错误率在改善"
        else:
            suggestion = "✅ 稳定: 错误率处于正常范围"

        predictions.append(PredictionItem(
            business_line_id=biz.id,
            business_line_name=biz.name,
            current_error_rate=round(current_rate, 2),
            trend_slope=round(slope, 2),
            trend_direction=direction,
            budget_exhaustion_eta_hours=eta_hours,
            burn_rate=round(burn_rate, 2),
            prediction_confidence=round(r_squared, 2),
            suggestion=suggestion,
        ))

    # Sort by risk (burn rate descending)
    predictions.sort(key=lambda p: p.burn_rate, reverse=True)

    return CapacityPredictionResponse(
        predictions=predictions,
        high_risk_count=high_risk,
    )
