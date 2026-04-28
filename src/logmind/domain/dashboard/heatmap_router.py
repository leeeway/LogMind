"""
Error Heatmap — API Router

Returns service × time-bucket error density matrix
for rendering GitHub-contribution-style heatmaps.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.tenant.models import BusinessLine
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
biz_repo = BaseRepository(BusinessLine)


class HeatmapCell(BaseModel):
    service_id: str
    service_name: str
    bucket: str  # ISO timestamp of time bucket start
    error_count: int
    warning_count: int
    total: int


class HeatmapResponse(BaseModel):
    services: list[dict]      # [{id, name, total_errors}]
    time_buckets: list[str]   # ISO timestamps
    cells: list[HeatmapCell]
    granularity: str          # "hour" | "day"


@router.get("/heatmap", response_model=HeatmapResponse)
async def get_error_heatmap(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(1, ge=1, le=30),
):
    """
    Build error heatmap matrix: services × time buckets.

    Granularity adapts automatically:
      - 1 day → hourly (24 columns)
      - 7+ days → daily (7-30 columns)
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    granularity = "hour" if days <= 1 else "day"
    trunc_fn = func.date_trunc(granularity, AnalysisResult.created_at)

    # Query: group by business_line × time_bucket × severity
    stmt = (
        select(
            LogAnalysisTask.business_line_id,
            trunc_fn.label("bucket"),
            func.sum(
                case((AnalysisResult.severity == "critical", 1), else_=0)
            ).label("error_count"),
            func.sum(
                case((AnalysisResult.severity == "warning", 1), else_=0)
            ).label("warning_count"),
            func.count().label("total"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= since,
        )
        .group_by(LogAnalysisTask.business_line_id, trunc_fn)
    )
    result = await session.execute(stmt)
    rows = result.all()

    # Load business line names
    biz_lines = await biz_repo.get_all(session, tenant_id=user.tenant_id, limit=100)
    biz_map = {b.id: b.name for b in biz_lines}

    # Build time bucket list
    bucket_set: set[str] = set()
    service_totals: dict[str, int] = {}
    cells: list[HeatmapCell] = []

    for row in rows:
        biz_id = row[0] or ""
        bucket_ts = row[1]
        bucket_str = bucket_ts.isoformat() if bucket_ts else ""
        error_count = int(row[2] or 0)
        warning_count = int(row[3] or 0)
        total = int(row[4] or 0)

        bucket_set.add(bucket_str)
        service_totals[biz_id] = service_totals.get(biz_id, 0) + error_count + warning_count

        cells.append(HeatmapCell(
            service_id=biz_id,
            service_name=biz_map.get(biz_id, biz_id[:8]),
            bucket=bucket_str,
            error_count=error_count,
            warning_count=warning_count,
            total=total,
        ))

    # Sort services by total errors (most errors first)
    services = sorted(
        [
            {"id": sid, "name": biz_map.get(sid, sid[:8]), "total_errors": total}
            for sid, total in service_totals.items()
        ],
        key=lambda s: s["total_errors"],
        reverse=True,
    )

    time_buckets = sorted(bucket_set)

    return HeatmapResponse(
        services=services,
        time_buckets=time_buckets,
        cells=cells,
        granularity=granularity,
    )
