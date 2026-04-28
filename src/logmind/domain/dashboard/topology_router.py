"""
Service Topology — API Router

Builds a graph of service dependencies from BusinessLine.related_services
with real-time health indicators.
"""

import json

from fastapi import APIRouter
from pydantic import BaseModel

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.tenant.models import BusinessLine
from logmind.domain.alert.models import AlertHistory
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
biz_repo = BaseRepository(BusinessLine)
alert_repo = BaseRepository(AlertHistory)


class TopologyNode(BaseModel):
    id: str
    name: str
    language: str
    is_core_path: bool
    business_weight: int
    ai_enabled: bool
    error_count: int = 0
    warning_count: int = 0
    alert_count: int = 0
    health: str = "healthy"  # healthy | warning | critical | unknown


class TopologyEdge(BaseModel):
    source: str
    target: str
    direction: str  # upstream | downstream


class TopologyResponse(BaseModel):
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]


@router.get("/topology", response_model=TopologyResponse)
async def get_topology(
    session: DBSession,
    user: CurrentUser,
):
    """
    Build service dependency topology graph.

    Nodes = active BusinessLines
    Edges = related_services (upstream/downstream)
    Each node includes real-time health from recent alerts.
    """
    from datetime import datetime, timedelta, timezone

    biz_lines = await biz_repo.get_all(
        session, tenant_id=user.tenant_id, filters={"is_active": True}
    )

    # Get recent alerts (last 24h) for health coloring
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    from sqlalchemy import select
    stmt = select(AlertHistory).where(
        AlertHistory.tenant_id == user.tenant_id,
        AlertHistory.fired_at >= since,
    )
    result = await session.execute(stmt)
    recent_alerts = list(result.scalars().all())

    # Count alerts per business line
    alert_counts: dict[str, dict] = {}
    for a in recent_alerts:
        biz_id = a.business_line_id or ""
        if biz_id not in alert_counts:
            alert_counts[biz_id] = {"critical": 0, "warning": 0, "total": 0}
        alert_counts[biz_id]["total"] += 1
        if a.severity == "critical":
            alert_counts[biz_id]["critical"] += 1
        elif a.severity == "warning":
            alert_counts[biz_id]["warning"] += 1

    nodes = []
    edges = []
    biz_ids = {b.id for b in biz_lines}

    for biz in biz_lines:
        ac = alert_counts.get(biz.id, {"critical": 0, "warning": 0, "total": 0})

        health = "healthy"
        if ac["critical"] > 0:
            health = "critical"
        elif ac["warning"] > 0:
            health = "warning"

        nodes.append(TopologyNode(
            id=biz.id,
            name=biz.name,
            language=biz.language,
            is_core_path=biz.is_core_path,
            business_weight=biz.business_weight,
            ai_enabled=biz.ai_enabled,
            error_count=ac["critical"],
            warning_count=ac["warning"],
            alert_count=ac["total"],
            health=health,
        ))

        # Parse related_services
        try:
            related = json.loads(biz.related_services) if biz.related_services else {}
        except (json.JSONDecodeError, TypeError):
            related = {}

        for upstream_id in related.get("upstream", []):
            if upstream_id in biz_ids:
                edges.append(TopologyEdge(
                    source=upstream_id,
                    target=biz.id,
                    direction="downstream",
                ))
        for downstream_id in related.get("downstream", []):
            if downstream_id in biz_ids:
                edges.append(TopologyEdge(
                    source=biz.id,
                    target=downstream_id,
                    direction="downstream",
                ))

    return TopologyResponse(nodes=nodes, edges=edges)
