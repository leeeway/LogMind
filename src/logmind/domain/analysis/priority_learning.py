"""
Priority Learning — Self-Tuning Alert Priority & Auto-Suppression

Learns from historical alert outcomes and operator feedback to
dynamically adjust the priority scoring engine.

Two capabilities:

  D. Priority Self-Tuning:
     Computes a score adjustment [-15, +10] based on:
       - AlertHistory acknowledgment rate: consistently unacked → lower
       - AnalysisResult feedback rate: negative feedback → lower
     Applied as an additional scoring dimension in PriorityDecisionEngine.

  G. Alert Fatigue Auto-Suppression:
     Suppresses notifications for known-noise patterns:
       - Known issue with status=ignored → suppress
       - Pattern alerted ≥5 times in 7 days with zero acknowledgment → suppress
     Suppressed alerts still appear in daily digest but don't notify.

Caching:
  In-memory cache with 10-minute TTL per business line.
"""

import time

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# ── Cache ────────────────────────────────────────────────
_adj_cache: dict[str, tuple[float, float]] = {}
_sup_cache: dict[str, tuple[bool, str, float]] = {}
_CACHE_TTL = 600  # 10 minutes


# ══════════════════════════════════════════════════════════
#  D. Priority Self-Tuning
# ══════════════════════════════════════════════════════════

async def compute_priority_adjustment(business_line_id: str) -> float:
    """
    Compute a priority score adjustment from historical patterns.

    Returns float in [-15, +10]:
      - Negative → alerts from this business line are rarely acknowledged
      - Zero → no history or balanced
      - Positive → alerts are consistently acted upon
    """
    now = time.monotonic()
    cached = _adj_cache.get(business_line_id)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        adjustment = await _query_adjustment(business_line_id)
    except Exception as e:
        logger.warning("priority_adjustment_failed", error=str(e))
        adjustment = 0.0

    _adj_cache[business_line_id] = (adjustment, now)
    return adjustment


