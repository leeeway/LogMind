"""
AI Cost Intelligence — Token spending analytics & ROI tracking

Tracks AI token consumption, cost per service, efficiency metrics,
and provides optimization recommendations.
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case, cast, Date

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.tenant.models import BusinessLine

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Response Models ──────────────────────────────────────

class CostByService(BaseModel):
    service: str
    service_id: str
    token_usage: int
    cost_usd: float
    task_count: int
    critical_findings: int
    cost_per_finding: float     # cost / max(findings, 1)
    efficiency_grade: str       # A/B/C/D


class DailyCost(BaseModel):
    date: str
    token_usage: int
    cost_usd: float
    task_count: int


class CostOptimization(BaseModel):
    service: str
    current_cost: float
    finding_rate: float         # findings per $1
    suggestion: str
    potential_saving_pct: float


class CostIntelligenceResponse(BaseModel):
    total_tokens: int
    total_cost_usd: float
    total_tasks: int
    avg_tokens_per_task: int
    avg_cost_per_task: float
    dedup_savings_pct: float    # % of tasks skipped by dedup
    finding_rate: float         # critical findings per 1000 tokens
    daily_trend: list[DailyCost]
    by_service: list[CostByService]
    optimizations: list[CostOptimization]
    roi_summary: str


@router.get("/ai-cost", response_model=CostIntelligenceResponse)
async def get_ai_cost(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(30, ge=7, le=90),
):
    """AI cost intelligence with ROI analysis."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    # ── Total metrics ────────────────────────────────────
    total_stmt = (
        select(
            func.sum(LogAnalysisTask.token_usage).label("tokens"),
            func.sum(LogAnalysisTask.cost_usd).label("cost"),
            func.count().label("total"),
            func.count(case((LogAnalysisTask.status == "skipped_dedup", 1))).label("deduped"),
        )
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
        )
    )
    totals = (await session.execute(total_stmt)).one()
    total_tokens = totals.tokens or 0
    total_cost = totals.cost or 0.0
    total_tasks = totals.total or 0
    deduped = totals.deduped or 0

    avg_tokens = total_tokens // max(total_tasks - deduped, 1)
    avg_cost = total_cost / max(total_tasks - deduped, 1)
    dedup_pct = deduped / max(total_tasks, 1) * 100

    # ── Critical findings count ──────────────────────────
    findings_stmt = (
        select(func.count())
        .select_from(AnalysisResult)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            AnalysisResult.severity == "critical",
        )
    )
    total_findings = (await session.execute(findings_stmt)).scalar() or 0
    finding_rate = total_findings / max(total_tokens, 1) * 1000

    # ── Daily trend ──────────────────────────────────────
    daily_stmt = (
        select(
            cast(LogAnalysisTask.created_at, Date).label("day"),
            func.sum(LogAnalysisTask.token_usage).label("tokens"),
            func.sum(LogAnalysisTask.cost_usd).label("cost"),
            func.count().label("cnt"),
        )
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            LogAnalysisTask.token_usage > 0,
        )
        .group_by("day")
        .order_by("day")
    )
    daily_trend = [
        DailyCost(
            date=str(r.day),
            token_usage=r.tokens or 0,
            cost_usd=round(r.cost or 0, 4),
            task_count=r.cnt,
        )
        for r in (await session.execute(daily_stmt)).all()
    ]

    # ── By service ───────────────────────────────────────
    biz_lines = list((await session.execute(
        select(BusinessLine.id, BusinessLine.name).where(
            BusinessLine.tenant_id == user.tenant_id,
            BusinessLine.is_active == True,  # noqa: E712
        )
    )).all())
    biz_names = {r.id: r.name for r in biz_lines}

    svc_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.sum(LogAnalysisTask.token_usage).label("tokens"),
            func.sum(LogAnalysisTask.cost_usd).label("cost"),
            func.count().label("cnt"),
        )
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            LogAnalysisTask.token_usage > 0,
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    svc_rows = (await session.execute(svc_stmt)).all()

    # Get findings per service
    svc_findings_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.count().label("cnt"),
        )
        .select_from(AnalysisResult)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            AnalysisResult.severity == "critical",
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    svc_findings = {r.business_line_id: r.cnt for r in (await session.execute(svc_findings_stmt)).all()}

    by_service = []
    for r in sorted(svc_rows, key=lambda x: (x.cost or 0), reverse=True):
        svc_id = r.business_line_id or "unknown"
        findings = svc_findings.get(svc_id, 0)
        cost = r.cost or 0
        cpf = cost / max(findings, 1)

        if findings > 0 and cpf < 0.5:
            grade = "A"
        elif findings > 0 and cpf < 2:
            grade = "B"
        elif findings > 0:
            grade = "C"
        else:
            grade = "D"

        by_service.append(CostByService(
            service=biz_names.get(svc_id, svc_id),
            service_id=svc_id,
            token_usage=r.tokens or 0,
            cost_usd=round(cost, 4),
            task_count=r.cnt,
            critical_findings=findings,
            cost_per_finding=round(cpf, 2),
            efficiency_grade=grade,
        ))

    # ── Optimization recommendations ─────────────────────
    optimizations = []
    for svc in by_service:
        if svc.efficiency_grade == "D" and svc.cost_usd > 0.1:
            optimizations.append(CostOptimization(
                service=svc.service,
                current_cost=svc.cost_usd,
                finding_rate=0,
                suggestion=f"🔴 零发现高消耗: {svc.task_count} 次分析无严重发现，建议关闭 AI 或降低巡检频率",
                potential_saving_pct=80,
            ))
        elif svc.efficiency_grade == "C" and svc.cost_usd > 0.5:
            optimizations.append(CostOptimization(
                service=svc.service,
                current_cost=svc.cost_usd,
                finding_rate=svc.critical_findings / max(svc.cost_usd, 0.01),
                suggestion=f"🟡 低效分析: 每 ${svc.cost_per_finding:.1f} 才发现 1 个问题，建议优化采样策略",
                potential_saving_pct=40,
            ))

    # ── ROI summary ──────────────────────────────────────
    estimated_manual_hours = total_findings * 0.5  # 30min per finding if manual
    estimated_manual_cost = estimated_manual_hours * 50  # $50/hr engineer cost
    roi_ratio = estimated_manual_cost / max(total_cost, 0.01)
    roi_summary = (
        f"AI 发现 {total_findings} 个严重问题，估算节省人工排查 {estimated_manual_hours:.0f} 小时 "
        f"(约 ${estimated_manual_cost:.0f})，ROI 约 {roi_ratio:.0f}x"
    )

    return CostIntelligenceResponse(
        total_tokens=total_tokens,
        total_cost_usd=round(total_cost, 2),
        total_tasks=total_tasks,
        avg_tokens_per_task=avg_tokens,
        avg_cost_per_task=round(avg_cost, 4),
        dedup_savings_pct=round(dedup_pct, 1),
        finding_rate=round(finding_rate, 2),
        daily_trend=daily_trend,
        by_service=by_service[:15],
        optimizations=optimizations[:5],
        roi_summary=roi_summary,
    )
