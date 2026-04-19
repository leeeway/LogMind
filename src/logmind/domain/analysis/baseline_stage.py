"""
Error Baseline Stage — Historical Error Frequency Baseline (Phase A3)

Queries the same ES index pattern for historical error counts over the past
7 days at the same time-of-day window, providing a meaningful baseline for
the PriorityDecisionEngine's frequency anomaly dimension (25% weight).

Without this stage, baseline_error_count is always 0, making the frequency
scoring dimension completely ineffective.

Flow:
  1. Query ES for error counts in the same index, same severity_threshold,
     for each of the past 7 days at the same time window
  2. Calculate the average daily error count
  3. Write baseline_error_count into ctx.log_metadata

Non-critical: if the query fails, pipeline continues with baseline=0.
"""

from datetime import datetime, timedelta, timezone

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)

# Number of historical days to sample for baseline
_BASELINE_DAYS = 7


class ErrorBaselineStage(PipelineStage):
    """
    Pipeline stage: compute historical error frequency baseline.

    Queries ES for the same index pattern + severity over the past N days
    at a comparable time window, then calculates the average error count.
    This provides a meaningful denominator for the PriorityDecisionEngine's
    frequency anomaly scoring dimension.

    Non-critical — if ES query fails, all logs pass through with baseline=0.
    """

    name = "error_baseline"
    is_critical = False

    def __init__(self, log_service=None):
        self._log_service = log_service

    @property
    def log_service(self):
        if self._log_service is None:
            from logmind.domain.log.service import log_service
            self._log_service = log_service
        return self._log_service

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.es_index_pattern or not ctx.time_from or not ctx.time_to:
            return ctx

        try:
            baseline = await self._compute_baseline(ctx)
            ctx.log_metadata["baseline_error_count"] = baseline

            logger.info(
                "error_baseline_computed",
                baseline=baseline,
                current=ctx.log_count,
                ratio=round(ctx.log_count / max(baseline, 1), 2),
                task_id=ctx.task_id,
            )
        except Exception as e:
            logger.warning("error_baseline_failed", error=str(e), task_id=ctx.task_id)
            ctx.log_metadata["baseline_error_count"] = 0

        return ctx

    async def _compute_baseline(self, ctx: PipelineContext) -> int:
        """
        Compute the average error count over the past N days at the same
        time-of-day window.

        Strategy:
          - For each of the past 7 days, query the error count in a window
            of the same duration as the current analysis window
          - Use ES count API (cheap — no documents returned)
          - Average the daily counts, excluding days with zero (might be
            index rotation / no data)
        """
        # Calculate the analysis window duration
        window_duration = ctx.time_to - ctx.time_from
        if window_duration.total_seconds() <= 0:
            return 0

        daily_counts = []

        for days_ago in range(1, _BASELINE_DAYS + 1):
            hist_to = ctx.time_to - timedelta(days=days_ago)
            hist_from = ctx.time_from - timedelta(days=days_ago)

            count = await self._count_errors(
                index_pattern=ctx.es_index_pattern,
                time_from=hist_from,
                time_to=hist_to,
                severity=ctx.severity_threshold,
            )
            if count > 0:
                daily_counts.append(count)

        if not daily_counts:
            return 0

        # Return average (rounded to int)
        return round(sum(daily_counts) / len(daily_counts))

    async def _count_errors(
        self,
        index_pattern: str,
        time_from: datetime,
        time_to: datetime,
        severity: str | None = None,
    ) -> int:
        """Count errors in ES using the count API (lightweight, no docs returned)."""
        filter_clauses = [
            {
                "range": {
                    "@timestamp": {
                        "gte": time_from.isoformat(),
                        "lte": time_to.isoformat(),
                    }
                }
            }
        ]

        # Add severity filter using the same logic as LogService.search_logs
        if severity and severity.lower() in ("error", "critical"):
            from logmind.domain.log.service import _SEVERITY_FILETYPE_MAP

            severity_should = [
                {"term": {"level": severity}},
                {"term": {"log.level": severity}},
                {"term": {"severity": severity}},
                {"term": {"loglevel": severity.upper()}},
            ]
            filetype_values = _SEVERITY_FILETYPE_MAP.get(severity.lower(), [])
            for ft in filetype_values:
                severity_should.append({"term": {"gy.filetype": ft}})
            # Add message-level error indicators
            severity_should.extend([
                {"match_phrase": {"message": "[ERROR]"}},
                {"match_phrase": {"message": "[FATAL]"}},
                {"match_phrase": {"message": "] ERROR "}},
                {"match_phrase": {"message": "Exception:"}},
            ])
            filter_clauses.append({
                "bool": {
                    "should": severity_should,
                    "minimum_should_match": 1,
                }
            })

        body = {
            "query": {
                "bool": {
                    "filter": filter_clauses,
                }
            }
        }

        try:
            result = await self.log_service.es.count(
                index=index_pattern,
                body=body,
            )
            return result.get("count", 0)
        except Exception:
            # Index might not exist for historical dates (rotation)
            return 0
