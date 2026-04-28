"""
Audit Log — API Router

Provides read-only access to the audit trail.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.tenant.audit import AuditLog
from logmind.shared.base_repository import BaseRepository
from logmind.shared.base_schema import PaginatedResponse
from logmind.shared.pagination import PaginationParams, get_pagination

logger = get_logger(__name__)

router = APIRouter(prefix="/audit-logs", tags=["AuditLogs"])
audit_repo = BaseRepository(AuditLog)


class AuditLogResponse(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    username: str
    action: str
    resource_type: str
    resource_id: str
    details: str
    ip_address: str
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=PaginatedResponse)
async def list_audit_logs(
    session: DBSession,
    user: CurrentUser,
    pagination: PaginationParams = Depends(get_pagination),
    action: str | None = None,
    resource_type: str | None = None,
    user_id: str | None = None,
):
    """
    List audit logs for the current tenant.
    
    Supports filtering by action type, resource type, and user.
    """
    filters = {}
    if action:
        filters["action"] = action
    if resource_type:
        filters["resource_type"] = resource_type
    if user_id:
        filters["user_id"] = user_id

    items = await audit_repo.get_all(
        session,
        tenant_id=user.tenant_id,
        offset=pagination.offset,
        limit=pagination.limit,
        filters=filters if filters else None,
    )
    total = await audit_repo.count(
        session,
        tenant_id=user.tenant_id,
        filters=filters if filters else None,
    )
    return PaginatedResponse.create(
        items=[AuditLogResponse.model_validate(a) for a in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )
