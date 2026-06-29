"""
Analysis Domain — Celery Async Tasks

Handles:
- Async log analysis execution (with AI toggle)
- AI-off mode: fetch + preprocess + direct webhook notification
- AI-on mode: full 8-stage pipeline
- Scheduled log patrol (cost-controlled)
- Old task cleanup
"""

import json
import re
from datetime import datetime, timedelta, timezone

from logmind.core.async_task import run_async
from logmind.core.celery_app import celery_app
from logmind.core.exceptions import AllProvidersFailedError, PipelineError
from logmind.core.logging import get_logger

# Severity → confidence mapping for self-learning storage
_SEVERITY_CONFIDENCE_MAP = {"critical": 0.95, "warning": 0.8, "info": 0.6}


def _compute_top_confidence(analysis_results: list[dict]) -> float:
    """Compute confidence score from the top severity in analysis results."""
    top_sev = max(
        (r.get("severity", "info") for r in analysis_results),
        key=lambda s: _SEVERITY_CONFIDENCE_MAP.get(s, 0.5),
        default="info",
    )
    return _SEVERITY_CONFIDENCE_MAP.get(top_sev, 0.7)

# Register Celery tasks defined in other modules so autodiscover picks them up
import logmind.domain.analysis.analysis_indexer  # noqa: F401 — registers index_analysis_result task

logger = get_logger(__name__)

_EMPTY_LOG_SUMMARY_MARKERS = (
    "(No logs found matching the query)",
    "... (truncated)",
    "... (更多日志请登录平台查看)",
)
_SUMMARY_WORD_RE = re.compile(r"[A-Za-z\u4e00-\u9fff]{3,}")


@celery_app.task(
    bind=True,
    name="logmind.domain.analysis.tasks.run_analysis_task",
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
    soft_time_limit=300,  # 5 minutes — raises SoftTimeLimitExceeded
    time_limit=360,       # 6 minutes — hard kill
)
def run_analysis_task(self, task_id: str):
    """
    Async Celery task: Execute a log analysis pipeline.

    Time limits:
      - soft_time_limit=300s: raises SoftTimeLimitExceeded, allowing graceful cleanup
      - time_limit=360s: hard kill if soft limit handler hangs
    """
    from celery.exceptions import SoftTimeLimitExceeded

    logger.info("celery_task_started", task_id=task_id)
    try:
        run_async(_execute_analysis(task_id))
    except SoftTimeLimitExceeded:
        logger.error("celery_task_timeout", task_id=task_id)
        # Mark task as failed in DB
        run_async(_mark_task_timeout(task_id))
    except Exception as exc:
        if _is_retryable_analysis_error(exc):
            max_retries = getattr(self, "max_retries", 2)
            if self.request.retries >= max_retries:
                logger.error(
                    "analysis_task_retry_exhausted",
                    task_id=task_id,
                    retries=self.request.retries,
                    error=str(exc),
                )
                run_async(_mark_task_failed(task_id, str(exc)))
                raise

            logger.warning(
                "analysis_task_retrying",
                task_id=task_id,
                retry=self.request.retries + 1,
                error=str(exc),
            )
            raise self.retry(exc=exc)
        raise


# ── Cost Estimation ──────────────────────────────────────

# Per-1K-token pricing (USD). Keyed by model name prefix.
# Conservative estimates; update when provider pricing changes.
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":       {"input": 0.005,  "output": 0.015},
    "gpt-4":        {"input": 0.03,   "output": 0.06},
    "gpt-3.5":      {"input": 0.0005, "output": 0.0015},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    "deepseek":     {"input": 0.0014, "output": 0.0028},
    "gemini":       {"input": 0.0005, "output": 0.0015},
    "qwen":         {"input": 0.002,  "output": 0.006},
}
_DEFAULT_PRICE = {"input": 0.002, "output": 0.006}  # Fallback


def _estimate_cost_usd(token_usage, provider_config_id: str = "") -> float:
    """
    Estimate API cost in USD from token usage.

    Attempts to load the model name from provider_config_id to match pricing.
    Falls back to default rate. Returns 0.0 on any error (never crashes).
    """
    if not token_usage:
        return 0.0
    try:
        model_name = ""
        if provider_config_id:
            try:
                from logmind.domain.provider.manager import provider_manager
                entry = provider_manager._cache.get(provider_config_id)
                if entry:
                    model_name = entry.config.default_model.lower()
            except Exception:
                pass

        # Find matching price tier
        pricing = _DEFAULT_PRICE
        for prefix, rates in _MODEL_PRICING.items():
            if prefix in model_name:
                pricing = rates
                break

        cost = (
            (token_usage.prompt_tokens / 1000.0) * pricing["input"]
            + (token_usage.completion_tokens / 1000.0) * pricing["output"]
        )
        return round(cost, 6)
    except Exception:
        return 0.0


