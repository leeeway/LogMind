"""
Priority Decision Engine — AI-Driven Alert Prioritization

Multi-dimensional scoring engine that calculates alert priority
(P0/P1/P2) and determines notification actions.

Scoring dimensions (total 100 points):
  - AI severity (30%): critical=30, warning=15, info=5
  - Error frequency anomaly (25%): current vs baseline ratio
  - Business weight (25%): configurable per business line (1-10)
  - Core path bonus (10%): critical paths like login/payment
  - Confidence score (10%): AI analysis confidence

Priority thresholds:
  - P0 (≥70): Immediate action required, wake on-call
  - P1 (≥40): Normal notification, may delay at night
  - P2 (<40): Silent, aggregated into daily digest

Night policy:
  - "always": notify immediately regardless of time
  - "p0_only": at night, only P0 gets immediate notification
  - "silent": all notifications delayed to morning
"""

from dataclasses import dataclass, field
from datetime import datetime

from logmind.core.logging import get_logger

logger = get_logger(__name__)


# ── Data Classes ─────────────────────────────────────────

@dataclass
class PriorityFactors:
    """Input factors for priority calculation."""

    # From AI analysis
    ai_severity: str = "info"       # critical / warning / info
    confidence: float = 0.5         # 0.0 - 1.0

    # From error frequency analysis
    current_error_count: int = 0    # Errors in current window
    baseline_error_count: int = 0   # Average errors in same window historically

    # From BusinessLine config
    business_weight: int = 5        # 1-10
    is_core_path: bool = False
    estimated_dau: int = 0

    # From context
    log_count: int = 0              # Total logs analyzed
    has_stack_traces: bool = False   # Exception traces present
    unique_error_types: int = 1     # Number of distinct error patterns

    # Self-learning adjustments (from priority_learning module)
    historical_adjustment: float = 0.0  # [-15, +10] from alert ack rate + feedback
    is_suppressed: bool = False         # Auto-suppression from fatigue detection
    suppression_reason: str = ""        # Human-readable suppression reason


@dataclass
class NotificationAction:
    """What to do with the alert."""
    should_notify: bool = True          # Send notification?
    should_wake: bool = False           # Wake on-call (P0 night)?
    delay_until_morning: bool = False   # Queue for morning delivery?
    include_in_digest: bool = True      # Include in daily digest?
    auto_remediate: bool = False        # Trigger auto-fix? (Phase B)
    reason: str = ""                    # Human-readable decision reason


@dataclass
class PriorityDecision:
    """Output of the priority decision engine."""
    priority: str = "P2"        # P0 / P1 / P2
    score: float = 0.0          # Raw score 0-100
    actions: NotificationAction = field(default_factory=NotificationAction)
    factors_summary: dict = field(default_factory=dict)  # For logging/debugging


# ── Engine ───────────────────────────────────────────────

# Severity to score mapping
_SEVERITY_SCORES = {
    "critical": 30,
    "error": 25,
    "warning": 15,
    "info": 5,
}

# Priority thresholds
_P0_THRESHOLD = 70
_P1_THRESHOLD = 40


