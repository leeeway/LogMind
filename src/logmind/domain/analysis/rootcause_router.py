"""
Root Cause Chain — API Router

Extracts cause-effect relationships from analysis results
and builds a directed acyclic graph for visualization.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult

logger = get_logger(__name__)

router = APIRouter(prefix="/analysis", tags=["Analysis"])


class GraphNode(BaseModel):
    id: str
    label: str
    severity: str
    service: str
    timestamp: str
    detail: str


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str  # 触发 / 导致 / 关联


class RootCauseResponse(BaseModel):
    task_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@router.get("/{task_id}/rootcause-chain", response_model=RootCauseResponse)
async def get_rootcause_chain(
    task_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """
    Build root-cause chain graph from analysis results.

    Extracts cause-effect patterns from AI analysis structured data
    and cross-service correlation results.
    """
    # Verify task
    stmt = select(LogAnalysisTask).where(
        LogAnalysisTask.id == task_id,
        LogAnalysisTask.tenant_id == user.tenant_id,
    )
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found")

    # Get analysis results
    stmt = (
        select(AnalysisResult)
        .where(AnalysisResult.task_id == task_id)
        .order_by(AnalysisResult.created_at)
    )
    result = await session.execute(stmt)
    results = result.scalars().all()

    if not results:
        return RootCauseResponse(task_id=task_id, nodes=[], edges=[])

    # Build graph from analysis results
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    node_ids: set[str] = set()

    for i, r in enumerate(results):
        node_id = f"result_{r.id[:8]}"
        if node_id in node_ids:
            continue
        node_ids.add(node_id)

        # Parse structured_data for cause-effect hints
        detail = ""
        causes: list[str] = []
        if isinstance(r.structured_data, dict):
            detail = r.structured_data.get("root_cause", "")
            # Extract mentioned services from cross-service analysis
            upstream = r.structured_data.get("upstream_service", "")
            if upstream:
                causes.append(upstream)

        nodes.append(GraphNode(
            id=node_id,
            label=(r.content or "")[:80],
            severity=r.severity or "info",
            service=r.result_type or "",
            timestamp=r.created_at.isoformat() if r.created_at else "",
            detail=detail or (r.content or "")[:200],
        ))

        # Link sequential results (temporal causation)
        if i > 0:
            prev_id = f"result_{results[i - 1].id[:8]}"
            if prev_id in node_ids:
                # Determine relation based on severity escalation
                prev_sev = results[i - 1].severity or "info"
                curr_sev = r.severity or "info"
                sev_order = {"info": 0, "warning": 1, "critical": 2}
                if sev_order.get(curr_sev, 0) > sev_order.get(prev_sev, 0):
                    relation = "导致"
                elif causes:
                    relation = "触发"
                else:
                    relation = "关联"

                edges.append(GraphEdge(
                    source=prev_id,
                    target=node_id,
                    relation=relation,
                ))

    return RootCauseResponse(
        task_id=task_id,
        nodes=nodes,
        edges=edges,
    )
