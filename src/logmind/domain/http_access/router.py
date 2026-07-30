"""Read-only operational status for the global HTTP access patrol."""

from fastapi import APIRouter

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
        "baseline": {
            "days": settings.http_access_baseline_days,
            "same_time_slot_minutes": settings.http_access_baseline_slot_minutes,
        },
        "indexes": list(settings.http_access_index_list),
        "last_run": await http_access_alert_state.get_run_snapshot(),
    }
