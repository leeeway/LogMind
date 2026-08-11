"""Read-only operational status for the global HTTP access patrol."""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from logmind.core.config import get_settings
from logmind.core.dependencies import CurrentUser, DBSession
from logmind.domain.http_access.governance import (
    CRITICAL_ROLES,
    VALID_ENVIRONMENTS,
    VALID_MODES,
    VALID_ROLES,
    discover_sites,
)
from logmind.domain.http_access.incident_store import record_feedback
from logmind.domain.http_access.models import aggregate_metrics
from logmind.domain.http_access.service import http_access_service
from logmind.domain.http_access.site_config import (
    GitRepositoryConfig,
    HttpAccessIncidentRecord,
    HttpAccessLearningRule,
    HttpAccessSiteConfig,
)
from logmind.domain.http_access.state import http_access_alert_state
from logmind.domain.tenant.audit import AuditLog

router = APIRouter(prefix="/http-access", tags=["HTTP Access"])


class SiteConfigUpdate(BaseModel):
    environment: str | None = None
    role: str | None = None
    monitoring_mode: str | None = None
    enable_4xx: bool | None = None
    enable_latency: bool | None = None
    enable_traffic_drop: bool | None = None
    owner: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)
    diagnostic_business_line_id: str | None = None
    repository_id: str | None = None
    deployment_service_name: str | None = Field(default=None, max_length=200)


class SiteConfigBulkUpdate(SiteConfigUpdate):
    site_ids: list[str] = Field(min_length=1, max_length=500)


class SiteDiscoveryRequest(BaseModel):
    # A short lookback makes first use practical without rescanning history.
    window_minutes: int = Field(default=60, ge=5, le=240)


class IncidentFeedbackRequest(BaseModel):
    action: str = Field(pattern=r"^(valid|false_positive|expected|resolved)$")
    comment: str = Field(default="", max_length=2000)


def _site_payload(item: HttpAccessSiteConfig) -> dict:
    try:
        sources = json.loads(item.sources or "[]")
    except json.JSONDecodeError:
        sources = []
    return {
        "id": item.id, "site": item.site, "sources": sources,
        "environment": item.environment, "role": item.role,
        "monitoring_mode": item.monitoring_mode,
        "enable_4xx": item.enable_4xx, "enable_latency": item.enable_latency,
        "enable_traffic_drop": item.enable_traffic_drop,
        "first_seen_at": item.first_seen_at, "last_seen_at": item.last_seen_at,
        "last_status": item.last_status, "owner": item.owner, "notes": item.notes,
        "updated_at": item.updated_at,
        "diagnostic_business_line_id": item.diagnostic_business_line_id,
        "repository_id": item.repository_id,
        "deployment_service_name": item.deployment_service_name,
    }


def _validate_site_changes(values: dict) -> None:
    if "environment" in values and values["environment"] not in VALID_ENVIRONMENTS:
        raise HTTPException(422, "environment must be production or test")
    if "role" in values and values["role"] not in VALID_ROLES:
        raise HTTPException(422, "invalid site role")
    if "monitoring_mode" in values and values["monitoring_mode"] not in VALID_MODES:
        raise HTTPException(422, "monitoring_mode must be observe, enabled or disabled")
    if (
        values.get("enable_traffic_drop")
        and "role" in values
        and values.get("role") not in CRITICAL_ROLES
    ):
        raise HTTPException(422, "traffic-drop monitoring requires an APP/account/payment/front role")


async def _audit_site_change(session, user, item, values: dict) -> None:
    session.add(AuditLog(
        tenant_id=user.tenant_id, user_id=user.sub, username="",
        action="http_access.site_config.update", resource_type="http_access_site",
        resource_id=item.id, details=json.dumps({"site": item.site, "changes": values}, ensure_ascii=False),
    ))


