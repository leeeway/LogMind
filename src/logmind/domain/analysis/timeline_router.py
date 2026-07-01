"""
Incident Timeline — Auto-reconstructed event timeline

Builds a chronological timeline from analysis results, alerts,
change points, and cross-service correlations.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.evidence import build_root_cause_evidence
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.alert.models import AlertHistory
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/analysis", tags=["Analysis"])
task_repo = BaseRepository(LogAnalysisTask)
result_repo = BaseRepository(AnalysisResult)
alert_repo = BaseRepository(AlertHistory)


class TimelineEvent(BaseModel):
    timestamp: str
    event_type: str  # alert | error_spike | change_point | ai_finding | stage | correlation
    severity: str  # critical | warning | info
    title: str
    description: str
    source: str = ""
    metadata: dict = {}


class TimelineResponse(BaseModel):
    task_id: str
    events: list[TimelineEvent]


@router.get("/{task_id}/timeline", response_model=TimelineResponse)
async def get_incident_timeline(
    task_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """
    Auto-reconstruct incident timeline from analysis data.

    Data sources:
    - Pipeline stage metrics (timing)
    - Analysis results (AI findings)
    - Alert history (same time window)
    - Change points (error rate spikes)
    - Cross-service correlations
    """
    task = await task_repo.get_by_id(session, task_id, tenant_id=user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    events: list[TimelineEvent] = []

    # 1. Task creation event
    if task.created_at:
        events.append(TimelineEvent(
            timestamp=task.created_at.isoformat() if hasattr(task.created_at, 'isoformat') else str(task.created_at),
            event_type="stage",
            severity="info",
            title="分析任务创建",
            description=f"类型: {task.task_type}, 日志数: {task.log_count or 0}",
            source="pipeline",
        ))

    # 2. Stage trace events
    if hasattr(task, "stage_metrics") and task.stage_metrics:
        try:
            stages = json.loads(task.stage_metrics)
            if isinstance(stages, list):
                for s in stages:
                    sev = "info" if s.get("status") == "ok" else "warning" if s.get("status") == "skipped" else "critical"
                    events.append(TimelineEvent(
                        timestamp=task.created_at.isoformat() if hasattr(task.created_at, 'isoformat') else str(task.created_at),
                        event_type="stage",
                        severity=sev,
                        title=f"Pipeline: {s.get('stage', '')}",
                        description=f"耗时 {s.get('duration_ms', 0)}ms — {s.get('status', '')}",
                        source="pipeline",
                        metadata={"duration_ms": s.get("duration_ms", 0), "status": s.get("status", "")},
                    ))
        except (json.JSONDecodeError, TypeError):
            pass

    # 3. Analysis results as timeline events
    results = list(await result_repo.get_all(session, filters={"task_id": task_id}))
    for r in results:
        events.append(TimelineEvent(
            timestamp=r.created_at.isoformat() if hasattr(r, 'created_at') and r.created_at else "",
            event_type="ai_finding",
            severity=r.severity or "info",
            title=f"AI 发现: {r.result_type}",
            description=(r.content or "")[:200],
            source="ai",
            metadata={"confidence": r.confidence_score, "result_type": r.result_type},
        ))

    task_created_at = (
        task.created_at.isoformat()
        if getattr(task, "created_at", None) and hasattr(task.created_at, "isoformat")
        else ""
    )
    evidence_summary = build_root_cause_evidence(results)
    for item in evidence_summary.get("evidence", []):
        kind = item.get("kind", "")
        if kind not in {"change_point", "cross_service"}:
            continue
        events.append(TimelineEvent(
            timestamp=item.get("timestamp") or task_created_at,
            event_type="change_point" if kind == "change_point" else "correlation",
            severity=item.get("severity") or "info",
            title=item.get("title") or ("错误率变点" if kind == "change_point" else "跨服务关联"),
            description=item.get("detail") or "",
            source=item.get("source") or kind,
            metadata={
                "evidence_id": item.get("id"),
                "kind": kind,
                "score": item.get("score", 0),
                "service": item.get("service", ""),
                "log_refs": item.get("log_refs", []),
            },
        ))

    # 4. Related alerts in the same time window
    if task.created_at:
        from datetime import timedelta
        window_start = task.created_at - timedelta(hours=1)
        window_end = task.created_at + timedelta(hours=1)

        from sqlalchemy import select
        stmt = select(AlertHistory).where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= window_start,
            AlertHistory.fired_at <= window_end,
        )
        result = await session.execute(stmt)
        related_alerts = list(result.scalars().all())

        for a in related_alerts[:20]:
            events.append(TimelineEvent(
                timestamp=a.fired_at.isoformat() if a.fired_at else "",
                event_type="alert",
                severity=a.severity or "warning",
                title=f"告警: {a.severity}",
                description=(a.message or "")[:200],
                source="alert",
                metadata={"status": a.status, "alert_id": a.id},
            ))

    # 5. Task completion
    if task.completed_at:
        events.append(TimelineEvent(
            timestamp=task.completed_at.isoformat() if hasattr(task.completed_at, 'isoformat') else str(task.completed_at),
            event_type="stage",
            severity="info",
            title="分析完成",
            description=f"Token: {task.token_usage or 0}, 成本: ${task.cost_usd or 0:.4f}",
            source="pipeline",
        ))

    # Sort by timestamp
    events.sort(key=lambda e: e.timestamp or "")

    return TimelineResponse(task_id=task_id, events=events)
