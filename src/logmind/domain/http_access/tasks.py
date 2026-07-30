"""Celery tasks and orchestration for global HTTP access-log patrol."""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from logmind.core.async_task import run_async
from logmind.core.celery_app import celery_app
from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.http_access.models import (
    AccessIncident,
    AccessRecovery,
    aggregate_metrics,
    detect_incidents,
    detect_route_incidents,
)
from logmind.domain.http_access.service import http_access_service
from logmind.domain.http_access.state import http_access_alert_state

logger = get_logger(__name__)

_DISPLAY_TZ = ZoneInfo("Asia/Shanghai")
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
_FORBIDDEN_UNSUPPORTED_AI_CLAIMS_RE = re.compile(
    r"数据库|DataIntegrityViolationException|SQL\s*截断|C#|\.NET\s*异常",
    re.IGNORECASE,
)


@celery_app.task(name="logmind.domain.http_access.tasks.scheduled_http_access_patrol")
def scheduled_http_access_patrol():
    """Run one global patrol; never fan out through business_line rows."""
    settings = get_settings()
    if not settings.http_access_patrol_enabled:
        logger.info("http_access_patrol_disabled")
        return
    run_async(_run_http_access_patrol())


@celery_app.task(name="logmind.domain.http_access.tasks.cleanup_http_access_metrics")
def cleanup_http_access_metrics():
    """Delete compact access metrics beyond their configured retention."""
    if not get_settings().http_access_patrol_enabled:
        return
    run_async(_cleanup_http_access_metrics())


async def _cleanup_http_access_metrics() -> None:
    deleted = await http_access_service.cleanup_metrics()
    logger.info("http_access_metrics_cleanup_completed", deleted=deleted)


async def _run_http_access_patrol(
    *,
    now: datetime | None = None,
    service=None,
    alert_state=None,
) -> dict[str, Any]:
    """Collect → baseline → detect → sample → one aggregated notification."""
    settings = get_settings()
    service = service or http_access_service
    alert_state = alert_state or http_access_alert_state
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)

    time_to = current_time.astimezone(UTC).replace(second=0, microsecond=0)
    time_from = time_to - timedelta(minutes=settings.http_access_window_minutes)

    metrics = await service.collect_window(time_from, time_to)
    try:
        baselines = await service.load_baselines(
            before=time_from,
            window_minutes=settings.http_access_window_minutes,
            days=settings.http_access_baseline_days,
        )
    except Exception as exc:
        baselines = {}
        logger.warning("http_access_baseline_load_failed", error=str(exc))
    windows = aggregate_metrics(metrics)
    incidents = detect_incidents(windows, baselines)
    route_metrics = []
    if any(window.status_4xx >= 10 for window in windows.values()):
        route_metrics = await service.collect_route_metrics(
            windows,
            time_from=time_from,
            time_to=time_to,
        )
    route_incidents = detect_route_incidents(route_metrics)
    if route_incidents:
        route_sites = {
            (incident.source, incident.site) for incident in route_incidents
        }
        incidents = [
            incident
            for incident in incidents
            if not (
                incident.kind == "http_4xx"
                and (incident.source, incident.site) in route_sites
            )
        ]
        incidents.extend(route_incidents)
    try:
        persisted = await service.persist_metrics(metrics)
    except Exception as exc:
        persisted = 0
        logger.error("http_access_metric_persist_failed", error=str(exc))

    batch = await alert_state.evaluate(incidents, now=time_to)
    result: dict[str, Any] = {
        "time_from": time_from.isoformat(),
        "time_to": time_to.isoformat(),
        "metric_count": len(metrics),
        "persisted_metric_count": persisted,
        "route_metric_count": len(route_metrics),
        "incident_count": len(incidents),
        "notification_incident_count": len(batch.due),
        "recovery_count": len(batch.recoveries),
        "notification_sent": False,
        "shadow_mode": not settings.http_access_notification_enabled,
    }

    notification_recoveries = (
        batch.recoveries
        if getattr(
            settings,
            "http_access_recovery_notification_enabled",
            False,
        )
        else []
    )
    result["notification_recovery_count"] = len(notification_recoveries)

    if not batch.due and not notification_recoveries:
        await alert_state.save(batch, delivered=True)
        logger.info("http_access_patrol_normal", **result)
        return result

    if not settings.http_access_notification_enabled:
        await alert_state.save(batch, delivered=False)
        logger.warning(
            "http_access_shadow_anomalies_detected",
            sites=sorted({incident.site for incident in batch.due})[:20],
            **result,
        )
        return result

    display_incidents, omitted_sites = _select_notification_incidents(
        batch.due,
        settings.http_access_max_notification_sites,
    )
    total_p0_sites, total_p1_sites = _count_priority_sites(batch.due)
    await _attach_samples(
        service,
        display_incidents,
        time_from=time_from,
        time_to=time_to,
        sample_size=settings.http_access_sample_size,
    )

    try:
        tenant_id = await _resolve_tenant_id()
    except Exception as exc:
        tenant_id = None
        logger.warning("http_access_tenant_resolve_failed", error=str(exc))
    if settings.http_access_ai_enabled and tenant_id and display_incidents:
        await _attach_ai_summaries(display_incidents, tenant_id)

    message = build_http_access_notification(
        display_incidents,
        notification_recoveries,
        time_from=time_from,
        time_to=time_to,
        omitted_sites=omitted_sites,
        total_p0_sites=total_p0_sites,
        total_p1_sites=total_p1_sites,
    )
    delivered = await _send_notification(message)
    await alert_state.save(batch, delivered=delivered)
    result["notification_sent"] = delivered

    if tenant_id:
        try:
            await _persist_alert_history(
                tenant_id=tenant_id,
                message=message,
                incidents=display_incidents,
                recoveries=notification_recoveries,
                delivered=delivered,
                fired_at=time_to,
            )
        except Exception as exc:
            logger.error("http_access_history_persist_failed", error=str(exc))

    logger.info("http_access_patrol_completed", **result)
    return result


