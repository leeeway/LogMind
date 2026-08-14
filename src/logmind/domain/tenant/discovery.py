"""
Tenant Domain — ES Index Auto-Discovery

Scans Elasticsearch for indices matching a configurable prefix (default: master-),
extracts service names, and registers them as DiscoveredIndex records for admin
confirmation before creating BusinessLine entries.

Modeled after the http_access discover_sites() pattern.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# ── Index name parsing ──────────────────────────────────
# Strip trailing date patterns and generation numbers:
#   master-game-api-2026.08.14                → game-api
#   .ds-master-8004-tong.oa.gyyx.cn-2026.07.29-001838 → 8004-tong.oa.gyyx.cn
#   master-app-server-2026.08                 → app-server
#   master-svc-000001                         → svc
#   master-billing-pay.gyyx.cn-2026.08.14     → billing-pay.gyyx.cn
_DS_SUFFIX_RE = re.compile(
    r"[-.]\d{4}[\.\-]\d{2}[\.\-]\d{2}[-.]\d{4,8}$|[-.]\d{4}[\.\-]\d{2}[\.\-]?\d{0,2}$|[-.]\d{6,}$"
)


def parse_service_name(index_name: str, prefix: str = "master-") -> str | None:
    """
    Extract service name from an ES index name.

    Examples:
        master-stage-billing-pay.gyyx.cn-2026.08.14 → stage-billing-pay.gyyx.cn
        .ds-master-8004-tong.oa.gyyx.cn-2026.07.29-001838 → 8004-tong.oa.gyyx.cn
        master-slowcoach-connector-server-000001     → slowcoach-connector-server
        master-game-api                              → game-api
        develop-game-api-2026.08.14                  → None  (wrong prefix)
        .kibana                                      → None  (system index)
    """
    if not index_name:
        return None
    name = index_name
    if name.startswith(".ds-"):
        name = name[4:]
    if not name.startswith(prefix):
        return None
    name = name[len(prefix):]
    # Strip date/numeric suffixes
    name = _DS_SUFFIX_RE.sub("", name)
    name = name.rstrip("-.")
    return name if name else None


async def _fetch_matching_indices(prefix: str, settings) -> list[str]:
    """
    Fetch index names matching prefix.
    Uses _field_caps endpoint to work with index-level read privileges
    and avoid cluster:monitor/state 403 Forbidden errors from cat.indices.
    """
    import httpx

    auth = (
        (settings.es_username, settings.es_password)
        if settings.es_username
        else None
    )
    base = settings.es_hosts_list[0] if settings.es_hosts_list else "http://localhost:9200"

    try:
        async with httpx.AsyncClient(
            verify=settings.es_verify_certs,
            trust_env=False,
            timeout=float(settings.es_request_timeout or 30),
        ) as http_client:
            r = await http_client.get(
                f"{base}/{prefix}*/_field_caps?fields=@timestamp",
                auth=auth,
            )
            if r.status_code == 200:
                return r.json().get("indices", [])
            else:
                logger.warning(
                    "field_caps_index_scan_non_200",
                    status_code=r.status_code,
                    body=r.text[:200],
                )
    except Exception as e:
        logger.warning("field_caps_index_scan_failed", error=str(e))

    # Fallback to ES client cat.indices if available
    from logmind.core.elasticsearch import get_es_client

    try:
        es = get_es_client()
        raw_indices = await es.cat.indices(
            index=f"{prefix}*", format="json", h="index"
        )
        return [idx.get("index", "") for idx in raw_indices if idx.get("index")]
    except Exception as e:
        logger.error("cat_indices_fallback_failed", error=str(e))
        return []


async def discover_indices(tenant_id: str) -> dict:
    """
    Scan ES for master-* indices, group by service name, and upsert
    into discovered_index table.

    Returns dict with counts: {"new": N, "updated": N, "total": N}
    """
    from logmind.core.config import get_settings
    from logmind.core.database import get_db_context
    from logmind.domain.tenant.models import BusinessLine, DiscoveredIndex

    settings = get_settings()
    prefix = settings.auto_discover_index_prefix
    now = datetime.now(timezone.utc)

    # 1. Fetch matching index names from ES
    index_names = await _fetch_matching_indices(prefix, settings)
    if not index_names:
        logger.info("discover_no_matching_indices_found", prefix=prefix)
        return {"new": 0, "updated": 0, "total": 0}

    # 2. Group by service name
    service_map: dict[str, int] = {}  # service_name -> index count
    for idx_name in index_names:
        svc = parse_service_name(idx_name, prefix)
        if svc:
            service_map[svc] = service_map.get(svc, 0) + 1

    if not service_map:
        logger.info("discover_no_new_indices", prefix=prefix)
        return {"new": 0, "updated": 0, "total": 0}

    # 3. Compare with existing DB records
    new_count = 0
    updated_count = 0

    async with get_db_context() as session:
        # Get already registered business line patterns
        biz_stmt = select(BusinessLine.es_index_pattern).where(
            BusinessLine.tenant_id == tenant_id
        )
        biz_result = await session.execute(biz_stmt)
        registered_patterns = {row[0] for row in biz_result.all()}

        # Get already discovered index patterns
        disc_stmt = select(DiscoveredIndex).where(
            DiscoveredIndex.tenant_id == tenant_id
        )
        disc_result = await session.execute(disc_stmt)
        existing_discovered = {
            d.index_pattern: d for d in disc_result.scalars().all()
        }

        for svc_name, doc_count in service_map.items():
            pattern = f"{prefix}{svc_name}*"

            # Skip if already registered as a business line
            if pattern in registered_patterns:
                continue

            existing = existing_discovered.get(pattern)
            if existing:
                # Update last_seen and doc_count
                existing.last_seen = now
                existing.doc_count = doc_count
                updated_count += 1
            else:
                # New discovery
                new_disc = DiscoveredIndex(
                    tenant_id=tenant_id,
                    index_name=svc_name,
                    index_pattern=pattern,
                    doc_count=doc_count,
                    first_seen=now,
                    last_seen=now,
                    status="pending",
                )
                session.add(new_disc)
                new_count += 1

        await session.flush()
        await session.commit()

    logger.info(
        "discover_completed",
        new=new_count,
        updated=updated_count,
        total_services=len(service_map),
    )
    return {
        "new": new_count,
        "updated": updated_count,
        "total": len(service_map),
    }


async def confirm_discovered(
    discovered_id: str, tenant_id: str
) -> dict:
    """
    Confirm a single discovered index — create a BusinessLine record.

    Returns the created business line info.
    """
    from logmind.core.config import get_settings
    from logmind.core.database import get_db_context
    from logmind.domain.tenant.models import BusinessLine, DiscoveredIndex

    settings = get_settings()

    async with get_db_context() as session:
        disc = await session.get(DiscoveredIndex, discovered_id)
        if not disc or disc.tenant_id != tenant_id:
            return {"error": "not_found"}
        if disc.status == "confirmed":
            return {"error": "already_confirmed", "business_line_id": disc.business_line_id}

        # Create business line
        biz = BusinessLine(
            tenant_id=tenant_id,
            name=disc.index_name,
            description=f"自动发现 — {disc.index_pattern}",
            es_index_pattern=disc.index_pattern,
            log_parse_config="{}",
            default_filters="{}",
            severity_threshold=settings.auto_discover_default_severity,
            language=settings.auto_discover_default_language,
            field_mapping="{}",
            ai_enabled=True,
            webhook_url="",
            is_active=True,
        )
        session.add(biz)
        await session.flush()

        disc.status = "confirmed"
        disc.business_line_id = biz.id

        await session.commit()

        logger.info(
            "discover_confirmed",
            index_name=disc.index_name,
            pattern=disc.index_pattern,
            biz_id=biz.id,
        )
        return {"business_line_id": biz.id, "name": biz.name}


async def ignore_discovered(discovered_id: str, tenant_id: str) -> bool:
    """Mark a discovered index as ignored."""
    from logmind.core.database import get_db_context
    from logmind.domain.tenant.models import DiscoveredIndex

    async with get_db_context() as session:
        disc = await session.get(DiscoveredIndex, discovered_id)
        if not disc or disc.tenant_id != tenant_id:
            return False
        disc.status = "ignored"
        await session.commit()
        logger.info("discover_ignored", index_name=disc.index_name)
        return True


async def confirm_all_discovered(tenant_id: str) -> dict:
    """Confirm all pending discovered indices at once."""
    from logmind.core.database import get_db_context
    from logmind.domain.tenant.models import DiscoveredIndex

    async with get_db_context() as session:
        stmt = select(DiscoveredIndex).where(
            DiscoveredIndex.tenant_id == tenant_id,
            DiscoveredIndex.status == "pending",
        )
        result = await session.execute(stmt)
        pending = result.scalars().all()

    confirmed = 0
    errors = 0
    for disc in pending:
        res = await confirm_discovered(disc.id, tenant_id)
        if "error" in res:
            errors += 1
        else:
            confirmed += 1

    return {"confirmed": confirmed, "errors": errors}
