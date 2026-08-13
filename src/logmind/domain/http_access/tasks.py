"""Celery tasks and orchestration for global HTTP access-log patrol."""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from logmind.core.async_task import run_async
from logmind.core.celery_app import celery_app
from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.http_access.governance import (
    discover_sites,
    filter_enabled_incidents,
    filter_notification_worthy_incidents,
    incident_is_enabled,
    incident_is_notification_worthy,
    role_for_site,
)
from logmind.domain.http_access.incident_store import (
    filter_learned_incidents,
    learn_stable_route_behaviors,
    mark_notification_delivered,
    mark_recovered_records,
    sync_incident_records,
)
from logmind.domain.http_access.models import (
    AccessIncident,
    AccessRecovery,
    aggregate_metrics,
    detect_incidents,
    detect_route_incidents,
    is_rejected_traffic_window,
    merge_cross_source_incidents,
    route_4xx_threshold,
)
from logmind.domain.http_access.service import http_access_service
from logmind.domain.http_access.state import http_access_alert_state

logger = get_logger(__name__)

_DISPLAY_TZ = ZoneInfo("Asia/Shanghai")
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
_FORBIDDEN_UNSUPPORTED_AI_CLAIMS_RE = re.compile(
    r"数据库|DataIntegrityViolationException|SQL\s*截断|C#|\.NET\s*异常|"
    r"Redis|Kafka|ZooKeeper|消息队列|线程池|连接池|NullReference|Exception|"
    r"内存泄漏|CPU\s*过高|GC\s*异常",
    re.IGNORECASE,
)
_ACTIONABLE_AI_SUMMARY_RE = re.compile(
    r"检查|排查|核对|确认|修复|回滚|扩容|限流|查看|调整|联系"
)


@celery_app.task(name="logmind.domain.http_access.tasks.scheduled_http_access_patrol")
def scheduled_http_access_patrol():
    """Run one global patrol; never fan out through business_line rows."""
    settings = get_settings()
    if not settings.http_access_patrol_enabled:
        logger.info("http_access_patrol_disabled")
        return
    run_async(_run_scheduled_http_access_patrol())


@celery_app.task(name="logmind.domain.http_access.tasks.cleanup_http_access_metrics")
def cleanup_http_access_metrics():
    """Delete compact access metrics beyond their configured retention."""
    if not get_settings().http_access_patrol_enabled:
        return
    run_async(_cleanup_http_access_metrics())


@celery_app.task(name="logmind.domain.http_access.tasks.send_http_access_pending_digest")
def send_http_access_pending_digest():
    """Weekday summary of unresolved P1s; realtime P0 stays independent."""
    if not get_settings().http_access_patrol_enabled:
        return
    run_async(_send_http_access_pending_digest())


@celery_app.task(name="logmind.domain.http_access.tasks.sync_http_repository")
def sync_http_repository(repository_id: str):
    run_async(_sync_http_repository(repository_id))


@celery_app.task(name="logmind.domain.http_access.tasks.scheduled_sync_http_repositories")
def scheduled_sync_http_repositories():
    run_async(_scheduled_sync_http_repositories())


async def _sync_http_repository(repository_id: str) -> None:
    from logmind.core.database import get_db_context
    from logmind.domain.http_access.repository import (
        RepositoryError,
        git_repository_service,
    )
    from logmind.domain.http_access.site_config import GitRepositoryConfig

    async with get_db_context() as session:
        repository = await session.get(GitRepositoryConfig, repository_id)
        if not repository or not repository.is_active:
            return
        repository.last_sync_status = "syncing"
    try:
        # Git network/disk I/O intentionally runs without holding a database
        # connection from the worker pool.
        result = await git_repository_service.sync(repository)
    except RepositoryError as exc:
        async with get_db_context() as session:
            current = await session.get(GitRepositoryConfig, repository_id)
            if current:
                current.last_sync_status = "failed"
                current.last_sync_error = str(exc)[:500]
        logger.warning(
            "http_access_repository_sync_failed",
            repository_id=repository_id,
            error=str(exc),
        )
        return
    async with get_db_context() as session:
        current = await session.get(GitRepositoryConfig, repository_id)
        if not current:
            return
        current.default_branch = repository.default_branch
        current.last_sync_status = "success"
        current.last_sync_error = ""
        current.last_synced_at = result["synced_at"]
        current.last_commit_sha = result["commit_sha"]
        current.cache_size_bytes = result["cache_size_bytes"]