def _select_notification_incidents(
    incidents: list[AccessIncident],
    max_sites: int,
) -> tuple[list[AccessIncident], int]:
    by_site: dict[str, list[AccessIncident]] = defaultdict(list)
    for incident in incidents:
        by_site[incident.site].append(incident)

    ranked_sites = sorted(
        by_site,
        key=lambda site: (
            min(
                _PRIORITY_RANK.get(item.priority, 9)
                for item in by_site[site]
            ),
            -max(item.impact for item in by_site[site]),
            site,
        ),
    )
    selected_sites = set(ranked_sites[:max_sites])
    selected = []
    for site in ranked_sites:
        if site not in selected_sites:
            continue
        selected.extend(
            sorted(
                by_site[site],
                key=lambda item: (
                    _PRIORITY_RANK.get(item.priority, 9),
                    -item.impact,
                ),
            )[:3]
        )
    return selected, max(0, len(ranked_sites) - len(selected_sites))


def _count_priority_sites(
    incidents: list[AccessIncident],
) -> tuple[int, int]:
    priorities: dict[str, str] = {}
    for incident in incidents:
        old = priorities.get(incident.site, "P2")
        priorities[incident.site] = min(
            (old, incident.priority),
            key=lambda value: _PRIORITY_RANK.get(value, 9),
        )
    return (
        sum(priority == "P0" for priority in priorities.values()),
        sum(priority == "P1" for priority in priorities.values()),
    )


async def _attach_samples(
    service,
    incidents: list[AccessIncident],
    *,
    time_from: datetime,
    time_to: datetime,
    sample_size: int,
) -> None:
    groups = sorted({(item.source, item.site) for item in incidents})
    prefer_latency = {
        group: all(
            item.kind == "latency"
            for item in incidents
            if (item.source, item.site) == group
        )
        for group in groups
    }
    route_keys = {
        group: [
            item.route_key
            for item in incidents
            if (item.source, item.site) == group and item.route_key
        ]
        if all(
            item.kind == "route_4xx"
            for item in incidents
            if (item.source, item.site) == group
        )
        else []
        for group in groups
    }
    results = await asyncio.gather(
        *[
            service.fetch_samples(
                source=source,
                site=site,
                time_from=time_from,
                time_to=time_to,
                size=sample_size,
                prefer_latency=prefer_latency[(source, site)],
                route_keys=route_keys[(source, site)],
            )
            for source, site in groups
        ],
        return_exceptions=True,
    )
    sample_map = {}
    for group, samples in zip(groups, results, strict=True):
        if isinstance(samples, Exception):
            logger.warning(
                "http_access_sample_fetch_failed",
                source=group[0],
                site=group[1],
                error=str(samples),
            )
            sample_map[group] = []
        else:
            sample_map[group] = samples
    for incident in incidents:
        incident.samples = sample_map.get((incident.source, incident.site), [])