def _normalize_error_summary(summary: str) -> str:
    """Strip placeholder markers and whitespace from direct-log alert summaries."""
    normalized = (summary or "").strip()
    for marker in _EMPTY_LOG_SUMMARY_MARKERS:
        normalized = normalized.replace(marker, "")
    return normalized.strip()


def _is_meaningful_error_summary(summary: str) -> bool:
    """Reject empty or obviously truncated summary fragments."""
    normalized = _normalize_error_summary(summary)
    if not normalized:
        return False
    if len(normalized) < 24:
        return False
    if normalized.endswith(":") and len(normalized) < 80:
        return False
    return bool(_SUMMARY_WORD_RE.search(normalized))


def _filter_direct_notification_noise(summary: str) -> tuple[str, int]:
    """Remove known non-fault business lines before sending direct log alerts."""
    from logmind.domain.log.business_noise import classify_line

    kept_lines: list[str] = []
    filtered_count = 0

    for line in (summary or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        is_noise, _ = classify_line(stripped)
        if is_noise:
            filtered_count += 1
            continue

        kept_lines.append(line)

    return "\n".join(kept_lines).strip(), filtered_count


def _is_retryable_analysis_error(exc: Exception) -> bool:
    """Return True for transient connection or transport failures."""
    from elastic_transport import ConnectionError as ESConnectionError
    from elastic_transport import ConnectionTimeout as ESConnectionTimeout
    from elastic_transport import TransportError as ESTransportError
    from sqlalchemy.exc import DBAPIError, OperationalError

    retryable_types = (
        ConnectionResetError,
        ConnectionError,
        TimeoutError,
        OSError,
        DBAPIError,
        OperationalError,
        ESConnectionError,
        ESConnectionTimeout,
        ESTransportError,
    )

    seen: set[int] = set()
    current: BaseException | None = exc
    while current and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, retryable_types):
            return True
        if isinstance(current, PipelineError):
            detail = getattr(current, "detail", {}) or {}
            error_text = str(detail.get("error", "")).lower()
            if any(
                marker in error_text
                for marker in (
                    "connection reset by peer",
                    "connection timed out",
                    "timed out",
                    "temporarily unavailable",
                    "server disconnected",
                )
            ):
                return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False


