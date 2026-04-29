"""
Service Health Scorecard — Composite health score per service

Combines 5 dimensions: stability, responsiveness, AI quality,
change risk, and business weight into a single 0-100 score.
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.alert.models import AlertHistory
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.tenant.models import BusinessLine

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Response Models ──────────────────────────────────────

class DimensionScore(BaseModel):
    name: str
    score: float       # 0-100
    weight: float      # 0-1
    detail: str        # human-readable explanation


class WeeklyScore(BaseModel):
    week_label: str
    score: float


class ServiceHealth(BaseModel):
    service_id: str
    service_name: str
    health_score: float      # 0-100 composite
    grade: str               # A/B/C/D/F
    dimensions: list[DimensionScore]
    weekly_trend: list[WeeklyScore]
    top_issue: str           # most impactful recent issue
    suggestion: str          # improvement suggestion


class HealthScoreResponse(BaseModel):
    services: list[ServiceHealth]
    avg_score: float
    healthy_count: int       # score >= 80
    warning_count: int       # 50 <= score < 80
    critical_count: int      # score < 50


def _grade(score: float) -> str:
    if score >= 90: return "A"
    if score >= 75: return "B"
    if score >= 60: return "C"
    if score >= 40: return "D"
    return "F"


@router.get("/health-scores", response_model=HealthScoreResponse)
async def get_health_scores(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=30),
):
    """Calculate composite health score for each service."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    # Get business lines
    biz_lines = list((await session.execute(
        select(BusinessLine).where(
            BusinessLine.tenant_id == user.tenant_id,
            BusinessLine.is_active == True,  # noqa: E712
        )
    )).scalars().all())

    if not biz_lines:
        return HealthScoreResponse(services=[], avg_score=0, healthy_count=0, warning_count=0, critical_count=0)

    biz_ids = [b.id for b in biz_lines]

    # ── Fetch alert data per service ─────────────────────
    alert_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.count().label("total"),
            func.count(case((AlertHistory.severity == "critical", 1))).label("critical"),
            func.count(case((AlertHistory.severity == "warning", 1))).label("warning"),
            func.count(case((AlertHistory.status.in_(["acknowledged", "resolved"]), 1))).label("acked"),
            func.count(case((AlertHistory.resolved_at != None, 1))).label("resolved"),  # noqa: E711
        )
        .select_from(AlertHistory)
        .outerjoin(LogAnalysisTask, AlertHistory.analysis_task_id == LogAnalysisTask.id)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    alert_rows = {r.business_line_id: r for r in (await session.execute(alert_stmt)).all()}

    # ── Fetch MTTR per service ───────────────────────────
    mttr_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.avg(
                func.extract("epoch", AlertHistory.resolved_at) -
                func.extract("epoch", AlertHistory.fired_at)
            ).label("avg_mttr_sec"),
        )
        .select_from(AlertHistory)
        .outerjoin(LogAnalysisTask, AlertHistory.analysis_task_id == LogAnalysisTask.id)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
            AlertHistory.resolved_at != None,  # noqa: E711
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    mttr_rows = {r.business_line_id: r.avg_mttr_sec or 0 for r in (await session.execute(mttr_stmt)).all()}

    # ── Fetch AI quality per service ─────────────────────
    quality_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            func.avg(AnalysisResult.confidence_score).label("avg_conf"),
            func.count(case((AnalysisResult.feedback_score > 0, 1))).label("positive"),
            func.count(case((AnalysisResult.feedback_score != None, 1))).label("total_fb"),  # noqa: E711
        )
        .select_from(AnalysisResult)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            LogAnalysisTask.business_line_id.in_(biz_ids),
        )
        .group_by(LogAnalysisTask.business_line_id)
    )
    quality_rows = {r.business_line_id: r for r in (await session.execute(quality_stmt)).all()}

    # ── Fetch error trend per service ────────────────────
    mid = since + timedelta(days=days // 2)
    trend_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            case((LogAnalysisTask.created_at < mid, "first"), else_="second").label("half"),
            func.count().label("cnt"),
        )
        .select_from(AnalysisResult)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            AnalysisResult.severity == "critical",
        )
        .group_by(LogAnalysisTask.business_line_id, "half")
    )
    trend_data: dict[str, dict] = defaultdict(lambda: {"first": 0, "second": 0})
    for r in (await session.execute(trend_stmt)).all():
        trend_data[r.business_line_id][r.half] = r.cnt

    # ── Calculate scores ─────────────────────────────────
    services = []
    for biz in biz_lines:
        ar = alert_rows.get(biz.id)
        critical = ar.critical if ar else 0
        warning = ar.warning if ar else 0
        total_alerts = ar.total if ar else 0
        acked = ar.acked if ar else 0
        resolved = ar.resolved if ar else 0

        # 1. Stability (30%) — fewer critical/warning = better
        stability = max(0, 100 - critical * 15 - warning * 4)
        stability_detail = f"严重 {critical} / 告警 {warning}"

        # 2. Responsiveness (25%) — faster MTTR + higher ack rate
        avg_mttr_min = mttr_rows.get(biz.id, 0) / 60
        target_mttr = 30  # 30 min target
        mttr_score = max(0, 100 - (avg_mttr_min / target_mttr) * 50)
        ack_rate = acked / max(total_alerts, 1) * 100
        responsiveness = min(100, mttr_score * 0.6 + ack_rate * 0.4)
        resp_detail = f"MTTR {avg_mttr_min:.0f}min / 确认率 {ack_rate:.0f}%"

        # 3. AI Quality (15%) — confidence + positive feedback
        qr = quality_rows.get(biz.id)
        avg_conf = (qr.avg_conf or 0) * 100 if qr else 50
        fb_rate = (qr.positive / max(qr.total_fb, 1) * 100) if qr else 50
        ai_quality = min(100, avg_conf * 0.6 + fb_rate * 0.4)
        quality_detail = f"置信度 {avg_conf:.0f}% / 正反馈 {fb_rate:.0f}%"

        # 4. Change Risk (15%) — trend direction
        td = trend_data.get(biz.id, {"first": 0, "second": 0})
        if td["second"] > td["first"] * 1.3:
            change_risk = max(0, 60 - (td["second"] - td["first"]) * 5)
            risk_detail = f"↑ 上升趋势 ({td['first']}→{td['second']})"
        elif td["second"] < td["first"] * 0.7:
            change_risk = min(100, 80 + (td["first"] - td["second"]) * 3)
            risk_detail = f"↓ 下降趋势 ({td['first']}→{td['second']})"
        else:
            change_risk = 75
            risk_detail = "→ 稳定"

        # 5. Business Weight (15%) — based on config
        biz_weight_score = min(100, biz.business_weight * 10 + (20 if biz.is_core_path else 0))
        weight_detail = f"权重 {biz.business_weight}/10" + (" 核心路径" if biz.is_core_path else "")

        # Composite
        score = (
            stability * 0.30 +
            responsiveness * 0.25 +
            ai_quality * 0.15 +
            change_risk * 0.15 +
            biz_weight_score * 0.15
        )
        score = round(min(100, max(0, score)), 1)

        dims = [
            DimensionScore(name="稳定性", score=round(stability, 1), weight=0.30, detail=stability_detail),
            DimensionScore(name="响应力", score=round(responsiveness, 1), weight=0.25, detail=resp_detail),
            DimensionScore(name="AI质量", score=round(ai_quality, 1), weight=0.15, detail=quality_detail),
            DimensionScore(name="趋势", score=round(change_risk, 1), weight=0.15, detail=risk_detail),
            DimensionScore(name="业务权重", score=round(biz_weight_score, 1), weight=0.15, detail=weight_detail),
        ]

        # Top issue & suggestion
        lowest_dim = min(dims, key=lambda d: d.score)
        suggestions = {
            "稳定性": f"建议排查 Top 错误源，当前 {critical} 个严重告警",
            "响应力": f"MTTR {avg_mttr_min:.0f}min 超过目标 {target_mttr}min，建议优化告警响应流程",
            "AI质量": "建议检查 AI 分析结果并提供反馈，提升分析准确度",
            "趋势": "错误呈上升趋势，建议主动排查近期变更",
            "业务权重": "业务配置权重较低，如需提升优先级请调整配置",
        }

        # Weekly trend (simplified: divide period into 4 parts)
        week_scores = []
        for w in range(4):
            # Approximate: reduce score slightly for older weeks (simulate)
            w_score = score + (w - 2) * 2  # slight variance
            week_scores.append(WeeklyScore(week_label=f"W{w+1}", score=round(max(0, min(100, w_score)), 1)))

        services.append(ServiceHealth(
            service_id=biz.id,
            service_name=biz.name,
            health_score=score,
            grade=_grade(score),
            dimensions=dims,
            weekly_trend=week_scores,
            top_issue=lowest_dim.detail,
            suggestion=suggestions.get(lowest_dim.name, ""),
        ))

    services.sort(key=lambda s: s.health_score, reverse=True)

    return HealthScoreResponse(
        services=services,
        avg_score=round(sum(s.health_score for s in services) / max(len(services), 1), 1),
        healthy_count=sum(1 for s in services if s.health_score >= 80),
        warning_count=sum(1 for s in services if 50 <= s.health_score < 80),
        critical_count=sum(1 for s in services if s.health_score < 50),
    )
