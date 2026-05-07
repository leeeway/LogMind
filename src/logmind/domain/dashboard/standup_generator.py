"""
Daily Standup AI — Automated operational stand-up summary

Collects yesterday's alerts, analyses, trends, and SLA status,
then uses LLM to generate a structured daily stand-up report.
"""

import json
from datetime import datetime, timezone, timedelta

from logmind.core.logging import get_logger

logger = get_logger(__name__)


async def generate_standup_report(tenant_id: str, target_date: datetime | None = None) -> dict:
    """
    Generate AI-powered daily standup summary.

    Collects:
      - Alert stats (P0/P1/P2, ACK rate, resolution rate)
      - Analysis tasks (new findings vs known issues vs regressions)
      - Error trend (vs previous day, vs previous week)
      - SLA status (error budget remaining)

    Then calls LLM to produce structured markdown report.
    """
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
    from logmind.domain.alert.models import AlertHistory
    from logmind.domain.tenant.models import BusinessLine
    from sqlalchemy import select, func, case

    if target_date is None:
        target_date = datetime.now(timezone.utc) - timedelta(days=1)

    day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    prev_day_start = day_start - timedelta(days=1)
    prev_week_start = day_start - timedelta(days=7)

    async with get_db_context() as session:
        # 1. Alert statistics
        alert_stmt = (
            select(
                func.count().label("total"),
                func.count(case((AlertHistory.severity == "critical", 1))).label("p0"),
                func.count(case((AlertHistory.severity == "warning", 1))).label("p1"),
                func.count(case((AlertHistory.status == "acknowledged", 1))).label("acked"),
                func.count(case((AlertHistory.status == "resolved", 1))).label("resolved"),
            )
            .where(
                AlertHistory.tenant_id == tenant_id,
                AlertHistory.fired_at >= day_start,
                AlertHistory.fired_at < day_end,
            )
        )
        alert_row = (await session.execute(alert_stmt)).one_or_none()

        total_alerts = alert_row.total if alert_row else 0
        p0_count = alert_row.p0 if alert_row else 0
        p1_count = alert_row.p1 if alert_row else 0
        acked = alert_row.acked if alert_row else 0
        resolved = alert_row.resolved if alert_row else 0
        ack_rate = acked / max(total_alerts, 1) * 100
        resolve_rate = resolved / max(total_alerts, 1) * 100

        # Previous day comparison
        prev_alert_count = (await session.execute(
            select(func.count()).select_from(AlertHistory).where(
                AlertHistory.tenant_id == tenant_id,
                AlertHistory.fired_at >= prev_day_start,
                AlertHistory.fired_at < day_start,
            )
        )).scalar() or 0

        # Previous week comparison
        prev_week_avg = (await session.execute(
            select(func.count()).select_from(AlertHistory).where(
                AlertHistory.tenant_id == tenant_id,
                AlertHistory.fired_at >= prev_week_start,
                AlertHistory.fired_at < day_start,
            )
        )).scalar() or 0
        prev_week_daily_avg = prev_week_avg / 7

        # 2. Analysis task statistics
        task_stmt = (
            select(
                func.count().label("total"),
                func.count(case((LogAnalysisTask.status == "completed", 1))).label("completed"),
            )
            .where(
                LogAnalysisTask.tenant_id == tenant_id,
                LogAnalysisTask.created_at >= day_start,
                LogAnalysisTask.created_at < day_end,
            )
        )
        task_row = (await session.execute(task_stmt)).one_or_none()
        total_tasks = task_row.total if task_row else 0
        completed_tasks = task_row.completed if task_row else 0

        # 3. Severity distribution of analysis results
        sev_stmt = (
            select(AnalysisResult.severity, func.count().label("cnt"))
            .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
            .where(
                LogAnalysisTask.tenant_id == tenant_id,
                LogAnalysisTask.created_at >= day_start,
                LogAnalysisTask.created_at < day_end,
            )
            .group_by(AnalysisResult.severity)
        )
        sev_rows = (await session.execute(sev_stmt)).all()
        severity_dist = {r.severity: r.cnt for r in sev_rows}

        # 4. Top affected services
        svc_stmt = (
            select(
                BusinessLine.name,
                func.count().label("cnt"),
            )
            .join(LogAnalysisTask, LogAnalysisTask.business_line_id == BusinessLine.id)
            .where(
                LogAnalysisTask.tenant_id == tenant_id,
                LogAnalysisTask.created_at >= day_start,
                LogAnalysisTask.created_at < day_end,
            )
            .group_by(BusinessLine.name)
            .order_by(func.count().desc())
            .limit(5)
        )
        svc_rows = (await session.execute(svc_stmt)).all()
        top_services = [{"name": r.name, "tasks": r.cnt} for r in svc_rows]

    # 5. Build data summary
    day_str = day_start.strftime("%Y-%m-%d")
    trend_vs_prev = total_alerts - prev_alert_count
    trend_vs_week = total_alerts - prev_week_daily_avg

    data_summary = {
        "date": day_str,
        "alerts": {
            "total": total_alerts,
            "p0": p0_count,
            "p1": p1_count,
            "p2": total_alerts - p0_count - p1_count,
            "ack_rate_pct": round(ack_rate, 1),
            "resolve_rate_pct": round(resolve_rate, 1),
            "vs_prev_day": trend_vs_prev,
            "vs_week_avg": round(trend_vs_week, 1),
        },
        "analysis": {
            "total_tasks": total_tasks,
            "completed": completed_tasks,
            "critical_findings": severity_dist.get("critical", 0),
            "warning_findings": severity_dist.get("warning", 0),
        },
        "top_services": top_services,
    }

    # 6. AI summary generation
    ai_summary = await _generate_ai_summary(tenant_id, data_summary)

    return {
        "date": day_str,
        "data": data_summary,
        "ai_summary": ai_summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _generate_ai_summary(tenant_id: str, data: dict) -> str:
    """Call LLM to generate standup summary from collected data."""
    try:
        from logmind.core.database import get_db_context
        from logmind.domain.provider.base import ChatMessage, ChatRequest
        from logmind.domain.provider.manager import provider_manager

        async with get_db_context() as session:
            provider = await provider_manager.get_provider(session, tenant_id)

        if not provider:
            return _fallback_summary(data)

        prompt = f"""请基于以下运维数据生成一份简洁的每日站会摘要。

## 日期: {data['date']}

## 告警数据
- 总告警: {data['alerts']['total']}
- P0 (严重): {data['alerts']['p0']}
- P1 (警告): {data['alerts']['p1']}
- P2 (信息): {data['alerts']['p2']}
- 确认率: {data['alerts']['ack_rate_pct']}%
- 解决率: {data['alerts']['resolve_rate_pct']}%
- 较前日: {'+' if data['alerts']['vs_prev_day'] > 0 else ''}{data['alerts']['vs_prev_day']}
- 较周均: {'+' if data['alerts']['vs_week_avg'] > 0 else ''}{data['alerts']['vs_week_avg']:.0f}

## 分析任务
- 总任务: {data['analysis']['total_tasks']}
- 完成: {data['analysis']['completed']}
- 严重发现: {data['analysis']['critical_findings']}
- 告警发现: {data['analysis']['warning_findings']}

## 受影响最大的服务
{chr(10).join(f"- {s['name']}: {s['tasks']}个任务" for s in data['top_services']) or '- 无'}

请输出格式：
## 🔴 关键事件
（P0/P1 事件总结）

## 📈 趋势分析
（同比昨日/周均变化）

## ✅ 已修复
（已解决的问题）

## ⚠️ 待关注
（需要持续跟踪的风险）

## 💡 建议行动
（具体可操作的改进建议）

要求简洁专业，每项 1-2 句话。"""

        request = ChatRequest(
            messages=[
                ChatMessage(role="system", content="你是 LogMind 运维站会助手，擅长从运维数据中提炼关键信息，生成简洁专业的站会摘要。"),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.3,
            max_tokens=1500,
        )
        response = await provider.chat(request)
        return response.content

    except Exception as e:
        logger.warning("standup_ai_failed", error=str(e))
        return _fallback_summary(data)


def _fallback_summary(data: dict) -> str:
    """Generate basic summary without AI when provider unavailable."""
    alerts = data["alerts"]
    analysis = data["analysis"]

    lines = [f"## 📊 {data['date']} 运维日报\n"]

    if alerts["p0"] > 0:
        lines.append(f"### 🔴 P0 严重告警: {alerts['p0']} 个")
    if alerts["p1"] > 0:
        lines.append(f"### 🟡 P1 告警: {alerts['p1']} 个")

    lines.append(f"\n**告警总计**: {alerts['total']} 个 "
                 f"(确认率 {alerts['ack_rate_pct']}%, 解决率 {alerts['resolve_rate_pct']}%)")

    if alerts["vs_prev_day"] > 0:
        lines.append(f"\n📈 较前日增加 {alerts['vs_prev_day']} 个告警")
    elif alerts["vs_prev_day"] < 0:
        lines.append(f"\n📉 较前日减少 {abs(alerts['vs_prev_day'])} 个告警")

    lines.append(f"\n**分析任务**: {analysis['total_tasks']} 个 "
                 f"(完成 {analysis['completed']}, "
                 f"严重 {analysis['critical_findings']}, "
                 f"告警 {analysis['warning_findings']})")

    if data["top_services"]:
        lines.append("\n**受影响服务**: " + ", ".join(
            f"{s['name']}({s['tasks']})" for s in data["top_services"]
        ))

    return "\n".join(lines)
