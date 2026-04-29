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
    # AlertHistory links to business lines via analysis_task_id → LogAnalysisTask
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    from sqlalchemy import select
    from logmind.domain.analysis.models import LogAnalysisTask

    stmt = (
        select(LogAnalysisTask.business_line_id, AlertHistory.severity)
        .join(LogAnalysisTask, AlertHistory.analysis_task_id == LogAnalysisTask.id, isouter=True)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
        )
    )
    result = await session.execute(stmt)
    alert_rows = result.all()

    # Count alerts per business line
    alert_counts: dict[str, dict] = {}
    for row in alert_rows:
        biz_id = row[0] or ""
        severity = row[1] or "warning"
        if biz_id not in alert_counts:
            alert_counts[biz_id] = {"critical": 0, "warning": 0, "total": 0}
        alert_counts[biz_id]["total"] += 1
        if severity == "critical":
            alert_counts[biz_id]["critical"] += 1
        elif severity == "warning":
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


# ── Blast Radius Endpoint ─────────────────────────────────

class BlastRadiusNode(BaseModel):
    id: str
    name: str
    depth: int  # 0=source, 1=direct downstream, 2=indirect...
    health: str
    alert_count: int
    business_weight: int
    is_core_path: bool


class BlastRadiusResponse(BaseModel):
    source_id: str
    source_name: str
    affected_nodes: list[BlastRadiusNode]
    total_affected: int
    max_depth: int
    affected_core_paths: int
    impact_score: float  # 0-100, weighted by business_weight + core_path
    impact_level: str  # low | medium | high | critical


@router.get("/topology/blast-radius", response_model=BlastRadiusResponse)
async def get_blast_radius(
    node_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """
    Calculate the blast radius of a service failure.

    BFS traversal from the given node through all downstream dependencies
    to determine how many services and users would be affected.
    """
    # Load full topology first
    biz_lines = await biz_repo.get_all(
        session, tenant_id=user.tenant_id, filters={"is_active": True}
    )
    biz_map = {b.id: b for b in biz_lines}

    if node_id not in biz_map:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Service not found")

    # Build adjacency list (source → [downstream targets])
    adjacency: dict[str, list[str]] = {b.id: [] for b in biz_lines}
    biz_ids = set(biz_map.keys())

    for biz in biz_lines:
        try:
            related = json.loads(biz.related_services) if biz.related_services else {}
        except (json.JSONDecodeError, TypeError):
            related = {}

        for downstream_id in related.get("downstream", []):
            if downstream_id in biz_ids:
                adjacency[biz.id].append(downstream_id)

        # If B lists A as upstream, A→B is a downstream edge
        for upstream_id in related.get("upstream", []):
            if upstream_id in biz_ids:
                if biz.id not in adjacency.get(upstream_id, []):
                    adjacency.setdefault(upstream_id, []).append(biz.id)

    # Get alert counts (reuse topology logic)
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from logmind.domain.analysis.models import LogAnalysisTask

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    alert_stmt = (
        select(LogAnalysisTask.business_line_id, AlertHistory.severity)
        .join(LogAnalysisTask, AlertHistory.analysis_task_id == LogAnalysisTask.id, isouter=True)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
        )
    )
    alert_result = await session.execute(alert_stmt)
    alert_counts: dict[str, dict] = {}
    for row in alert_result.all():
        biz_id = row[0] or ""
        severity = row[1] or "warning"
        if biz_id not in alert_counts:
            alert_counts[biz_id] = {"critical": 0, "warning": 0, "total": 0}
        alert_counts[biz_id]["total"] += 1
        if severity == "critical":
            alert_counts[biz_id]["critical"] += 1

    # BFS traversal
    visited: dict[str, int] = {}  # node_id → depth
    queue = [(node_id, 0)]
    visited[node_id] = 0

    while queue:
        current_id, depth = queue.pop(0)
        for downstream_id in adjacency.get(current_id, []):
            if downstream_id not in visited:
                visited[downstream_id] = depth + 1
                queue.append((downstream_id, depth + 1))

    # Build result
    source_biz = biz_map[node_id]
    affected: list[BlastRadiusNode] = []
    affected_core = 0
    total_weight = 0

    for nid, depth in visited.items():
        if depth == 0:
            continue  # Skip source itself
        biz = biz_map.get(nid)
        if not biz:
            continue

        ac = alert_counts.get(nid, {"critical": 0, "warning": 0, "total": 0})
        health = "critical" if ac.get("critical", 0) > 0 else "warning" if ac.get("warning", 0) > 0 else "healthy"

        affected.append(BlastRadiusNode(
            id=nid,
            name=biz.name,
            depth=depth,
            health=health,
            alert_count=ac.get("total", 0),
            business_weight=biz.business_weight,
            is_core_path=biz.is_core_path,
        ))

        total_weight += biz.business_weight
        if biz.is_core_path:
            affected_core += 1

    # Sort by depth first, then by business_weight descending
    affected.sort(key=lambda n: (n.depth, -n.business_weight))

    max_depth = max((n.depth for n in affected), default=0)
    total_affected = len(affected)

    # Impact score: weighted combination
    # - Number of affected services (30%)
    # - Total business weight (30%)
    # - Core path hits (25%)
    # - Max depth (15%)
    all_count = len(biz_lines) or 1
    svc_factor = min(total_affected / all_count, 1.0) * 30
    weight_factor = min(total_weight / (all_count * 5), 1.0) * 30
    core_factor = min(affected_core / max(1, sum(1 for b in biz_lines if b.is_core_path)), 1.0) * 25
    depth_factor = min(max_depth / 4, 1.0) * 15

    impact_score = round(svc_factor + weight_factor + core_factor + depth_factor, 1)

    if impact_score >= 70:
        impact_level = "critical"
    elif impact_score >= 45:
        impact_level = "high"
    elif impact_score >= 20:
        impact_level = "medium"
    else:
        impact_level = "low"

    return BlastRadiusResponse(
        source_id=node_id,
        source_name=source_biz.name,
        affected_nodes=affected,
        total_affected=total_affected,
        max_depth=max_depth,
        affected_core_paths=affected_core,
        impact_score=impact_score,
        impact_level=impact_level,
    )