class PriorityDecisionEngine:
    """
    Multi-dimensional alert priority calculator.

    Usage:
        engine = PriorityDecisionEngine()
        factors = PriorityFactors(ai_severity="critical", ...)
        decision = engine.decide(factors, night_policy="p0_only", night_hours="22:00-08:00")
    """

    def decide(
        self,
        factors: PriorityFactors,
        night_policy: str = "p0_only",
        night_hours: str = "22:00-08:00",
    ) -> PriorityDecision:
        """Calculate priority and determine notification actions."""

        # 1. Calculate raw score
        score, breakdown = self._calculate_score(factors)

        # 2. Map score to priority level
        priority = self._score_to_priority(score)

        # 3. Determine notification actions based on priority + night policy
        is_night = self._is_night_time(night_hours)
        actions = self._determine_actions(priority, night_policy, is_night, factors)

        decision = PriorityDecision(
            priority=priority,
            score=round(score, 1),
            actions=actions,
            factors_summary=breakdown,
        )

        logger.info(
            "priority_decision",
            priority=priority,
            score=round(score, 1),
            is_night=is_night,
            should_notify=actions.should_notify,
            should_wake=actions.should_wake,
            delay=actions.delay_until_morning,
            reason=actions.reason,
        )

        return decision

    def _calculate_score(self, f: PriorityFactors) -> tuple[float, dict]:
        """
        Calculate priority score (0-100) from multiple dimensions.

        Returns (score, breakdown_dict) for transparency.
        """
        # Dimension 1: AI severity (max 30)
        severity_score = _SEVERITY_SCORES.get(f.ai_severity, 5)

        # Dimension 2: Error frequency anomaly (max 25)
        baseline = max(f.baseline_error_count, 1)  # Avoid division by zero
        freq_ratio = f.current_error_count / baseline
        frequency_score = min(freq_ratio * 5, 25)  # Cap at 25

        # Dimension 3: Business weight (max 25)
        clamped_weight = max(1, min(f.business_weight, 10))
        business_score = clamped_weight * 2.5

        # Dimension 4: Core path bonus (max 10)
        core_path_score = 10.0 if f.is_core_path else 0.0

        # Dimension 5: AI confidence (max 10)
        confidence_score = min(f.confidence, 1.0) * 10

        # ── Bonus modifiers ──────────────────────────────
        bonus = 0.0

        # Stack traces increase severity confidence
        if f.has_stack_traces and f.ai_severity in ("critical", "error"):
            bonus += 3.0

        # Multiple distinct error types suggest systemic issue
        if f.unique_error_types >= 3:
            bonus += 5.0

        # High log count amplifier (500+ errors = serious)
        if f.log_count >= 500:
            bonus += 3.0
        elif f.log_count >= 100:
            bonus += 1.0

        # ── Self-learning: historical adjustment ─────────
        # Negative = alerts from this pattern are consistently ignored
        # Positive = alerts are consistently acted upon
        historical = max(-15.0, min(10.0, f.historical_adjustment))

        total = (
            severity_score
            + frequency_score
            + business_score
            + core_path_score
            + confidence_score
            + bonus
            + historical
        )

        # Cap at 100, floor at 0
        total = max(0.0, min(total, 100.0))

        breakdown = {
            "severity": round(severity_score, 1),
            "frequency": round(frequency_score, 1),
            "business": round(business_score, 1),
            "core_path": round(core_path_score, 1),
            "confidence": round(confidence_score, 1),
            "bonus": round(bonus, 1),
            "historical": round(historical, 1),
            "freq_ratio": round(freq_ratio, 2),
        }

        return total, breakdown

    def _score_to_priority(self, score: float) -> str:
        """Map numeric score to P0/P1/P2."""
        if score >= _P0_THRESHOLD:
            return "P0"
        elif score >= _P1_THRESHOLD:
            return "P1"
        return "P2"

    def _determine_actions(
        self,
        priority: str,
        night_policy: str,
        is_night: bool,
        factors: PriorityFactors,
    ) -> NotificationAction:
        """Determine what notification actions to take."""

        action = NotificationAction(include_in_digest=True)

        if priority == "P0":
            action.should_notify = True
            action.reason = f"🔴 P0: {factors.ai_severity} 级别错误"

            if is_night:
                if night_policy == "always":
                    action.should_wake = True
                    action.reason += "，夜间策略=always，立即通知"
                elif night_policy == "p0_only":
                    action.should_wake = True
                    action.reason += "，夜间 P0 紧急通知"
                elif night_policy == "silent":
                    action.should_notify = False
                    action.delay_until_morning = True
                    action.reason += "，夜间静默，延迟到早上"
            else:
                action.reason += "，立即通知"

        elif priority == "P1":
            action.reason = f"🟡 P1: {factors.ai_severity} 级别错误"

            if is_night:
                if night_policy == "always":
                    action.should_notify = True
                    action.reason += "，夜间策略=always，正常通知"
                elif night_policy in ("p0_only", "silent"):
                    action.should_notify = False
                    action.delay_until_morning = True
                    action.reason += f"，夜间策略={night_policy}，延迟到早上"
            else:
                action.should_notify = True
                action.reason += "，正常通知"

        else:  # P2
            action.should_notify = False
            action.reason = f"🟢 P2: 低优先级，仅记录到日报"

        # ── Self-learning: auto-suppression override ─────
        if factors.is_suppressed:
            action.should_notify = False
            action.should_wake = False
            action.delay_until_morning = False
            action.include_in_digest = True  # Still show in digest
            action.reason = f"🔇 自动抑制: {factors.suppression_reason}"

        return action

    @staticmethod
    def _is_night_time(night_hours: str) -> bool:
        """
        Check if current time is within the night window.

        Format: "HH:MM-HH:MM" (e.g., "22:00-08:00")
        Supports cross-midnight windows (22:00-08:00).
        """
        try:
            parts = night_hours.split("-")
            if len(parts) != 2:
                return False

            start_h, start_m = map(int, parts[0].strip().split(":"))
            end_h, end_m = map(int, parts[1].strip().split(":"))

            now = datetime.now()
            current_minutes = now.hour * 60 + now.minute
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            if start_minutes <= end_minutes:
                # Same day window (e.g., 08:00-18:00)
                return start_minutes <= current_minutes < end_minutes
            else:
                # Cross-midnight window (e.g., 22:00-08:00)
                return current_minutes >= start_minutes or current_minutes < end_minutes

        except (ValueError, IndexError):
            return False


# Singleton
priority_engine = PriorityDecisionEngine()