async def _execute_analysis(task_id: str):
    """
    Run the analysis pipeline for a given task.

    Two modes based on BusinessLine.ai_enabled:
      - ai_enabled=True:  Full 8-stage pipeline (Fetch → AI → Alert → Persist)
      - ai_enabled=False: Lightweight mode (Fetch → Preprocess → Webhook)
    """
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.analysis.pipeline import (
        AnalysisPipeline,
        PipelineContext,
    )
    from logmind.domain.analysis.stages import (
        BusinessNoiseFilterStage,
        ChangePointDetectionStage,
        CrossServiceCorrelationStage,
        LogFetchStage,
        LogPreprocessStage,
        LogQualityFilterStage,
        PersistStage,
        PriorityDecisionStage,
        PromptBuildStage,
        ResultParseStage,
    )
    from logmind.domain.analysis.agent_stage import AgentInferenceStage
    from logmind.domain.analysis.baseline_stage import ErrorBaselineStage
    from logmind.domain.analysis.fingerprint_stage import ErrorFingerprintStage
    from logmind.domain.analysis.semantic_dedup import SemanticDedupStage
    from logmind.domain.log.service import log_service
    from logmind.domain.prompt.engine import prompt_engine
    from logmind.domain.prompt.models import PromptTemplate
    from logmind.domain.provider.manager import provider_manager
    from logmind.domain.tenant.models import BusinessLine
    from logmind.shared.base_repository import BaseRepository

    prompt_repo = BaseRepository(PromptTemplate)

    async with get_db_context() as session:
        # 1. Load task
        task = await session.get(LogAnalysisTask, task_id)
        if not task:
            logger.error("task_not_found", task_id=task_id)
            return

        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        await session.flush()

        # 2. Load business line
        biz = await session.get(BusinessLine, task.business_line_id)
        if not biz:
            task.status = "failed"
            task.error_message = "Business line not found"
            task.completed_at = datetime.now(timezone.utc)
            await session.flush()
            return

        # Snapshot business line config
        ai_enabled = biz.ai_enabled
        webhook_url = biz.webhook_url or ""
        biz_name = biz.name
        biz_language = biz.language

    # 3. Build pipeline — dynamically based on ai_enabled
    if ai_enabled:
        # Full AI pipeline with quality filter + fingerprint dedup + semantic dedup
        stages = [
            LogFetchStage(log_service),
            LogPreprocessStage(),
            LogQualityFilterStage(),            # Layer 0: Smart quality filter
            BusinessNoiseFilterStage(),          # Layer 0.5: Business noise recognition
            ErrorBaselineStage(log_service),     # Historical baseline for frequency scoring
            ChangePointDetectionStage(log_service), # Error rate spike detection
            ErrorFingerprintStage(),             # Layer 1: Fast MD5 dedup
            SemanticDedupStage(),                # Layer 2: Vector semantic dedup
            CrossServiceCorrelationStage(log_service),  # Cross-service root cause correlation
            PromptBuildStage(prompt_engine, prompt_repo),
            AgentInferenceStage(provider_manager),
            ResultParseStage(),
            PriorityDecisionStage(),             # P0/P1/P2 priority decision
            PersistStage(),
        ]
    else:
        # Lightweight: only fetch and preprocess (no AI, no persist)
        stages = [
            LogFetchStage(log_service),
            LogPreprocessStage(),
            LogQualityFilterStage(),            # Smart quality filter for AI-off mode too
            BusinessNoiseFilterStage(),          # Business noise recognition for AI-off mode too
        ]

    pipeline = AnalysisPipeline(stages=stages)

    # 4. Build context
    query_params = {}
    try:
        query_params = json.loads(task.query_params)
    except Exception:
        pass

    ctx = PipelineContext(
        tenant_id=task.tenant_id,
        task_id=task_id,
        business_line_id=task.business_line_id,
        business_line_name=biz_name,
        es_index_pattern=biz.es_index_pattern,
        severity_threshold=biz.severity_threshold,
        language=biz_language,
        time_from=task.time_from,
        time_to=task.time_to,
        query=query_params.get("query", ""),
        extra_filters=query_params.get("extra_filters", {}),
        provider_config_id=task.provider_config_id or "",
        prompt_template_id=task.prompt_template_id or "",
        # Priority Decision Engine config from BusinessLine
        business_weight=biz.business_weight,
        is_core_path=biz.is_core_path,
        estimated_dau=biz.estimated_dau,
        night_policy=biz.night_policy,
        night_hours=biz.night_hours,
        # Cross-service correlation config
        related_services=json.loads(biz.related_services) if biz.related_services else {},
    )

    # 5. Execute pipeline
    from logmind.core.elasticsearch import close_celery_es_client
    try:
        ctx = await pipeline.run(ctx)

        # Check if fingerprint stage filtered out ALL logs (no new errors)
        fingerprint_new = ctx.log_metadata.get("fingerprint_new")
        if ai_enabled and fingerprint_new is not None and fingerprint_new == 0:
            # All errors were previously analyzed — skip notification
            logger.info(
                "task_skipped_all_fingerprinted",
                task_id=task_id,
                filtered=ctx.log_metadata.get("fingerprint_filtered", 0),
            )
            async with get_db_context() as session:
                task = await session.get(LogAnalysisTask, task_id)
                task.status = "completed"
                task.log_count = ctx.log_count
                task.token_usage = 0
                task.completed_at = datetime.now(timezone.utc)
                task.error_message = (
                    f"跳过分析: 全部 {ctx.log_metadata.get('fingerprint_filtered', 0)} 条错误"
                    f"已在近期分析过（指纹去重）"
                )
                task.stage_metrics = json.dumps(ctx.stage_metrics, ensure_ascii=False)
                await session.flush()
            return  # No notification needed

        # Check if log quality filter + business noise filter removed ALL logs
        if ctx.log_count == 0 or not ctx.processed_logs.strip():
            quality_filtered = ctx.log_metadata.get("quality_filtered", 0)
            noise_filtered = ctx.log_metadata.get("business_noise_filtered", 0)
            noise_categories = ctx.log_metadata.get("business_noise_categories", {})
            logger.info(
                "task_skipped_all_filtered",
                task_id=task_id,
                quality_filtered=quality_filtered,
                noise_filtered=noise_filtered,
                noise_categories=noise_categories,
            )

            # Build descriptive skip message
            skip_parts = []
            if quality_filtered > 0:
                skip_parts.append(f"{quality_filtered} 条 INFO/DEBUG 噪声")
            if noise_filtered > 0:
                cat_desc = ", ".join(
                    f"{cat}({cnt}条)" for cat, cnt in noise_categories.items()
                ) if noise_categories else ""
                skip_parts.append(
                    f"{noise_filtered} 条业务流程日志"
                    + (f"（{cat_desc}）" if cat_desc else "")
                )
            skip_reason = "、".join(skip_parts) if skip_parts else "全部日志"

            async with get_db_context() as session:
                task = await session.get(LogAnalysisTask, task_id)
                task.status = "completed"
                task.log_count = 0
                task.token_usage = 0
                task.completed_at = datetime.now(timezone.utc)
                task.error_message = f"跳过分析: {skip_reason}经过滤后无需处理"
                task.stage_metrics = json.dumps(ctx.stage_metrics, ensure_ascii=False)
                await session.flush()
            return  # No real errors, no notification

        if ai_enabled:
            # ── AI mode: update task + send AI alert ──────
            async with get_db_context() as session:
                task = await session.get(LogAnalysisTask, task_id)
                task.status = "completed"
                task.log_count = ctx.log_count
                task.token_usage = ctx.token_usage.total_tokens if ctx.token_usage else 0
                task.cost_usd = _estimate_cost_usd(
                    ctx.token_usage, ctx.provider_config_id
                ) if ctx.token_usage else 0.0
                task.provider_config_id = ctx.provider_config_id
                task.prompt_template_id = ctx.prompt_template_id
                task.completed_at = datetime.now(timezone.utc)
                task.stage_metrics = json.dumps(ctx.stage_metrics, ensure_ascii=False)
                if ctx.errors:
                    task.error_message = "; ".join(ctx.errors)

                # Persist Agent tool call chain
                if ctx.tool_call_records:
                    from logmind.domain.analysis.models import AgentToolCall
                    for rec in ctx.tool_call_records:
                        session.add(AgentToolCall(
                            task_id=task_id,
                            step=rec["step"],
                            tool_name=rec["tool_name"],
                            arguments=rec["arguments"],
                            result_preview=rec["result_preview"],
                            result_length=rec["result_length"],
                            duration_ms=rec["duration_ms"],
                            success=rec["success"],
                        ))

                await session.flush()

            # Fire alerts based on priority decision
            priority = ctx.priority_decision.get("priority", "P1")
            should_notify = ctx.priority_decision.get("should_notify", True)
            delay_morning = ctx.priority_decision.get("delay_until_morning", False)
            reason = ctx.priority_decision.get("reason", "")

            if should_notify and ctx.alerts_fired:
                logger.info(
                    "sending_priority_alert",
                    priority=priority,
                    reason=reason,
                    task_id=ctx.task_id,
                )
                await _send_ai_alerts(ctx, webhook_url, task_id)
            elif delay_morning:
                logger.info(
                    "alert_delayed_to_morning",
                    priority=priority,
                    reason=reason,
                    task_id=ctx.task_id,
                )
                # P1/P2 at night — stored for morning digest
            else:
                logger.info(
                    "alert_suppressed",
                    priority=priority,
                    reason=reason,
                    task_id=ctx.task_id,
                )

            # Self-learning hooks (non-critical, fire-and-forget)
            await _run_learning_hooks(ctx, task_id)
        else:
            # ── AI-off mode: send direct error notification ──
            async with get_db_context() as session:
                task = await session.get(LogAnalysisTask, task_id)
                task.status = "completed"
                task.log_count = ctx.log_count
                task.token_usage = 0  # No AI used
                task.completed_at = datetime.now(timezone.utc)
                task.stage_metrics = json.dumps(ctx.stage_metrics, ensure_ascii=False)
                await session.flush()

            # Send direct webhook notification if errors found
            if ctx.log_count > 0:
                await _send_error_log_notification(ctx, webhook_url)

    except Exception as e:
        logger.error("pipeline_failed", task_id=task_id, error=str(e))

        if _is_retryable_analysis_error(e):
            logger.warning(
                "pipeline_failed_retryable",
                task_id=task_id,
                error=str(e),
            )
            raise

        async with get_db_context() as session:
            task = await session.get(LogAnalysisTask, task_id)
            task.status = "failed"
            task.error_message = str(e)
            task.completed_at = datetime.now(timezone.utc)
            # Persist whatever stage metrics we collected before failure
            task.stage_metrics = json.dumps(ctx.stage_metrics, ensure_ascii=False)
            await session.flush()

        # If AI was enabled but pipeline failed, notify the AI/pipeline fault first.
        if ai_enabled:
            await _send_pipeline_error_notification(ctx, str(e), webhook_url)
            await _maybe_send_plain_error_fallback(ctx, e, webhook_url)
    finally:
        await close_celery_es_client()


