"""Persistence helpers and policy gates for HTTP access site governance."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

from logmind.core.database import get_db_context
from logmind.domain.http_access.models import AccessIncident, AccessWindow
from logmind.domain.http_access.site_config import HttpAccessSiteConfig

CRITICAL_ROLES = {"app", "account", "payment", "front"}
VALID_MODES = {"observe", "enabled", "disabled"}
VALID_ENVIRONMENTS = {"production", "test"}
VALID_ROLES = CRITICAL_ROLES | {"general", "cdn_download"}


def source_set(value: str) -> set[str]:
    try:
        result = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return set()
    return {str(item) for item in result if item in {"nginx", "ingress"}}


async def discover_sites(
    tenant_id: str | None,
    windows: dict[tuple[str, str], AccessWindow],
    *,
    observed_at: datetime,
) -> dict[str, HttpAccessSiteConfig]:
    """Upsert hosts seen in this window. Every undiscovered host starts observe."""
    if not tenant_id:
        return {}
    grouped: dict[str, set[str]] = defaultdict(set)
    for source, site in windows:
        grouped[site].add(source)
    if not grouped:
        return {}
    async with get_db_context() as session:
        result = await session.execute(
            select(HttpAccessSiteConfig).where(
                HttpAccessSiteConfig.tenant_id == tenant_id,
                HttpAccessSiteConfig.site.in_(grouped),
            )
        )
        configs = {row.site: row for row in result.scalars().all()}
        for site, sources in grouped.items():
            config = configs.get(site)
            if config is None:
                config = HttpAccessSiteConfig(
                    tenant_id=tenant_id,
                    site=site,
                    sources=json.dumps(sorted(sources)),
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                )
                session.add(config)
                configs[site] = config
                continue
            config.sources = json.dumps(sorted(source_set(config.sources) | sources))
            config.last_seen_at = observed_at
        await session.flush()
        return configs


def incident_is_enabled(incident: AccessIncident, config: HttpAccessSiteConfig | None) -> bool:
    """Gate all notifications without stopping metric/baseline collection."""
    if config is None or config.monitoring_mode != "enabled" or config.environment == "test":
        return False
    if incident.kind == "route_4xx":
        return bool(config.enable_4xx)
    if incident.kind == "latency":
        return bool(config.enable_latency)
    if incident.kind == "traffic_drop":
        return bool(config.enable_traffic_drop) and config.role in CRITICAL_ROLES
    return True


def filter_enabled_incidents(
    incidents: list[AccessIncident],
    configs: dict[str, HttpAccessSiteConfig],
) -> list[AccessIncident]:
    return [item for item in incidents if incident_is_enabled(item, configs.get(item.site))]


def incident_is_notification_worthy(
    incident: AccessIncident,
    config: HttpAccessSiteConfig | None,
    *,
    critical_only: bool = True,
    general_latency_min_p95_ms: int = 10000,
    general_latency_min_slow_count: int = 100,
    general_latency_min_slow_rate: float = 0.20,
) -> bool:
    """Keep WeCom as a must-see channel while retaining backend evidence.

    P0 and confirmed 5xx remain eligible for every explicitly enabled
    production site. Other P1 symptoms normally require a critical business
    role. An unclassified site can still escape the gate when latency is both
    severe and broad, preventing a missing role label from hiding an outage.
    """
    if not critical_only:
        return True
    if incident.priority == "P0" or incident.kind == "http_5xx":
        return True
    role = config.role if config else "general"
    if incident.kind in {"route_4xx", "traffic_drop"}:
        return role in CRITICAL_ROLES
    if incident.kind == "latency":
        if role in CRITICAL_ROLES:
            return True
        slow_rate = (
            incident.slow_2s_count / incident.successful_count
            if incident.successful_count
            else 0.0
        )
        return (
            role != "cdn_download"
            and incident.p95_ms >= general_latency_min_p95_ms
            and incident.slow_2s_count >= general_latency_min_slow_count
            and slow_rate >= general_latency_min_slow_rate
        )
    return False


def filter_notification_worthy_incidents(
    incidents: list[AccessIncident],
    configs: dict[str, HttpAccessSiteConfig],
    **policy,
) -> list[AccessIncident]:
    return [
        item
        for item in incidents
        if incident_is_notification_worthy(
            item,
            configs.get(item.site),
            **policy,
        )
    ]


def role_for_site(site: str, configs: dict[str, HttpAccessSiteConfig]) -> str:
    config = configs.get(site)
    return config.role if config else "general"