async def _resolve_tenant_id() -> str | None:
    """
    Resolve AI/history ownership without reading business_line.

    A configured tenant wins. Otherwise auto-select only when the deployment
    has exactly one active tenant; multi-tenant deployments must opt in.
    """
    from sqlalchemy import select

    from logmind.core.database import get_db_context
    from logmind.domain.tenant.models import Tenant

    settings = get_settings()
    async with get_db_context() as session:
        if settings.http_access_tenant_id:
            tenant = await session.get(Tenant, settings.http_access_tenant_id)
            if tenant and tenant.is_active:
                return tenant.id
            logger.warning(
                "http_access_configured_tenant_unavailable",
                tenant_id=settings.http_access_tenant_id,
            )
            return None

        result = await session.execute(
            select(Tenant).where(Tenant.is_active.is_(True)).limit(2)
        )
        tenants = list(result.scalars().all())
        if len(tenants) == 1:
            return tenants[0].id
        logger.warning(
            "http_access_tenant_ambiguous",
            active_tenant_count=len(tenants),
        )
        return None


async def _attach_ai_summaries(
    incidents: list[AccessIncident],
    tenant_id: str,
) -> None:
    from logmind.core.database import get_db_context
    from logmind.domain.provider.base import ChatMessage, ChatRequest
    from logmind.domain.provider.manager import provider_manager

    payload = []
    for incident in incidents:
        payload.append(
            {
                "key": incident.key,
                "source": incident.source,
                "server_name": incident.site,
                "anomaly_type": incident.kind,
                "priority": incident.priority,
                "request_count": incident.request_count,
                "current_value": round(incident.current_value, 6),
                "baseline_value": round(incident.baseline_value, 6),
                "status_4xx": incident.status_4xx,
                "status_5xx": incident.status_5xx,
                "gateway_5xx": incident.gateway_5xx,
                "upstream_5xx": incident.upstream_5xx,
                "p95_ms": round(incident.p95_ms, 3),
                "route": incident.route_key,
                "samples": [
                    sample.to_ai_dict() for sample in incident.samples[:5]
                ],
            }
        )

    system_prompt = (
        "你只分析给定的 Nginx/Ingress HTTP access 聚合指标和脱敏样本。"
        "不得推断数据库写入失败、SQL截断、DataIntegrityViolationException、"
        "C#/.NET异常或任何输入中不存在的应用内部故障。"
        "upstream出现5xx时可判断为后端/upstream异常；外层5xx且无upstream证据时"
        "只能判断为网关路由、连接或上游不可达；上下游均400且耗时低时只能判断为"
        "客户端参数或业务校验异常。证据不足时明确写“仅能确认访问层现象”。"
        "返回严格JSON：{\"items\":[{\"key\":\"原key\",\"summary\":\"不超过100字\"}]}。"
    )
    request = ChatRequest(
        messages=[
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(
                role="user",
                content=json.dumps(payload, ensure_ascii=False),
            ),
        ],
        temperature=0.1,
        max_tokens=1200,
    )
    try:
        async with get_db_context() as session:
            response, _ = await provider_manager.chat_with_fallback(
                session=session,
                tenant_id=tenant_id,
                request=request,
            )
        summaries = _parse_ai_summaries(response.content)
        for incident in incidents:
            incident.ai_summary = summaries.get(incident.key, "")
    except Exception as exc:
        logger.warning("http_access_ai_summary_failed", error=str(exc))


