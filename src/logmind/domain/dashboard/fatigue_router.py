"""
Alert Fatigue Dashboard — Measure and reduce alert noise

Analyzes alert acknowledgment patterns, identifies noise sources,
and provides actionable recommendations to reduce alert fatigue.
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case, cast, Date

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.alert.models import AlertHistory
from logmind.domain.analysis.models import LogAnalysisTask

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Response Models ──────────────────────────────────────

class FatigueMetrics(BaseModel):
    noise_ratio_pct: float         # non-acked / total
    avg_ack_delay_minutes: float   # avg(acked_at - fired_at)
    false_alarm_rate_pct: float    # P2 never-acked / total P2
    storm_count: int               # bursts of >5 alerts/5min
    fatigue_index: int             # composite 0-100
    fatigue_level: str             # healthy / warning / critical


class DailyFatigueTrend(BaseModel):
    date: str
    total_alerts: int
    acked: int
    noise_ratio_pct: float
    avg_ack_delay_minutes: float


class NoiseSource(BaseModel):
    pattern: str                   # representative message prefix
    count: int
    acked_count: int
    ack_rate_pct: float
    avg_severity: str
    suggestion: str                # AI recommendation


class TeamHealth(BaseModel):
    fatigue_index: int
    fatigue_level: str
    daily_trends: list[DailyFatigueTrend]
    noise_ratio_pct: float
    avg_ack_delay_minutes: float
    improvement_vs_last_week: float  # positive = improving


class AlertFatigueResponse(BaseModel):
    metrics: FatigueMetrics
    daily_trends: list[DailyFatigueTrend]
    top_noise: list[NoiseSource]
    team_health: TeamHealth


# ── Helpers ──────────────────────────────────────────────

def _message_pattern(msg: str) -> str:
    """Extract pattern key from alert message (first 100 chars, numbers normalized)."""
    import re
    clean = msg[:100]
    clean = re.sub(r"\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}:\d{2}[.\d]*Z?", "[TIME]", clean)
    clean = re.sub(r"\b[0-9a-f]{8,}\b", "[ID]", clean)
    clean = re.sub(r"\b\d+\b", "N", clean)
    return clean.strip()


# ── Endpoint ─────────────────────────────────────────────

@router.get("/alert-fatigue", response_model=AlertFatigueResponse)
async def get_alert_fatigue(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=30),
):
    """Comprehensive alert fatigue analysis."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    prev_since = since - timedelta(days=days)  # previous period for comparison

    # ── 1. Fetch all alerts in window ────────────────────
    stmt = (
        select(AlertHistory)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
        )
        .order_by(AlertHistory.fired_at)
    )
    alerts = list((await session.execute(stmt)).scalars().all())

    # ── 2. Fetch previous period for comparison ──────────
    prev_stmt = (
        select(
            func.count().label("total"),
            func.count(case((AlertHistory.status.in_(["acknowledged", "resolved"]), 1))).label("acked"),
        )
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= prev_since,
            AlertHistory.fired_at < since,
        )
    )
    prev_row = (await session.execute(prev_stmt)).one()
    prev_noise = (1 - prev_row.acked / max(prev_row.total, 1)) * 100

    # ── 3. Core metrics ─────────────────────────────────
    total = len(alerts)
    acked = sum(1 for a in alerts if a.status in ("acknowledged", "resolved"))
    noise_ratio = (1 - acked / max(total, 1)) * 100

    # Ack delay
    ack_delays = []
    for a in alerts:
        if a.acked_at and a.fired_at:
            delay = (a.acked_at - a.fired_at).total_seconds() / 60
            ack_delays.append(delay)
    avg_ack_delay = sum(ack_delays) / len(ack_delays) if ack_delays else 0

    # False alarm rate (P2 never acked)
    p2_total = sum(1 for a in alerts if a.priority == "P2")
    p2_never_acked = sum(1 for a in alerts if a.priority == "P2" and a.status == "fired")
    false_alarm = p2_never_acked / max(p2_total, 1) * 100

    # Storm count (>5 alerts within 5 min for same tenant)
    storm_count = 0
    for i, a in enumerate(alerts):
        window_end = a.fired_at + timedelta(minutes=5)
        count_in_window = sum(1 for b in alerts[i:] if b.fired_at <= window_end)
        if count_in_window > 5:
            storm_count += 1
    # Deduplicate storm windows roughly
    storm_count = max(0, storm_count // 5)

    # Fatigue index (0-100)
    fatigue_index = min(100, int(
        noise_ratio * 0.4 +
        min(avg_ack_delay, 120) / 120 * 30 +
        false_alarm * 0.2 +
        min(storm_count, 10) * 1
    ))
    if fatigue_index < 30:
        fatigue_level = "healthy"
    elif fatigue_index < 60:
        fatigue_level = "warning"
    else:
        fatigue_level = "critical"

    # ── 4. Daily trends ──────────────────────────────────
    daily_data: dict[str, dict] = defaultdict(lambda: {"total": 0, "acked": 0, "delays": []})
    for a in alerts:
        day = a.fired_at.strftime("%Y-%m-%d")
        daily_data[day]["total"] += 1
        if a.status in ("acknowledged", "resolved"):
            daily_data[day]["acked"] += 1
        if a.acked_at and a.fired_at:
            daily_data[day]["delays"].append(
                (a.acked_at - a.fired_at).total_seconds() / 60
            )

    daily_trends = []
    for day in sorted(daily_data):
        d = daily_data[day]
        avg_delay = sum(d["delays"]) / len(d["delays"]) if d["delays"] else 0
        daily_trends.append(DailyFatigueTrend(
            date=day,
            total_alerts=d["total"],
            acked=d["acked"],
            noise_ratio_pct=round((1 - d["acked"] / max(d["total"], 1)) * 100, 1),
            avg_ack_delay_minutes=round(avg_delay, 1),
        ))

    # ── 5. Top noise sources ─────────────────────────────
    pattern_groups: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "acked": 0, "severities": []}
    )
    for a in alerts:
        pattern = _message_pattern(a.message)
        pg = pattern_groups[pattern]
        pg["count"] += 1
        if a.status in ("acknowledged", "resolved"):
            pg["acked"] += 1
        pg["severities"].append(a.severity)

    top_noise = []
    for pattern, pg in sorted(pattern_groups.items(), key=lambda x: x[1]["count"], reverse=True):
        ack_rate = pg["acked"] / max(pg["count"], 1) * 100
        if ack_rate > 80:
            continue  # Not noise

        # Most common severity
        sev_count = defaultdict(int)
        for s in pg["severities"]:
            sev_count[s] += 1
        avg_sev = max(sev_count, key=sev_count.get)

        # Generate suggestion
        if ack_rate < 10 and pg["count"] >= 5:
            suggestion = "🔇 建议静默: 从未被确认，可能是误报或已知噪音"
        elif ack_rate < 30:
            suggestion = "⬇️ 建议降级: 确认率极低，可降为 P2 或合并告警"
        elif pg["count"] > 20:
            suggestion = "🔄 建议聚合: 触发频率过高，建议增加聚合窗口"
        else:
            suggestion = "👀 持续观察: 噪音倾向，建议关注趋势"

        top_noise.append(NoiseSource(
            pattern=pattern[:100],
            count=pg["count"],
            acked_count=pg["acked"],
            ack_rate_pct=round(ack_rate, 1),
            avg_severity=avg_sev,
            suggestion=suggestion,
        ))

    # ── 6. Team health ───────────────────────────────────
    improvement = prev_noise - noise_ratio  # positive = improving

    return AlertFatigueResponse(
        metrics=FatigueMetrics(
            noise_ratio_pct=round(noise_ratio, 1),
            avg_ack_delay_minutes=round(avg_ack_delay, 1),
            false_alarm_rate_pct=round(false_alarm, 1),
            storm_count=storm_count,
            fatigue_index=fatigue_index,
            fatigue_level=fatigue_level,
        ),
        daily_trends=daily_trends,
        top_noise=top_noise[:10],
        team_health=TeamHealth(
            fatigue_index=fatigue_index,
            fatigue_level=fatigue_level,
            daily_trends=daily_trends,
            noise_ratio_pct=round(noise_ratio, 1),
            avg_ack_delay_minutes=round(avg_ack_delay, 1),
            improvement_vs_last_week=round(improvement, 1),
        ),
    )
