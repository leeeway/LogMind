"""
Weekly Report — AI-powered report generator.

Aggregates weekly data and generates AI summary + action items.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.alert.models import AlertHistory
from logmind.domain.tenant.models import BusinessLine
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
biz_repo = BaseRepository(BusinessLine)


class DailyTrend(BaseModel):
    date: str
    error_count: int
    warning_count: int
    task_count: int


class TopService(BaseModel):
    service_id: str
    service_name: str
    error_count: int
    change_pct: float  # week-over-week change %


class WeeklyReportResponse(BaseModel):
    week_start: str
    week_end: str
    # KPIs
    total_tasks: int
    total_errors: int
    total_warnings: int
    total_alerts: int
    p0_alerts: int
    success_rate: float
    # Trends
    daily_trends: list[DailyTrend]
    # Rankings
    top_services: list[TopService]
    # AI Summary
    ai_summary: str
    action_items: list[str]


@router.get("/weekly-report", response_model=WeeklyReportResponse)
async def get_weekly_report(
    session: DBSession,
    user: CurrentUser,
    week_offset: int = Query(0, ge=-12, le=0, description="0=this week, -1=last week"),
):
    """
    Generate weekly operations report with AI summary.
    """
    # Calculate week boundaries
    today = datetime.now(timezone.utc)
    week_start = today + timedelta(weeks=week_offset)
    week_start = week_start - timedelta(days=week_start.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    # Previous week for comparison
    prev_start = week_start - timedelta(days=7)

    # 1. Task statistics
    task_stmt = (
        select(func.count())
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= week_start,
            LogAnalysisTask.created_at < week_end,
        )
    )
    total_tasks = (await session.execute(task_stmt)).scalar() or 0

    completed_stmt = (
        select(func.count())
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= week_start,
            LogAnalysisTask.created_at < week_end,
            LogAnalysisTask.status == "completed",
        )
    )
    completed = (await session.execute(completed_stmt)).scalar() or 0
    success_rate = round((completed / total_tasks * 100) if total_tasks > 0 else 100, 1)

    # 2. Severity counts
    sev_stmt = (
        select(
            AnalysisResult.severity,
            func.count().label("cnt"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= week_start,
            AnalysisResult.created_at < week_end,
        )
        .group_by(AnalysisResult.severity)
    )
    sev_result = await session.execute(sev_stmt)
    sev_map = {row[0]: int(row[1]) for row in sev_result.all()}
    total_errors = sev_map.get("critical", 0)
    total_warnings = sev_map.get("warning", 0)

    # 3. Alert counts
    alert_stmt = (
        select(func.count())
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= week_start,
            AlertHistory.fired_at < week_end,
        )
    )
    total_alerts = (await session.execute(alert_stmt)).scalar() or 0

    p0_stmt = (
        select(func.count())
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= week_start,
            AlertHistory.fired_at < week_end,
            AlertHistory.severity == "critical",
        )
    )
    p0_alerts = (await session.execute(p0_stmt)).scalar() or 0

    # 4. Daily trends
    trunc_day = func.date_trunc("day", AnalysisResult.created_at)
    trend_stmt = (
        select(
            trunc_day.label("day"),
            func.sum(case((AnalysisResult.severity == "critical", 1), else_=0)).label("errors"),
            func.sum(case((AnalysisResult.severity == "warning", 1), else_=0)).label("warnings"),
            func.count().label("tasks"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= week_start,
            AnalysisResult.created_at < week_end,
        )
        .group_by(trunc_day)
        .order_by(trunc_day)
    )
    trend_result = await session.execute(trend_stmt)
    daily_trends = [
        DailyTrend(
            date=row[0].strftime("%m-%d") if row[0] else "",
            error_count=int(row[1] or 0),
            warning_count=int(row[2] or 0),
            task_count=int(row[3] or 0),
        )
        for row in trend_result.all()
    ]

    # 5. Top services
    biz_lines = await biz_repo.get_all(session, tenant_id=user.tenant_id, limit=100)
    biz_map = {b.id: b.name for b in biz_lines}

    svc_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.sum(case((AnalysisResult.severity == "critical", 1), else_=0)).label("errors"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= week_start,
            AnalysisResult.created_at < week_end,
        )
        .group_by(LogAnalysisTask.business_line_id)
        .order_by(func.sum(case((AnalysisResult.severity == "critical", 1), else_=0)).desc())
        .limit(5)
    )
    svc_result = await session.execute(svc_stmt)

    # Previous week comparison
    prev_svc_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.sum(case((AnalysisResult.severity == "critical", 1), else_=0)).label("errors"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= prev_start,
            AnalysisResult.created_at < week_start,
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    prev_result = await session.execute(prev_svc_stmt)
    prev_map = {row[0]: int(row[1] or 0) for row in prev_result.all()}

    top_services = []
    for row in svc_result.all():
        svc_id = row[0] or ""
        errors = int(row[1] or 0)
        prev_errors = prev_map.get(svc_id, 0)
        change = round(((errors - prev_errors) / max(prev_errors, 1)) * 100, 1)
        top_services.append(TopService(
            service_id=svc_id,
            service_name=biz_map.get(svc_id, svc_id[:8]),
            error_count=errors,
            change_pct=change,
        ))

    # 6. AI Summary (template — production would call LLM)
    trend_desc = "稳定" if total_errors < 5 else "偏高" if total_errors < 20 else "严重"
    top_svc_desc = "、".join([s.service_name for s in top_services[:3]]) if top_services else "无"

    ai_summary = f"""## 本周运维概况

本周共执行 **{total_tasks}** 次分析任务，成功率 **{success_rate}%**。
发现 **{total_errors}** 个严重错误和 **{total_warnings}** 个告警级问题。
触发告警 **{total_alerts}** 条，其中 P0 级 **{p0_alerts}** 条。

### 趋势分析
本周错误趋势{trend_desc}，主要问题集中在 {top_svc_desc} 等服务。
{"⚠️ P0 告警较多，建议重点关注。" if p0_alerts > 2 else "整体运维态势可控。"}
"""

    action_items = []
    for s in top_services[:3]:
        if s.error_count > 0:
            arrow = "↑" if s.change_pct > 0 else "↓" if s.change_pct < 0 else "—"
            action_items.append(
                f"{s.service_name}: {s.error_count} 个错误 ({arrow}{abs(s.change_pct)}%)，建议排查根因"
            )
    if p0_alerts > 0:
        action_items.append(f"本周有 {p0_alerts} 条 P0 告警，建议复盘处置流程")
    if not action_items:
        action_items.append("本周运维状况良好，继续保持 ✅")

    return WeeklyReportResponse(
        week_start=week_start.strftime("%Y-%m-%d"),
        week_end=week_end.strftime("%Y-%m-%d"),
        total_tasks=total_tasks,
        total_errors=total_errors,
        total_warnings=total_warnings,
        total_alerts=total_alerts,
        p0_alerts=p0_alerts,
        success_rate=success_rate,
        daily_trends=daily_trends,
        top_services=top_services,
        ai_summary=ai_summary,
        action_items=action_items,
    )
