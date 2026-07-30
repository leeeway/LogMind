"""Read-only operational status for the global HTTP access patrol."""

from collections import Counter

from fastapi import APIRouter, Query

from logmind.core.config import get_settings
from logmind.domain.http_access.state import http_access_alert_state

router = APIRouter(prefix="/http-access", tags=["HTTP Access"])


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
        "notification_cooldown_minutes": (
            settings.http_access_notification_cooldown_minutes
        ),
        "repeat_notification_minutes": getattr(
            settings,
            "http_access_repeat_notification_minutes",
            240,
        ),
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
