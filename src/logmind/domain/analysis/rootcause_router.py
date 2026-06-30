"""
Root Cause Chain — API Router

Extracts cause-effect relationships from analysis results
and builds a directed acyclic graph for visualization.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.analysis.evidence import build_root_cause_evidence

logger = get_logger(__name__)

router = APIRouter(prefix="/analysis", tags=["Analysis"])


class GraphNode(BaseModel):
    id: str
    label: str
    severity: str
    service: str
    timestamp: str
    detail: str
    node_type: str = "finding"
    score: float = 0.0
    evidence_count: int = 0


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str  # 触发 / 导致 / 关联
    confidence: float = 0.0


class EvidenceItem(BaseModel):
    id: str
    kind: str
    title: str
    detail: str
    service: str = ""
    severity: str = "info"
    timestamp: str = ""
    score: float = 0.0
    source: str = ""
    log_refs: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class RootCauseCandidate(BaseModel):
    id: str
    title: str
    service: str = ""
    reason: str
    severity: str = "info"
    score: float = 0.0
    confidence: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)
    next_verifications: list[str] = Field(default_factory=list)


class RootCauseResponse(BaseModel):
    task_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    candidates: list[RootCauseCandidate] = Field(default_factory=list)
    next_verifications: list[str] = Field(default_factory=list)


def build_rootcause_graph(task_id: str, results: list[AnalysisResult]) -> RootCauseResponse:
    """Build graph data from persisted analysis results and derived evidence."""
    summary = build_root_cause_evidence(results)
    evidence_items = [EvidenceItem(**item) for item in summary["evidence"]]
    candidates = [RootCauseCandidate(**item) for item in summary["candidates"]]

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for candidate in candidates:
        nodes.append(GraphNode(
            id=candidate.id,
            label=candidate.title[:80],
            severity=candidate.severity,
            service=candidate.service,
            timestamp="",
            detail=candidate.reason,
            node_type="candidate",
            score=candidate.score,
            evidence_count=len(candidate.evidence_refs),
        ))

    evidence_ids = {item.id for item in evidence_items}
    for item in evidence_items:
        nodes.append(GraphNode(
            id=item.id,
            label=item.title[:80],
            severity=item.severity,
            service=item.service,
            timestamp=item.timestamp,
            detail=item.detail,
            node_type=item.kind,
            score=item.score,
            evidence_count=len(item.log_refs),
        ))

    for candidate in candidates:
        for evidence_id in candidate.evidence_refs:
            if evidence_id not in evidence_ids:
                continue
            edges.append(GraphEdge(
                source=evidence_id,
                target=candidate.id,
                relation="支撑",
                confidence=candidate.score,
            ))

    # Fallback for very old summary-only results with no candidates/evidence.
    if not nodes:
        node_ids: set[str] = set()
        for i, result in enumerate(results):
            node_id = f"result_{result.id[:8]}"
            if node_id in node_ids:
                continue
            node_ids.add(node_id)
            nodes.append(GraphNode(
                id=node_id,
                label=(result.content or "")[:80],
                severity=result.severity or "info",
                service=result.result_type or "",
                timestamp=result.created_at.isoformat() if result.created_at else "",
                detail=(result.content or "")[:200],
                node_type="finding",
                score=float(result.confidence_score or 0),
            ))
            if i > 0:
                prev_id = f"result_{results[i - 1].id[:8]}"
                if prev_id in node_ids:
                    edges.append(GraphEdge(source=prev_id, target=node_id, relation="关联"))

    return RootCauseResponse(
        task_id=task_id,
        nodes=nodes,
        edges=edges,
        evidence=evidence_items,
        candidates=candidates,
        next_verifications=summary["next_verifications"],
    )


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

    return build_rootcause_graph(task_id, list(results))
