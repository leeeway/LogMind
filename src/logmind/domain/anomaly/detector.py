"""
Anomaly Detector — Z-Score Based Real-time Error Spike Detection

Replaces brute-force "scan everything" patrol with intelligent detection:
  1. Query current 5-min error count from ES
  2. Query historical 24h baseline (hourly buckets) for mean + stddev
  3. Compute Z-Score = (current - mean) / std
  4. Classify: critical (Z>3), warning (Z>2), normal (Z<=2)

Only services with anomaly trigger the expensive AI analysis pipeline.
Normal services are skipped → saves ~50%+ AI token cost.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from logmind.core.logging import get_logger
from logmind.domain.log.service import build_base_severity_filter

logger = get_logger(__name__)

# ── Configuration ────────────────────────────────────────
CURRENT_WINDOW_MINUTES = 5
BASELINE_HOURS = 24
Z_CRITICAL = 3.0
Z_WARNING = 2.0
MIN_BASELINE_SAMPLES = 4  # Need at least 4 hourly data points


@dataclass
class AnomalyResult:
    """Result of anomaly detection for a single business line."""
    is_anomaly: bool = False
    level: str = "normal"  # normal / warning / critical
    z_score: float = 0.0
    current_errors: int = 0
    baseline_mean: float = 0.0
    baseline_std: float = 0.0
    detail: str = ""


class AnomalyDetector:
    """
    Z-Score sliding window anomaly detector.

    Designed to run as a pre-filter before patrol analysis tasks.
    Fast execution (~100ms per service) using ES aggregations only.
    """

    async def detect(
        self,
        index_pattern: str,
        window_minutes: int = CURRENT_WINDOW_MINUTES,
        severity_threshold: str = "error",
    ) -> AnomalyResult:
        """
        Detect anomaly for a single service/index pattern.

        Steps:
          1. Count errors in last `window_minutes`
          2. Get hourly error baseline from last 24h
          3. Compute Z-Score
          4. Classify anomaly level

        Returns AnomalyResult with detection details.
        """
        from logmind.core.elasticsearch import get_es_client

        try:
            es = get_es_client()
            now = datetime.now(timezone.utc)

            # 1. Current window error count
            current_errors = await self._count_errors(
                es, index_pattern,
                since=now - timedelta(minutes=window_minutes),
                until=now,
                severity_threshold=severity_threshold,
            )

            # 2. Historical baseline (24h hourly buckets)
            baseline_mean, baseline_std = await self._compute_baseline(
                es, index_pattern,
                since=now - timedelta(hours=BASELINE_HOURS),
                until=now - timedelta(minutes=window_minutes),
                bucket_minutes=window_minutes,
                severity_threshold=severity_threshold,
            )

            # 3. Z-Score
            if baseline_std > 0:
                z_score = (current_errors - baseline_mean) / baseline_std
            elif current_errors > baseline_mean * 2 and current_errors > 5:
                # Std is 0 (constant baseline) but current is much higher
                z_score = Z_CRITICAL + 1  # Force critical
            else:
                z_score = 0.0

            # 4. Classify
            if z_score >= Z_CRITICAL:
                level = "critical"
                is_anomaly = True
                detail = (
                    f"🔴 错误突增: 当前 {window_minutes}min 内 {current_errors} 条错误，"
                    f"基线 {baseline_mean:.1f}±{baseline_std:.1f}，"
                    f"Z-Score={z_score:.2f}"
                )
            elif z_score >= Z_WARNING:
                level = "warning"
                is_anomaly = True
                detail = (
                    f"🟡 错误偏高: 当前 {window_minutes}min 内 {current_errors} 条错误，"
                    f"基线 {baseline_mean:.1f}±{baseline_std:.1f}，"
                    f"Z-Score={z_score:.2f}"
                )
            else:
                level = "normal"
                is_anomaly = False
                detail = f"正常: {current_errors} errors (Z={z_score:.2f})"

            result = AnomalyResult(
                is_anomaly=is_anomaly,
                level=level,
                z_score=round(z_score, 2),
                current_errors=current_errors,
                baseline_mean=round(baseline_mean, 1),
                baseline_std=round(baseline_std, 1),
                detail=detail,
            )

            if is_anomaly:
                logger.warning(
                    "anomaly_detected",
                    index=index_pattern,
                    level=level,
                    z_score=round(z_score, 2),
                    current=current_errors,
                    baseline_mean=round(baseline_mean, 1),
                )

            return result

        except Exception as e:
            logger.error("anomaly_detection_failed", index=index_pattern, error=str(e))
            # On failure, assume anomaly to avoid missing real issues
            return AnomalyResult(
                is_anomaly=True,
                level="warning",
                detail=f"检测失败(fallback=anomaly): {str(e)[:100]}",
            )

    async def _count_errors(
        self,
        es,
        index_pattern: str,
        since: datetime,
        until: datetime,
        severity_threshold: str = "error",
    ) -> int:
        """Count logs at or above the configured severity in a time window."""
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {
                            "gte": since.isoformat(),
                            "lt": until.isoformat(),
                        }}},
                        self._build_severity_filter(severity_threshold),
                    ]
                }
            },
        }
        try:
            resp = await es.search(index=index_pattern, body=body)
            return resp.get("hits", {}).get("total", {}).get("value", 0)
        except Exception:
            return 0

    async def _compute_baseline(
        self, es, index_pattern: str,
        since: datetime, until: datetime,
        bucket_minutes: int = 5,
        severity_threshold: str = "error",
    ) -> tuple[float, float]:
        """
        Compute mean and stddev of error counts over historical window.

        Uses date_histogram with fixed_interval matching the detection window.
        Returns (mean, std) of per-bucket error counts.
        """
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {
                            "gte": since.isoformat(),
                            "lt": until.isoformat(),
                        }}},
                        self._build_severity_filter(severity_threshold),
                    ]
                }
            },
            "aggs": {
                "timeline": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": f"{bucket_minutes}m",
                    }
                }
            },
        }

        try:
            resp = await es.search(index=index_pattern, body=body)
            buckets = resp.get("aggregations", {}).get("timeline", {}).get("buckets", [])

            if len(buckets) < MIN_BASELINE_SAMPLES:
                # Not enough data for meaningful statistics
                return 0.0, 0.0

            counts = [b["doc_count"] for b in buckets]
            mean = sum(counts) / len(counts)
            variance = sum((c - mean) ** 2 for c in counts) / len(counts)
            std = math.sqrt(variance)

            return mean, std

        except Exception:
            return 0.0, 0.0

    @staticmethod
    def _build_severity_filter(severity_threshold: str) -> dict:
        """Build an ES filter aligned with LogService severity matching."""
        return build_base_severity_filter(severity_threshold or "error")


# Singleton
anomaly_detector = AnomalyDetector()