async def _run_learning_hooks(ctx, task_id: str):
    """
    Post-analysis self-learning hooks (non-critical).

    Handles:
      - Vector index write-back (for future semantic dedup)
      - Error signal storage (for quality filter evolution)
      - Experience rule storage (for prompt evolution)
      - Profile cache invalidation
    """
    # Hook 1: Index analysis conclusions into vector store for future dedup
    if ctx.analysis_results and not ctx.semantic_dedup_hit:
        try:
            from logmind.domain.analysis.analysis_indexer import index_analysis_result
            combined_content = "\n\n".join(
                f"[{r.get('severity', 'info').upper()}] {r.get('content', '')}"
                for r in ctx.analysis_results
            )
            error_sig = ctx.error_signature
            if not error_sig:
                from logmind.domain.analysis.semantic_dedup import extract_error_signature
                error_sig = extract_error_signature(ctx.processed_logs, ctx.language)
            if error_sig and len(error_sig) >= 20:
                top_severity = "info"
                for r in ctx.analysis_results:
                    s = r.get("severity", "info")
                    if s == "critical":
                        top_severity = "critical"
                        break
                    elif s == "warning" and top_severity != "critical":
                        top_severity = "warning"
                index_analysis_result.delay(
                    task_id=task_id,
                    business_line_id=ctx.business_line_id,
                    error_signature=error_sig,
                    analysis_content=combined_content[:3000],
                    severity=top_severity,
                )
                logger.info("analysis_index_dispatched", task_id=task_id)
        except Exception as e:
            logger.warning("analysis_index_dispatch_failed", error=str(e))

    # Hook 2: Store AI-learned error signals for self-learning loop
    if ctx.learned_signals:
        try:
            from logmind.domain.log.error_signals import store_learned_signal
            confidence = _compute_top_confidence(ctx.analysis_results)
            stored_count = 0
            for signal in ctx.learned_signals:
                await store_learned_signal(
                    signal=signal,
                    source_task_id=task_id,
                    business_line_id=ctx.business_line_id,
                    confidence=confidence,
                )
                stored_count += 1
            logger.info(
                "learned_signals_stored",
                count=stored_count,
                signals=ctx.learned_signals[:5],
                task_id=task_id,
            )
        except Exception as e:
            logger.warning("learned_signals_store_failed", error=str(e))

    # Hook 3: Store AI-extracted experience rules for prompt evolution
    if ctx.learned_rules:
        try:
            from logmind.domain.analysis.business_profile import store_experience_rule
            confidence = _compute_top_confidence(ctx.analysis_results)
            for rule in ctx.learned_rules:
                await store_experience_rule(
                    rule=rule,
                    business_line_id=ctx.business_line_id,
                    source_task_id=task_id,
                    confidence=confidence,
                )
            logger.info(
                "experience_rules_stored",
                count=len(ctx.learned_rules),
                rules=ctx.learned_rules[:3],
                task_id=task_id,
            )
        except Exception as e:
            logger.warning("experience_rules_store_failed", error=str(e))

    # Hook 4: Invalidate profile cache so next analysis picks up new data
    try:
        from logmind.domain.analysis.business_profile import invalidate_profile_cache
        invalidate_profile_cache(ctx.business_line_id)
    except Exception:
        pass

    # Hook 5: AI classified logs as business noise → auto-learn noise rule
    if ctx.log_metadata.get("noise_classification") == "business_noise":
        try:
            from logmind.domain.log.business_noise import store_learned_noise_rule
            noise_pattern = ctx.error_signature[:100] if ctx.error_signature else ""
            if not noise_pattern:
                # Fallback: use first 100 chars of processed logs as pattern
                noise_pattern = ctx.processed_logs.strip()[:100]
            if noise_pattern and len(noise_pattern) >= 5:
                await store_learned_noise_rule(
                    pattern=noise_pattern,
                    business_line_id=ctx.business_line_id,
                    category=ctx.log_metadata.get("noise_category", "ai_learned"),
                    reason=ctx.log_metadata.get("noise_reason", "AI判定为业务噪声"),
                    source_task_id=task_id,
                    confidence=0.7,
                )
                logger.info(
                    "noise_rule_learned",
                    pattern=noise_pattern[:60],
                    category=ctx.log_metadata.get("noise_category"),
                    task_id=task_id,
                )
        except Exception as e:
            logger.warning("noise_rule_learn_failed", error=str(e))


