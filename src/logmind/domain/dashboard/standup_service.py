"""
Daily Standup Service

Shared helpers for manual and scheduled standup generation/sharing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from logmind.core.logging import get_logger

logger = get_logger(__name__)

_STANDUP_TZ = ZoneInfo("Asia/Shanghai")
_AUTO_SHARE_LOCK_TTL_SECONDS = 30 * 60
_AUTO_SHARE_SUCCESS_TTL_SECONDS = 7 * 24 * 60 * 60
_AUTO_SHARE_WINDOW_MINUTES = 5


def default_standup_target_date(now_utc: datetime | None = None) -> datetime:
    """Default UI/report target: previous local day at local midnight."""
    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(_STANDUP_TZ) - timedelta(days=1)
    return datetime(local_now.year, local_now.month, local_now.day, tzinfo=_STANDUP_TZ)


def auto_share_target_date(now_utc: datetime | None = None) -> datetime:
    """Auto-share target: current local day at local midnight."""
    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(_STANDUP_TZ)
    return datetime(local_now.year, local_now.month, local_now.day, tzinfo=_STANDUP_TZ)


def parse_standup_date(date_str: str | None) -> datetime | None:
    """Parse YYYY-MM-DD into local midnight."""
    if not date_str:
        return None
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.replace(tzinfo=_STANDUP_TZ)


def load_daily_standup_settings(raw_settings: str | None) -> dict:
    """
    Load standup auto-share settings from tenant.settings JSON.

    Default behavior enables daily auto-share at 21:00 Asia/Shanghai.
    Set {"daily_standup": {"enabled": false}} to opt out.
    """
    defaults = {
        "enabled": True,
        "send_hour": 21,
        "send_minute": 0,
    }
    if not raw_settings:
        return defaults

    try:
        payload = json.loads(raw_settings)
    except (TypeError, json.JSONDecodeError):
        logger.warning("invalid_tenant_settings_json_for_standup")
        return defaults

    config = payload.get("daily_standup")
    if not isinstance(config, dict):
        return defaults

    enabled = config.get("enabled", defaults["enabled"])
    send_hour = config.get("send_hour", defaults["send_hour"])
    send_minute = config.get("send_minute", defaults["send_minute"])

    if not isinstance(enabled, bool):
        enabled = defaults["enabled"]
    if not isinstance(send_hour, int) or not 0 <= send_hour <= 23:
        send_hour = defaults["send_hour"]
    if not isinstance(send_minute, int) or not 0 <= send_minute <= 59:
        send_minute = defaults["send_minute"]

    return {
        "enabled": enabled,
        "send_hour": send_hour,
        "send_minute": send_minute,
    }


def is_daily_standup_due(raw_settings: str | None, now_utc: datetime | None = None) -> bool:
    """Check whether the tenant is due for auto-share within the dispatch window."""
    config = load_daily_standup_settings(raw_settings)
    if not config["enabled"]:
        return False

    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(_STANDUP_TZ)
    current_minutes = local_now.hour * 60 + local_now.minute
    target_minutes = config["send_hour"] * 60 + config["send_minute"]

    return target_minutes <= current_minutes < target_minutes + _AUTO_SHARE_WINDOW_MINUTES


async def get_tenant_standup_webhook_urls(session, tenant_id: str) -> list[str]:
    """Collect unique webhook URLs from active business lines for a tenant."""
    from sqlalchemy import select

    from logmind.domain.tenant.models import BusinessLine

    stmt = select(BusinessLine.webhook_url).where(
        BusinessLine.tenant_id == tenant_id,
        BusinessLine.is_active == True,  # noqa: E712
        BusinessLine.webhook_url != None,  # noqa: E711
        BusinessLine.webhook_url != "",
    ).distinct()
    rows = (await session.execute(stmt)).scalars().all()
    return sorted(set(rows))


def build_standup_share_markdown(report_date: str, ai_summary: str) -> str:
    """Build a stable share payload with a clear title."""
    return f"## 📋 LogMind 每日站会 - {report_date}\n\n{ai_summary}"


async def share_standup_for_tenant(
    tenant_id: str,
    target_date: datetime | None = None,
) -> dict:
    """Generate a standup report and send it to all tenant webhooks."""
    from logmind.core.database import get_db_context
    from logmind.domain.alert.channels.webhook import send_webhook_notification
    from logmind.domain.dashboard.standup_generator import generate_standup_report

    result = await generate_standup_report(tenant_id, target_date)

    async with get_db_context() as session:
        webhook_urls = await get_tenant_standup_webhook_urls(session, tenant_id)

    sent_count = 0
    content = build_standup_share_markdown(result["date"], result["ai_summary"])

    for url in webhook_urls:
        try:
            ok = await send_webhook_notification(content, webhook_url=url)
            if ok:
                sent_count += 1
        except Exception as exc:
            logger.warning("standup_share_failed", tenant_id=tenant_id, url=url[:30], error=str(exc))

    return {
        "ok": True,
        "sent_count": sent_count,
        "date": result["date"],
    }


def _auto_share_success_key(tenant_id: str, report_date: str) -> str:
    return f"logmind:standup:auto:sent:{tenant_id}:{report_date}"


def _auto_share_lock_key(tenant_id: str, report_date: str) -> str:
    return f"logmind:standup:auto:lock:{tenant_id}:{report_date}"


async def try_acquire_auto_share_lock(tenant_id: str, report_date: str) -> bool:
    """Acquire a short-lived lock for auto-share execution."""
    from logmind.core.redis import get_redis_client

    redis = get_redis_client()
    try:
        if await redis.exists(_auto_share_success_key(tenant_id, report_date)):
            return False
        return bool(
            await redis.set(
                _auto_share_lock_key(tenant_id, report_date),
                "1",
                ex=_AUTO_SHARE_LOCK_TTL_SECONDS,
                nx=True,
            )
        )
    finally:
        await redis.aclose()


async def mark_auto_share_complete(tenant_id: str, report_date: str) -> None:
    """Mark an auto-share as successfully completed for the report date."""
    from logmind.core.redis import get_redis_client

    redis = get_redis_client()
    try:
        await redis.set(
            _auto_share_success_key(tenant_id, report_date),
            "1",
            ex=_AUTO_SHARE_SUCCESS_TTL_SECONDS,
        )
    finally:
        await redis.delete(_auto_share_lock_key(tenant_id, report_date))
        await redis.aclose()


async def release_auto_share_lock(tenant_id: str, report_date: str) -> None:
    """Release the auto-share lock when dispatch fails before completion."""
    from logmind.core.redis import get_redis_client

    redis = get_redis_client()
    try:
        await redis.delete(_auto_share_lock_key(tenant_id, report_date))
    finally:
        await redis.aclose()