@router.get("/sites")
async def list_http_access_sites(
    session: DBSession,
    user: CurrentUser,
    search: str = "",
    source: str = "",
    environment: str = "",
    role: str = "",
    monitoring_mode: str = "",
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    stmt = select(HttpAccessSiteConfig).where(HttpAccessSiteConfig.tenant_id == user.tenant_id)
    if search:
        stmt = stmt.where(HttpAccessSiteConfig.site.ilike(f"%{search.strip()}%"))
    if environment:
        stmt = stmt.where(HttpAccessSiteConfig.environment == environment)
    if role:
        stmt = stmt.where(HttpAccessSiteConfig.role == role)
    if monitoring_mode:
        stmt = stmt.where(HttpAccessSiteConfig.monitoring_mode == monitoring_mode)
    if source:
        stmt = stmt.where(HttpAccessSiteConfig.sources.ilike(f'%"{source}"%'))
    result = await session.execute(stmt.order_by(HttpAccessSiteConfig.last_seen_at.desc()).limit(limit))
    items = [_site_payload(item) for item in result.scalars().all()]
    return {"items": items, "total": len(items)}


@router.post("/sites/discover")
async def discover_http_access_sites(
    req: SiteDiscoveryRequest,
    user: CurrentUser,
) -> dict:
    """Discover active hosts for the signed-in tenant without business lines.

    This is intentionally available while the scheduled patrol is in shadow or
    disabled mode, so operators can govern sites before enabling notifications.
    """
    time_to = datetime.now(UTC).replace(second=0, microsecond=0)
    time_from = time_to - timedelta(minutes=req.window_minutes)
    metrics = await http_access_service.collect_window(time_from, time_to)
    configs = await discover_sites(
        user.tenant_id,
        aggregate_metrics(metrics),
        observed_at=time_to,
    )
    return {
        "discovered": len(configs),
        "metric_count": len(metrics),
        "time_from": time_from.isoformat(),
        "time_to": time_to.isoformat(),
    }


@router.patch("/sites/{site_id}")
async def update_http_access_site(site_id: str, req: SiteConfigUpdate, session: DBSession, user: CurrentUser) -> dict:
    item = await session.get(HttpAccessSiteConfig, site_id)
    if not item or item.tenant_id != user.tenant_id:
        raise HTTPException(404, "HTTP access site not found")
    # ``exclude_unset`` preserves an explicit JSON null, allowing an operator
    # to remove a business-line or repository mapping.
    values = req.model_dump(exclude_unset=True)
    for text_field in ("owner", "notes", "deployment_service_name"):
        if text_field in values and values[text_field] is None:
            values[text_field] = ""
    if values.get("role") and values["role"] not in CRITICAL_ROLES:
        values.setdefault("enable_traffic_drop", False)
    _validate_site_changes(values)
    if values.get("diagnostic_business_line_id"):
        from logmind.domain.tenant.models import BusinessLine
        business_line = await session.get(BusinessLine, values["diagnostic_business_line_id"])
        if not business_line or business_line.tenant_id != user.tenant_id:
            raise HTTPException(422, "diagnostic business line is not in this tenant")
    if values.get("repository_id"):
        repository = await session.get(GitRepositoryConfig, values["repository_id"])
        if not repository or repository.tenant_id != user.tenant_id:
            raise HTTPException(422, "repository is not in this tenant")
    effective_role = values.get("role", item.role)
    effective_traffic_drop = values.get(
        "enable_traffic_drop", item.enable_traffic_drop
    )
    if effective_traffic_drop and effective_role not in CRITICAL_ROLES:
        raise HTTPException(422, "traffic-drop monitoring requires a critical role")
    for key, value in values.items():
        setattr(item, key, value)
    if item.environment == "test" or item.monitoring_mode == "disabled":
        item.last_status = "silent"
    await _audit_site_change(session, user, item, values)
    await session.flush()
    return _site_payload(item)


@router.patch("/site-bulk-update")
async def bulk_update_http_access_sites(req: SiteConfigBulkUpdate, session: DBSession, user: CurrentUser) -> dict:
    values = req.model_dump(exclude_unset=True, exclude={"site_ids"})
    for text_field in ("owner", "notes", "deployment_service_name"):
        if text_field in values and values[text_field] is None:
            values[text_field] = ""
    if values.get("role") and values["role"] not in CRITICAL_ROLES:
        values.setdefault("enable_traffic_drop", False)
    _validate_site_changes(values)
    if values.get("diagnostic_business_line_id"):
        from logmind.domain.tenant.models import BusinessLine
        business_line = await session.get(BusinessLine, values["diagnostic_business_line_id"])
        if not business_line or business_line.tenant_id != user.tenant_id:
            raise HTTPException(422, "diagnostic business line is not in this tenant")
    if values.get("repository_id"):
        repository = await session.get(GitRepositoryConfig, values["repository_id"])
        if not repository or repository.tenant_id != user.tenant_id:
            raise HTTPException(422, "repository is not in this tenant")
    result = await session.execute(select(HttpAccessSiteConfig).where(
        HttpAccessSiteConfig.tenant_id == user.tenant_id,
        HttpAccessSiteConfig.id.in_(req.site_ids),
    ))
    items = list(result.scalars().all())
    for item in items:
        if values.get("enable_traffic_drop") and values.get("role", item.role) not in CRITICAL_ROLES:
            raise HTTPException(422, f"{item.site} is not a critical role")
        for key, value in values.items():
            setattr(item, key, value)
        await _audit_site_change(session, user, item, values)
    await session.flush()
    return {"updated": len(items)}


@router.get("/status")
async def get_http_access_patrol_status() -> dict:
    """Expose safe runtime settings and the latest patrol outcome."""
    settings = get_settings()
    if not settings.http_access_patrol_enabled:
        mode = "disabled"
    elif settings.http_access_notification_enabled:
        mode = "active"
    else:
        mode = "shadow"

    return {
        "mode": mode,
        "patrol_enabled": settings.http_access_patrol_enabled,
        "notification_enabled": settings.http_access_notification_enabled,
        "recovery_notification_enabled": (
            settings.http_access_recovery_notification_enabled
        ),
        "ai_enabled": settings.http_access_ai_enabled,
        "window_minutes": settings.http_access_window_minutes,
        "notification_global_cooldown_minutes": 0,
        "notification_delivery_lease_seconds": 60,
        "p1_repeat_policy": "escalation_only",
        "max_route_candidate_sites": (
            settings.http_access_max_route_candidate_sites
        ),
        "route_4xx_thresholds": {
            "nginx_csharp": {
                "min_count": getattr(
                    settings, "http_access_nginx_4xx_min_count", 100
                ),
                "min_rate": getattr(
                    settings, "http_access_nginx_4xx_min_rate", 0.30
                ),
            },
            "ingress_java": {
                "min_count": getattr(
                    settings, "http_access_ingress_4xx_min_count", 20
                ),
                "min_rate": getattr(
                    settings, "http_access_ingress_4xx_min_rate", 0.10
                ),
            },
        },
        "route_baseline": {
            "index": getattr(
                settings,
                "http_access_route_metrics_index",
                "logmind-http-access-route-metrics-v1",
            ),
            "min_samples": getattr(
                settings,
                "http_access_route_baseline_min_samples",
                6,
            ),
            "min_days": getattr(
                settings,
                "http_access_route_baseline_min_days",
                3,
            ),
            "rate_multiplier": getattr(
                settings,
                "http_access_route_baseline_rate_multiplier",
                2.0,
            ),
            "rate_delta": getattr(
                settings,
                "http_access_route_baseline_rate_delta",
                0.10,
            ),
        },
        "run_history_limit": settings.http_access_run_history_limit,
        "baseline": {
            "days": settings.http_access_baseline_days,
            "same_time_slot_minutes": settings.http_access_baseline_slot_minutes,
        },
        "indexes": list(settings.http_access_index_list),
        "last_run": await http_access_alert_state.get_run_snapshot(),
    }


@router.get("/governance-status")
async def get_http_access_governance_status(session: DBSession, user: CurrentUser) -> dict:
    settings = get_settings()
    mode_rows = await session.execute(
        select(HttpAccessSiteConfig.monitoring_mode, func.count())
        .where(HttpAccessSiteConfig.tenant_id == user.tenant_id)
        .group_by(HttpAccessSiteConfig.monitoring_mode)
    )
    modes = {str(mode): int(count) for mode, count in mode_rows.all()}
    pending_count = int((await session.execute(
        select(func.count()).select_from(HttpAccessIncidentRecord).where(
            HttpAccessIncidentRecord.tenant_id == user.tenant_id,
            HttpAccessIncidentRecord.notification_pending.is_(True),
        )
    )).scalar_one())
    unresolved_count = int((await session.execute(
        select(func.count()).select_from(HttpAccessIncidentRecord).where(
            HttpAccessIncidentRecord.tenant_id == user.tenant_id,
            HttpAccessIncidentRecord.status.in_(["open", "acknowledged"]),
        )
    )).scalar_one())
    last_run = await http_access_alert_state.get_run_snapshot()
    last_time = last_run.get("completed_at") or last_run.get("time_to")
    stale = True
    if last_time:
        try:
            parsed = datetime.fromisoformat(str(last_time))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            stale = datetime.now(UTC) - parsed > timedelta(
                minutes=settings.http_access_window_minutes * 2
            )
        except ValueError:
            stale = True
    healthy = bool(
        settings.http_access_patrol_enabled
        and modes.get("enabled", 0) > 0
        and not stale
    )
    return {
        "healthy": healthy,
        "patrol_enabled": settings.http_access_patrol_enabled,
        "notification_enabled": settings.http_access_notification_enabled,
        "site_modes": {
            "enabled": modes.get("enabled", 0),
            "observe": modes.get("observe", 0),
            "disabled": modes.get("disabled", 0),
        },
        "pending_notification_count": pending_count,
        "unresolved_incident_count": unresolved_count,
        "heartbeat_stale": stale,
        "last_run": last_run,
    }


@router.get("/runs")
async def get_http_access_patrol_runs(
    limit: int = Query(default=288, ge=1, le=2016),
) -> dict:
    """Return recent patrol runs and a compact shadow-mode summary."""
    runs = await http_access_alert_state.get_run_history(limit=limit)
    return {
        "summary": _summarize_runs(runs),
        "runs": runs,
    }


@router.get("/pending")
async def list_pending_http_access_incidents(session: DBSession, user: CurrentUser) -> dict:
    """Durable unresolved incidents for the current tenant."""
    result = await session.execute(
        select(HttpAccessIncidentRecord)
        .where(
            HttpAccessIncidentRecord.tenant_id == user.tenant_id,
            HttpAccessIncidentRecord.status.in_(["open", "acknowledged"]),
        )
        .order_by(HttpAccessIncidentRecord.priority, HttpAccessIncidentRecord.last_seen_at.desc())
        .limit(500)
    )
    return {"items": [_incident_payload(item) for item in result.scalars().all()]}


@router.get("/learning-rules")
async def list_http_access_learning_rules(session: DBSession, user: CurrentUser) -> dict:
    result = await session.execute(
        select(HttpAccessLearningRule)
        .where(HttpAccessLearningRule.tenant_id == user.tenant_id)
        .order_by(HttpAccessLearningRule.updated_at.desc())
        .limit(500)
    )
    return {
        "items": [
            {
                "id": item.id,
                "site": item.site,
                "kind": item.kind,
                "disposition": item.disposition,
                "source": item.source,
                "confidence": item.confidence,
                "reason": item.reason,
                "expires_at": item.expires_at,
                "hit_count": item.hit_count,
                "last_hit_at": item.last_hit_at,
            }
            for item in result.scalars().all()
        ]
    }


@router.get("/incidents/{incident_id}")
async def get_http_access_incident(incident_id: str, session: DBSession, user: CurrentUser) -> dict:
    item = await session.get(HttpAccessIncidentRecord, incident_id)
    if not item or item.tenant_id != user.tenant_id:
        raise HTTPException(404, "HTTP access incident not found")
    return _incident_payload(item)


@router.post("/incidents/{incident_id}/feedback")
async def submit_http_access_feedback(
    incident_id: str,
    req: IncidentFeedbackRequest,
    session: DBSession,
    user: CurrentUser,
) -> dict:
    item = await session.get(HttpAccessIncidentRecord, incident_id)
    if not item or item.tenant_id != user.tenant_id:
        raise HTTPException(404, "HTTP access incident not found")
    await record_feedback(
        session,
        item,
        action=req.action,
        comment=req.comment.strip(),
        user_id=user.sub,
    )
    await session.flush()
    session.add(AuditLog(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        username="",
        action=f"http_access.incident.{req.action}",
        resource_type="http_access_incident",
        resource_id=item.id,
        details=json.dumps({"site": item.site, "comment": req.comment[:500]}, ensure_ascii=False),
    ))
    return _incident_payload(item)


def _incident_payload(item: HttpAccessIncidentRecord) -> dict:
    def parsed(value: str, fallback):
        try:
            return json.loads(value or "")
        except (TypeError, json.JSONDecodeError):
            return fallback
    return {
        "id": item.id,
        "fingerprint": item.fingerprint,
        "site": item.site,
        "source": item.source,
        "sources": parsed(item.sources, []),
        "kind": item.kind,
        "priority": item.priority,
        "route_key": item.route_key,
        "status": item.status,
        "first_seen_at": item.first_seen_at,
        "last_seen_at": item.last_seen_at,
        "recovered_at": item.recovered_at,
        "last_notified_at": item.last_notified_at,
        "last_digest_at": item.last_digest_at,
        "notification_pending": item.notification_pending,
        "notification_count": item.notification_count,
        "current_impact": item.current_impact,
        "peak_impact": item.peak_impact,
        "evidence": parsed(item.evidence_json, {}),
        "diagnosis": _sanitize_diagnosis(parsed(item.diagnosis_json, {})),
        "feedback": item.feedback,
        "feedback_comment": item.feedback_comment,
        "handled_by": item.handled_by,
    }


def _sanitize_diagnosis(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    result = dict(value)
    findings = []
    for finding in result.get("code_findings", []):
        if not isinstance(finding, dict):
            continue
        safe = dict(finding)
        safe["snippets"] = [
            {key: snippet.get(key) for key in ("file", "line", "symbol")}
            for snippet in safe.get("snippets", [])
            if isinstance(snippet, dict)
        ]
        findings.append(safe)
    result["code_findings"] = findings
    return result


def _summarize_runs(runs: list[dict]) -> dict:
    status_counts: Counter[str] = Counter()
    incident_type_counts: Counter[str] = Counter()
    affected_site_windows: Counter[str] = Counter()
    notification_count = 0

    for run in runs:
        status_counts[str(run.get("run_status") or "unknown")] += 1
        notification_count += int(bool(run.get("notification_sent")))
        sites_in_run: set[str] = set()
        incidents = run.get("top_incidents", [])
        if not isinstance(incidents, list):
            continue
        for incident in incidents:
            if not isinstance(incident, dict):
                continue
            kind = str(incident.get("kind") or "unknown")
            site = str(incident.get("site") or "")
            incident_type_counts[kind] += 1
            if site:
                sites_in_run.add(site)
        affected_site_windows.update(sites_in_run)

    newest = runs[0] if runs else {}
    oldest = runs[-1] if runs else {}
    return {
        "run_count": len(runs),
        "from": oldest.get("time_from") or oldest.get("completed_at"),
        "to": newest.get("time_to") or newest.get("completed_at"),
        "status_counts": dict(status_counts),
        "notification_count": notification_count,
        "incident_type_counts": dict(incident_type_counts),
        "top_sites": [
            {"site": site, "affected_windows": count}
            for site, count in affected_site_windows.most_common(10)
        ],
    }
