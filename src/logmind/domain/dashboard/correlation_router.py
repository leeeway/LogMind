"""
Service Correlation Matrix — Co-failure analysis & cascade risk scoring

Analyzes historical co-occurrence of critical errors across services
within time windows to infer dependencies and assess cascade risk.
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.tenant.models import BusinessLine

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Response Models ──────────────────────────────────────

class CorrelationCell(BaseModel):
    service_a: str
    service_b: str
    co_occurrence_count: int
    a_leads_count: int   # A appeared first
    b_leads_count: int   # B appeared first
    correlation_strength: float  # 0-1


class CascadeRisk(BaseModel):
    service: str
    service_id: str
    impact_score: float      # 0-100
    downstream_count: int    # how many services fail after this one
    cascade_chain: list[str]  # ordered list of affected services
    avg_cascade_delay_min: float  # avg time before downstream fails


class CorrelationMatrixResponse(BaseModel):
    services: list[str]          # ordered service name list
    matrix: list[list[int]]      # NxN co-occurrence matrix
    correlations: list[CorrelationCell]
    cascade_risks: list[CascadeRisk]


# ── Endpoint ─────────────────────────────────────────────

@router.get("/service-correlation", response_model=CorrelationMatrixResponse)
async def get_service_correlation(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(14, ge=1, le=60),
    window_minutes: int = Query(30, ge=5, le=120),
):
    """Build service co-failure correlation matrix from historical data."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    window = timedelta(minutes=window_minutes)

    # Get active business lines
    biz_stmt = select(BusinessLine).where(
        BusinessLine.tenant_id == user.tenant_id,
        BusinessLine.is_active == True,  # noqa: E712
    )
    biz_lines = list((await session.execute(biz_stmt)).scalars().all())
    biz_map = {b.id: b.name for b in biz_lines}
    biz_ids = list(biz_map.keys())
    service_names = [biz_map[bid] for bid in biz_ids]

    if len(biz_ids) < 2:
        return CorrelationMatrixResponse(
            services=service_names,
            matrix=[[0] * len(biz_ids) for _ in biz_ids],
            correlations=[],
            cascade_risks=[],
        )

    # Get critical error events per service with timestamps
    # (using AnalysisResult severity=critical joined with LogAnalysisTask)
    events_stmt = (
        select(
            LogAnalysisTask.business_line_id,
            LogAnalysisTask.created_at,
        )
        .join(AnalysisResult, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            LogAnalysisTask.business_line_id.in_(biz_ids),
            AnalysisResult.severity == "critical",
        )
        .group_by(LogAnalysisTask.business_line_id, LogAnalysisTask.created_at)
        .order_by(LogAnalysisTask.created_at)
    )
    events = list((await session.execute(events_stmt)).all())

    # Group events by service
    service_events: dict[str, list[datetime]] = defaultdict(list)
    for biz_id, ts in events:
        service_events[biz_id].append(ts)

    # Build co-occurrence matrix
    n = len(biz_ids)
    matrix = [[0] * n for _ in range(n)]
    leads: dict[tuple[int, int], int] = defaultdict(int)  # (i,j) -> count where i leads
    delays: dict[tuple[int, int], list[float]] = defaultdict(list)

    for i in range(n):
        for j in range(i + 1, n):
            bid_i, bid_j = biz_ids[i], biz_ids[j]
            events_i = service_events.get(bid_i, [])
            events_j = service_events.get(bid_j, [])

            co_count = 0
            for ts_i in events_i:
                for ts_j in events_j:
                    delta = abs((ts_i - ts_j).total_seconds())
                    if delta <= window.total_seconds():
                        co_count += 1
                        delay_min = delta / 60
                        if ts_i < ts_j:
                            leads[(i, j)] += 1
                            delays[(i, j)].append(delay_min)
                        else:
                            leads[(j, i)] += 1
                            delays[(j, i)].append(delay_min)

            matrix[i][j] = co_count
            matrix[j][i] = co_count

    # Build correlation details
    correlations = []
    for i in range(n):
        for j in range(i + 1, n):
            if matrix[i][j] == 0:
                continue

            total_i = len(service_events.get(biz_ids[i], []))
            total_j = len(service_events.get(biz_ids[j], []))
            max_possible = max(total_i, total_j, 1)
            strength = min(1.0, matrix[i][j] / max_possible)

            correlations.append(CorrelationCell(
                service_a=service_names[i],
                service_b=service_names[j],
                co_occurrence_count=matrix[i][j],
                a_leads_count=leads.get((i, j), 0),
                b_leads_count=leads.get((j, i), 0),
                correlation_strength=round(strength, 2),
            ))

    correlations.sort(key=lambda c: c.co_occurrence_count, reverse=True)

    # Build cascade risk scores
    cascade_risks = []
    for i in range(n):
        bid = biz_ids[i]
        # Count how many other services fail after this one
        downstream = []
        total_delay = []
        for j in range(n):
            if i == j:
                continue
            lead_count = leads.get((i, j), 0)
            if lead_count > 0:
                downstream.append(service_names[j])
                total_delay.extend(delays.get((i, j), []))

        if not downstream:
            continue

        # Impact score: downstream_count * avg_co_occurrence_strength
        total_events = len(service_events.get(bid, []))
        downstream_ratio = len(downstream) / max(n - 1, 1)
        impact = min(100, int(downstream_ratio * 60 + min(total_events, 50) * 0.8))

        avg_delay = sum(total_delay) / len(total_delay) if total_delay else 0

        cascade_risks.append(CascadeRisk(
            service=service_names[i],
            service_id=bid,
            impact_score=impact,
            downstream_count=len(downstream),
            cascade_chain=downstream[:5],
            avg_cascade_delay_min=round(avg_delay, 1),
        ))

    cascade_risks.sort(key=lambda c: c.impact_score, reverse=True)

    return CorrelationMatrixResponse(
        services=service_names,
        matrix=matrix,
        correlations=correlations[:20],
        cascade_risks=cascade_risks[:10],
    )
