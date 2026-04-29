"""
Alert Storm Detector — Merge burst alerts into single summary.

When a service fires > STORM_THRESHOLD alerts within STORM_WINDOW_SECONDS,
subsequent alerts are suppressed and a single storm summary is sent instead.

Uses in-memory counter (process-local). For multi-instance deployments,
swap to Redis-based counters.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# ── Configuration ────────────────────────────────────────
STORM_WINDOW_SECONDS = 300  # 5 minutes
STORM_THRESHOLD = 5  # alerts within window to trigger storm mode


@dataclass
class StormResult:
    is_storm: bool = False
    storm_count: int = 0
    should_suppress: bool = False
    storm_summary: str = ""


@dataclass
class _StormWindow:
    timestamps: list[float] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    storm_notified: bool = False


class AlertStormDetector:
    """
    Sliding-window alert storm detector.

    Tracks alert frequency per (business_line_id, severity) pair.
    When count exceeds STORM_THRESHOLD within STORM_WINDOW_SECONDS:
      - First STORM_THRESHOLD alerts: sent normally
      - Next alerts: suppressed
      - When storm ends (no new alert for STORM_WINDOW_SECONDS):
        sends a single storm summary with aggregated count
    """

    def __init__(self):
        # Key: (biz_line_id, severity) → _StormWindow
        self._windows: dict[tuple[str, str], _StormWindow] = defaultdict(_StormWindow)

    def check_storm(
        self,
        business_line_id: str,
        severity: str,
        alert_message: str = "",
    ) -> StormResult:
        """
        Check if current alert is part of a storm.

        Returns StormResult indicating whether to suppress this alert
        and optionally a storm summary to send instead.
        """
        now = time.monotonic()
        key = (business_line_id, severity)
        window = self._windows[key]

        # Purge old timestamps outside window
        cutoff = now - STORM_WINDOW_SECONDS
        while window.timestamps and window.timestamps[0] < cutoff:
            window.timestamps.pop(0)
            if window.messages:
                window.messages.pop(0)

        # Add current
        window.timestamps.append(now)
        window.messages.append(alert_message[:200])
        count = len(window.timestamps)

        if count < STORM_THRESHOLD:
            # Below threshold — normal send, reset storm flag
            window.storm_notified = False
            return StormResult(is_storm=False, storm_count=count)

        # Storm detected!
        if not window.storm_notified:
            # First time exceeding threshold — send storm summary
            window.storm_notified = True

            # Deduplicate messages for summary
            unique_msgs = list(dict.fromkeys(window.messages))[:5]
            summary_lines = "\n".join(f"  • {m}" for m in unique_msgs)

            summary = (
                f"🌊 **告警风暴** — {STORM_WINDOW_SECONDS // 60} 分钟内 "
                f"{count} 条告警\n\n"
                f"**Top 错误摘要**:\n{summary_lines}\n\n"
                f"_后续相同告警已自动抑制，风暴结束后恢复通知_"
            )

            logger.warning(
                "alert_storm_detected",
                business_line_id=business_line_id,
                severity=severity,
                count=count,
            )

            return StormResult(
                is_storm=True,
                storm_count=count,
                should_suppress=False,  # Send this one (storm summary)
                storm_summary=summary,
            )

        # Already in storm and already notified — suppress
        return StormResult(
            is_storm=True,
            storm_count=count,
            should_suppress=True,
        )

    def reset(self, business_line_id: str, severity: str):
        """Reset storm window for a specific key."""
        key = (business_line_id, severity)
        if key in self._windows:
            del self._windows[key]


# Singleton
alert_storm_detector = AlertStormDetector()
