"""
Change Impact Analyzer — Track deployments & correlate with error spikes

Sources:
  1. Manual API: POST /api/v1/changes (engineer marks a deploy)
  2. Auto-detect: Log patterns like "Application started", "Version: xxx"
  3. Webhook: GitLab/GitHub CI push events

Correlation:
  When Z-Score change-point is detected, auto-search for changes within ±30min window.
"""

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/changes", tags=["Changes"])


# ── In-memory store (production would use DB) ────────────
# For simplicity, using tenant-scoped in-memory list.
# In production, this would be a database table.
_change_store: dict[str, list[dict]] = {}  # tenant_id -> [change_events]


class ChangeEventCreate(BaseModel):
    service_name: str
    change_type: str = Field(description="deploy / config / rollback / scale / hotfix")
    version: str = ""
    operator: str = ""
    description: str = ""
    timestamp: str | None = None  # ISO format, defaults to now


class ChangeEventResponse(BaseModel):
    id: str
    tenant_id: str
    service_name: str
    change_type: str
    version: str
    operator: str
    description: str
    timestamp: str
    correlated_spikes: int = 0


class ChangeTimelineResponse(BaseModel):
    changes: list[ChangeEventResponse]
    total: int


class ChangeImpactResponse(BaseModel):
    change: ChangeEventResponse
    blast_radius: list[dict]  # [{service, error_count_before, error_count_after, impact_pct}]
    correlated_alerts: int
    risk_score: float  # 0-100
    ai_assessment: str


@router.post("", response_model=ChangeEventResponse)
async def create_change_event(
    req: ChangeEventCreate,
    session: DBSession,
    user: CurrentUser,
):
    """Record a deployment/config change event."""
    ts = req.timestamp or datetime.now(timezone.utc).isoformat()
    event = {
        "id": str(uuid.uuid4()),
        "tenant_id": user.tenant_id,
        "service_name": req.service_name,
        "change_type": req.change_type,
        "version": req.version,
        "operator": req.operator or user.sub,
        "description": req.description,
        "timestamp": ts,
        "correlated_spikes": 0,
    }
    _change_store.setdefault(user.tenant_id, []).append(event)
    logger.info("change_recorded", service=req.service_name, type=req.change_type, version=req.version)
    return ChangeEventResponse(**event)


