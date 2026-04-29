"""
MTTR Health Dashboard — Repair time analytics & bottleneck detection

Tracks Mean Time To Resolve trends, identifies slow-repair patterns,
and provides actionable bottleneck analysis.
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.alert.models import AlertHistory
from logmind.domain.analysis.models import LogAnalysisTask
from logmind.domain.tenant.models import BusinessLine

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Response Models ──────────────────────────────────────

class MTTRByPriority(BaseModel):
    priority: str
    mttr_minutes: float
    count: int
    resolved_count: int


class MTTRByService(BaseModel):
    service: str
    service_id: str
    mttr_minutes: float
    incident_count: int
    resolved_count: int


class MTTRDistribution(BaseModel):
    bucket: str           # "<5min", "5-30min", "30m-2h", "2h-8h", ">8h"
    count: int
    percentage: float


class WeeklyTrend(BaseModel):
    week_label: str       # "W1", "W2"...
    start_date: str
    mttr_minutes: float
    incident_count: int


class Bottleneck(BaseModel):
    alert_id: str
    message: str
    priority: str
    service: str
    total_minutes: float
    ack_delay_minutes: float   # fired_at → acked_at
    fix_delay_minutes: float   # acked_at → resolved_at
    bottleneck_phase: str      # "detection" or "resolution"
    fired_at: str


class MTTRHealthResponse(BaseModel):
    overall_mttr_minutes: float
    overall_mttr_vs_last_week: float  # negative = improving
    total_incidents: int
    resolved_count: int
    resolution_rate_pct: float
    by_priority: list[MTTRByPriority]
    by_service: list[MTTRByService]
    distribution: list[MTTRDistribution]
    weekly_trends: list[WeeklyTrend]
    bottlenecks: list[Bottleneck]


# ── Endpoint ─────────────────────────────────────────────

@router.get("/mttr-health", response_model=MTTRHealthResponse)
async def get_mttr_health(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(30, ge=7, le=90),
):
    """Comprehensive MTTR health analysis."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    # Fetch all alerts with resolution data
    # JOIN LogAnalysisTask to get business_line_id
    stmt = (
        select(
            AlertHistory,
            LogAnalysisTask.business_line_id.label("biz_id"),
        )
        .outerjoin(
            LogAnalysisTask,
            AlertHistory.analysis_task_id == LogAnalysisTask.id,
        )
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
        )
        .order_by(AlertHistory.fired_at)
    )
    rows = list((await session.execute(stmt)).all())
    alerts = [(r[0], r[1]) for r in rows]

    # Resolve service names
    biz_ids = set(biz_id for _, biz_id in alerts if biz_id)
    biz_names = {}
    if biz_ids:
        biz_stmt = select(BusinessLine.id, BusinessLine.name).where(
            BusinessLine.id.in_(list(biz_ids))
        )
        biz_rows = (await session.execute(biz_stmt)).all()
        biz_names = {r.id: r.name for r in biz_rows}

    # ── 1. Overall MTTR ──────────────────────────────────
    all_mttr = []
    for alert, _ in alerts:
        if alert.resolved_at and alert.fired_at:
            mttr = (alert.resolved_at - alert.fired_at).total_seconds() / 60
            all_mttr.append(mttr)

    overall_mttr = sum(all_mttr) / len(all_mttr) if all_mttr else 0
    total_incidents = len(alerts)
    resolved = len(all_mttr)
    res_rate = resolved / max(total_incidents, 1) * 100

    # Last week comparison
    one_week_ago = now - timedelta(days=7)
    last_week_mttr_vals = []
    prev_week_mttr_vals = []
    for alert, _ in alerts:
        if not (alert.resolved_at and alert.fired_at):
            continue
        mttr = (alert.resolved_at - alert.fired_at).total_seconds() / 60
        if alert.fired_at >= one_week_ago:
            last_week_mttr_vals.append(mttr)
        elif alert.fired_at >= one_week_ago - timedelta(days=7):
            prev_week_mttr_vals.append(mttr)

    last_week_mttr = sum(last_week_mttr_vals) / len(last_week_mttr_vals) if last_week_mttr_vals else 0
    prev_week_mttr = sum(prev_week_mttr_vals) / len(prev_week_mttr_vals) if prev_week_mttr_vals else 0
    mttr_change = last_week_mttr - prev_week_mttr

    # ── 2. By priority ───────────────────────────────────
    priority_data: dict[str, dict] = defaultdict(lambda: {"mttr": [], "count": 0, "resolved": 0})
    for alert, _ in alerts:
        p = alert.priority or "P2"
        priority_data[p]["count"] += 1
        if alert.resolved_at and alert.fired_at:
            mttr = (alert.resolved_at - alert.fired_at).total_seconds() / 60
            priority_data[p]["mttr"].append(mttr)
            priority_data[p]["resolved"] += 1

    by_priority = []
    for p in ["P0", "P1", "P2"]:
        pd = priority_data.get(p, {"mttr": [], "count": 0, "resolved": 0})
        avg = sum(pd["mttr"]) / len(pd["mttr"]) if pd["mttr"] else 0
        by_priority.append(MTTRByPriority(
            priority=p,
            mttr_minutes=round(avg, 1),
            count=pd["count"],
            resolved_count=pd["resolved"],
        ))

    # ── 3. By service ────────────────────────────────────
    service_data: dict[str, dict] = defaultdict(lambda: {"mttr": [], "count": 0, "resolved": 0})
    for alert, biz_id in alerts:
        svc = biz_id or "unknown"
        service_data[svc]["count"] += 1
        if alert.resolved_at and alert.fired_at:
            mttr = (alert.resolved_at - alert.fired_at).total_seconds() / 60
            service_data[svc]["mttr"].append(mttr)
            service_data[svc]["resolved"] += 1

    by_service = []
    for svc_id, sd in sorted(service_data.items(), key=lambda x: len(x[1]["mttr"]), reverse=True):
        avg = sum(sd["mttr"]) / len(sd["mttr"]) if sd["mttr"] else 0
        svc_name = biz_names.get(svc_id, svc_id)
        by_service.append(MTTRByService(
            service=svc_name,
            service_id=svc_id,
            mttr_minutes=round(avg, 1),
            incident_count=sd["count"],
            resolved_count=sd["resolved"],
        ))

    # ── 4. Distribution histogram ────────────────────────
    buckets = {"<5min": 0, "5-30min": 0, "30m-2h": 0, "2h-8h": 0, ">8h": 0}
    for m in all_mttr:
        if m < 5:
            buckets["<5min"] += 1
        elif m < 30:
            buckets["5-30min"] += 1
        elif m < 120:
            buckets["30m-2h"] += 1
        elif m < 480:
            buckets["2h-8h"] += 1
        else:
            buckets[">8h"] += 1

    distribution = [
        MTTRDistribution(
            bucket=b,
            count=c,
            percentage=round(c / max(len(all_mttr), 1) * 100, 1),
        )
        for b, c in buckets.items()
    ]

    # ── 5. Weekly trends ─────────────────────────────────
    weeks: dict[int, dict] = defaultdict(lambda: {"mttr": [], "count": 0, "start": None})
    for alert, _ in alerts:
        week_num = (alert.fired_at - since).days // 7
        weeks[week_num]["count"] += 1
        if weeks[week_num]["start"] is None:
            weeks[week_num]["start"] = alert.fired_at
        if alert.resolved_at and alert.fired_at:
            mttr = (alert.resolved_at - alert.fired_at).total_seconds() / 60
            weeks[week_num]["mttr"].append(mttr)

    weekly_trends = []
    for wk in sorted(weeks):
        wd = weeks[wk]
        avg = sum(wd["mttr"]) / len(wd["mttr"]) if wd["mttr"] else 0
        start_date = wd["start"].strftime("%m-%d") if wd["start"] else ""
        weekly_trends.append(WeeklyTrend(
            week_label=f"W{wk + 1}",
            start_date=start_date,
            mttr_minutes=round(avg, 1),
            incident_count=wd["count"],
        ))

    # ── 6. Bottlenecks (slowest resolved alerts) ─────────
    bottlenecks = []
    for alert, biz_id in alerts:
        if not (alert.resolved_at and alert.fired_at):
            continue
        total_min = (alert.resolved_at - alert.fired_at).total_seconds() / 60
        ack_delay = 0.0
        fix_delay = total_min
        if alert.acked_at:
            ack_delay = (alert.acked_at - alert.fired_at).total_seconds() / 60
            fix_delay = (alert.resolved_at - alert.acked_at).total_seconds() / 60

        phase = "detection" if ack_delay > fix_delay else "resolution"
        svc_name = biz_names.get(biz_id, biz_id) if biz_id else "unknown"

        bottlenecks.append(Bottleneck(
            alert_id=alert.id,
            message=alert.message[:120],
            priority=alert.priority or "P2",
            service=svc_name,
            total_minutes=round(total_min, 1),
            ack_delay_minutes=round(ack_delay, 1),
            fix_delay_minutes=round(fix_delay, 1),
            bottleneck_phase=phase,
            fired_at=alert.fired_at.isoformat(),
        ))

    bottlenecks.sort(key=lambda b: b.total_minutes, reverse=True)

    return MTTRHealthResponse(
        overall_mttr_minutes=round(overall_mttr, 1),
        overall_mttr_vs_last_week=round(mttr_change, 1),
        total_incidents=total_incidents,
        resolved_count=resolved,
        resolution_rate_pct=round(res_rate, 1),
        by_priority=by_priority,
        by_service=by_service[:10],
        distribution=distribution,
        weekly_trends=weekly_trends,
        bottlenecks=bottlenecks[:10],
    )
