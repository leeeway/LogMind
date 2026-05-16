"""
Ops Efficiency Radar — Team productivity metrics & improvement tracking

Measures 6 dimensions of operational efficiency with month-over-month
comparison and auto-generated improvement reports.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.alert.models import AlertHistory
from logmind.domain.analysis.models import AnalysisResult, LogAnalysisTask

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
    action_items: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    north_star: str = ""


class OpsEfficiencyResponse(BaseModel):
    overall_score: float
    overall_previous: float
    overall_change: float
    grade: str
    dimensions: list[EfficiencyDimension]
    weekly_trend: list[WeeklyEfficiency]
    report: EfficiencyReport


def _duration_minutes(start: datetime | None, end: datetime | None) -> float | None:
    """Return a non-negative minute duration for DB datetimes."""
    if start is None or end is None:
        return None

    if start.tzinfo is None and end.tzinfo is not None:
        start = start.replace(tzinfo=end.tzinfo)
    elif start.tzinfo is not None and end.tzinfo is None:
        end = end.replace(tzinfo=start.tzinfo)

    minutes = (end - start).total_seconds() / 60
    return max(0.0, minutes)


async def _calc_period_metrics(session, tenant_id: str, since: datetime, until: datetime) -> dict:
    """Calculate all efficiency metrics for a time period."""
    # Keep the aggregation in Python instead of dialect-specific SQL expressions.
    # This endpoint is shown on the exec dashboard, so graceful cross-database behavior
    # matters more than shaving a few milliseconds from small dashboard windows.
    alert_stmt = (
        select(
            AlertHistory.status,
            AlertHistory.severity,
            AlertHistory.priority,
            AlertHistory.message,
            AlertHistory.fired_at,
            AlertHistory.acked_at,
            AlertHistory.resolved_at,
        )
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == tenant_id,
            AlertHistory.fired_at >= since,
            AlertHistory.fired_at < until,
        )
    )
    alert_rows = (await session.execute(alert_stmt)).all()
    total_alerts = len(alert_rows)
    acked_alerts = sum(
        1
        for row in alert_rows
        if row.status in {"acknowledged", "resolved"} or row.acked_at is not None
    )
    resolved_alerts = sum(1 for row in alert_rows if row.resolved_at is not None)
    p0_alerts = sum(1 for row in alert_rows if row.priority == "P0")
    critical_alerts = sum(1 for row in alert_rows if row.severity == "critical")
    mttr_values = [
        duration
        for row in alert_rows
        if (duration := _duration_minutes(row.fired_at, row.resolved_at)) is not None
    ]
    avg_mttr_min = sum(mttr_values) / len(mttr_values) if mttr_values else 0.0
    unacked_alerts = sum(1 for row in alert_rows if row.status == "fired" and row.acked_at is None)
    noise_ratio = unacked_alerts / max(total_alerts, 1) * 100

    ai_stmt = (
        select(
            LogAnalysisTask.id,
            LogAnalysisTask.status,
            LogAnalysisTask.token_usage,
        )
        .select_from(LogAnalysisTask)
        .where(
            LogAnalysisTask.tenant_id == tenant_id,
            LogAnalysisTask.created_at >= since,
            LogAnalysisTask.created_at < until,
        )
    )
    task_rows = (await session.execute(ai_stmt)).all()
    total_tasks = len(task_rows)
    ai_used = sum(1 for row in task_rows if (row.token_usage or 0) > 0)
    ai_usage_rate = ai_used / max(total_tasks, 1) * 100
    skipped_or_cached = sum(1 for row in task_rows if row.status in {"skipped", "skipped_dedup", "cached"})
    automation_rate = (ai_used + skipped_or_cached) / max(total_tasks, 1) * 100

    quality_stmt = (
        select(
            AnalysisResult.feedback_score,
            AnalysisResult.severity,
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
    finding_rows = (await session.execute(quality_stmt)).all()
    total_findings = len(finding_rows)
    positive_findings = sum(1 for row in finding_rows if (row.feedback_score or 0) > 0)
    finding_quality = positive_findings / max(total_findings, 1) * 100

    return {
        "total_alerts": total_alerts,
        "p0_alerts": p0_alerts,
        "critical_alerts": critical_alerts,
        "ack_rate": acked_alerts / max(total_alerts, 1) * 100,
        "resolve_rate": resolved_alerts / max(total_alerts, 1) * 100,
        "avg_mttr_min": avg_mttr_min,
        "noise_ratio": noise_ratio,
        "ai_usage_rate": ai_usage_rate,
        "automation_rate": automation_rate,
        "finding_quality": finding_quality,
        "total_findings": total_findings,
        "total_tasks": total_tasks,
    }


@router.get("/ops-efficiency", response_model=OpsEfficiencyResponse)
async def get_ops_efficiency(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(30, ge=7, le=90),
):
    """Calculate operational efficiency score across core SRE dimensions."""
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
        _score("自动化成熟度", "🧭", lambda c, p: (
            min(100, c["automation_rate"]),
            min(100, p["automation_rate"]),
            f"自动处理/复用率 {c['automation_rate']:.0f}% / 任务 {c['total_tasks']} 个",
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
    weights = [0.22, 0.18, 0.10, 0.12, 0.14, 0.16, 0.08]
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
            + min(100, w_metrics["automation_rate"]) * 0.10
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
    action_items = []
    risk_flags = []

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
            "自动化成熟度": "把高频重复故障沉淀成自动化诊断入口和复用规则",
            "发现质量": "鼓励团队对 AI 分析结果提供反馈，帮助模型持续优化",
            "响应覆盖": "告警确认率偏低，建议完善值班响应机制",
            "告警负载": "告警量偏高，建议排查 top 噪音源并优化告警规则",
        }
        suggestions.append(f"💡 {d.name}: {suggestion_map.get(d.name, '持续关注')}")

    if current["avg_mttr_min"] > 45:
        risk_flags.append(f"MTTR 已达到 {current['avg_mttr_min']:.0f} 分钟，存在恢复时间过长风险")
        action_items.append("把最近 3 个 P0/P1 故障复盘成一键诊断模板，并挂到指挥中心入口")
    if current["noise_ratio"] > 45:
        risk_flags.append(f"未确认告警占比 {current['noise_ratio']:.0f}%，可能正在产生告警疲劳")
        action_items.append("筛出本周重复触发最多的 5 条告警，执行降噪或抑制策略")
    if current["ai_usage_rate"] < 35 and current["total_tasks"] > 0:
        risk_flags.append(f"AI 覆盖率仅 {current['ai_usage_rate']:.0f}%，诊断自动化空间较大")
        action_items.append("优先为核心服务开启 AI 分析，并为低价值任务启用去重跳过")
    if current["p0_alerts"] or current["critical_alerts"]:
        risk_flags.append(
            f"本期出现 {current['p0_alerts']} 个 P0 / {current['critical_alerts']} 个 critical 告警"
        )
        action_items.append("将 P0/critical 告警默认升级到故障作战室并自动附带证据链")
    if not action_items:
        action_items.append("保持当前节奏，每周复核最低分维度并把有效经验写入知识库")

    north_star = (
        f"本期北极星：把综合分从 {overall:.1f} 提升到 {min(100, overall + 8):.1f}，"
        f"优先提升 {sorted_dims[0].name}。"
    )

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
            action_items=action_items[:4],
            risk_flags=risk_flags[:4],
            north_star=north_star,
        ),
    )
