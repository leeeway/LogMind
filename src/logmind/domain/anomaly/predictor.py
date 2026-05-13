"""
Predictive Alerting — Time-Series Trend Forecasting

Predicts future error rates using weighted linear regression on recent
hourly error counts from ES. Designed to run alongside the Z-Score
anomaly detector as a forward-looking complement.

Algorithm:
  1. Query hourly error counts for the last 24h (ES date_histogram)
  2. Compute weighted linear regression on the most recent 6h
     (exponential decay weights — recent hours matter more)
  3. Extrapolate trend to predict errors in the next 30 minutes
  4. Compare prediction against historical baseline to classify severity
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from logmind.core.logging import get_logger
from logmind.domain.anomaly.detector import AnomalyDetector

logger = get_logger(__name__)

PREDICTION_HORIZON_MINUTES = 30
TREND_WINDOW_HOURS = 6
BASELINE_HOURS = 24
RISING_SLOPE_THRESHOLD = 0.3
CRITICAL_MULTIPLIER = 3.0
WARNING_MULTIPLIER = 2.0


@dataclass
class PredictionResult:
    predicted_errors_30m: float = 0.0
    predicted_level: str = "normal"  # normal / warning / critical
    trend_direction: str = "stable"  # rising / stable / falling
    trend_slope: float = 0.0
    confidence: float = 0.0
    current_rate: float = 0.0
    baseline_mean: float = 0.0
    detail: str = ""


class TrendPredictor:
    """
    Lightweight time-series predictor using weighted linear regression.
    Runs on ES aggregation data — no external ML dependencies.
    """

    # --- PLACEHOLDER_PREDICTOR_BODY ---

    async def predict(
        self,
        index_pattern: str,
        severity_threshold: str = "error",
    ) -> PredictionResult:
        """
        Predict error trend for the next 30 minutes.

        Steps:
          1. Get hourly error counts for last 24h
          2. Weighted linear regression on last 6h
          3. Extrapolate and classify
        """
        from logmind.core.elasticsearch import get_es_client

        try:
            es = get_es_client()
            now = datetime.now(timezone.utc)

            hourly_counts = await self._get_hourly_counts(
                es, index_pattern,
                since=now - timedelta(hours=BASELINE_HOURS),
                until=now,
                severity_threshold=severity_threshold,
            )

            if len(hourly_counts) < 4:
                return PredictionResult(detail="历史数据不足，无法预测")

            # Baseline stats (full 24h)
            baseline_mean = sum(hourly_counts) / len(hourly_counts)
            baseline_std = math.sqrt(
                sum((c - baseline_mean) ** 2 for c in hourly_counts) / len(hourly_counts)
            )

            # Recent window for trend (last 6h)
            recent = hourly_counts[-TREND_WINDOW_HOURS:]
            current_rate = recent[-1] if recent else 0

            # Weighted linear regression
            slope, intercept, r_squared = self._weighted_linear_regression(recent)

            # Extrapolate: predict value 0.5 hours ahead
            steps_ahead = PREDICTION_HORIZON_MINUTES / 60.0
            predicted = intercept + slope * (len(recent) - 1 + steps_ahead)
            predicted = max(predicted, 0)

            # Classify trend direction
            if slope > RISING_SLOPE_THRESHOLD * baseline_mean:
                trend_direction = "rising"
            elif slope < -RISING_SLOPE_THRESHOLD * baseline_mean:
                trend_direction = "falling"
            else:
                trend_direction = "stable"

            # Classify predicted severity
            if baseline_std > 0:
                predicted_z = (predicted - baseline_mean) / baseline_std
            else:
                predicted_z = 0 if predicted <= baseline_mean else CRITICAL_MULTIPLIER

            if predicted_z >= CRITICAL_MULTIPLIER or predicted >= baseline_mean * CRITICAL_MULTIPLIER:
                predicted_level = "critical"
            elif predicted_z >= WARNING_MULTIPLIER or predicted >= baseline_mean * WARNING_MULTIPLIER:
                predicted_level = "warning"
            else:
                predicted_level = "normal"

            # Confidence based on R² of regression
            confidence = min(max(r_squared, 0.0), 1.0)

            detail = (
                f"过去{TREND_WINDOW_HOURS}h趋势{'上升' if trend_direction == 'rising' else '下降' if trend_direction == 'falling' else '平稳'}，"
                f"当前小时 {current_rate:.0f} 条错误，"
                f"预测未来30分钟约 {predicted:.0f} 条"
                f"（基线均值 {baseline_mean:.0f}）"
            )

            return PredictionResult(
                predicted_errors_30m=round(predicted, 1),
                predicted_level=predicted_level,
                trend_direction=trend_direction,
                trend_slope=round(slope, 2),
                confidence=round(confidence, 2),
                current_rate=current_rate,
                baseline_mean=round(baseline_mean, 1),
                detail=detail,
            )

        except Exception as e:
            logger.error("prediction_failed", index=index_pattern, error=str(e))
            return PredictionResult(detail=f"预测失败: {str(e)[:100]}")

    async def _get_hourly_counts(
        self, es, index_pattern: str,
        since: datetime, until: datetime,
        severity_threshold: str = "error",
    ) -> list[float]:
        """Get hourly error counts from ES date_histogram."""
        detector = AnomalyDetector()
        severity_filter = detector._build_severity_filter(severity_threshold)

        body = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {"gte": since.isoformat(), "lt": until.isoformat()}}},
                        severity_filter,
                    ]
                }
            },
            "aggs": {
                "hourly": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": "1h",
                    }
                }
            },
        }

        try:
            resp = await es.search(index=index_pattern, body=body)
            buckets = resp.get("aggregations", {}).get("hourly", {}).get("buckets", [])
            return [float(b["doc_count"]) for b in buckets]
        except Exception:
            return []

    @staticmethod
    def _weighted_linear_regression(values: list[float]) -> tuple[float, float, float]:
        """
        Weighted linear regression with exponential decay weights.
        More recent values have higher weight.

        Returns (slope, intercept, r_squared).
        """
        n = len(values)
        if n < 2:
            return 0.0, values[0] if values else 0.0, 0.0

        # Exponential decay weights: w_i = exp(i / n) — last point has highest weight
        weights = [math.exp(i / n) for i in range(n)]
        total_w = sum(weights)

        # Weighted means
        x_vals = list(range(n))
        wx_sum = sum(w * x for w, x in zip(weights, x_vals))
        wy_sum = sum(w * y for w, y in zip(weights, values))
        x_mean = wx_sum / total_w
        y_mean = wy_sum / total_w

        # Weighted slope and intercept
        numerator = sum(w * (x - x_mean) * (y - y_mean) for w, x, y in zip(weights, x_vals, values))
        denominator = sum(w * (x - x_mean) ** 2 for w, x in zip(weights, x_vals))

        if abs(denominator) < 1e-10:
            return 0.0, y_mean, 0.0

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean

        # R² (coefficient of determination)
        ss_res = sum(w * (y - (intercept + slope * x)) ** 2 for w, x, y in zip(weights, x_vals, values))
        ss_tot = sum(w * (y - y_mean) ** 2 for w, y in zip(weights, values))
        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-10 else 0.0

        return slope, intercept, r_squared


# Singleton
trend_predictor = TrendPredictor()
