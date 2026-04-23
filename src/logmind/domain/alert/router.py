"""
Alert Domain — API Router
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.domain.alert.models import AlertHistory, AlertRule
from logmind.shared.base_repository import BaseRepository
from logmind.shared.base_schema import MessageResponse, PaginatedResponse
from logmind.shared.pagination import PaginationParams, get_pagination

router = APIRouter(prefix="/alerts", tags=["Alerts"])
rule_repo = BaseRepository(AlertRule)
history_repo = BaseRepository(AlertHistory)


# ── Alert Rules ──────────────────────────────────────────
from pydantic import BaseModel, Field
from logmind.shared.base_schema import BaseSchema


class AlertRuleCreate(BaseModel):
    business_line_id: str
    name: str = Field(..., max_length=100)
    description: str = ""
    rule_type: str = Field(..., pattern=r"^(keyword|pattern|ai_anomaly|threshold)$")
    conditions: dict = Field(default_factory=dict)
    severity: str = Field("warning", pattern=r"^(critical|warning|info)$")
    notify_channels: list[str] = Field(default_factory=list)
    cron_expression: str = "*/30 * * * *"


class AlertRuleResponse(BaseSchema):
    id: str
    tenant_id: str
    business_line_id: str
    name: str
    rule_type: str
    severity: str
    cron_expression: str
    is_active: bool
    created_at: datetime


class AlertHistoryResponse(BaseSchema):
    id: str
    alert_rule_id: str | None
    analysis_task_id: str | None
    status: str
    severity: str
    priority: str
    message: str
    fired_at: datetime
    acked_at: datetime | None
    acked_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None


@router.post("/rules", response_model=AlertRuleResponse, status_code=201)
async def create_alert_rule(
    req: AlertRuleCreate, session: DBSession, user: CurrentUser
):
    """Create an alert rule."""
    rule = AlertRule(
        tenant_id=user.tenant_id,
        business_line_id=req.business_line_id,
        name=req.name,
        description=req.description,
        rule_type=req.rule_type,
        conditions=json.dumps(req.conditions),
        severity=req.severity,
        notify_channels=json.dumps(req.notify_channels),
        cron_expression=req.cron_expression,
    )
    rule = await rule_repo.create(session, rule)
    return AlertRuleResponse.model_validate(rule)


@router.get("/rules", response_model=PaginatedResponse)
async def list_alert_rules(session: DBSession, user: CurrentUser):
    """List alert rules for current tenant."""
    items = await rule_repo.get_all(session, tenant_id=user.tenant_id)
    total = await rule_repo.count(session, tenant_id=user.tenant_id)
    return PaginatedResponse.create(
        items=[AlertRuleResponse.model_validate(r) for r in items],
        total=total, page=1, page_size=50,
    )


@router.get("/history", response_model=PaginatedResponse)
async def list_alert_history(
    session: DBSession,
    user: CurrentUser,
    pagination: PaginationParams = Depends(get_pagination),
):
    """List alert history for current tenant."""
    items = await history_repo.get_all(
        session,
        tenant_id=user.tenant_id,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    total = await history_repo.count(session, tenant_id=user.tenant_id)
    return PaginatedResponse.create(
        items=[AlertHistoryResponse.model_validate(h) for h in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


# ── Alert Acknowledge & Resolve ──────────────────────────

@router.post("/history/{alert_id}/ack", response_model=AlertHistoryResponse)
async def acknowledge_alert(
    alert_id: str, session: DBSession, user: CurrentUser
):
    """
    Acknowledge an alert — marks it as seen by on-call.

    This feeds into the priority self-learning system:
    alerts that are frequently ACK'd receive positive
    historical adjustment (+score), while ignored alerts
    get negative adjustment (-score).
    """
    alert = await history_repo.get_by_id(session, alert_id, tenant_id=user.tenant_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status not in ("fired",):
        raise HTTPException(status_code=400, detail=f"Cannot ACK alert in status: {alert.status}")

    alert.status = "acknowledged"
    alert.acked_at = datetime.now(timezone.utc)
    alert.acked_by = user.username
    await session.flush()

    return AlertHistoryResponse.model_validate(alert)


@router.post("/history/{alert_id}/resolve", response_model=AlertHistoryResponse)
async def resolve_alert(
    alert_id: str, session: DBSession, user: CurrentUser
):
    """
    Resolve an alert — marks the issue as fixed.

    Resolved alerts contribute to the experience knowledge base.
    If an auto-ACK'd alert is resolved quickly, it boosts future
    priority scoring for similar patterns.
    """
    alert = await history_repo.get_by_id(session, alert_id, tenant_id=user.tenant_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status == "resolved":
        raise HTTPException(status_code=400, detail="Alert already resolved")

    alert.status = "resolved"
    alert.resolved_at = datetime.now(timezone.utc)
    alert.resolved_by = user.username
    # Auto-ack if not yet acknowledged
    if not alert.acked_at:
        alert.acked_at = alert.resolved_at
        alert.acked_by = user.username
    await session.flush()

    return AlertHistoryResponse.model_validate(alert)
