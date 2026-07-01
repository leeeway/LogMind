"""
Log Domain — API Router
"""

import fnmatch
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.domain.log.schemas import (
    ESIndexInfo,
    LogQueryRequest,
    LogQueryResponse,
    LogStatsResponse,
)
from logmind.domain.log.service import log_service
from logmind.domain.tenant.access import (
    get_active_business_line_or_404,
    list_active_business_lines,
)

router = APIRouter(prefix="/logs", tags=["Logs"])


async def _resolve_index_pattern(
    session: DBSession,
    user: CurrentUser,
    index_pattern: str | None,
    business_line_id: str | None,
) -> str:
    """Resolve an ES index pattern from a tenant-owned business line."""
    biz = await get_active_business_line_or_404(
        session,
        user.tenant_id,
        business_line_id,
    )
    if not biz.es_index_pattern:
        raise HTTPException(status_code=400, detail="Business line has no ES index pattern")

    return biz.es_index_pattern


@router.post("/search", response_model=LogQueryResponse)
async def search_logs(req: LogQueryRequest, session: DBSession, user: CurrentUser):
    """Search logs from Elasticsearch."""
    resolved_index_pattern = await _resolve_index_pattern(
        session=session,
        user=user,
        index_pattern=req.index_pattern,
        business_line_id=req.business_line_id,
    )
    safe_req = req.model_copy(
        update={
            "index_pattern": resolved_index_pattern,
            "business_line_id": req.business_line_id,
        }
    )
    return await log_service.search_logs(safe_req)


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
    session: DBSession,
    user: CurrentUser,
    pattern: str = Query("*", description="Index pattern filter"),
):
    """List available ES indices for the current tenant's business lines."""
    biz_lines = await list_active_business_lines(session, user.tenant_id)
    by_name: dict[str, ESIndexInfo] = {}

    for biz in biz_lines:
        if not biz.es_index_pattern:
            continue
        for idx in await log_service.list_indices(biz.es_index_pattern):
            if fnmatch.fnmatch(idx.name, pattern):
                by_name[idx.name] = idx

    return sorted(by_name.values(), key=lambda idx: idx.name)
