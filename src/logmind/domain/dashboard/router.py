"""
Dashboard Domain — API Router (Enhanced)

Aggregation endpoints for the Dashboard UI.

Endpoints:
  - GET /overview           — High-level KPIs (existing, refactored)
  - GET /trends             — Time-series data for charts (error/token/task trends)
  - GET /business-health    — Per-business-line health scoring
  - GET /cost-analysis      — Token consumption + dedup savings estimation
  - GET /dedup-stats        — Fingerprint / semantic dedup hit rate statistics
  - GET /ai-effectiveness   — AI accuracy trends, MTTR, top error patterns
  - GET /agent-tool-analytics — Agent tool usage ranking & strategy patterns
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import case, cast, func, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.alert.models import AlertHistory
from logmind.domain.analysis.models import AnalysisResult, LogAnalysisTask
from logmind.domain.tenant.models import BusinessLine
from logmind.shared.base_schema import BaseSchema

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Shared Helpers ───────────────────────────────────────

def _build_time_range(days: int) -> tuple[datetime, datetime]:
    """Return (since, now) in UTC."""
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days), now


def _base_task_filter(
    stmt,
    tenant_id: str,
    since: datetime,
    business_line_id: str | None = None,
):
    """Apply standard tenant + time + optional biz_line filters."""
    stmt = stmt.where(
        LogAnalysisTask.tenant_id == tenant_id,
        LogAnalysisTask.created_at >= since,
    )
    if business_line_id:
        stmt = stmt.where(LogAnalysisTask.business_line_id == business_line_id)
    return stmt


async def _count_tasks(
    session: AsyncSession,
    tenant_id: str,
    since: datetime,
    status: str | None = None,
    business_line_id: str | None = None,
) -> int:
    """Count tasks matching criteria."""
    stmt = (
        select(func.count())
        .select_from(LogAnalysisTask)
    )
    stmt = _base_task_filter(stmt, tenant_id, since, business_line_id)
    if status:
        stmt = stmt.where(LogAnalysisTask.status == status)
    result = await session.execute(stmt)
    return result.scalar_one()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Endpoint 1: Overview (existing, refactored with schemas)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SeverityCount(BaseSchema):
    severity: str
    count: int


class RecentTask(BaseSchema):
    id: str
    status: str
    task_type: str
    log_count: int
    token_usage: int
    created_at: str | None


class DashboardOverview(BaseSchema):
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    total_alerts: int
    total_tokens_used: int
    total_business_lines: int
    recent_tasks: list[RecentTask]
    severity_distribution: list[SeverityCount]


@router.get("/overview", response_model=DashboardOverview)
async def get_dashboard_overview(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=90),
):
    """Get dashboard overview statistics for current tenant."""
    tenant_id = user.tenant_id
    since, _ = _build_time_range(days)

    # Task counts — single query with conditional counting
    count_stmt = (
        select(
            func.count().label("total"),
            func.count(case(
                (LogAnalysisTask.status == "completed", 1),
            )).label("completed"),
            func.count(case(
                (LogAnalysisTask.status == "failed", 1),
            )).label("failed"),
            func.coalesce(func.sum(LogAnalysisTask.token_usage), 0).label("tokens"),
        )
        .select_from(LogAnalysisTask)
    )
    count_stmt = _base_task_filter(count_stmt, tenant_id, since)
    counts = (await session.execute(count_stmt)).one()

    # Alert count
    alert_stmt = (
        select(func.count())
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == tenant_id,
            AlertHistory.fired_at >= since,
        )
    )
    total_alerts = (await session.execute(alert_stmt)).scalar_one()

    # Business lines
    biz_stmt = (
        select(func.count())
        .select_from(BusinessLine)
        .where(
            BusinessLine.tenant_id == tenant_id,
            BusinessLine.is_active == True,  # noqa: E712
        )
    )
    total_biz = (await session.execute(biz_stmt)).scalar_one()

    # Recent tasks
    recent_stmt = (
        select(LogAnalysisTask)
        .where(LogAnalysisTask.tenant_id == tenant_id)
        .order_by(LogAnalysisTask.created_at.desc())
        .limit(10)
    )
    recent_result = await session.execute(recent_stmt)
    recent_tasks = [
        RecentTask(
            id=t.id,
            status=t.status,
            task_type=t.task_type,
            log_count=t.log_count or 0,
            token_usage=t.token_usage or 0,
            created_at=t.created_at.isoformat() if t.created_at else None,
        )
        for t in recent_result.scalars().all()
    ]

    # Severity distribution
    sev_stmt = (
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
    sev_rows = (await session.execute(sev_stmt)).all()

    return DashboardOverview(
        total_tasks=counts.total,
        completed_tasks=counts.completed,
        failed_tasks=counts.failed,
        total_alerts=total_alerts,
        total_tokens_used=counts.tokens,
        total_business_lines=total_biz,
        recent_tasks=recent_tasks,
        severity_distribution=[
            SeverityCount(severity=r.severity, count=r.count)
            for r in sev_rows
        ],
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Endpoint 2: Trends (time-series for charts)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TrendDataPoint(BaseSchema):
    """Single data point for time-series charts."""
    period: str = Field(..., description="ISO date or datetime string")
    task_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    log_count: int = 0
    token_usage: int = 0
    alert_count: int = 0


class TrendResponse(BaseSchema):
    granularity: str = Field(..., description="day or hour")
    data: list[TrendDataPoint]
    period_start: str
    period_end: str


@router.get("/trends", response_model=TrendResponse)
async def get_dashboard_trends(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=90),
    granularity: Literal["day", "hour"] = Query("day"),
    business_line_id: str | None = Query(None),
):
    """
    Get time-series trend data for charts.

    Granularity:
      - day: one data point per calendar day (for 7d/30d/90d views)
      - hour: one data point per hour (for 1d/3d views, max 7 days)

    Returns empty data points for periods with no activity to
    ensure smooth chart rendering.
    """
    tenant_id = user.tenant_id
    since, now = _build_time_range(days)

    # Clamp hour granularity to 7 days max to avoid excessive data
    if granularity == "hour" and days > 7:
        granularity = "day"

    # Determine the SQL date truncation
    # Use func.date_trunc for PostgreSQL; fallback to DATE() for MySQL
    if granularity == "hour":
        trunc_expr = func.date_trunc("hour", LogAnalysisTask.created_at)
    else:
        trunc_expr = func.date_trunc("day", LogAnalysisTask.created_at)

    # Task trend query — aggregate by period
    task_trend_stmt = (
        select(
            trunc_expr.label("period"),
            func.count().label("task_count"),
            func.count(case(
                (LogAnalysisTask.status == "completed", 1),
            )).label("completed_count"),
            func.count(case(
                (LogAnalysisTask.status == "failed", 1),
            )).label("failed_count"),
            func.coalesce(func.sum(LogAnalysisTask.log_count), 0).label("log_count"),
            func.coalesce(func.sum(LogAnalysisTask.token_usage), 0).label("token_usage"),
        )
        .select_from(LogAnalysisTask)
        .group_by(trunc_expr)
        .order_by(trunc_expr)
    )
    task_trend_stmt = _base_task_filter(task_trend_stmt, tenant_id, since, business_line_id)
    task_rows = (await session.execute(task_trend_stmt)).all()

    # Alert trend query
    if granularity == "hour":
        alert_trunc = func.date_trunc("hour", AlertHistory.fired_at)
    else:
        alert_trunc = func.date_trunc("day", AlertHistory.fired_at)

    alert_trend_stmt = (
        select(
            alert_trunc.label("period"),
            func.count().label("alert_count"),
        )
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == tenant_id,
            AlertHistory.fired_at >= since,
        )
        .group_by(alert_trunc)
        .order_by(alert_trunc)
    )
    alert_rows = (await session.execute(alert_trend_stmt)).all()
    alert_map: dict[str, int] = {
        r.period.isoformat(): r.alert_count for r in alert_rows
    }

    # Generate complete time-series with zero-fills
    delta = timedelta(hours=1) if granularity == "hour" else timedelta(days=1)
    # Align start to boundary
    if granularity == "hour":
        current = since.replace(minute=0, second=0, microsecond=0)
    else:
        current = since.replace(hour=0, minute=0, second=0, microsecond=0)

    # Build a lookup from DB results
    task_map: dict[str, dict] = {}
    for r in task_rows:
        key = r.period.isoformat()
        task_map[key] = {
            "task_count": r.task_count,
            "completed_count": r.completed_count,
            "failed_count": r.failed_count,
            "log_count": r.log_count,
            "token_usage": r.token_usage,
        }

    data_points: list[TrendDataPoint] = []
    while current <= now:
        key = current.isoformat()
        t = task_map.get(key, {})
        data_points.append(TrendDataPoint(
            period=key,
            task_count=t.get("task_count", 0),
            completed_count=t.get("completed_count", 0),
            failed_count=t.get("failed_count", 0),
            log_count=t.get("log_count", 0),
            token_usage=t.get("token_usage", 0),
            alert_count=alert_map.get(key, 0),
        ))
        current += delta

    return TrendResponse(
        granularity=granularity,
        data=data_points,
        period_start=since.isoformat(),
        period_end=now.isoformat(),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Endpoint 3: Business Health
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BusinessHealthItem(BaseSchema):
    """Health metrics for a single business line."""
    business_line_id: str
    business_line_name: str
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    success_rate: float = Field(0.0, description="0.0-1.0")
    total_logs: int = 0
    total_tokens: int = 0
    critical_count: int = 0
    warning_count: int = 0
    health_score: float = Field(
        100.0,
        description="0-100 health score. Lower = more concerning",
    )
    is_core_path: bool = False
    business_weight: int = 5


class BusinessHealthResponse(BaseSchema):
    items: list[BusinessHealthItem]
    period_days: int


@router.get("/business-health", response_model=BusinessHealthResponse)
async def get_business_health(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=90),
):
    """
    Per-business-line health scoring and ranking.

    Health score (0-100) is calculated as:
      100 - (critical × 15) - (warning × 5) - (failed_rate × 20)

    Lower scores indicate more pressing issues.
    Items are sorted by health_score ascending (worst first).
    """
    tenant_id = user.tenant_id
    since, _ = _build_time_range(days)

    # Get all active business lines for the tenant
    biz_stmt = (
        select(BusinessLine)
        .where(
            BusinessLine.tenant_id == tenant_id,
            BusinessLine.is_active == True,  # noqa: E712
        )
    )
    biz_result = await session.execute(biz_stmt)
    biz_lines = biz_result.scalars().all()

    if not biz_lines:
        return BusinessHealthResponse(items=[], period_days=days)

    # Aggregate task stats per business line in a single query
    task_stats_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.count().label("total"),
            func.count(case(
                (LogAnalysisTask.status == "completed", 1),
            )).label("completed"),
            func.count(case(
                (LogAnalysisTask.status == "failed", 1),
            )).label("failed"),
            func.coalesce(func.sum(LogAnalysisTask.log_count), 0).label("logs"),
            func.coalesce(func.sum(LogAnalysisTask.token_usage), 0).label("tokens"),
        )
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    task_stats_rows = (await session.execute(task_stats_stmt)).all()
    task_stats_map: dict[str, dict] = {
        r.business_line_id: {
            "total": r.total,
            "completed": r.completed,
            "failed": r.failed,
            "logs": r.logs,
            "tokens": r.tokens,
        }
        for r in task_stats_rows
    }

    # Aggregate severity counts per business line
    sev_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            AnalysisResult.severity,
            func.count().label("count"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
            AnalysisResult.severity.in_(["critical", "warning"]),
        )
        .group_by(LogAnalysisTask.business_line_id, AnalysisResult.severity)
    )
    sev_rows = (await session.execute(sev_stmt)).all()
    sev_map: dict[str, dict[str, int]] = {}
    for r in sev_rows:
        biz_id = r.business_line_id
        if biz_id not in sev_map:
            sev_map[biz_id] = {"critical": 0, "warning": 0}
        sev_map[biz_id][r.severity] = r.count

    # Build response items
    items: list[BusinessHealthItem] = []
    for biz in biz_lines:
        stats = task_stats_map.get(biz.id, {})
        total = stats.get("total", 0)
        completed = stats.get("completed", 0)
        failed = stats.get("failed", 0)
        success_rate = completed / max(total, 1)

        sevs = sev_map.get(biz.id, {})
        critical = sevs.get("critical", 0)
        warning = sevs.get("warning", 0)

        # Health score: 100 - penalties
        failed_rate = failed / max(total, 1)
        health_score = max(0.0, min(100.0,
            100.0
            - (critical * 15)
            - (warning * 5)
            - (failed_rate * 20)
        ))

        items.append(BusinessHealthItem(
            business_line_id=biz.id,
            business_line_name=biz.name,
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            success_rate=round(success_rate, 4),
            total_logs=stats.get("logs", 0),
            total_tokens=stats.get("tokens", 0),
            critical_count=critical,
            warning_count=warning,
            health_score=round(health_score, 1),
            is_core_path=biz.is_core_path,
            business_weight=biz.business_weight,
        ))

    # Sort by health_score ascending (worst first)
    items.sort(key=lambda x: x.health_score)

    return BusinessHealthResponse(items=items, period_days=days)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Endpoint 4: Cost Analysis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CostByBusinessLine(BaseSchema):
    business_line_id: str
    business_line_name: str
    tokens_used: int
    task_count: int
    avg_tokens_per_task: int


class CostByTaskType(BaseSchema):
    task_type: str
    tokens_used: int
    task_count: int


class DedupSavings(BaseSchema):
    """Estimated savings from various dedup layers."""
    quality_filtered_tasks: int = Field(
        0, description="Tasks skipped due to log quality filter (INFO/noise)"
    )
    fingerprint_skipped_tasks: int = Field(
        0, description="Tasks skipped due to MD5 fingerprint dedup"
    )
    semantic_dedup_tasks: int = Field(
        0, description="Tasks completed with token_usage=0 (semantic dedup hit)"
    )
    total_dedup_tasks: int = 0
    avg_tokens_per_ai_task: int = Field(
        0, description="Average tokens for tasks that actually used AI"
    )
    estimated_tokens_saved: int = Field(
        0, description="total_dedup_tasks × avg_tokens_per_ai_task"
    )
    savings_percentage: float = Field(
        0.0, description="Percentage of tokens saved vs hypothetical total"
    )


class CostAnalysisResponse(BaseSchema):
    total_tokens: int
    total_tasks: int
    ai_tasks: int = Field(0, description="Tasks that consumed tokens")
    avg_tokens_per_task: int
    by_business_line: list[CostByBusinessLine]
    by_task_type: list[CostByTaskType]
    dedup_savings: DedupSavings
    period_days: int


@router.get("/cost-analysis", response_model=CostAnalysisResponse)
async def get_cost_analysis(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=90),
):
    """
    Token consumption breakdown and dedup savings estimation.

    Dedup savings are estimated by:
      1. Counting tasks that were completed with token_usage=0
         (quality filter, fingerprint dedup, semantic dedup)
      2. Multiplying by average tokens per AI task
    """
    tenant_id = user.tenant_id
    since, _ = _build_time_range(days)

    # Overall aggregation
    overall_stmt = (
        select(
            func.count().label("total_tasks"),
            func.coalesce(func.sum(LogAnalysisTask.token_usage), 0).label("total_tokens"),
            # Tasks that actually consumed tokens (AI inference ran)
            func.count(case(
                (LogAnalysisTask.token_usage > 0, 1),
            )).label("ai_tasks"),
            # Tasks where AI was skipped due to various dedup layers
            func.count(case(
                (
                    (LogAnalysisTask.status == "completed") &
                    (LogAnalysisTask.token_usage == 0) &
                    LogAnalysisTask.error_message.like("%质量过滤%"),
                    1,
                ),
            )).label("quality_filtered"),
            func.count(case(
                (
                    (LogAnalysisTask.status == "completed") &
                    (LogAnalysisTask.token_usage == 0) &
                    LogAnalysisTask.error_message.like("%指纹去重%"),
                    1,
                ),
            )).label("fingerprint_skipped"),
            # Semantic dedup: completed with 0 tokens, NOT quality/fingerprint
            # Use coalesce() for NULL-safety: error_message may be NULL for
            # semantic dedup hits (no error occurred, just reused conclusions)
            func.count(case(
                (
                    (LogAnalysisTask.status == "completed") &
                    (LogAnalysisTask.token_usage == 0) &
                    ~func.coalesce(LogAnalysisTask.error_message, "").like("%质量过滤%") &
                    ~func.coalesce(LogAnalysisTask.error_message, "").like("%指纹去重%"),
                    1,
                ),
            )).label("semantic_dedup"),
        )
        .select_from(LogAnalysisTask)
    )
    overall_stmt = _base_task_filter(overall_stmt, tenant_id, since)
    overall = (await session.execute(overall_stmt)).one()

    total_tokens = overall.total_tokens
    total_tasks = overall.total_tasks
    ai_tasks = overall.ai_tasks
    avg_per_task = total_tokens // max(ai_tasks, 1)

    # Dedup savings estimation
    quality_filtered = overall.quality_filtered
    fingerprint_skipped = overall.fingerprint_skipped
    # For semantic dedup: tasks completed with 0 tokens that are NOT quality/fingerprint
    # This requires error_message to NOT contain the other patterns, or be NULL
    # We handle the edge case where error_message might be NULL
    semantic_dedup = overall.semantic_dedup

    total_dedup = quality_filtered + fingerprint_skipped + semantic_dedup
    estimated_saved = total_dedup * avg_per_task
    hypothetical_total = total_tokens + estimated_saved
    savings_pct = (estimated_saved / max(hypothetical_total, 1)) * 100

    # By business line
    biz_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.coalesce(func.sum(LogAnalysisTask.token_usage), 0).label("tokens"),
            func.count().label("count"),
        )
        .select_from(LogAnalysisTask)
        .group_by(LogAnalysisTask.business_line_id)
    )
    biz_stmt = _base_task_filter(biz_stmt, tenant_id, since)
    biz_rows = (await session.execute(biz_stmt)).all()

    # Resolve business line names
    biz_name_map = await _get_biz_name_map(session, tenant_id)

    by_biz = [
        CostByBusinessLine(
            business_line_id=r.business_line_id,
            business_line_name=biz_name_map.get(r.business_line_id, r.business_line_id[:8]),
            tokens_used=r.tokens,
            task_count=r.count,
            avg_tokens_per_task=r.tokens // max(r.count, 1),
        )
        for r in biz_rows
    ]
    by_biz.sort(key=lambda x: x.tokens_used, reverse=True)

    # By task type
    type_stmt = (
        select(
            LogAnalysisTask.task_type,
            func.coalesce(func.sum(LogAnalysisTask.token_usage), 0).label("tokens"),
            func.count().label("count"),
        )
        .select_from(LogAnalysisTask)
        .group_by(LogAnalysisTask.task_type)
    )
    type_stmt = _base_task_filter(type_stmt, tenant_id, since)
    type_rows = (await session.execute(type_stmt)).all()
    by_type = [
        CostByTaskType(
            task_type=r.task_type,
            tokens_used=r.tokens,
            task_count=r.count,
        )
        for r in type_rows
    ]

    return CostAnalysisResponse(
        total_tokens=total_tokens,
        total_tasks=total_tasks,
        ai_tasks=ai_tasks,
        avg_tokens_per_task=avg_per_task,
        by_business_line=by_biz,
        by_task_type=by_type,
        dedup_savings=DedupSavings(
            quality_filtered_tasks=quality_filtered,
            fingerprint_skipped_tasks=fingerprint_skipped,
            semantic_dedup_tasks=semantic_dedup,
            total_dedup_tasks=total_dedup,
            avg_tokens_per_ai_task=avg_per_task,
            estimated_tokens_saved=estimated_saved,
            savings_percentage=round(savings_pct, 1),
        ),
        period_days=days,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Endpoint 5: Dedup Statistics (detailed)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DedupDayBreakdown(BaseSchema):
    """Per-day dedup statistics."""
    date: str
    total_tasks: int = 0
    ai_inferred: int = 0
    quality_filtered: int = 0
    fingerprint_skipped: int = 0
    semantic_dedup_hit: int = 0


class FeedbackStats(BaseSchema):
    """Feedback loop statistics."""
    total_results: int = 0
    positive_feedback: int = 0
    negative_feedback: int = 0
    no_feedback: int = 0
    feedback_rate: float = Field(0.0, description="0.0-1.0")


class DedupStatsResponse(BaseSchema):
    overall_dedup_rate: float = Field(
        0.0, description="Percentage of tasks that skipped AI (0-100)"
    )
    daily_breakdown: list[DedupDayBreakdown]
    feedback: FeedbackStats
    period_days: int


@router.get("/dedup-stats", response_model=DedupStatsResponse)
async def get_dedup_stats(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=90),
):
    """
    Detailed dedup hit rate statistics with daily breakdown.

    Provides visibility into how much AI inference is being saved by
    the 4-layer cost control system:
      Layer 0: Log quality filter (INFO/noise removal)
      Layer 1: Error fingerprint (MD5 dedup, Redis)
      Layer 2: Semantic dedup (vector KNN, ES)
      Layer 3: Agent memory (search_similar_incidents tool)
    """
    tenant_id = user.tenant_id
    since, now = _build_time_range(days)

    # Daily breakdown — aggregate by day
    day_trunc = func.date_trunc("day", LogAnalysisTask.created_at)

    daily_stmt = (
        select(
            day_trunc.label("period"),
            func.count().label("total"),
            func.count(case(
                (LogAnalysisTask.token_usage > 0, 1),
            )).label("ai_inferred"),
            func.count(case(
                (
                    (LogAnalysisTask.status == "completed") &
                    (LogAnalysisTask.token_usage == 0) &
                    LogAnalysisTask.error_message.like("%质量过滤%"),
                    1,
                ),
            )).label("quality_filtered"),
            func.count(case(
                (
                    (LogAnalysisTask.status == "completed") &
                    (LogAnalysisTask.token_usage == 0) &
                    LogAnalysisTask.error_message.like("%指纹去重%"),
                    1,
                ),
            )).label("fingerprint_skipped"),
            # Semantic dedup: completed with 0 tokens, NOT quality/fingerprint
            # Use coalesce() for NULL-safety: error_message may be NULL for
            # semantic dedup hits (no error occurred, just reused conclusions)
            func.count(case(
                (
                    (LogAnalysisTask.status == "completed") &
                    (LogAnalysisTask.token_usage == 0) &
                    ~func.coalesce(LogAnalysisTask.error_message, "").like("%质量过滤%") &
                    ~func.coalesce(LogAnalysisTask.error_message, "").like("%指纹去重%"),
                    1,
                ),
            )).label("semantic_dedup"),
        )
        .select_from(LogAnalysisTask)
        .group_by(day_trunc)
        .order_by(day_trunc)
    )
    daily_stmt = _base_task_filter(daily_stmt, tenant_id, since)
    daily_rows = (await session.execute(daily_stmt)).all()

    # Build daily breakdown with zero-fill
    daily_map: dict[str, dict] = {}
    total_all = 0
    total_dedup = 0
    for r in daily_rows:
        key = r.period.strftime("%Y-%m-%d")
        dedup_count = r.quality_filtered + r.fingerprint_skipped + r.semantic_dedup
        daily_map[key] = {
            "total": r.total,
            "ai_inferred": r.ai_inferred,
            "quality_filtered": r.quality_filtered,
            "fingerprint_skipped": r.fingerprint_skipped,
            "semantic_dedup": r.semantic_dedup,
        }
        total_all += r.total
        total_dedup += dedup_count

    daily_breakdown: list[DedupDayBreakdown] = []
    current = since.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= now:
        key = current.strftime("%Y-%m-%d")
        d = daily_map.get(key, {})
        daily_breakdown.append(DedupDayBreakdown(
            date=key,
            total_tasks=d.get("total", 0),
            ai_inferred=d.get("ai_inferred", 0),
            quality_filtered=d.get("quality_filtered", 0),
            fingerprint_skipped=d.get("fingerprint_skipped", 0),
            semantic_dedup_hit=d.get("semantic_dedup", 0),
        ))
        current += timedelta(days=1)

    overall_rate = (total_dedup / max(total_all, 1)) * 100

    # Feedback statistics
    fb_stmt = (
        select(
            func.count().label("total"),
            func.count(case(
                (AnalysisResult.feedback_score == 1, 1),
            )).label("positive"),
            func.count(case(
                (AnalysisResult.feedback_score == -1, 1),
            )).label("negative"),
            func.count(case(
                (AnalysisResult.feedback_score.is_(None), 1),
            )).label("no_feedback"),
        )
        .select_from(AnalysisResult)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
        )
    )
    fb = (await session.execute(fb_stmt)).one()
    fb_total = fb.total
    fb_with = fb.positive + fb.negative
    feedback = FeedbackStats(
        total_results=fb_total,
        positive_feedback=fb.positive,
        negative_feedback=fb.negative,
        no_feedback=fb.no_feedback,
        feedback_rate=round(fb_with / max(fb_total, 1), 4),
    )

    return DedupStatsResponse(
        overall_dedup_rate=round(overall_rate, 1),
        daily_breakdown=daily_breakdown,
        feedback=feedback,
        period_days=days,
    )


# ── Shared Helpers ───────────────────────────────────────

async def _get_biz_name_map(session: AsyncSession, tenant_id: str) -> dict[str, str]:
    """Get {business_line_id: name} map for tenant."""
    stmt = (
        select(BusinessLine.id, BusinessLine.name)
        .where(BusinessLine.tenant_id == tenant_id)
    )
    result = await session.execute(stmt)
    return {r.id: r.name for r in result.all()}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Endpoint 6: AI Effectiveness — Accuracy & MTTR Tracking
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AccuracyTrend(BaseSchema):
    """Per-day AI accuracy based on user feedback."""
    date: str
    total_results: int = 0
    positive: int = 0
    negative: int = 0
    accuracy_rate: float = Field(0.0, description="0.0-1.0, positive / (positive + negative)")


class TopErrorPattern(BaseSchema):
    """Most frequent error patterns."""
    pattern: str = Field(..., description="Error type/exception class or first line summary")
    count: int
    avg_confidence: float = 0.0
    last_seen: str | None = None


class AIEffectivenessResponse(BaseSchema):
    # Overall accuracy
    total_feedback_results: int = 0
    positive_count: int = 0
    negative_count: int = 0
    overall_accuracy: float = Field(0.0, description="0.0-1.0")
    # Accuracy trend
    accuracy_trend: list[AccuracyTrend]
    # MTTR (Mean Time To Resolution) — avg time from task creation to completion
    avg_mttr_minutes: float = Field(0.0, description="Average minutes from create to complete")
    # Token efficiency
    avg_tokens_per_result: int = 0
    # Dedup savings summary
    total_tokens_saved: int = 0
    savings_percentage: float = 0.0
    # Top error patterns
    top_error_patterns: list[TopErrorPattern]
    period_days: int


@router.get("/ai-effectiveness", response_model=AIEffectivenessResponse)
async def get_ai_effectiveness(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=90),
    business_line_id: str | None = Query(None),
):
    """
    AI analysis effectiveness tracking dashboard.

    Metrics:
      - Accuracy trend: based on operator +1/-1 feedback
      - MTTR: average time from task creation to completion
      - Token efficiency: average tokens per useful result
      - Top error patterns: most frequently analyzed errors
    """
    tenant_id = user.tenant_id
    since, now = _build_time_range(days)

    # ── 1. Feedback accuracy ─────────────────────────────
    # Daily accuracy trend
    day_trunc = func.date_trunc("day", LogAnalysisTask.created_at)
    accuracy_stmt = (
        select(
            day_trunc.label("period"),
            func.count().label("total"),
            func.count(case(
                (AnalysisResult.feedback_score == 1, 1),
            )).label("positive"),
            func.count(case(
                (AnalysisResult.feedback_score == -1, 1),
            )).label("negative"),
        )
        .select_from(AnalysisResult)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
        )
        .group_by(day_trunc)
        .order_by(day_trunc)
    )
    if business_line_id:
        accuracy_stmt = accuracy_stmt.where(
            LogAnalysisTask.business_line_id == business_line_id
        )
    accuracy_rows = (await session.execute(accuracy_stmt)).all()

    # Build trend with zero-fill
    acc_map: dict[str, dict] = {}
    total_pos, total_neg, total_fb = 0, 0, 0
    for r in accuracy_rows:
        key = r.period.strftime("%Y-%m-%d")
        with_fb = r.positive + r.negative
        acc_map[key] = {
            "total": r.total,
            "positive": r.positive,
            "negative": r.negative,
            "rate": r.positive / max(with_fb, 1) if with_fb > 0 else 0.0,
        }
        total_pos += r.positive
        total_neg += r.negative
        total_fb += r.total

    accuracy_trend: list[AccuracyTrend] = []
    current = since.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= now:
        key = current.strftime("%Y-%m-%d")
        d = acc_map.get(key, {})
        accuracy_trend.append(AccuracyTrend(
            date=key,
            total_results=d.get("total", 0),
            positive=d.get("positive", 0),
            negative=d.get("negative", 0),
            accuracy_rate=round(d.get("rate", 0.0), 4),
        ))
        current += timedelta(days=1)

    with_feedback = total_pos + total_neg
    overall_accuracy = total_pos / max(with_feedback, 1) if with_feedback > 0 else 0.0

    # ── 2. MTTR ──────────────────────────────────────────
    mttr_stmt = (
        select(
            func.avg(
                func.extract("epoch", LogAnalysisTask.completed_at)
                - func.extract("epoch", LogAnalysisTask.created_at)
            ).label("avg_seconds"),
        )
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
            LogAnalysisTask.status == "completed",
            LogAnalysisTask.completed_at.isnot(None),
        )
    )
    if business_line_id:
        mttr_stmt = mttr_stmt.where(
            LogAnalysisTask.business_line_id == business_line_id
        )
    mttr_result = (await session.execute(mttr_stmt)).scalar_one_or_none()
    avg_mttr_minutes = round((mttr_result or 0) / 60, 1)

    # ── 3. Token efficiency ──────────────────────────────
    token_stmt = (
        select(
            func.coalesce(func.sum(LogAnalysisTask.token_usage), 0).label("total_tokens"),
            func.count(case(
                (LogAnalysisTask.token_usage > 0, 1),
            )).label("ai_tasks"),
            func.count(case(
                ((LogAnalysisTask.status == "completed") &
                 (LogAnalysisTask.token_usage == 0), 1),
            )).label("dedup_tasks"),
        )
        .select_from(LogAnalysisTask)
    )
    token_stmt = _base_task_filter(token_stmt, tenant_id, since, business_line_id)
    token_row = (await session.execute(token_stmt)).one()
    avg_tokens = token_row.total_tokens // max(token_row.ai_tasks, 1)
    estimated_saved = token_row.dedup_tasks * avg_tokens
    hypothetical = token_row.total_tokens + estimated_saved
    savings_pct = (estimated_saved / max(hypothetical, 1)) * 100

    # ── 4. Top error patterns ────────────────────────────
    # Extract from AnalysisResult — group by first 120 chars of content
    pattern_stmt = (
        select(
            func.substring(AnalysisResult.content, 1, 120).label("pattern"),
            func.count().label("count"),
            func.avg(AnalysisResult.confidence_score).label("avg_confidence"),
            func.max(AnalysisResult.created_at).label("last_seen"),
        )
        .select_from(AnalysisResult)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
            AnalysisResult.severity.in_(["critical", "warning"]),
        )
        .group_by(func.substring(AnalysisResult.content, 1, 120))
        .order_by(func.count().desc())
        .limit(10)
    )
    if business_line_id:
        pattern_stmt = pattern_stmt.where(
            LogAnalysisTask.business_line_id == business_line_id
        )
    pattern_rows = (await session.execute(pattern_stmt)).all()

    top_patterns = [
        TopErrorPattern(
            pattern=r.pattern,
            count=r.count,
            avg_confidence=round(float(r.avg_confidence or 0), 3),
            last_seen=r.last_seen.isoformat() if r.last_seen else None,
        )
        for r in pattern_rows
    ]

    return AIEffectivenessResponse(
        total_feedback_results=total_fb,
        positive_count=total_pos,
        negative_count=total_neg,
        overall_accuracy=round(overall_accuracy, 4),
        accuracy_trend=accuracy_trend,
        avg_mttr_minutes=avg_mttr_minutes,
        avg_tokens_per_result=avg_tokens,
        total_tokens_saved=estimated_saved,
        savings_percentage=round(savings_pct, 1),
        top_error_patterns=top_patterns,
        period_days=days,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Endpoint 7: Agent Tool Analytics — Tool Usage & Strategy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ToolUsageRank(BaseSchema):
    """Usage statistics for a single agent tool."""
    tool_name: str
    call_count: int
    success_count: int
    failure_count: int
    success_rate: float = Field(0.0, description="0.0-1.0")
    avg_duration_ms: int = 0
    avg_result_length: int = 0


class ToolChainPattern(BaseSchema):
    """
    A common tool invocation sequence observed across tasks.

    Correlates tool chains with analysis quality to identify
    which sequences produce the best results.
    """
    chain: str = Field(
        ..., description="Tool sequence, e.g. 'search_logs → count_error_patterns → get_log_context'"
    )
    occurrence_count: int
    avg_quality_score: float = Field(
        0.0, description="Average quality grade: high=3, medium=2, low=1"
    )
    avg_steps: float = 0.0


class AgentToolAnalyticsResponse(BaseSchema):
    total_tool_calls: int = 0
    total_tasks_with_agent: int = 0
    avg_tools_per_task: float = 0.0
    avg_steps_per_task: float = 0.0
    # Tool usage ranking
    tool_usage: list[ToolUsageRank]
    # Common tool chain patterns
    tool_chains: list[ToolChainPattern]
    period_days: int


@router.get("/agent-tool-analytics", response_model=AgentToolAnalyticsResponse)
async def get_agent_tool_analytics(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=90),
    business_line_id: str | None = Query(None),
):
    """
    Agent tool usage analytics and strategy optimization insights.

    Mines AgentToolCall records to:
      1. Rank tools by usage frequency and success rate
      2. Identify common tool chain patterns (sequences)
      3. Correlate tool chains with analysis quality
      4. Surface the most effective investigation strategies

    Use this to understand how the AI agent investigates errors
    and which strategies produce the highest quality analyses.
    """
    from logmind.domain.analysis.models import AgentToolCall

    tenant_id = user.tenant_id
    since, _ = _build_time_range(days)

    # ── 1. Tool usage ranking ────────────────────────────
    usage_stmt = (
        select(
            AgentToolCall.tool_name,
            func.count().label("call_count"),
            func.count(case(
                (AgentToolCall.success == True, 1),  # noqa: E712
            )).label("success_count"),
            func.count(case(
                (AgentToolCall.success == False, 1),  # noqa: E712
            )).label("failure_count"),
            func.avg(AgentToolCall.duration_ms).label("avg_duration"),
            func.avg(AgentToolCall.result_length).label("avg_result_len"),
        )
        .select_from(AgentToolCall)
        .join(LogAnalysisTask, AgentToolCall.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
        )
        .group_by(AgentToolCall.tool_name)
        .order_by(func.count().desc())
    )
    if business_line_id:
        usage_stmt = usage_stmt.where(
            LogAnalysisTask.business_line_id == business_line_id
        )
    usage_rows = (await session.execute(usage_stmt)).all()

    tool_usage = [
        ToolUsageRank(
            tool_name=r.tool_name,
            call_count=r.call_count,
            success_count=r.success_count,
            failure_count=r.failure_count,
            success_rate=round(r.success_count / max(r.call_count, 1), 4),
            avg_duration_ms=int(r.avg_duration or 0),
            avg_result_length=int(r.avg_result_len or 0),
        )
        for r in usage_rows
    ]
    total_calls = sum(t.call_count for t in tool_usage)

    # ── 2. Per-task tool chains ──────────────────────────
    # Get all tool calls ordered by (task_id, step)
    chain_stmt = (
        select(
            AgentToolCall.task_id,
            AgentToolCall.step,
            AgentToolCall.tool_name,
        )
        .join(LogAnalysisTask, AgentToolCall.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
        )
        .order_by(AgentToolCall.task_id, AgentToolCall.step)
    )
    if business_line_id:
        chain_stmt = chain_stmt.where(
            LogAnalysisTask.business_line_id == business_line_id
        )
    chain_rows = (await session.execute(chain_stmt)).all()

    # Group by task_id → build chain string
    from collections import defaultdict
    task_chains: dict[str, list[str]] = defaultdict(list)
    for r in chain_rows:
        task_chains[r.task_id].append(r.tool_name)

    total_tasks_with_agent = len(task_chains)
    avg_tools = total_calls / max(total_tasks_with_agent, 1)
    avg_steps = (
        sum(len(c) for c in task_chains.values()) / max(total_tasks_with_agent, 1)
    )

    # Count chain patterns and correlate with quality
    # Get quality scores for tasks that have agent tool calls
    quality_map: dict[str, float] = {}
    if task_chains:
        task_ids = list(task_chains.keys())
        # Batch fetch quality (approximate by avg confidence of results)
        quality_stmt = (
            select(
                AnalysisResult.task_id,
                func.avg(AnalysisResult.confidence_score).label("avg_conf"),
            )
            .where(AnalysisResult.task_id.in_(task_ids))
            .group_by(AnalysisResult.task_id)
        )
        quality_rows = (await session.execute(quality_stmt)).all()
        for r in quality_rows:
            # Map confidence to quality grade: >=0.8=high(3), >=0.5=med(2), else low(1)
            conf = float(r.avg_conf or 0)
            if conf >= 0.8:
                quality_map[r.task_id] = 3.0
            elif conf >= 0.5:
                quality_map[r.task_id] = 2.0
            else:
                quality_map[r.task_id] = 1.0

    chain_counter: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "quality_sum": 0.0, "step_sum": 0,
    })
    for task_id, tools in task_chains.items():
        chain_key = " → ".join(tools)
        chain_counter[chain_key]["count"] += 1
        chain_counter[chain_key]["quality_sum"] += quality_map.get(task_id, 2.0)
        chain_counter[chain_key]["step_sum"] += len(tools)

    # Sort by frequency, take top 10
    sorted_chains = sorted(
        chain_counter.items(), key=lambda x: x[1]["count"], reverse=True
    )[:10]

    tool_chains = [
        ToolChainPattern(
            chain=chain_key,
            occurrence_count=data["count"],
            avg_quality_score=round(data["quality_sum"] / max(data["count"], 1), 2),
            avg_steps=round(data["step_sum"] / max(data["count"], 1), 1),
        )
        for chain_key, data in sorted_chains
    ]

    return AgentToolAnalyticsResponse(
        total_tool_calls=total_calls,
        total_tasks_with_agent=total_tasks_with_agent,
        avg_tools_per_task=round(avg_tools, 1),
        avg_steps_per_task=round(avg_steps, 1),
        tool_usage=tool_usage,
        tool_chains=tool_chains,
        period_days=days,
    )

