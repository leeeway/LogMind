"""
Error Trend Detection — Proactive Alerting for Accelerating Error Rates

Detects when the error rate is accelerating (not just high) and triggers
early warnings before the situation becomes critical.

Strategy:
  1. Compare error count in the recent window (e.g. last 30min) vs baseline (e.g. prior 3h average per 30min)
  2. If the ratio exceeds the configured threshold (e.g. 3x), inject a P0 escalation signal
  3. Works independently of AI analysis — purely statistical, zero-cost

Use cases:
  - Gradual degradation: error rate doubles every 10 minutes → detected before avalanche
  - Burst errors: sudden 10x spike → immediate P0 escalation
  - Normal fluctuation: 1.2x daily pattern → not flagged

Integration:
  Called from PersistStage after analysis completes, or standalone via Celery task.
  Results are stored in PipelineContext.log_metadata for notification enrichment.
"""

from datetime import datetime, timedelta, timezone

from logmind.core.config import get_settings
from logmind.core.logging import get_logger

logger = get_logger(__name__)

# Default configuration
_DEFAULT_RECENT_WINDOW_MINUTES = 30
_DEFAULT_BASELINE_HOURS = 3
_DEFAULT_ACCELERATION_THRESHOLD = 3.0  # 3x baseline → trigger
_DEFAULT_MIN_BASELINE_COUNT = 5        # Need at least 5 errors in baseline to avoid noise


async def detect_error_trend(
    business_line_id: str,
    es_index_pattern: str,
    recent_window_minutes: int = _DEFAULT_RECENT_WINDOW_MINUTES,
    baseline_hours: int = _DEFAULT_BASELINE_HOURS,
    acceleration_threshold: float = _DEFAULT_ACCELERATION_THRESHOLD,
) -> dict:
    """
    Detect accelerating error rates for a business line.

    Returns:
        {
            "is_accelerating": bool,
            "ratio": float,           # current / baseline
            "recent_count": int,      # errors in recent window
            "baseline_avg": float,    # avg errors per window in baseline
            "threshold": float,       # configured threshold
            "severity": str,          # "critical" / "warning" / "normal"
        }
    """
    from logmind.domain.log.service import log_service

    now = datetime.now(timezone.utc)
    recent_start = now - timedelta(minutes=recent_window_minutes)
    baseline_start = now - timedelta(hours=baseline_hours)

    try:
        # Count errors in the recent window
        recent_count = await _count_errors(
            log_service, es_index_pattern, recent_start, now
        )

        # Count errors in the baseline period (excluding recent window)
        baseline_count = await _count_errors(
            log_service, es_index_pattern, baseline_start, recent_start
        )

        # Calculate baseline average per window
        baseline_windows = (baseline_hours * 60) / recent_window_minutes
        baseline_avg = baseline_count / max(baseline_windows, 1)

        # Calculate acceleration ratio
        if baseline_avg < _DEFAULT_MIN_BASELINE_COUNT:
            # Not enough baseline data — can't determine trend
            ratio = 0.0
            is_accelerating = False
        else:
            ratio = recent_count / baseline_avg
            is_accelerating = ratio >= acceleration_threshold

        # Determine severity based on ratio
        if ratio >= acceleration_threshold * 2:
            severity = "critical"  # 6x+ → critical
        elif ratio >= acceleration_threshold:
            severity = "warning"   # 3x+ → warning
        else:
            severity = "normal"

        result = {
            "is_accelerating": is_accelerating,
            "ratio": round(ratio, 2),
            "recent_count": recent_count,
            "baseline_avg": round(baseline_avg, 1),
            "threshold": acceleration_threshold,
            "severity": severity,
        }

        if is_accelerating:
            logger.warning(
                "error_trend_accelerating",
                biz_id=business_line_id,
                ratio=result["ratio"],
                recent=recent_count,
                baseline_avg=result["baseline_avg"],
                severity=severity,
            )

        return result

    except Exception as e:
        logger.warning(
            "error_trend_detection_failed",
            biz_id=business_line_id,
            error=str(e),
        )
        return {
            "is_accelerating": False,
            "ratio": 0.0,
            "recent_count": 0,
            "baseline_avg": 0.0,
            "threshold": acceleration_threshold,
            "severity": "unknown",
            "error": str(e)[:200],
        }


async def _count_errors(
    log_service,
    es_index_pattern: str,
    time_from: datetime,
    time_to: datetime,
) -> int:
    """Count error-level logs in a time range using ES count API."""
    try:
        es = log_service.es

        query = {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {
                        "gte": time_from.isoformat(),
                        "lt": time_to.isoformat(),
                    }}},
                ],
                "should": [
                    {"match_phrase": {"message": "[ERROR]"}},
                    {"match_phrase": {"message": "] ERROR "}},
                    {"term": {"gy.filetype": "error.log"}},
                    {"term": {"log.level": "error"}},
                ],
                "minimum_should_match": 1,
            }
        }

        result = await es.count(index=es_index_pattern, query=query)
        return result.get("count", 0)

    except Exception:
        return 0


def format_trend_alert(trend: dict, business_line_name: str) -> str:
    """
    Format a trend alert message for webhook notification.

    Only called when is_accelerating is True.
    """
    emoji = "🔴" if trend["severity"] == "critical" else "🟡"
    ratio_text = f"{trend['ratio']:.1f}x"

    lines = [
        f"## {emoji} 错误趋势预警",
        "",
        f"**业务线**: {business_line_name}",
        f"**加速倍率**: {ratio_text} (阈值: {trend['threshold']}x)",
        f"**最近窗口错误数**: {trend['recent_count']}",
        f"**基线平均**: {trend['baseline_avg']}",
        "",
        "---",
        "",
        "> ⚠️ 错误率正在加速增长，建议立即排查是否有新发布或基础设施异常。",
    ]
    return "\n".join(lines)
