"""
Log Replay — Time-bucketed log aggregation for Time Travel UI.

Returns per-minute (or per-5-minute) aggregated log statistics
with representative sample logs for each bucket.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, case

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/logs", tags=["Logs"])
task_repo = BaseRepository(LogAnalysisTask)


class ReplayBucket(BaseModel):
    timestamp: str
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    total: int = 0
    sample_logs: list[dict] = []


class ReplayResponse(BaseModel):
    time_from: str
    time_to: str
    granularity_minutes: int
    buckets: list[ReplayBucket]
    total_events: int


@router.get("/replay", response_model=ReplayResponse)
async def get_log_replay(
    session: DBSession,
    user: CurrentUser,
    time_from: str = Query(..., description="ISO timestamp start"),
    time_to: str = Query(..., description="ISO timestamp end"),
    service_id: str | None = Query(None),
    granularity: int = Query(1, ge=1, le=60, description="Minutes per bucket"),
):
    """
    Get time-bucketed log aggregation for replay/time-travel.

    Returns minute-by-minute (or custom granularity) error counts
    with sample log entries for each bucket.
    """
    try:
        t_from = datetime.fromisoformat(time_from.replace("Z", "+00:00"))
        t_to = datetime.fromisoformat(time_to.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Invalid time format. Use ISO 8601.")

    # Cap window to 24 hours
    if (t_to - t_from).total_seconds() > 86400:
        t_to = t_from + timedelta(hours=24)

    # Query analysis results in time window, grouped by time bucket
    trunc_fn = func.date_trunc("minute", AnalysisResult.created_at)

    stmt = (
        select(
            trunc_fn.label("bucket"),
            func.sum(case((AnalysisResult.severity == "critical", 1), else_=0)).label("errors"),
            func.sum(case((AnalysisResult.severity == "warning", 1), else_=0)).label("warnings"),
            func.sum(case((AnalysisResult.severity == "info", 1), else_=0)).label("infos"),
            func.count().label("total"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= t_from,
            AnalysisResult.created_at <= t_to,
        )
        .group_by(trunc_fn)
        .order_by(trunc_fn)
    )

    if service_id:
        stmt = stmt.where(LogAnalysisTask.business_line_id == service_id)

    result = await session.execute(stmt)
    rows = result.all()

    # Build buckets with sample logs
    buckets: list[ReplayBucket] = []
    total_events = 0

    for row in rows:
        bucket_ts = row[0]
        errors = int(row[1] or 0)
        warnings = int(row[2] or 0)
        infos = int(row[3] or 0)
        total = int(row[4] or 0)
        total_events += total

        buckets.append(ReplayBucket(
            timestamp=bucket_ts.isoformat() if bucket_ts else "",
            error_count=errors,
            warning_count=warnings,
            info_count=infos,
            total=total,
        ))

    # Fetch sample logs for top-error buckets (limit to avoid heavy queries)
    if buckets:
        top_buckets = sorted(buckets, key=lambda b: b.error_count, reverse=True)[:5]
        for b in top_buckets:
            if not b.timestamp:
                continue
            try:
                bucket_start = datetime.fromisoformat(b.timestamp)
                bucket_end = bucket_start + timedelta(minutes=granularity)
                sample_stmt = (
                    select(AnalysisResult.severity, AnalysisResult.content, AnalysisResult.result_type)
                    .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
                    .where(
                        LogAnalysisTask.tenant_id == user.tenant_id,
                        AnalysisResult.created_at >= bucket_start,
                        AnalysisResult.created_at < bucket_end,
                    )
                    .limit(5)
                )
                sample_result = await session.execute(sample_stmt)
                b.sample_logs = [
                    {"severity": r[0], "content": (r[1] or "")[:200], "type": r[2]}
                    for r in sample_result.all()
                ]
            except Exception:
                pass

    return ReplayResponse(
        time_from=t_from.isoformat(),
        time_to=t_to.isoformat(),
        granularity_minutes=granularity,
        buckets=buckets,
        total_events=total_events,
    )
