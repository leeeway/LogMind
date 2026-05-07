"""
Ops Efficiency Radar — Team productivity metrics & improvement tracking

Measures 6 dimensions of operational efficiency with month-over-month
comparison and auto-generated improvement reports.
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

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Response Models ──────────────────────────────────────

class EfficiencyDimension(BaseModel):
    name: str
    score: float           # 0-100
    previous_score: float  # last period
    change: float          # positive = improving
    detail: str
    icon: str


class WeeklyEfficiency(BaseModel):
    week_label: str
    score: float


class EfficiencyReport(BaseModel):
    highlights: list[str]
    improvements_needed: list[str]
    suggestions: list[str]


class OpsEfficiencyResponse(BaseModel):
    overall_score: float
    overall_previous: float
    overall_change: float
    grade: str
    dimensions: list[EfficiencyDimension]
    weekly_trend: list[WeeklyEfficiency]
    report: EfficiencyReport


async def _calc_period_metrics(session, tenant_id: str, since: datetime, until: datetime) -> dict:
    """Calculate all efficiency metrics for a time period."""
    # Alert metrics
    alert_stmt = (
        select(
            func.count().label("total"),
            func.count(case((AlertHistory.status.in_(["acknowledged", "resolved"]), 1))).label("acked"),
            func.count(case((AlertHistory.resolved_at != None, 1))).label("resolved"),  # noqa: E711
        )
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == tenant_id,
            AlertHistory.fired_at >= since,
            AlertHistory.fired_at < until,
        )
    )
    ar = (await session.execute(alert_stmt)).one()

    # MTTR
    mttr_stmt = (
        select(
            func.avg(
                func.extract("epoch", AlertHistory.resolved_at) -
                func.extract("epoch", AlertHistory.fired_at)
            ).label("avg_mttr"),
        )
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == tenant_id,
            AlertHistory.fired_at >= since,
            AlertHistory.fired_at < until,
            AlertHistory.resolved_at != None,  # noqa: E711
        )
    )
    mttr_row = (await session.execute(mttr_stmt)).one()
    avg_mttr_min = (mttr_row.avg_mttr or 0) / 60

    # Noise ratio (P2 never acked)
    noise_stmt = (
        select(
            func.count().label("total"),
            func.count(case((AlertHistory.status == "fired", 1))).label("unacked"),
        )
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == tenant_id,
            AlertHistory.fired_at >= since,
            AlertHistory.fired_at < until,
        )
    )
    nr = (await session.execute(noise_stmt)).one()
    noise_ratio = (nr.unacked or 0) / max(nr.total or 1, 1) * 100

    # AI utilization
    ai_stmt = (
        select(
            func.count().label("total"),
            func.count(case((LogAnalysisTask.token_usage > 0, 1))).label("ai_used"),
        )
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
            LogAnalysisTask.created_at < until,
        )
    )
    ai_row = (await session.execute(ai_stmt)).one()
    ai_usage_rate = (ai_row.ai_used or 0) / max(ai_row.total or 1, 1) * 100

    # Critical findings with positive feedback
    quality_stmt = (
        select(
            func.count().label("total"),
            func.count(case((AnalysisResult.feedback_score > 0, 1))).label("positive"),
        )
        .select_from(AnalysisResult)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
            LogAnalysisTask.created_at < until,
            AnalysisResult.severity.in_(["critical", "warning"]),
        )
    )
    qr = (await session.execute(quality_stmt)).one()
    finding_quality = (qr.positive or 0) / max(qr.total or 1, 1) * 100

    # Recurrence rate (alerts with same message pattern appearing >3 times)
    total_alerts = ar.total or 0

    return {
        "total_alerts": total_alerts,
        "ack_rate": (ar.acked or 0) / max(total_alerts, 1) * 100,
        "resolve_rate": (ar.resolved or 0) / max(total_alerts, 1) * 100,
        "avg_mttr_min": avg_mttr_min,
        "noise_ratio": noise_ratio,
        "ai_usage_rate": ai_usage_rate,
        "finding_quality": finding_quality,
        "total_findings": qr.total or 0,
    }


@router.get("/ops-efficiency", response_model=OpsEfficiencyResponse)
async def get_ops_efficiency(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(30, ge=7, le=90),
):
    """Calculate 6-dimension operational efficiency score."""
    now = datetime.now(timezone.utc)
    current_since = now - timedelta(days=days)
    prev_since = current_since - timedelta(days=days)

    current = await _calc_period_metrics(session, user.tenant_id, current_since, now)
    previous = await _calc_period_metrics(session, user.tenant_id, prev_since, current_since)

    # ── Dimension scores ─────────────────────────────────

    def _score(name, icon, calc_fn):
        curr_val, prev_val, detail = calc_fn(current, previous)
        return EfficiencyDimension(
            name=name, icon=icon,
            score=round(curr_val, 1),
            previous_score=round(prev_val, 1),
            change=round(curr_val - prev_val, 1),
            detail=detail,
        )

    dimensions = [
        _score("MTTR 趋势", "⏱️", lambda c, p: (
            min(100, max(0, 100 - c["avg_mttr_min"] * 1.5)),
            min(100, max(0, 100 - p["avg_mttr_min"] * 1.5)),
            f"当前 {c['avg_mttr_min']:.0f}min / 上期 {p['avg_mttr_min']:.0f}min",
        )),
        _score("告警质量", "📊", lambda c, p: (
            max(0, 100 - c["noise_ratio"]),
            max(0, 100 - p["noise_ratio"]),
            f"噪音率 {c['noise_ratio']:.0f}% / 上期 {p['noise_ratio']:.0f}%",
        )),
        _score("AI 利用率", "🤖", lambda c, p: (
            min(100, c["ai_usage_rate"]),
            min(100, p["ai_usage_rate"]),
            f"AI 分析占比 {c['ai_usage_rate']:.0f}%",
        )),
        _score("发现质量", "🎯", lambda c, p: (
            min(100, c["finding_quality"] * 1.5 + 30),
            min(100, p["finding_quality"] * 1.5 + 30),
            f"正反馈率 {c['finding_quality']:.0f}% / {c['total_findings']} 个发现",
        )),
        _score("响应覆盖", "🛡️", lambda c, p: (
            (c["ack_rate"] * 0.5 + c["resolve_rate"] * 0.5),
            (p["ack_rate"] * 0.5 + p["resolve_rate"] * 0.5),
            f"确认率 {c['ack_rate']:.0f}% / 解决率 {c['resolve_rate']:.0f}%",
        )),
        _score("告警负载", "📈", lambda c, p: (
            max(0, 100 - c["total_alerts"] * 0.5),
            max(0, 100 - p["total_alerts"] * 0.5),
            f"本期 {c['total_alerts']} 个 / 上期 {p['total_alerts']} 个",
        )),
    ]

    # Composite score
    weights = [0.25, 0.20, 0.10, 0.15, 0.20, 0.10]
    overall = sum(d.score * w for d, w in zip(dimensions, weights))
    overall_prev = sum(d.previous_score * w for d, w in zip(dimensions, weights))

    # Grade
    if overall >= 85: grade = "S"
    elif overall >= 70: grade = "A"
    elif overall >= 55: grade = "B"
    elif overall >= 40: grade = "C"
    else: grade = "D"

    # Weekly trend (approximate by dividing period into weeks)
    week_count = max(1, days // 7)
    weekly_trend = []
    for w in range(week_count):
        w_start = current_since + timedelta(weeks=w)
        w_end = w_start + timedelta(weeks=1)
        if w_end > now:
            w_end = now
        w_metrics = await _calc_period_metrics(session, user.tenant_id, w_start, w_end)
        w_score = (
            min(100, max(0, 100 - w_metrics["avg_mttr_min"] * 1.5)) * 0.25
            + max(0, 100 - w_metrics["noise_ratio"]) * 0.20
            + (w_metrics["ack_rate"] * 0.5 + w_metrics["resolve_rate"] * 0.5) * 0.20
        )
        # Simplified for performance
        weekly_trend.append(WeeklyEfficiency(
            week_label=f"W{w+1}",
            score=round(max(0, min(100, w_score + 30)), 1),
        ))

    # ── Auto-generated report ────────────────────────────
    highlights = []
    improvements = []
    suggestions = []

    for d in dimensions:
        if d.change > 5:
            highlights.append(f"✅ {d.name} 提升 {d.change:.0f} 分: {d.detail}")
        elif d.change < -5:
            improvements.append(f"⚠️ {d.name} 下降 {abs(d.change):.0f} 分: {d.detail}")

    if not highlights:
        highlights.append("📌 各维度保持稳定，无明显变化")

    # Suggestions based on lowest dimensions
    sorted_dims = sorted(dimensions, key=lambda d: d.score)
    for d in sorted_dims[:2]:
        suggestion_map = {
            "MTTR 趋势": "建议优化告警响应流程，引入自动化诊断加速修复",
            "告警质量": "建议梳理低价值告警规则，启用告警疲劳自抑制",
            "AI 利用率": "更多服务开启 AI 分析可提升问题发现效率",
            "发现质量": "鼓励团队对 AI 分析结果提供反馈，帮助模型持续优化",
            "响应覆盖": "告警确认率偏低，建议完善值班响应机制",
            "告警负载": "告警量偏高，建议排查 top 噪音源并优化告警规则",
        }
        suggestions.append(f"💡 {d.name}: {suggestion_map.get(d.name, '持续关注')}")

    return OpsEfficiencyResponse(
        overall_score=round(overall, 1),
        overall_previous=round(overall_prev, 1),
        overall_change=round(overall - overall_prev, 1),
        grade=grade,
        dimensions=dimensions,
        weekly_trend=weekly_trend,
        report=EfficiencyReport(
            highlights=highlights,
            improvements_needed=improvements,
            suggestions=suggestions,
        ),
    )
