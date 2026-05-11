"""
Log Domain — API Router
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.domain.tenant.models import BusinessLine
from logmind.domain.log.schemas import (
    ESIndexInfo,
    LogQueryRequest,
    LogQueryResponse,
    LogStatsResponse,
)
from logmind.domain.log.service import log_service

router = APIRouter(prefix="/logs", tags=["Logs"])


async def _resolve_index_pattern(
    session: DBSession,
    user: CurrentUser,
    index_pattern: str | None,
    business_line_id: str | None,
) -> str:
    """Resolve an ES index pattern from a direct input or a business line ID."""
    if index_pattern:
        return index_pattern

    if not business_line_id:
        raise HTTPException(
            status_code=422,
            detail="Either index_pattern or business_line_id is required",
        )

    biz = await session.get(BusinessLine, business_line_id)
    if not biz or biz.tenant_id != user.tenant_id or not biz.is_active:
        raise HTTPException(status_code=404, detail="Business line not found")
    if not biz.es_index_pattern:
        raise HTTPException(status_code=400, detail="Business line has no ES index pattern")

    return biz.es_index_pattern


@router.post("/search", response_model=LogQueryResponse)
async def search_logs(req: LogQueryRequest, user: CurrentUser):
    """Search logs from Elasticsearch."""
    return await log_service.search_logs(req)


@router.get("/stats", response_model=LogStatsResponse)
async def get_log_stats(
    session: DBSession,
    user: CurrentUser,
    index_pattern: str | None = Query(None, description="ES index pattern"),
    business_line_id: str | None = Query(None, description="Business line ID"),
    hours: int = Query(1, ge=1, le=168, description="Lookback hours"),
):
    """Get log statistics for the last N hours."""
    time_to = datetime.utcnow()
    time_from = time_to - timedelta(hours=hours)
    resolved_index_pattern = await _resolve_index_pattern(
        session=session,
        user=user,
        index_pattern=index_pattern,
        business_line_id=business_line_id,
    )
    return await log_service.get_log_stats(resolved_index_pattern, time_from, time_to)


@router.get("/indices", response_model=list[ESIndexInfo])
async def list_indices(
    user: CurrentUser,
    pattern: str = Query("*", description="Index pattern filter"),
):
    """List available ES indices."""
    return await log_service.list_indices(pattern)
