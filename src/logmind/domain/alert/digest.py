"""
Alert Domain — Daily/Weekly Analysis Digest

Generates aggregated reports of analysis activity and pushes
them via Webhook. Provides operational visibility:
  - New error patterns discovered
  - Dedup savings (skipped analyses)
  - Top N most frequent errors
  - Token consumption trend
  - Per-business-line health summary

Runs as a Celery Beat scheduled task (daily at 09:00 AM local time).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from logmind.core.async_task import run_async
from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(name="logmind.domain.alert.tasks.send_daily_digest")
def send_daily_digest():
    """Daily analysis digest — runs via Celery Beat."""
    logger.info("daily_digest_started")
    run_async(_generate_and_send_digest(hours=24))


@celery_app.task(name="logmind.domain.alert.tasks.send_weekly_digest")
def send_weekly_digest():
    """Weekly analysis digest — runs via Celery Beat."""
    logger.info("weekly_digest_started")
    run_async(_generate_and_send_digest(hours=168))


async def _generate_and_send_digest(hours: int = 24):
    """
    Generate and send an analysis digest report.

    Queries analysis tasks from the last N hours, aggregates statistics,
    and formats a Markdown report for Webhook delivery.
    """
    from sqlalchemy import func, select

    from logmind.core.config import get_settings
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import AnalysisResult, LogAnalysisTask
    from logmind.domain.tenant.models import BusinessLine, Tenant

    settings = get_settings()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    period_label = "日报" if hours <= 24 else "周报"

    async with get_db_context() as session:
        # Get all tenants
        tenants_stmt = select(Tenant)
        tenants_result = await session.execute(tenants_stmt)
        tenants = tenants_result.scalars().all()

        for tenant in tenants:
            try:
                report = await _build_tenant_digest(
                    session, tenant.id, tenant.name, cutoff, now, period_label
                )
                if report:
                    await _send_digest_webhook(tenant.id, report, session)
                    logger.info(
                        "digest_sent",
                        tenant=tenant.name,
                        period=period_label,
                    )
            except Exception as e:
                logger.error(
                    "digest_failed",
                    tenant=tenant.name,
                    error=str(e),
                )


async def _build_tenant_digest(
    session, tenant_id: str, tenant_name: str,
    cutoff: datetime, now: datetime, period_label: str,
) -> str | None:
    """Build a Markdown digest report for a tenant."""
    from sqlalchemy import func, select

    from logmind.domain.analysis.models import AnalysisResult, LogAnalysisTask
    from logmind.domain.tenant.models import BusinessLine

    # ── 1. Overall statistics ────────────────────────────
    tasks_stmt = select(LogAnalysisTask).where(
        LogAnalysisTask.tenant_id == tenant_id,
        LogAnalysisTask.created_at >= cutoff,
    )
    tasks_result = await session.execute(tasks_stmt)
    tasks = tasks_result.scalars().all()

    if not tasks:
        return None  # No activity, skip digest

    total_tasks = len(tasks)
    completed = sum(1 for t in tasks if t.status == "completed")
    failed = sum(1 for t in tasks if t.status == "failed")
    total_logs = sum(t.log_count or 0 for t in tasks)
    total_tokens = sum(t.token_usage or 0 for t in tasks)
    dedup_skipped = sum(
        1 for t in tasks
        if t.error_message and "质量过滤" in t.error_message
    )

    # ── 2. Per-business-line breakdown ───────────────────
    biz_ids = set(t.business_line_id for t in tasks if t.business_line_id)
    biz_stats = []
    for biz_id in biz_ids:
        biz = await session.get(BusinessLine, biz_id)
        biz_name = biz.name if biz else biz_id[:8]
        biz_tasks = [t for t in tasks if t.business_line_id == biz_id]
        biz_errors = sum(t.log_count or 0 for t in biz_tasks)
        biz_tokens = sum(t.token_usage or 0 for t in biz_tasks)
        biz_failed = sum(1 for t in biz_tasks if t.status == "failed")

        biz_stats.append({
            "name": biz_name,
            "tasks": len(biz_tasks),
            "logs": biz_errors,
            "tokens": biz_tokens,
            "failed": biz_failed,
        })

    biz_stats.sort(key=lambda x: x["logs"], reverse=True)

    # ── 3. Top severity results ──────────────────────────
    results_stmt = select(AnalysisResult).where(
        AnalysisResult.created_at >= cutoff,
    )
    results_result = await session.execute(results_stmt)
    results = results_result.scalars().all()

    critical_results = [
        r for r in results
        if r.severity in ("critical", "error")
    ]

    # ── 4. Build Markdown report ─────────────────────────
    lines = [
        f"## 📊 LogMind 分析{period_label}",
        f"**租户**: {tenant_name}",
        f"**时间范围**: {cutoff.strftime('%m-%d %H:%M')} ~ {now.strftime('%m-%d %H:%M')} (UTC)",
        "",
        "---",
        "",
        "### 📈 总体统计",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 分析任务总数 | {total_tasks} |",
        f"| 成功完成 | {completed} |",
        f"| 失败/超时 | {failed} |",
        f"| 质量过滤跳过 | {dedup_skipped} |",
        f"| 分析日志总数 | {total_logs:,} |",
        f"| Token 总消耗 | {total_tokens:,} |",
        "",
    ]

    # Business line breakdown
    if biz_stats:
        lines.extend([
            "### 🏢 业务线概况",
            "",
            "| 业务线 | 分析次数 | 日志数 | Token | 失败 |",
            "|--------|---------|--------|-------|------|",
        ])
        for bs in biz_stats[:10]:
            status_icon = "🔴" if bs["failed"] > 0 else "🟢"
            lines.append(
                f"| {status_icon} {bs['name']} | {bs['tasks']} | "
                f"{bs['logs']:,} | {bs['tokens']:,} | {bs['failed']} |"
            )
        lines.append("")

    # Critical findings
    if critical_results:
        lines.extend([
            f"### 🔴 严重问题发现 ({len(critical_results)} 个)",
            "",
        ])
        for i, r in enumerate(critical_results[:5]):
            content_preview = (r.content or "")[:150]
            lines.append(f"{i+1}. **[{r.severity.upper()}]** {content_preview}")
        lines.append("")

    # Efficiency metrics
    if total_tasks > 0:
        skip_rate = (dedup_skipped / total_tasks * 100) if dedup_skipped else 0
        avg_tokens = total_tokens // max(completed, 1)
        lines.extend([
            "### 💰 效率指标",
            "",
            f"- 质量过滤跳过率: **{skip_rate:.1f}%** (节省 Token)",
            f"- 平均每次分析 Token: **{avg_tokens:,}**",
            f"- 任务成功率: **{completed/total_tasks*100:.1f}%**",
            "",
        ])

    lines.append("---")
    lines.append("> 📱 登录 LogMind 平台查看完整分析历史和趋势图表。")

    return "\n".join(lines)


async def _send_digest_webhook(tenant_id: str, report: str, session):
    """Send digest report to all configured webhooks for the tenant."""
    from sqlalchemy import select

    from logmind.domain.alert.channels.webhook import send_webhook
    from logmind.domain.tenant.models import BusinessLine

    # Collect unique webhook URLs from all business lines
    biz_stmt = select(BusinessLine).where(
        BusinessLine.tenant_id == tenant_id,
        BusinessLine.is_active == True,
    )
    result = await session.execute(biz_stmt)
    biz_lines = result.scalars().all()

    webhook_urls = set()
    for biz in biz_lines:
        if biz.webhook_url:
            webhook_urls.add(biz.webhook_url)

    # Send to all unique webhooks (deduplicated)
    for url in webhook_urls:
        try:
            await send_webhook(url=url, content=report)
            logger.info("digest_webhook_sent", url=url[:50])
        except Exception as e:
            logger.warning("digest_webhook_failed", url=url[:50], error=str(e))