def _parse_ai_summaries(content: str) -> dict[str, str]:
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    summaries: dict[str, str] = {}
    items = parsed.get("items", []) if isinstance(parsed, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", ""))
        summary = " ".join(str(item.get("summary", "")).split())[:160]
        if (
            key
            and summary
            and not _FORBIDDEN_UNSUPPORTED_AI_CLAIMS_RE.search(summary)
        ):
            summaries[key] = summary
    return summaries


def build_http_access_notification(
    incidents: list[AccessIncident],
    recoveries: list[AccessRecovery],
    *,
    time_from: datetime,
    time_to: datetime,
    omitted_sites: int = 0,
    total_p0_sites: int | None = None,
    total_p1_sites: int | None = None,
) -> str:
    """Build one compact, privacy-safe WeCom message for the patrol window."""
    grouped: dict[str, list[AccessIncident]] = defaultdict(list)
    for incident in incidents:
        grouped[incident.site].append(incident)

    ranked_sites = sorted(
        grouped,
        key=lambda site: (
            min(_PRIORITY_RANK.get(i.priority, 9) for i in grouped[site]),
            -max(i.impact for i in grouped[site]),
            site,
        ),
    )
    site_priorities = {
        site: min(
            (item.priority for item in grouped[site]),
            key=lambda p: _PRIORITY_RANK.get(p, 9),
        )
        for site in grouped
    }
    p0_count = (
        total_p0_sites
        if total_p0_sites is not None
        else sum(priority == "P0" for priority in site_priorities.values())
    )
    p1_count = (
        total_p1_sites
        if total_p1_sites is not None
        else sum(priority == "P1" for priority in site_priorities.values())
    )
    highest = "P0" if p0_count else "P1" if p1_count else "恢复"
    icon = "🔴" if highest == "P0" else "🟡" if highest == "P1" else "🟢"

    local_from = _to_local(time_from)
    local_to = _to_local(time_to)
    lines = [
        f"## {icon} HTTP访问异常汇总",
        f"**时间**: {local_from:%Y-%m-%d %H:%M} ~ {local_to:%H:%M}",
        f"**异常站点**: P0 {p0_count}个 / P1 {p1_count}个",
        "",
    ]

    for index, site in enumerate(ranked_sites, start=1):
        site_incidents = grouped[site]
        priority = site_priorities[site]
        sources = "/".join(
            sorted({_display_source(item.source) for item in site_incidents})
        )
        lines.append(f"**{index}. {site} [{priority}] · {sources}**")
        for incident in sorted(
            site_incidents,
            key=lambda item: (
                _PRIORITY_RANK.get(item.priority, 9),
                -item.impact,
            ),
        )[:3]:
            lines.append(f"- {_incident_metric_text(incident)}")

        samples = [
            sample
            for incident in site_incidents
            for sample in incident.samples
        ]
        explicit_routes = [
            incident.route_key
            for incident in sorted(
                site_incidents,
                key=lambda item: -item.impact,
            )
            if incident.route_key
        ]
        sample_routes = [
            f"{sample.method} {sample.route}" for sample in samples if sample.route
        ]
        if not explicit_routes and sample_routes:
            from collections import Counter

            top_route = Counter(sample_routes).most_common(1)[0][0]
            lines.append(f"- 主要接口: {top_route}")
        upstreams = [
            sample.upstream_addr
            for sample in samples
            if sample.upstream_addr
            and (
                (sample.upstream_status or 0) >= 500
                or sample.status in {502, 503, 504}
            )
        ]
        if upstreams:
            from collections import Counter

            lines.append(
                f"- 异常upstream: {Counter(upstreams).most_common(1)[0][0]}"
            )
        ai_summaries = list(
            dict.fromkeys(
                item.ai_summary for item in site_incidents if item.ai_summary
            )
        )
        if ai_summaries:
            lines.append(f"- AI判断: {ai_summaries[0]}")
        else:
            lines.append(f"- 判断: {_deterministic_diagnosis(site_incidents)}")
        lines.append("")

    if omitted_sites:
        lines.append(f"> 另有 {omitted_sites} 个较低优先级异常站点未展开。")
    if recoveries:
        lines.append("**恢复**:")
        for recovery in recoveries[:10]:
            lines.append(
                f"- {recovery.site} · {_display_source(recovery.source)}"
                f" · {_display_kind(recovery.kind)}"
            )
        if len(recoveries) > 10:
            lines.append(f"- 另有 {len(recoveries) - 10} 个恢复信号")

    message = "\n".join(lines).strip()
    return message if len(message) <= 3500 else message[:3499].rstrip() + "…"


def _incident_metric_text(incident: AccessIncident) -> str:
    if incident.kind == "http_5xx":
        baseline = incident.baseline_value * 100
        return (
            f"5xx {incident.status_5xx}/{incident.request_count}"
            f"（{incident.current_value * 100:.2f}%，基线{baseline:.2f}%）"
        )
    if incident.kind == "http_4xx":
        return (
            f"4xx {incident.status_4xx}/{incident.request_count}"
            f"（{incident.current_value * 100:.2f}%，"
            f"基线{incident.baseline_value * 100:.2f}%）"
        )
    if incident.kind == "route_4xx":
        return (
            f"{incident.route_key} · 4xx "
            f"{incident.status_4xx}/{incident.request_count}"
            f"（{incident.current_value * 100:.2f}%）"
        )
    if incident.kind == "latency":
        return (
            f"p95 {incident.current_value:.0f}ms"
            f"（基线{incident.baseline_value:.0f}ms）"
        )
    return (
        f"请求量 {incident.current_value:.0f}"
        f"（基线{incident.baseline_value:.0f}/5分钟）"
    )


def _deterministic_diagnosis(incidents: list[AccessIncident]) -> str:
    if any(item.upstream_5xx > 0 for item in incidents):
        return "upstream 已返回5xx，优先检查对应后端服务。"
    if any(
        item.gateway_5xx > 0 and item.upstream_5xx == 0
        for item in incidents
    ):
        return "网关出现502/503/504但缺少upstream 5xx证据，检查路由、连接和上游可达性。"
    if any(item.kind in {"http_4xx", "route_4xx"} for item in incidents):
        return "客户端参数或业务校验失败显著增加，检查主要接口的请求约束。"
    if any(item.kind == "latency" for item in incidents):
        return "访问层延迟显著高于历史基线，结合upstream耗时检查后端或网关排队。"
    if any(item.kind == "traffic_drop" for item in incidents):
        return "站点流量连续低于历史基线，检查入口可用性和日志采集状态。"
    return "仅能确认访问层统计异常，暂无应用内部故障证据。"


def _display_source(source: str) -> str:
    return "Ingress" if source == "ingress" else "Nginx"


def _display_kind(kind: str) -> str:
    return {
        "http_5xx": "5xx",
        "http_4xx": "4xx",
        "route_4xx": "接口4xx",
        "latency": "延迟",
        "traffic_drop": "流量",
    }.get(kind, kind)


def _to_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_DISPLAY_TZ)