async def _query_adjustment(business_line_id: str) -> float:
    """Query DB for alert ack rate and feedback scores."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from logmind.core.database import get_db_context
    from logmind.domain.alert.models import AlertHistory
    from logmind.domain.analysis.models import AnalysisResult, LogAnalysisTask

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    adjustment = 0.0

    async with get_db_context() as session:
        # Sub-query: recent task IDs for this business line
        recent_task_ids = (
            select(LogAnalysisTask.id)
            .where(
                LogAnalysisTask.business_line_id == business_line_id,
                LogAnalysisTask.created_at >= cutoff,
            )
        )

        # ── Factor 1: Alert acknowledgment rate ──────────
        total_stmt = select(func.count(AlertHistory.id)).where(
            AlertHistory.analysis_task_id.in_(recent_task_ids),
        )
        total_result = await session.execute(total_stmt)
        total_alerts = total_result.scalar() or 0

        if total_alerts >= 3:  # Need minimum sample
            acked_stmt = select(func.count(AlertHistory.id)).where(
                AlertHistory.analysis_task_id.in_(recent_task_ids),
                AlertHistory.status.in_(["acknowledged", "resolved"]),
            )
            acked_result = await session.execute(acked_stmt)
            acked_count = acked_result.scalar() or 0

            ack_rate = acked_count / total_alerts
            # 0% ack → -7.5, 50% ack → 0, 100% ack → +7.5
            adjustment += (ack_rate - 0.5) * 15

        # ── Factor 2: Feedback score trend ───────────────
        feedback_stmt = select(AnalysisResult.feedback_score).where(
            AnalysisResult.task_id.in_(recent_task_ids),
            AnalysisResult.feedback_score.isnot(None),
        )
        fb_result = await session.execute(feedback_stmt)
        scores = [row[0] for row in fb_result if row[0] is not None]

        if len(scores) >= 2:
            avg_score = sum(scores) / len(scores)
            # avg_score in [-1, 1] → adjustment in [-5, +5]
            adjustment += avg_score * 5

    # Clamp to [-15, +10]
    adjustment = max(-15.0, min(10.0, adjustment))

    if abs(adjustment) > 1:
        logger.info(
            "priority_adjustment_computed",
            biz=business_line_id,
            adjustment=round(adjustment, 1),
        )

    return adjustment


# ══════════════════════════════════════════════════════════
#  G. Alert Fatigue Auto-Suppression
# ══════════════════════════════════════════════════════════

async def check_suppression(
    business_line_id: str,
    error_signature: str = "",
) -> tuple[bool, str]:
    """
    Check if this alert pattern should be auto-suppressed.

    Returns (should_suppress, reason).

    Suppression triggers:
      1. Error signature matches a known issue with status=ignored in ES
      2. Pattern has ≥5 unacknowledged alerts in the last 7 days
    """
    cache_key = f"{business_line_id}:{error_signature[:50]}"
    now = time.monotonic()

    cached = _sup_cache.get(cache_key)
    if cached and (now - cached[2]) < _CACHE_TTL:
        return cached[0], cached[1]

    should_suppress = False
    reason = ""

    # ── Trigger 1: Known issue with status=ignored ───────
    try:
        result = await _check_ignored_known_issue(business_line_id, error_signature)
        if result:
            should_suppress, reason = True, result
    except Exception as e:
        logger.warning("suppression_es_check_failed", error=str(e))

    # ── Trigger 2: Repeated unacknowledged alerts ────────
    if not should_suppress:
        try:
            result = await _check_unacked_fatigue(business_line_id)
            if result:
                should_suppress, reason = True, result
        except Exception as e:
            logger.warning("suppression_db_check_failed", error=str(e))

    _sup_cache[cache_key] = (should_suppress, reason, now)
    return should_suppress, reason


async def _check_ignored_known_issue(
    business_line_id: str, error_signature: str
) -> str | None:
    """Check ES for ignored known issues matching this error."""
    if not error_signature or len(error_signature) < 20:
        return None

    from logmind.domain.log.service import log_service

    es = log_service.es
    vectors_index = "logmind-analysis-vectors"

    exists = await es.indices.exists(index=vectors_index)
    if not exists:
        return None

    result = await es.search(
        index=vectors_index,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"business_line_id": business_line_id}},
                        {"term": {"status": "ignored"}},
                    ]
                }
            },
            "size": 10,
            "_source": ["error_signature"],
        },
    )

    for hit in result.get("hits", {}).get("hits", []):
        ignored_sig = hit["_source"].get("error_signature", "")
        if not ignored_sig:
            continue
        # Fuzzy substring match (80-char prefix overlap)
        sig_prefix = error_signature[:80]
        ign_prefix = ignored_sig[:80]
        if sig_prefix in ignored_sig or ign_prefix in error_signature:
            return f"匹配已忽略的已知问题: {ignored_sig[:60]}"

    return None


async def _check_unacked_fatigue(business_line_id: str) -> str | None:
    """Check DB for repeated unacknowledged alerts (fatigue pattern)."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from logmind.core.database import get_db_context
    from logmind.domain.alert.models import AlertHistory
    from logmind.domain.analysis.models import LogAnalysisTask

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    async with get_db_context() as session:
        recent_task_ids = (
            select(LogAnalysisTask.id)
            .where(
                LogAnalysisTask.business_line_id == business_line_id,
                LogAnalysisTask.created_at >= cutoff,
            )
        )

        # Count alerts still in "fired" status (never acknowledged)
        unacked_stmt = select(func.count(AlertHistory.id)).where(
            AlertHistory.analysis_task_id.in_(recent_task_ids),
            AlertHistory.status == "fired",
        )
        result = await session.execute(unacked_stmt)
        unacked = result.scalar() or 0

        if unacked >= 5:
            return f"过去7天 {unacked} 次告警未确认，判定为低关注模式"

    return None


# ══════════════════════════════════════════════════════════
#  Cache Management
# ══════════════════════════════════════════════════════════

def invalidate_priority_cache(business_line_id: str | None = None):
    """Invalidate priority learning caches."""
    global _adj_cache, _sup_cache

    if business_line_id:
        _adj_cache.pop(business_line_id, None)
        keys = [k for k in _sup_cache if k.startswith(business_line_id)]
        for k in keys:
            _sup_cache.pop(k, None)
    else:
        _adj_cache.clear()
        _sup_cache.clear()