async def _scheduled_sync_http_repositories() -> None:
    from sqlalchemy import select

    from logmind.core.database import get_db_context
    from logmind.domain.http_access.site_config import GitRepositoryConfig

    async with get_db_context() as session:
        result = await session.execute(
            select(GitRepositoryConfig.id).where(
                GitRepositoryConfig.is_active.is_(True)
            )
        )
        repository_ids = list(result.scalars().all())
    for repository_id in repository_ids:
        sync_http_repository.delay(repository_id)


async def _send_http_access_pending_digest() -> bool:
    now = datetime.now(_DISPLAY_TZ)
    if now.weekday() >= 5:
        return False
    tenant_id = await _resolve_tenant_id()
    if not tenant_id:
        return False
    from sqlalchemy import or_, select

    from logmind.core.database import get_db_context
    from logmind.domain.http_access.site_config import (
        HttpAccessIncidentRecord,
        HttpAccessSiteConfig,
    )

    local_day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = local_day_start.astimezone(UTC)
    active_cutoff = datetime.now(UTC) - timedelta(minutes=15)

    async with get_db_context() as session:
        result = await session.execute(
            select(HttpAccessIncidentRecord).where(
                HttpAccessIncidentRecord.tenant_id == tenant_id,
                HttpAccessIncidentRecord.priority == "P1",
                HttpAccessIncidentRecord.status.in_(["open", "acknowledged"]),
                HttpAccessIncidentRecord.last_seen_at >= active_cutoff,
                or_(
                    HttpAccessIncidentRecord.last_digest_at.is_(None),
                    HttpAccessIncidentRecord.last_digest_at < day_start_utc,
                ),
            ).order_by(HttpAccessIncidentRecord.first_seen_at)
        )
        rows = list(result.scalars().all())
        site_result = await session.execute(
            select(HttpAccessSiteConfig).where(
                HttpAccessSiteConfig.tenant_id == tenant_id,
                HttpAccessSiteConfig.site.in_({item.site for item in rows}),
            )
        )
        site_configs = {item.site: item for item in site_result.scalars().all()}
    pending = []
    for item in rows:
        config = site_configs.get(item.site)
        incident = _incident_from_record(item)
        if not incident_is_enabled(incident, config):
            continue
        if not incident_is_notification_worthy(
            incident,
            config,
            critical_only=getattr(
                get_settings(),
                "http_access_critical_notifications_only",
                True,
            ),
            general_latency_min_p95_ms=getattr(
                get_settings(),
                "http_access_latency_general_min_p95_ms",
                10000,
            ),
            general_latency_min_slow_count=getattr(
                get_settings(),
                "http_access_latency_general_min_slow_count",
                100,
            ),
            general_latency_min_slow_rate=getattr(
                get_settings(),
                "http_access_latency_general_min_slow_rate",
                0.20,
            ),
        ):
            continue
        pending.append(
            {
                "site": item.site,
                "source": item.source,
                "kind": item.kind,
                "first_seen": item.first_seen_at.isoformat(),
                "last_seen": item.last_seen_at.isoformat(),
                "role": config.role if config else "general",
                "route": item.route_key,
                "id": item.id,
            }
        )
    if not pending:
        return False
    pending.sort(key=lambda item: str(item.get("first_seen", "")))
    await _attach_pending_digest_routes(pending[:10])
    lines = ["## 🟡 HTTP访问待处理摘要", f"**时间**: {now:%Y-%m-%d %H:%M}", ""]
    for item in pending[:10]:
        first_seen = _parse_state_time(item.get("first_seen"))
        duration = ""
        if first_seen:
            duration_minutes = max(
                5,
                int(
                    (now - first_seen.astimezone(_DISPLAY_TZ)).total_seconds()
                    // 60
                ),
            )
            duration = f"，持续{duration_minutes}分钟"
        lines.append(_format_pending_digest_line(item, duration=duration))
    if len(pending) > 10:
        lines.append(f"- 另有 {len(pending) - 10} 个待处理问题")
    delivered = await _send_notification("\n".join(lines))
    if delivered:
        delivered_ids = [str(item["id"]) for item in pending]
        async with get_db_context() as session:
            result = await session.execute(
                select(HttpAccessIncidentRecord).where(
                    HttpAccessIncidentRecord.tenant_id == tenant_id,
                    HttpAccessIncidentRecord.id.in_(delivered_ids),
                )
            )
            for record in result.scalars().all():
                record.last_digest_at = datetime.now(UTC)
    logger.info(
        "http_access_pending_digest_completed",
        pending_count=len(pending),
        delivered=delivered,
    )
    return delivered