async def _send_notification(message: str) -> bool:
    from logmind.domain.alert.channels.webhook import send_webhook_notification

    settings = get_settings()
    return await send_webhook_notification(
        message,
        webhook_url=settings.http_access_webhook_url or None,
    )


async def _persist_alert_history(
    *,
    tenant_id: str,
    message: str,
    incidents: list[AccessIncident],
    recoveries: list[AccessRecovery],
    delivered: bool,
    fired_at: datetime,
) -> None:
    from logmind.core.database import get_db_context
    from logmind.domain.alert.models import AlertHistory

    priorities = [item.priority for item in incidents]
    priority = min(
        priorities or ["P2"],
        key=lambda value: _PRIORITY_RANK.get(value, 9),
    )
    severity = "critical" if priority == "P0" else "warning"
    if not incidents and recoveries:
        severity = "info"

    record = AlertHistory(
        alert_rule_id=None,
        analysis_task_id=None,
        tenant_id=tenant_id,
        status="fired",
        severity=severity,
        message=message,
        notify_result=json.dumps(
            {
                "sent": delivered,
                "incident_count": len(incidents),
                "recovery_count": len(recoveries),
            },
            ensure_ascii=False,
        ),
        fired_at=fired_at,
        priority=priority,
        alert_type="http_access",
        business_line_id=None,
    )
    async with get_db_context() as session:
        session.add(record)
        await session.flush()