async def _send_ai_alerts(ctx, webhook_url: str, task_id: str):
    """Send AI analysis alert notifications and persist AlertHistory records."""
    from logmind.domain.alert.aggregator import alert_aggregator
    from logmind.domain.alert.channels.webhook import notify_ai_alert
    from logmind.domain.alert.storm_detector import alert_storm_detector

    for alert in ctx.alerts_fired:
        severity = alert.get("severity", "warning")
        content = alert.get("content", "")

        # Prepend priority + issue status labels to alert content
        priority = ctx.priority_decision.get("priority", "P1")
        score = ctx.priority_decision.get("score", 0)
        priority_icons = {"P0": "🔴", "P1": "🟡", "P2": "🟢"}
        priority_label = f"{priority_icons.get(priority, '')} [{priority}|{score}分]"

        # Issue status label: first-seen / regression / known-issue
        issue_label = ""
        if ctx.log_metadata.get("is_regression"):
            resolved_at = ctx.log_metadata.get("regression_resolved_at", "")[:10]
            issue_label = f"🔄 [回归] 上次修复于 {resolved_at} | "
        elif ctx.log_metadata.get("is_first_seen"):
            issue_label = "🆕 [首次发现] "
        elif ctx.log_metadata.get("known_issue_hit_count", 0) > 1:
            hit_count = ctx.log_metadata["known_issue_hit_count"]
            issue_label = f"📋 [已知问题|第{hit_count}次] "

        reason = (ctx.priority_decision.get("reason") or "").strip()
        reason_line = f"\n通知原因: {reason}" if reason else ""

        log_refs_line = ""
        raw_refs = alert.get("source_log_refs", "[]")
        try:
            parsed_refs = json.loads(raw_refs) if isinstance(raw_refs, str) else raw_refs
        except Exception:
            parsed_refs = []
        if isinstance(parsed_refs, list):
            log_refs = [str(ref)[:120] for ref in parsed_refs if ref][:3]
            if log_refs:
                log_refs_line = f"\n日志引用: {', '.join(log_refs)}"

        content = f"{priority_label} {issue_label}{content}{reason_line}{log_refs_line}"

        # ── Storm Detection ──────────────────────────────────
        storm = alert_storm_detector.check_storm(
            business_line_id=ctx.business_line_id,
            severity=severity,
            alert_message=content[:200],
        )
        if storm.should_suppress:
            logger.info(
                "alert_storm_suppressed",
                storm_count=storm.storm_count,
                biz=ctx.business_line_name,
                task_id=ctx.task_id,
            )
            continue  # Skip — already sent storm summary
        if storm.storm_summary:
            content = storm.storm_summary  # Replace with aggregated summary

        # Check aggregation window
        should_send, agg_count = await alert_aggregator.should_send(
            business_line_id=ctx.business_line_id,
            severity=severity,
            error_signature=ctx.error_signature,
            alert_summary=content[:200],
        )

        if not should_send:
            logger.info(
                "ai_alert_aggregated",
                count=agg_count,
                biz=ctx.business_line_name,
                severity=severity,
                task_id=ctx.task_id,
            )
            continue

        # Send webhook notification and capture result
        notify_success = False
        notify_result_data = {}
        try:
            notify_success = await notify_ai_alert(
                business_line=ctx.business_line_name,
                domain=ctx.domain,
                branch=ctx.branch,
                host_name=ctx.host_name,
                language=ctx.language,
                severity=severity,
                content=content,
                task_id=ctx.task_id,
                log_count=ctx.log_count,
                webhook_url=webhook_url or None,
            )
            notify_result_data = {"success": notify_success, "channel": "webhook"}
        except Exception as e:
            logger.error("ai_alert_notification_failed", error=str(e))
            notify_result_data = {"success": False, "error": str(e)[:200]}

        # Persist AlertHistory record
        alert_record_id = None
        try:
            from logmind.core.database import get_db_context
            from logmind.domain.alert.models import AlertHistory

            async with get_db_context() as session:
                alert_record = AlertHistory(
                    # alert_rule_id is None — this alert was generated by AI analysis,
                    # not triggered by a user-defined AlertRule.
                    alert_rule_id=None,
                    analysis_task_id=task_id,
                    tenant_id=ctx.tenant_id,
                    status="fired",
                    severity=severity,
                    message=content[:4000],
                    notify_result=json.dumps(notify_result_data, ensure_ascii=False),
                    fired_at=datetime.now(timezone.utc),
                    priority=priority,
                )
                session.add(alert_record)
                await session.flush()
                alert_record_id = alert_record.id
        except Exception as e:
            logger.error("alert_history_persist_failed", error=str(e))

        # ── Auto-create Incident for P0 alerts ──────────────────
        if priority == "P0" and alert_record_id:
            try:
                await _auto_create_incident(
                    tenant_id=ctx.tenant_id,
                    title=f"[自动] {ctx.business_line_name} — {severity.upper()} 告警",
                    description=content[:500],
                    severity="P0",
                    alert_id=alert_record_id,
                    task_id=task_id,
                )
            except Exception as e:
                logger.error("auto_incident_create_failed", error=str(e))

        # ── Runbook Auto-Remediation ─────────────────────────
        if priority in ("P0", "P1"):
            try:
                from logmind.domain.alert.runbook_executor import runbook_executor
                from logmind.core.database import get_db_context as _get_db
                from logmind.domain.tenant.models import BusinessLine

                async with _get_db() as session:
                    biz = await session.get(BusinessLine, ctx.business_line_id)
                    if biz and biz.auto_remediation_config and biz.auto_remediation_config != "{}":
                        rb_result = await runbook_executor.execute(
                            config_json=biz.auto_remediation_config,
                            priority=priority,
                            alert_message=content[:500],
                            service_name=ctx.business_line_name,
                            task_id=task_id,
                        )
                        if rb_result.executed:
                            logger.info(
                                "runbook_executed",
                                summary=rb_result.summary,
                                priority=priority,
                                service=ctx.business_line_name,
                                task_id=task_id,
                            )
            except Exception as e:
                logger.warning("runbook_execution_failed", error=str(e))