def _incident_from_record(record) -> AccessIncident:
    try:
        evidence = json.loads(record.evidence_json or "{}")
    except (TypeError, json.JSONDecodeError):
        evidence = {}
    return AccessIncident(
        source=record.source,
        site=record.site,
        kind=record.kind,
        priority=record.priority,
        request_count=int(evidence.get("request_count", 0) or 0),
        current_value=float(evidence.get("current_value", 0) or 0),
        baseline_value=float(evidence.get("baseline_value", 0) or 0),
        status_4xx=int(evidence.get("status_4xx", 0) or 0),
        status_5xx=int(evidence.get("status_5xx", 0) or 0),
        p95_ms=float(evidence.get("p95_ms", 0) or 0),
        successful_count=int(evidence.get("successful_count", 0) or 0),
        slow_2s_count=int(evidence.get("slow_2s_count", 0) or 0),
        route_key=record.route_key,
    )


async def _attach_pending_digest_routes(pending: list[dict[str, Any]]) -> None:
    missing = [item for item in pending if not item.get("route")]
    if not missing:
        return
    calls = []
    for item in missing:
        last_seen = _parse_state_time(item.get("last_seen")) or datetime.now(UTC)
        calls.append(
            http_access_service.fetch_samples(
                source=str(item.get("source", "nginx")),
                site=str(item.get("site", "")),
                time_from=last_seen - timedelta(minutes=10),
                time_to=last_seen + timedelta(minutes=1),
                size=10,
                prefer_latency=item.get("kind") == "latency",
                route_keys=[],
            )
        )
    results = await asyncio.gather(*calls, return_exceptions=True)
    for item, samples in zip(missing, results, strict=True):
        if isinstance(samples, Exception):
            logger.warning(
                "http_access_pending_route_sample_failed",
                site=item.get("site"),
                error=str(samples),
            )
            continue
        routes = [
            f"{sample.method} {sample.route}"
            for sample in samples
            if sample.method and sample.route
        ]
        if routes:
            item["route"] = Counter(routes).most_common(1)[0][0]


def _format_pending_digest_line(item: dict[str, Any], *, duration: str) -> str:
    line = (
        f"- {item.get('site')} · "
        f"{_display_source(str(item.get('source', 'nginx')))}"
        f" · {_display_kind(str(item.get('kind', '')))}{duration}"
        f" · {_display_role(str(item.get('role') or 'general'))}"
    )
    route = str(item.get("route") or "").strip()
    return f"{line}\n  主要接口: {route}" if route else line


def _parse_state_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


async def _cleanup_http_access_metrics() -> None:
    deleted = await http_access_service.cleanup_metrics()
    logger.info("http_access_metrics_cleanup_completed", deleted=deleted)