@router.get("/timeline", response_model=ChangeTimelineResponse)
async def get_change_timeline(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=30),
    service: str | None = None,
):
    """Get change event timeline."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    events = _change_store.get(user.tenant_id, [])

    filtered = []
    for e in events:
        try:
            ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < since:
            continue
        if service and e["service_name"] != service:
            continue
        filtered.append(e)

    # Also correlate with analysis spikes
    from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
    for e in filtered:
        try:
            ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
            window_start = ts - timedelta(minutes=30)
            window_end = ts + timedelta(minutes=60)

            stmt = (
                select(func.count())
                .select_from(AnalysisResult)
                .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
                .where(
                    LogAnalysisTask.tenant_id == user.tenant_id,
                    LogAnalysisTask.created_at >= window_start,
                    LogAnalysisTask.created_at <= window_end,
                    AnalysisResult.severity == "critical",
                )
            )
            spike_count = (await session.execute(stmt)).scalar() or 0
            e["correlated_spikes"] = spike_count
        except Exception:
            pass

    filtered.sort(key=lambda x: x["timestamp"], reverse=True)

    return ChangeTimelineResponse(
        changes=[ChangeEventResponse(**e) for e in filtered],
        total=len(filtered),
    )


@router.get("/{change_id}/impact", response_model=ChangeImpactResponse)
async def get_change_impact(
    change_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """Analyze blast radius of a specific change event."""
    events = _change_store.get(user.tenant_id, [])
    event = next((e for e in events if e["id"] == change_id), None)
    if not event:
        raise HTTPException(404, "Change event not found")

    from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
    from logmind.domain.alert.models import AlertHistory
    from logmind.domain.tenant.models import BusinessLine

    ts = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
    before_start = ts - timedelta(hours=1)
    after_end = ts + timedelta(hours=2)

    # Get all business lines for blast radius
    biz_stmt = select(BusinessLine).where(
        BusinessLine.tenant_id == user.tenant_id,
        BusinessLine.is_active == True,  # noqa: E712
    )
    biz_lines = (await session.execute(biz_stmt)).scalars().all()

    blast_radius = []
    for biz in biz_lines:
        # Errors before change
        before_count = (await session.execute(
            select(func.count())
            .select_from(AnalysisResult)
            .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
            .where(
                LogAnalysisTask.tenant_id == user.tenant_id,
                LogAnalysisTask.business_line_id == biz.id,
                LogAnalysisTask.created_at >= before_start,
                LogAnalysisTask.created_at < ts,
                AnalysisResult.severity.in_(["critical", "warning"]),
            )
        )).scalar() or 0

        # Errors after change
        after_count = (await session.execute(
            select(func.count())
            .select_from(AnalysisResult)
            .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
            .where(
                LogAnalysisTask.tenant_id == user.tenant_id,
                LogAnalysisTask.business_line_id == biz.id,
                LogAnalysisTask.created_at >= ts,
                LogAnalysisTask.created_at <= after_end,
                AnalysisResult.severity.in_(["critical", "warning"]),
            )
        )).scalar() or 0

        if before_count > 0 or after_count > 0:
            impact = ((after_count - before_count) / max(before_count, 1)) * 100
            blast_radius.append({
                "service": biz.name,
                "error_count_before": before_count,
                "error_count_after": after_count,
                "impact_pct": round(impact, 1),
            })

    blast_radius.sort(key=lambda x: x["impact_pct"], reverse=True)

    # Count correlated alerts
    alert_count = (await session.execute(
        select(func.count()).where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= ts,
            AlertHistory.fired_at <= after_end,
        )
    )).scalar() or 0

    # Risk score
    high_impact = [b for b in blast_radius if b["impact_pct"] > 50]
    risk_score = min(100, len(high_impact) * 25 + alert_count * 10 + event.get("correlated_spikes", 0) * 15)

    # AI assessment
    if risk_score > 60:
        assessment = f"🔴 高风险变更: {len(high_impact)} 个服务受到显著影响，建议立即回滚并排查"
    elif risk_score > 30:
        assessment = f"🟡 中风险变更: 部分服务指标波动，建议持续观察 30 分钟"
    elif blast_radius:
        assessment = "🟢 低风险变更: 指标波动在正常范围内"
    else:
        assessment = "✅ 安全变更: 未检测到异常影响"

    return ChangeImpactResponse(
        change=ChangeEventResponse(**event),
        blast_radius=blast_radius[:10],
        correlated_alerts=alert_count,
        risk_score=risk_score,
        ai_assessment=assessment,
    )


# ── Webhook receiver for GitLab/GitHub CI ────────────────

class CIWebhookPayload(BaseModel):
    """Simplified CI webhook payload (GitLab/GitHub compatible)."""
    ref: str = ""
    project_name: str = ""
    user_name: str = ""
    commit_message: str = ""
    status: str = ""  # success / failed


@router.post("/webhook/{tenant_id}")
async def ci_webhook(tenant_id: str, payload: CIWebhookPayload):
    """
    Receive CI/CD webhook from GitLab/GitHub.
    Auto-creates a change event when deployment succeeds.
    """
    if payload.status and payload.status != "success":
        return {"ok": True, "skipped": True, "reason": f"status={payload.status}"}

    service = payload.project_name or payload.ref.split("/")[-1] if payload.ref else "unknown"
    event = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "service_name": service,
        "change_type": "deploy",
        "version": payload.ref,
        "operator": payload.user_name or "CI/CD",
        "description": (payload.commit_message or "")[:200],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlated_spikes": 0,
    }
    _change_store.setdefault(tenant_id, []).append(event)
    logger.info("ci_webhook_change", service=service, ref=payload.ref)
    return {"ok": True, "change_id": event["id"]}