async def _send_error_log_notification(ctx, webhook_url: str):
    """Send direct error log notification (AI disabled mode), with aggregation."""
    from logmind.domain.alert.aggregator import alert_aggregator
    from logmind.domain.alert.channels.webhook import notify_error_logs

    normalized_summary = _normalize_error_summary(ctx.processed_logs)
    normalized_summary, direct_noise_count = _filter_direct_notification_noise(normalized_summary)
    if direct_noise_count:
        logger.info(
            "error_log_notification_noise_filtered",
            biz=ctx.business_line_name,
            task_id=ctx.task_id,
            filtered=direct_noise_count,
        )

    if not _is_meaningful_error_summary(normalized_summary) or ctx.log_count <= 0:
        logger.info(
            "error_log_notification_skipped_empty",
            biz=ctx.business_line_name,
            task_id=ctx.task_id,
        )
        return

    # Check aggregation window
    should_send, agg_count = await alert_aggregator.should_send(
        business_line_id=ctx.business_line_id,
        severity="error",
        error_signature=normalized_summary[:200],
        alert_summary=normalized_summary[:200],
    )

    if not should_send:
        logger.info(
            "error_log_alert_aggregated",
            count=agg_count,
            biz=ctx.business_line_name,
            task_id=ctx.task_id,
        )
        return

    # Build a concise error summary from preprocessed logs
    error_summary = normalized_summary
    if len(error_summary) > 1500:
        error_summary = error_summary[:1500] + "\n... (更多日志请登录平台查看)"

    try:
        await notify_error_logs(
            business_line=ctx.business_line_name,
            domain=ctx.domain,
            branch=ctx.branch,
            host_name=ctx.host_name,
            language=ctx.language,
            log_count=ctx.log_count,
            error_summary=error_summary,
            time_from=ctx.time_from,
            time_to=ctx.time_to,
            webhook_url=webhook_url or None,
        )
    except Exception as e:
        logger.error("error_log_notification_failed", error=str(e))