async def _run_scheduled_http_access_patrol() -> dict[str, Any]:
    """Run one patrol under a distributed lease shared by all workers."""
    settings = get_settings()
    lease = await http_access_alert_state.acquire_patrol_lease(
        ttl_seconds=settings.http_access_window_minutes * 60,
    )
    if lease is None:
        result = {
            "run_status": "skipped_overlap",
            "notification_sent": False,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        await http_access_alert_state.save_run_snapshot(result)
        logger.info("http_access_patrol_overlap_skipped")
        return result
    try:
        return await _run_http_access_patrol()
    except Exception as exc:
        await http_access_alert_state.save_run_snapshot(
            {
                "run_status": "failed",
                "notification_sent": False,
                "error": str(exc)[:300],
                "completed_at": datetime.now(UTC).isoformat(),
            }
        )
        raise
    finally:
        await http_access_alert_state.release_patrol_lease(lease)


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
    # Discovery is deliberately independent from business_line.  Hosts become
    # visible immediately but start in observe mode, so discovery can never
    # create an unexpected production notification.
    try:
        tenant_id = await _resolve_tenant_id()
        site_configs = await discover_sites(
            tenant_id,
            windows,
            observed_at=time_to,
        )
    except Exception as exc:
        tenant_id = None
        site_configs = {}
        logger.error("http_access_site_discovery_failed", error=str(exc))
    incidents = detect_incidents(
        windows,
        baselines,
        latency_nginx_min_p95_ms=getattr(
            settings, "http_access_latency_nginx_min_p95_ms", 3000
        ),
        latency_ingress_min_p95_ms=getattr(
            settings, "http_access_latency_ingress_min_p95_ms", 2500
        ),
        latency_min_success_count=getattr(
            settings, "http_access_latency_min_success_count", 200
        ),
        latency_min_slow_count=getattr(
            settings, "http_access_latency_min_slow_count", 50
        ),
        latency_min_slow_rate=getattr(
            settings, "http_access_latency_min_slow_rate", 0.10
        ),
        latency_severe_p95_ms=getattr(
            settings, "http_access_latency_severe_p95_ms", 10000
        ),
        latency_severe_min_slow_count=getattr(
            settings, "http_access_latency_severe_min_slow_count", 20
        ),
    )
    route_eligible_by_source: dict[str, int] = defaultdict(int)
    route_rejected_site_count = 0
    for window in windows.values():
        min_count, _min_rate = route_4xx_threshold(
            window.source,
            nginx_min_count=getattr(
                settings,
                "http_access_nginx_4xx_min_count",
                100,
            ),
            nginx_min_rate=getattr(
                settings,
                "http_access_nginx_4xx_min_rate",
                0.30,
            ),
            ingress_min_count=getattr(
                settings,
                "http_access_ingress_4xx_min_count",
                20,
            ),
            ingress_min_rate=getattr(
                settings,
                "http_access_ingress_4xx_min_rate",
                0.10,
            ),
        )
        if window.status_4xx < min_count:
            continue
        if is_rejected_traffic_window(window):
            route_rejected_site_count += 1
            continue
        route_eligible_by_source[window.source] += 1
    max_route_candidates = getattr(
        settings,
        "http_access_max_route_candidate_sites",
        20,
    )
    route_omitted_site_count = sum(
        max(0, count - max_route_candidates)
        for count in route_eligible_by_source.values()
    )
    route_metrics = []
    if route_eligible_by_source:
        route_metrics = await service.collect_route_metrics(
            windows,
            time_from=time_from,
            time_to=time_to,
        )
    route_baselines = {}
    load_route_baselines = getattr(service, "load_route_baselines", None)
    if route_metrics and callable(load_route_baselines):
        try:
            route_baselines = await load_route_baselines(
                before=time_from,
                days=settings.http_access_baseline_days,
            )
        except Exception as exc:
            logger.warning(
                "http_access_route_baseline_load_failed",
                error=str(exc),
            )
    route_incidents = detect_route_incidents(
        route_metrics,
        windows,
        route_baselines,
        nginx_min_count=getattr(
            settings,
            "http_access_nginx_4xx_min_count",
            100,
        ),
        nginx_min_rate=getattr(
            settings,
            "http_access_nginx_4xx_min_rate",
            0.30,
        ),
        ingress_min_count=getattr(
            settings,
            "http_access_ingress_4xx_min_count",
            20,
        ),
        ingress_min_rate=getattr(
            settings,
            "http_access_ingress_4xx_min_rate",
            0.10,
        ),
        baseline_min_samples=getattr(
            settings,
            "http_access_route_baseline_min_samples",
            6,
        ),
        baseline_min_days=getattr(
            settings,
            "http_access_route_baseline_min_days",
            3,
        ),
        baseline_rate_multiplier=getattr(
            settings,
            "http_access_route_baseline_rate_multiplier",
            2.0,
        ),
        baseline_rate_delta=getattr(
            settings,
            "http_access_route_baseline_rate_delta",
            0.10,
        ),
    )
    if route_incidents:
        incidents.extend(route_incidents)
    auto_learned_count = 0
    if tenant_id and route_metrics and route_baselines:
        try:
            auto_learned_count = await learn_stable_route_behaviors(
                tenant_id,
                route_metrics,
                route_baselines,
                observed_at=time_to,
            )
        except Exception as exc:
            logger.warning("http_access_auto_learning_failed", error=str(exc))
    incidents = merge_cross_source_incidents(incidents)
    candidate_incident_count = len(incidents)
    # If the patrol has no tenant owner it cannot safely apply a tenant policy;
    # retain legacy global behavior rather than silently dropping a P0.
    if tenant_id:
        incidents = filter_enabled_incidents(incidents, site_configs)
        site_enabled_incident_count = len(incidents)
        for incident in incidents:
            incident.site_role = role_for_site(incident.site, site_configs)
        try:
            incidents, learned_suppressed_count = await filter_learned_incidents(
                tenant_id,
                incidents,
                now=time_to,
            )
        except Exception as exc:
            learned_suppressed_count = 0
            logger.warning("http_access_learning_filter_failed_open", error=str(exc))
    else:
        site_enabled_incident_count = len(incidents)
        learned_suppressed_count = 0
    notification_incidents = incidents
    if tenant_id:
        notification_incidents = filter_notification_worthy_incidents(
            incidents,
            site_configs,
            critical_only=getattr(
                settings,
                "http_access_critical_notifications_only",
                True,
            ),
            general_latency_min_p95_ms=getattr(
                settings,
                "http_access_latency_general_min_p95_ms",
                10000,
            ),
            general_latency_min_slow_count=getattr(
                settings,
                "http_access_latency_general_min_slow_count",
                100,
            ),
            general_latency_min_slow_rate=getattr(
                settings,
                "http_access_latency_general_min_slow_rate",
                0.20,
            ),
        )
    notification_policy_suppressed_count = (
        len(incidents) - len(notification_incidents)
    )
    try:
        persisted = await service.persist_metrics(metrics)
    except Exception as exc:
        persisted = 0
        logger.error("http_access_metric_persist_failed", error=str(exc))
    persisted_route_metrics = 0
    persist_route_metrics = getattr(service, "persist_route_metrics", None)
    if route_metrics and callable(persist_route_metrics):
        try:
            persisted_route_metrics = await persist_route_metrics(
                route_metrics,
                observed_at=time_to,
            )
        except Exception as exc:
            logger.error(
                "http_access_route_metric_persist_failed",
                error=str(exc),
            )

    batch = await alert_state.evaluate(notification_incidents, now=time_to)
    try:
        await sync_incident_records(
            tenant_id,
            incidents,
            due_fingerprints={item.fingerprint for item in batch.due},
            observed_at=time_to,
        )
        await mark_recovered_records(
            tenant_id,
            batch.recoveries,
            recovered_at=time_to,
        )
    except Exception as exc:
        # Durable history must not become a new reason to miss a real alert.
        logger.error("http_access_incident_persistence_failed", error=str(exc))
    result: dict[str, Any] = {
        "time_from": time_from.isoformat(),
        "time_to": time_to.isoformat(),
        "metric_count": len(metrics),
        "persisted_metric_count": persisted,
        "persisted_route_metric_count": persisted_route_metrics,
        "route_baseline_count": len(route_baselines),
        "route_metric_count": len(route_metrics),
        "route_candidate_site_count": len(
            {(item.source, item.site) for item in route_metrics}
        ),
        "route_rejected_site_count": route_rejected_site_count,
        "route_omitted_site_count": route_omitted_site_count,
        "incident_count": len(incidents),
        "candidate_incident_count": candidate_incident_count,
        "suppressed_site_mode_count": candidate_incident_count - site_enabled_incident_count,
        "learned_suppressed_count": learned_suppressed_count,
        "notification_policy_suppressed_count": (
            notification_policy_suppressed_count
        ),
        "auto_learned_count": auto_learned_count,
        "incident_site_count": len({item.site for item in incidents}),
        "top_incidents": _build_incident_snapshot(incidents),
        "notification_incident_count": len(batch.due),
        "recovery_count": len(batch.recoveries),
        "notification_sent": False,
        "notification_throttled": False,
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
        return await _finalize_patrol_result(alert_state, result, "normal")

    if not settings.http_access_notification_enabled:
        await alert_state.save(batch, delivered=False)
        logger.warning(
            "http_access_shadow_anomalies_detected",
            sites=sorted({incident.site for incident in batch.due})[:20],
            **result,
        )
        return await _finalize_patrol_result(alert_state, result, "shadow")

    reservation = None
    reserve_summary = getattr(alert_state, "reserve_summary", None)
    if callable(reserve_summary):
        reservation = await reserve_summary(batch.due)
    if callable(reserve_summary) and reservation is None:
        await alert_state.save(batch, delivered=False)
        result["notification_throttled"] = True
        logger.info("http_access_notification_throttled", **result)
        return await _finalize_patrol_result(alert_state, result, "throttled")

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

    if tenant_id and display_incidents:
        from logmind.domain.http_access.diagnostics import (
            enrich_incident_diagnostics,
        )

        try:
            await enrich_incident_diagnostics(
                tenant_id,
                display_incidents,
                site_configs,
                time_from=time_from,
                time_to=time_to,
            )
        except Exception as exc:
            logger.warning("http_access_diagnostics_failed_open", error=str(exc))

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
    try:
        delivered = await _send_notification(message)
    except Exception:
        finish_summary = getattr(alert_state, "finish_summary", None)
        if reservation is not None and callable(finish_summary):
            await finish_summary(reservation, delivered=False)
        raise
    await alert_state.save(batch, delivered=delivered)
    if delivered:
        try:
            await mark_notification_delivered(
                tenant_id,
                batch.due,
                delivered_at=time_to,
            )
        except Exception as exc:
            logger.error("http_access_delivery_persistence_failed", error=str(exc))
    finish_summary = getattr(alert_state, "finish_summary", None)
    if reservation is not None and callable(finish_summary):
        await finish_summary(reservation, delivered=delivered)
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
    status = "notified" if delivered else "notification_failed"
    return await _finalize_patrol_result(alert_state, result, status)


async def _finalize_patrol_result(
    alert_state,
    result: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    result["run_status"] = status
    result["completed_at"] = datetime.now(UTC).isoformat()
    save_snapshot = getattr(alert_state, "save_run_snapshot", None)
    if callable(save_snapshot):
        await save_snapshot(result)
    return result


def _build_incident_snapshot(
    incidents: list[AccessIncident],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Keep privacy-safe candidate details for the 24-hour shadow history."""
    ranked = sorted(
        incidents,
        key=lambda item: (
            _PRIORITY_RANK.get(item.priority, 9),
            -item.impact,
            item.site,
            item.kind,
        ),
    )
    return [
        {
            "priority": item.priority,
            "source": item.source,
            "site": item.site,
            "kind": item.kind,
            "route": item.route_key,
            "request_count": item.request_count,
            "status_4xx": item.status_4xx,
            "status_5xx": item.status_5xx,
            "current_value": round(item.current_value, 6),
            "baseline_value": round(item.baseline_value, 6),
            "p95_ms": round(item.p95_ms, 3),
            "successful_count": item.successful_count,
            "slow_2s_count": item.slow_2s_count,
        }
        for item in ranked[:limit]
    ]


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
        samples = sample_map.get((incident.source, incident.site), [])
        if incident.route_key:
            incident.samples = [
                sample
                for sample in samples
                if f"{sample.method} {sample.route}" == incident.route_key
            ]
        else:
            incident.samples = samples


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
                "successful_request_count": max(
                    0,
                    incident.request_count
                    - incident.status_4xx
                    - incident.status_5xx,
                ),
                "current_value": round(incident.current_value, 6),
                "baseline_value": round(incident.baseline_value, 6),
                "status_4xx": incident.status_4xx,
                "status_counts": incident.status_counts,
                "upstream_status_counts": incident.upstream_status_counts,
                "status_5xx": incident.status_5xx,
                "gateway_5xx": incident.gateway_5xx,
                "upstream_5xx": incident.upstream_5xx,
                "p95_ms": round(incident.p95_ms, 3),
                "successful_count": incident.successful_count,
                "slow_2s_count": incident.slow_2s_count,
                "route": incident.route_key,
                "samples": [
                    sample.to_ai_dict() for sample in incident.samples[:5]
                ],
                "application_evidence": incident.diagnostic_evidence[:10],
                "knowledge_sources": incident.knowledge_sources[:10],
                "code_findings": incident.code_findings[:3],
            }
        )

    system_prompt = (
        "你只分析给定的 Nginx/Ingress HTTP access 聚合指标和脱敏样本。"
        "source=nginx通常对应C#站点，source=ingress通常对应Kubernetes Java站点，"
        "但这只用于给出排查方向，不能据此臆测应用异常。query_parameters和"
        "body_fields仅是脱敏后的参数字段名，可用于指出缺少或校验失败的字段范围。"
        "不得推断数据库写入失败、SQL截断、DataIntegrityViolationException、"
        "C#/.NET异常或任何输入中不存在的应用内部故障。"
        "upstream出现5xx时可判断为后端/upstream异常；外层5xx且无upstream证据时"
        "只能判断为网关路由、连接或上游不可达；上下游均400且耗时低时只能判断为"
        "客户端参数或业务校验异常；499表示客户端在响应前断开。"
        "证据不足时写清楚应先检查哪一项，"
        "application_evidence来自同时间段应用错误日志，knowledge_sources来自内部知识库，"
        "code_findings只在CI提供的线上commit中检索；只有应用堆栈命中代码符号或相关文件"
        "同时出现在部署diff时，才能提高代码关联置信度。"
        "不要复述指标，不使用“访问层现象”等晦涩套话。"
        "使用运维人员一眼能看懂的中文，不超过60字。"
        "返回严格JSON：{\"items\":[{\"key\":\"原key\",\"summary\":\"结论和动作\"}]}。"
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
        summaries = _parse_ai_summaries(
            response.content,
            evidence_by_key={
                item.key: " ".join(item.diagnostic_evidence)
                for item in incidents
            },
        )
        for incident in incidents:
            incident.ai_summary = summaries.get(incident.key, "")
    except Exception as exc:
        logger.warning("http_access_ai_summary_failed", error=str(exc))


def _parse_ai_summaries(
    content: str,
    evidence_by_key: dict[str, str] | None = None,
) -> dict[str, str]:
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
        summary = " ".join(str(item.get("summary", "")).split())[:80]
        unsupported = _FORBIDDEN_UNSUPPORTED_AI_CLAIMS_RE.search(summary)
        evidence = (evidence_by_key or {}).get(key, "")
        if (
            key
            and summary
            and (not unsupported or _claim_is_supported(unsupported.group(0), evidence))
            and _ACTIONABLE_AI_SUMMARY_RE.search(summary)
        ):
            summaries[key] = summary
    return summaries


def _claim_is_supported(claim: str, evidence: str) -> bool:
    """Allow internal conclusions only when application evidence names them."""
    if not evidence:
        return False
    claim_lower = claim.lower()
    evidence_lower = evidence.lower()
    aliases = {
        "数据库": ("database", "sql", "dataintegrity", "数据库"),
        "sql": ("sql", "database", "dataintegrity"),
        "exception": ("exception",),
        "c#": ("c#", ".net", "system."),
        ".net": (".net", "system."),
        "redis": ("redis",),
        "kafka": ("kafka",),
        "zookeeper": ("zookeeper",),
        "消息队列": ("消息队列", "message queue", "rabbitmq", "kafka"),
        "线程池": ("线程池", "threadpool"),
        "连接池": ("连接池", "connection pool"),
        "内存泄漏": ("内存泄漏", "outofmemory", "memory leak"),
        "cpu": ("cpu",),
        "gc": (" gc ", "garbage collection"),
    }
    markers = next(
        (values for name, values in aliases.items() if name in claim_lower),
        (claim_lower,),
    )
    return any(marker in evidence_lower for marker in markers)


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
        f"## {icon} HTTP关键风险告警",
        f"**时间窗口**: {local_from:%Y-%m-%d %H:%M} ~ {local_to:%H:%M}",
        f"**需要关注**: P0 {p0_count}个，P1 {p1_count}个",
        "",
    ]

    for index, site in enumerate(ranked_sites, start=1):
        site_incidents = grouped[site]
        priority = site_priorities[site]
        sources = "/".join(sorted({
            _display_source(source)
            for item in site_incidents
            for source in (item.sources or [item.source])
        }))
        role = next((item.site_role for item in site_incidents if item.site_role), "general")
        lines.append(f"**{index}. {site} [{priority}] · {sources} · {_display_role(role)}**")
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
            top_route = Counter(sample_routes).most_common(1)[0][0]
            sample_label = (
                "慢请求样本" if any(
                    item.kind == "latency" for item in site_incidents
                ) else "主要接口"
            )
            lines.append(f"- {sample_label}: {top_route}")
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
            lines.append(
                f"- 异常upstream: {Counter(upstreams).most_common(1)[0][0]}"
            )
        query_parameters = [
            name
            for sample in samples
            for name in sample.query_parameters
        ]
        body_fields = [
            name
            for sample in samples
            for name in sample.body_fields
        ]
        has_4xx_incident = any(
            item.kind in {"http_4xx", "route_4xx"} for item in site_incidents
        )
        if has_4xx_incident and query_parameters:
            lines.append(
                "- 查询参数字段: "
                + "、".join(
                    name
                    for name, _count in Counter(query_parameters).most_common(8)
                )
            )
        if has_4xx_incident and body_fields:
            lines.append(
                "- 请求体字段: "
                + "、".join(
                    name for name, _count in Counter(body_fields).most_common(8)
                )
            )
        ai_summaries = list(
            dict.fromkeys(
                item.ai_summary for item in site_incidents if item.ai_summary
            )
        )
        if ai_summaries:
            lines.append(f"- 建议: {ai_summaries[0]}")
        else:
            lines.append(f"- 建议: {_deterministic_diagnosis(site_incidents)}")
        knowledge_sources = list(dict.fromkeys(
            source for item in site_incidents for source in item.knowledge_sources
        ))
        if knowledge_sources:
            lines.append("- 参考经验: " + "、".join(knowledge_sources[:3]))
        code_findings = [
            finding for item in site_incidents for finding in item.code_findings
            if finding.get("confidence") in {"high", "medium"}
        ]
        if code_findings:
            finding = code_findings[0]
            files = finding.get("matched_files") or []
            if files:
                lines.append(
                    f"- 代码关联({finding.get('confidence')}): {files[0]}"
                    f" @{str(finding.get('commit_sha', ''))[:12]}"
                )
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
        baseline_text = (
            f"平时{incident.baseline_value * 100:.2f}%"
            if incident.baseline_value > 0
            else "暂无历史基线"
        )
        return (
            f"服务错误: {incident.status_5xx}/{incident.request_count}"
            f"（{incident.current_value * 100:.2f}%，{baseline_text}"
            f"{_observed_text(incident)}）"
        )
    if incident.kind == "http_4xx":
        return (
            f"请求被拒绝: {incident.status_4xx}/{incident.request_count}"
            f"（{incident.current_value * 100:.2f}%，"
            f"平时{incident.baseline_value * 100:.2f}%）"
        )
    if incident.kind == "route_4xx":
        status_label = _dominant_status_label(incident)
        baseline_text = (
            f"，平时{incident.baseline_value * 100:.2f}%"
            if incident.baseline_value > 0
            else ""
        )
        return (
            f"接口异常: {incident.route_key}，{status_label} "
            f"{incident.status_4xx}/{incident.request_count}"
            f"（{incident.current_value * 100:.2f}%{baseline_text}"
            f"{_observed_text(incident)}）"
        )
    if incident.kind == "latency":
        affected = ""
        if incident.successful_count > 0 and incident.slow_2s_count > 0:
            affected = (
                f"；≥2秒 {incident.slow_2s_count}/"
                f"{incident.successful_count}次"
                f"（{incident.slow_2s_count / incident.successful_count:.1%}）"
            )
        return (
            f"响应变慢: 成功请求P95 {_duration_text(incident.current_value)}"
            f"（平时{_duration_text(incident.baseline_value)}"
            f"{_observed_text(incident)}{affected}）"
        )
    return (
        f"流量骤降: 当前{incident.current_value:.0f}次"
        f"（平时{incident.baseline_value:.0f}次/5分钟）"
    )


def _deterministic_diagnosis(incidents: list[AccessIncident]) -> str:
    if any(item.upstream_5xx > 0 for item in incidents):
        return "后端服务已返回5xx，优先检查对应服务实例和最近发布。"
    if any(
        item.gateway_5xx > 0 and item.upstream_5xx == 0
        for item in incidents
    ):
        return "网关无法正常连接后端，检查路由、端口和服务实例是否可达。"
    if any(item.kind in {"http_4xx", "route_4xx"} for item in incidents):
        status_counts: defaultdict[int, int] = defaultdict(int)
        for incident in incidents:
            for status, count in incident.status_counts.items():
                status_counts[status] += count
        dominant_status = (
            max(status_counts, key=status_counts.get) if status_counts else 0
        )
        if dominant_status == 404:
            if any(item.source == "ingress" for item in incidents):
                return "404明显增多，检查Ingress路径、Java路由和服务版本是否一致。"
            return "404明显增多，检查C#路由映射、发布版本和客户端请求路径。"
        if dominant_status == 499:
            if max((item.p95_ms for item in incidents), default=0) >= 2000:
                return "499伴随长耗时，检查后端响应变慢及调用方超时设置。"
            return "客户端主动断开增多，检查调用方超时、取消请求和网络质量。"
        return {
            400: "400明显增多，结合上方参数字段检查必填项、格式和业务校验日志。",
            401: "认证失败增多，检查凭证是否过期以及认证服务状态。",
            403: "权限拒绝增多，检查账号权限、鉴权策略和来源限制。",
            405: "请求方法不被支持，检查客户端Method和后端接口映射。",
            406: "服务无法接受请求格式，检查Accept、Content-Type及协商规则。",
            408: "客户端请求超时增多，检查网络质量和请求发送是否完整。",
            429: "请求被限流，检查调用量、限流策略和客户端重试。",
        }.get(
            dominant_status,
            "接口4xx增多，结合参数字段和对应应用校验日志定位。",
        )
    if any(item.kind == "latency" for item in incidents):
        return "成功请求持续变慢，优先检查后端处理耗时和网关排队。"
    if any(item.kind == "traffic_drop" for item in incidents):
        return "访问量持续骤降，检查入口可用性和日志采集是否中断。"
    return "访问指标异常，先检查主要接口和对应后端服务。"


def _duration_text(milliseconds: float) -> str:
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.1f}秒"
    return f"{milliseconds:.0f}毫秒"


def _observed_text(incident: AccessIncident) -> str:
    return (
        f"，持续{incident.observed_minutes}分钟"
        if incident.observed_minutes > 0
        else ""
    )


def _display_source(source: str) -> str:
    return "Ingress/Java" if source == "ingress" else "Nginx/C#"


def _display_role(role: str) -> str:
    return {
        "app": "APP", "account": "账号", "payment": "支付",
        "front": "前台", "cdn_download": "CDN/下载", "general": "通用",
    }.get(role, "通用")


def _dominant_status_label(incident: AccessIncident) -> str:
    counts = {
        status: count
        for status, count in incident.status_counts.items()
        if 400 <= status < 500 and count > 0
    }
    if not counts:
        counts = dict(
            Counter(
                sample.status
                for sample in incident.samples
                if 400 <= sample.status < 500
            )
        )
    if not counts:
        return "4xx"
    status, count = max(counts.items(), key=lambda item: item[1])
    if count >= incident.status_4xx * 0.80:
        return str(status)
    return f"4xx（主要{status}）"


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
