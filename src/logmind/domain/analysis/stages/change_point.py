"""
Change-Point Detection Stage — Error Rate Spike Awareness

Detects statistically significant error rate changes using minute-level
time series from Elasticsearch + rolling-window Z-Score analysis.

Algorithm:
  1. Query ES for minute-level error counts over the last N hours
  2. Compute rolling mean and std (window = 30 minutes)
  3. For each minute bucket, compute z-score = (count - mean) / std
  4. Mark change-points where z-score exceeds threshold
  5. Classify overall trend: stable / increasing / spike / declining

This stage is NON-CRITICAL: if ES aggregation fails or there is
insufficient data, the pipeline proceeds without change-point info.

Thread Safety:
  Stateless — all data flows through PipelineContext.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────

# Minimum number of time buckets required for statistical analysis.
# With less data, z-score is unreliable.
MIN_BUCKETS_FOR_ANALYSIS = 15

# Rolling window size in minutes for baseline computation
ROLLING_WINDOW_MINUTES = 30

# Minimum std to prevent division by zero on perfectly flat series
MIN_STD = 0.5


# ── Data Types ───────────────────────────────────────────

@dataclass(frozen=True)
class ChangePoint:
    """An individual detected change-point in the error rate time series."""

    timestamp: str       # ISO 8601
    before_rate: float   # avg errors/min before this point
    after_rate: float    # avg errors/min after this point
    z_score: float       # z-score at this point
    bucket_count: int    # raw count in this minute bucket

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "before_rate": round(self.before_rate, 2),
            "after_rate": round(self.after_rate, 2),
            "z_score": round(self.z_score, 2),
            "bucket_count": self.bucket_count,
        }


# ── Stage ────────────────────────────────────────────────

class ChangePointDetectionStage(PipelineStage):
    """
    Detect error rate spikes in the recent time series.

    Non-critical: pipeline proceeds even if detection fails.
    """

    name = "change_point_detection"
    is_critical = False

    def __init__(self, log_service):
        self.log_service = log_service

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        settings = get_settings()

        if not settings.analysis_changepoint_enabled:
            logger.info("change_point_disabled", task_id=ctx.task_id)
            return ctx

        if not ctx.time_to:
            return ctx

        try:
            # Determine time window for analysis
            window_hours = settings.analysis_changepoint_window_hours
            analysis_end = ctx.time_to
            analysis_start = analysis_end - timedelta(hours=window_hours)

            # Query ES for minute-level error counts
            time_series = await self._fetch_error_time_series(
                ctx.business_line_id,
                analysis_start,
                analysis_end,
            )

            if len(time_series) < MIN_BUCKETS_FOR_ANALYSIS:
                logger.info(
                    "change_point_insufficient_data",
                    buckets=len(time_series),
                    required=MIN_BUCKETS_FOR_ANALYSIS,
                    task_id=ctx.task_id,
                )
                return ctx

            # Run Z-Score change-point detection
            threshold = settings.analysis_changepoint_threshold
            counts = [bucket["count"] for bucket in time_series]
            timestamps = [bucket["timestamp"] for bucket in time_series]

            change_points = detect_change_points(
                counts, timestamps, threshold=threshold
            )

            # Classify overall trend
            trend = classify_trend(counts)

            ctx.change_points = [cp.to_dict() for cp in change_points]
            ctx.error_rate_trend = trend

            logger.info(
                "change_point_detection_completed",
                change_points_found=len(change_points),
                trend=trend,
                total_buckets=len(time_series),
                task_id=ctx.task_id,
            )

        except Exception as e:
            logger.warning("change_point_detection_failed", error=str(e), task_id=ctx.task_id)

        return ctx

    async def _fetch_error_time_series(
        self,
        business_line_id: str,
        time_from: datetime,
        time_to: datetime,
    ) -> list[dict]:
        """
        Query ES for minute-level error counts using date_histogram aggregation.

        Returns list of {"timestamp": "...", "count": N} dicts sorted by time.
        """
        from logmind.core.database import get_db_context
        from logmind.domain.tenant.models import BusinessLine

        # Load index pattern from BusinessLine
        async with get_db_context() as session:
            biz = await session.get(BusinessLine, business_line_id)
            if not biz:
                return []
            index_pattern = biz.es_index_pattern

        try:
            resp = await self.log_service.es.search(
                index=index_pattern,
                size=0,
                query={
                    "bool": {
                        "filter": [
                            {"range": {"@timestamp": {
                                "gte": time_from.isoformat(),
                                "lte": time_to.isoformat(),
                            }}},
                        ]
                    }
                },
                aggs={
                    "error_rate": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "fixed_interval": "1m",
                            "min_doc_count": 0,
                            "extended_bounds": {
                                "min": time_from.isoformat(),
                                "max": time_to.isoformat(),
                            },
                        }
                    }
                },
            )

            buckets = resp.get("aggregations", {}).get("error_rate", {}).get("buckets", [])
            return [
                {
                    "timestamp": b["key_as_string"],
                    "count": b["doc_count"],
                }
                for b in buckets
            ]
        except Exception as e:
            logger.warning("change_point_es_query_failed", error=str(e))
            return []


# ── Pure Detection Functions (testable without ES) ───────

def detect_change_points(
    counts: list[int | float],
    timestamps: list[str],
    *,
    threshold: float = 3.0,
    rolling_window: int = ROLLING_WINDOW_MINUTES,
) -> list[ChangePoint]:
    """
    Detect change-points in a time series using rolling Z-Score.

    Args:
        counts: Error counts per minute bucket.
        timestamps: ISO 8601 timestamps corresponding to each bucket.
        threshold: Z-score threshold for marking a change-point.
        rolling_window: Number of trailing buckets for baseline.

    Returns:
        List of ChangePoint objects where z-score exceeds threshold.
    """
    if len(counts) < rolling_window + 1:
        return []

    if len(counts) != len(timestamps):
        raise ValueError(
            f"counts ({len(counts)}) and timestamps ({len(timestamps)}) must have same length"
        )

    change_points: list[ChangePoint] = []

    for i in range(rolling_window, len(counts)):
        # Compute rolling baseline from preceding window
        window = counts[i - rolling_window: i]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        std = max(math.sqrt(variance), MIN_STD)

        current = counts[i]
        z = (current - mean) / std

        if z >= threshold:
            # Compute before/after average rates
            before_rate = mean
            after_window = counts[i: min(i + 10, len(counts))]
            after_rate = sum(after_window) / len(after_window) if after_window else current

            change_points.append(ChangePoint(
                timestamp=timestamps[i],
                before_rate=before_rate,
                after_rate=after_rate,
                z_score=z,
                bucket_count=int(current),
            ))

    return change_points


def classify_trend(counts: list[int | float]) -> str:
    """
    Classify the overall error rate trend.

    Divides the series into three equal segments and compares averages:
      - spike: any change-point > 3x the first-third average
      - increasing: last third > first third by > 50%
      - declining: last third < first third by > 30%
      - stable: otherwise

    Returns:
        One of: "stable", "increasing", "spike", "declining"
    """
    if len(counts) < 6:
        return "unknown"

    third = len(counts) // 3
    first = counts[:third]
    last = counts[-third:]

    avg_first = sum(first) / len(first) if first else 0
    avg_last = sum(last) / len(last) if last else 0
    peak = max(counts)

    # Spike: peak is dramatically higher than the baseline
    if avg_first > 0 and peak > avg_first * 5:
        return "spike"
    if avg_first == 0 and peak > 10:
        return "spike"

    # Increasing/declining trend
    if avg_first > 0:
        ratio = avg_last / avg_first
        if ratio > 1.5:
            return "increasing"
        if ratio < 0.7:
            return "declining"

    return "stable"