async def _send_pipeline_error_notification(ctx, error_message: str, webhook_url: str):
    """
    Send pipeline/model error notification, rate-limited via aggregator.

    Uses a dedicated 'pipeline_error' severity key so it doesn't interfere
    with the normal AI alert aggregation window. The window is aligned to
    analysis_cooldown_minutes (default 10 min) to avoid notification flooding
    when all providers are repeatedly unavailable.
    """
    from logmind.core.config import get_settings
    from logmind.domain.alert.aggregator import alert_aggregator
    from logmind.domain.alert.channels.webhook import notify_pipeline_error

    settings = get_settings()
    # Use a longer aggregation window for infrastructure errors
    # to avoid spamming when providers are intermittently unavailable.
    pipeline_error_window = getattr(settings, "pipeline_error_cooldown_minutes", 240) * 60

    # Create a temporary aggregator with the pipeline-error-specific window
    from logmind.domain.alert.aggregator import AlertAggregator
    pipeline_agg = AlertAggregator(window_seconds=pipeline_error_window)

    should_send, agg_count = await pipeline_agg.should_send(
        business_line_id=ctx.business_line_id,
        severity="pipeline_error",
        error_signature=None,
        alert_summary=error_message[:200],
    )

    if not should_send:
        logger.info(
            "pipeline_error_notification_aggregated",
            count=agg_count,
            biz=ctx.business_line_name,
            task_id=ctx.task_id,
        )
        return

    try:
        await notify_pipeline_error(
            business_line=ctx.business_line_name,
            domain=ctx.domain,
            error_message=error_message,
            task_id=ctx.task_id,
            webhook_url=webhook_url or None,
        )
    except Exception as e:
        logger.error("pipeline_error_notification_failed", error=str(e))


def _should_send_plain_error_fallback(ctx, exc: Exception) -> bool:
    """
    Decide whether an AI pipeline failure should degrade to a normal error alert.

    Only do this when we already have usable preprocessed logs and the failure
    happened in the AI/post-AI portion of the pipeline.
    """
    normalized_summary = _normalize_error_summary(ctx.processed_logs)
    if ctx.log_count <= 0 or not _is_meaningful_error_summary(normalized_summary):
        return False

    if isinstance(exc, AllProvidersFailedError):
        return True

    if isinstance(exc, PipelineError):
        detail = getattr(exc, "detail", {}) or {}
        failed_stage = detail.get("stage", "")
        if failed_stage in {"prompt_build", "ai_inference", "result_parse", "persist"}:
            return True

        error_text = str(detail.get("error", "")).lower()
        if any(
            marker in error_text
            for marker in (
                "all providers failed",
                "server disconnected",
                "chat/completions",
                "400 bad request",
            )
        ):
            return True

    error_text = str(exc).lower()
    return any(
        marker in error_text
        for marker in (
            "all providers failed",
            "server disconnected",
            "chat/completions",
            "400 bad request",
        )
    )


