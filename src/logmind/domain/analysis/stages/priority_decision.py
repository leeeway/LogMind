"""Priority Decision Stage — P0/P1/P2 scoring + night policy."""

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)


class PriorityDecisionStage(PipelineStage):
    """AI-driven alert priority decision engine. Non-critical."""

    name = "priority_decision"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        from logmind.domain.analysis.priority_engine import PriorityDecisionEngine, PriorityFactors

        engine = PriorityDecisionEngine()

        top_severity = "info"
        top_confidence = 0.5
        unique_errors = set()

        for r in ctx.analysis_results:
            sev = r.get("severity", "info")
            if self._severity_rank(sev) > self._severity_rank(top_severity):
                top_severity = sev
            conf = r.get("confidence_score", 0.5)
            if conf > top_confidence:
                top_confidence = conf
            if r.get("result_type") in ("anomaly", "root_cause"):
                unique_errors.add(r.get("content", "")[:80])

        current_errors = ctx.log_count
        baseline_errors = ctx.log_metadata.get("baseline_error_count", 0)
        if baseline_errors == 0:
            baseline_errors = max(current_errors, 1)

        # Self-learning: load historical adjustments
        historical_adj = 0.0
        is_suppressed = False
        suppression_reason = ""
        try:
            from logmind.domain.analysis.priority_learning import (
                check_suppression, compute_priority_adjustment)
            historical_adj = await compute_priority_adjustment(ctx.business_line_id)
            is_suppressed, suppression_reason = await check_suppression(
                ctx.business_line_id, ctx.error_signature)
        except Exception as e:
            logger.warning("priority_learning_failed", error=str(e))

        factors = PriorityFactors(
            ai_severity=top_severity, confidence=top_confidence,
            current_error_count=current_errors, baseline_error_count=baseline_errors,
            business_weight=ctx.business_weight, is_core_path=ctx.is_core_path,
            estimated_dau=ctx.estimated_dau, log_count=ctx.log_count,
            has_stack_traces=ctx.has_stack_traces,
            unique_error_types=max(len(unique_errors), 1),
            historical_adjustment=historical_adj,
            is_suppressed=is_suppressed, suppression_reason=suppression_reason,
        )

        decision = engine.decide(factors=factors, night_policy=ctx.night_policy,
                                  night_hours=ctx.night_hours)

        ctx.priority_decision = {
            "priority": decision.priority, "score": decision.score,
            "should_notify": decision.actions.should_notify,
            "should_wake": decision.actions.should_wake,
            "delay_until_morning": decision.actions.delay_until_morning,
            "include_in_digest": decision.actions.include_in_digest,
            "reason": decision.actions.reason,
            "factors": decision.factors_summary,
        }

        # Regression Override
        if ctx.log_metadata.get("is_regression"):
            ctx.priority_decision["priority"] = "P0"
            ctx.priority_decision["should_notify"] = True
            ctx.priority_decision["should_wake"] = True
            ctx.priority_decision["reason"] = (
                f"🔄 [回归] 已修复的问题再次出现 — 自动升级为 P0 "
                f"(原始评分: {decision.score})")
            logger.warning("regression_priority_upgrade",
                           original_priority=decision.priority, task_id=ctx.task_id)

        if decision.actions.should_notify:
            alertable_results = [
                r for r in ctx.analysis_results
                if r.get("severity") in ("critical", "warning", "error")
                and r.get("confidence_score", 0) >= 0.4
            ]
            if not alertable_results:
                alertable_results = ctx.analysis_results[:1]
            ctx.alerts_fired = alertable_results

        logger.info("priority_decision_result", priority=decision.priority,
                     score=decision.score, should_notify=decision.actions.should_notify,
                     reason=decision.actions.reason, task_id=ctx.task_id)
        return ctx

    @staticmethod
    def _severity_rank(severity: str) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}.get(severity, 0)
