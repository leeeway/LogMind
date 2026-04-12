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
    message: str
    fired_at: datetime
    resolved_at: datetime | None


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
