"""
Log Domain — API Router
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from logmind.core.dependencies import CurrentUser
from logmind.domain.log.schemas import (
    ESIndexInfo,
    LogQueryRequest,
    LogQueryResponse,
    LogStatsResponse,
)
from logmind.domain.log.service import log_service

router = APIRouter(prefix="/logs", tags=["Logs"])


@router.post("/search", response_model=LogQueryResponse)
async def search_logs(req: LogQueryRequest, user: CurrentUser):
    """Search logs from Elasticsearch."""
    return await log_service.search_logs(req)


@router.get("/stats", response_model=LogStatsResponse)
async def get_log_stats(
    user: CurrentUser,
    index_pattern: str = Query(..., description="ES index pattern"),
    hours: int = Query(1, ge=1, le=168, description="Lookback hours"),
):
    """Get log statistics for the last N hours."""
    time_to = datetime.utcnow()
    time_from = time_to - timedelta(hours=hours)
    return await log_service.get_log_stats(index_pattern, time_from, time_to)


@router.get("/indices", response_model=list[ESIndexInfo])
async def list_indices(
    user: CurrentUser,
    pattern: str = Query("*", description="Index pattern filter"),
):
    """List available ES indices."""
    return await log_service.list_indices(pattern)