async def _maybe_send_plain_error_fallback(ctx, exc: Exception, webhook_url: str) -> bool:
    """Send a normal error-log alert when AI analysis fails but logs are available."""
    if not _should_send_plain_error_fallback(ctx, exc):
        return False

    logger.warning(
        "sending_plain_error_fallback_after_ai_failure",
        task_id=ctx.task_id,
        business_line_id=ctx.business_line_id,
        error=str(exc)[:300],
    )

    try:
        await _send_error_log_notification(ctx, webhook_url)
        return True
    except Exception as fallback_exc:
        logger.error(
            "plain_error_fallback_failed",
            task_id=ctx.task_id,
            error=str(fallback_exc),
        )
        return False


@celery_app.task(name="logmind.domain.analysis.tasks.cleanup_old_tasks")
def cleanup_old_tasks():
    """Clean up analysis tasks older than 30 days."""
    run_async(_cleanup_old_tasks())


async def _cleanup_old_tasks():
    from sqlalchemy import delete, select

    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import (
        AgentToolCall,
        AnalysisResult,
        LogAnalysisTask,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    async with get_db_context() as session:
        # Subquery: find task IDs older than cutoff
        old_task_ids = (
            select(LogAnalysisTask.id)
            .where(LogAnalysisTask.created_at < cutoff)
            .scalar_subquery()
        )

        # Delete in FK-safe order: children first, then parent
        # 1. AgentToolCall (FK → log_analysis_task)
        r1 = await session.execute(
            delete(AgentToolCall).where(AgentToolCall.task_id.in_(old_task_ids))
        )
        # 2. AnalysisResult (FK → log_analysis_task)
        r2 = await session.execute(
            delete(AnalysisResult).where(AnalysisResult.task_id.in_(old_task_ids))
        )
        # 3. LogAnalysisTask (parent)
        r3 = await session.execute(
            delete(LogAnalysisTask).where(LogAnalysisTask.created_at < cutoff)
        )

        logger.info(
            "old_tasks_cleaned",
            cutoff=cutoff.isoformat(),
            deleted_tool_calls=r1.rowcount,
            deleted_results=r2.rowcount,
            deleted_tasks=r3.rowcount,
        )


async def _mark_task_timeout(task_id: str):
    """Mark a task as failed due to Celery soft time limit exceeded."""
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask

    try:
        async with get_db_context() as session:
            task = await session.get(LogAnalysisTask, task_id)
            if task:
                task.status = "failed"
                task.error_message = "分析超时: 任务执行超过 5 分钟被终止"
                task.completed_at = datetime.now(timezone.utc)
                await session.flush()
                logger.info("task_marked_timeout", task_id=task_id)
    except Exception as e:
        logger.error("mark_timeout_failed", task_id=task_id, error=str(e))


async def _mark_task_failed(task_id: str, error_message: str):
    """Mark a task as failed after retry attempts are exhausted."""
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask

    try:
        async with get_db_context() as session:
            task = await session.get(LogAnalysisTask, task_id)
            if task:
                task.status = "failed"
                task.error_message = error_message
                task.completed_at = datetime.now(timezone.utc)
                await session.flush()
                logger.info("task_marked_failed", task_id=task_id)
    except Exception as e:
        logger.error("mark_task_failed_failed", task_id=task_id, error=str(e))


async def _auto_create_incident(
    tenant_id: str,
    title: str,
    description: str,
    severity: str,
    alert_id: str,
    task_id: str,
):
    """
    Auto-create an Incident when a P0 alert fires.

    Links the alert and analysis task to the incident for full traceability.
    """
    import uuid
    from logmind.core.database import get_db_context
    from logmind.domain.incident import Incident, IncidentEvent

    async with get_db_context() as session:
        incident = Incident(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            title=title,
            description=description,
            severity=severity,
            status="investigating",
            assignee="system",
            related_alert_ids=[alert_id],
            related_task_ids=[task_id],
            tags=["auto-created"],
        )
        session.add(incident)

        # Initial timeline event
        event = IncidentEvent(
            id=str(uuid.uuid4()),
            incident_id=incident.id,
            event_type="alert",
            content=f"🤖 AI 自动创建故障 — P0 告警触发\n\n{description[:300]}",
            user="LogMind AI",
        )
        session.add(event)
        await session.flush()

    logger.info(
        "auto_incident_created",
        incident_id=incident.id,
        severity=severity,
        alert_id=alert_id,
        task_id=task_id,
    )
