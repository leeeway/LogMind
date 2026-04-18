"""
Alert Domain — Intelligent Alert Aggregation

Prevents alert flooding by aggregating multiple alerts from the same
root cause within a time window.

Mechanism:
  1. Before sending a webhook notification, check Redis for a recent
     alert with the same signature (error_signature or business_line+severity)
  2. If found within window → increment count, DON'T send
  3. If not found or window expired → send notification, create window

This reduces notification fatigue (e.g., 50 identical NullPointerExceptions
in 5 minutes → only 1 notification with count=50).
"""

import hashlib
import json
from datetime import datetime, timezone

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# Default aggregation window: 5 minutes
_DEFAULT_WINDOW_SECONDS = 300


class AlertAggregator:
    """
    Redis-backed alert aggregation.

    Key format: logmind:alert_agg:{signature_hash}
    Value: JSON with count, first_seen, last_seen, alert_data
    """

    def __init__(self, window_seconds: int = _DEFAULT_WINDOW_SECONDS):
        self.window_seconds = window_seconds

    async def should_send(
        self,
        business_line_id: str,
        severity: str,
        error_signature: str | None = None,
        alert_summary: str = "",
    ) -> tuple[bool, int]:
        """
        Check if this alert should be sent or aggregated.

        Returns: (should_send: bool, aggregated_count: int)
          - (True, 1): First occurrence — send the notification
          - (False, N): Already N occurrences in window — don't send
        """
        from logmind.core.redis import get_redis

        try:
            redis = get_redis()
            agg_key = self._make_agg_key(business_line_id, severity, error_signature)

            existing = await redis.get(agg_key)
            now = datetime.now(timezone.utc).isoformat()

            if existing:
                # Within aggregation window — increment count
                data = json.loads(existing)
                data["count"] += 1
                data["last_seen"] = now
                # Store updated count with remaining TTL
                ttl = await redis.ttl(agg_key)
                if ttl > 0:
                    await redis.setex(agg_key, ttl, json.dumps(data))
                else:
                    await redis.setex(
                        agg_key, self.window_seconds, json.dumps(data)
                    )

                logger.info(
                    "alert_aggregated",
                    count=data["count"],
                    biz_id=business_line_id,
                    severity=severity,
                )
                return False, data["count"]

            # First occurrence — allow sending
            data = {
                "count": 1,
                "first_seen": now,
                "last_seen": now,
                "business_line_id": business_line_id,
                "severity": severity,
                "summary": alert_summary[:200],
            }
            await redis.setex(
                agg_key, self.window_seconds, json.dumps(data)
            )

            return True, 1

        except Exception as e:
            # If Redis fails, always send (fail-open)
            logger.warning("alert_aggregation_failed", error=str(e))
            return True, 1

    async def get_pending_count(
        self,
        business_line_id: str,
        severity: str,
        error_signature: str | None = None,
    ) -> int:
        """Get the current aggregated count for an alert signature."""
        from logmind.core.redis import get_redis

        try:
            redis = get_redis()
            agg_key = self._make_agg_key(business_line_id, severity, error_signature)
            existing = await redis.get(agg_key)
            if existing:
                data = json.loads(existing)
                return data.get("count", 0)
        except Exception:
            pass
        return 0

    def _make_agg_key(
        self,
        business_line_id: str,
        severity: str,
        error_signature: str | None = None,
    ) -> str:
        """Create a Redis key for alert aggregation."""
        if error_signature:
            sig_hash = hashlib.md5(error_signature.encode()).hexdigest()[:16]
            return f"logmind:alert_agg:{business_line_id}:{sig_hash}"
        return f"logmind:alert_agg:{business_line_id}:{severity}"


# Singleton
alert_aggregator = AlertAggregator()
