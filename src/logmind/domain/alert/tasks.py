"""
Alert Domain — Celery Tasks

Scheduled log patrol — Fan-out architecture for multi-business-line parallelism.

Design:
  scheduled_log_patrol (Beat) → dispatches N independent patrol tasks
  patrol_single_business_line (Worker) → each runs independently

Benefits:
  - True parallel execution across Celery workers
  - Single business line failure doesn't block others
  - Scales linearly with worker count
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from logmind.core.async_task import run_async
from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(name="logmind.domain.alert.tasks.scheduled_log_patrol")
def scheduled_log_patrol():
    """
    Scheduled log patrol dispatcher — runs periodically via Celery Beat.

    Fan-out pattern: queries all eligible business lines, then dispatches
    an independent patrol task for each one. This is fast (only DB reads)
    and ensures no single business line can block the entire patrol cycle.
    """
    logger.info("scheduled_patrol_dispatcher_started")
    run_async(_dispatch_patrols())


async def _dispatch_patrols():
    """
    Query all active business lines, check cooldown, and dispatch
    individual patrol tasks for eligible ones.
    """
    from sqlalchemy import select

    from logmind.core.config import get_settings
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.tenant.models import BusinessLine

    settings = get_settings()
    now = datetime.now(timezone.utc)
    cooldown = timedelta(minutes=settings.effective_patrol_interval_minutes)

    dispatched = 0
    skipped = 0

    async with get_db_context() as session:
        # Get all active business lines
        stmt = select(BusinessLine).where(BusinessLine.is_active == True)
        result = await session.execute(stmt)
        business_lines = result.scalars().all()

        for biz in business_lines:
            # Check cooldown — skip if recently analyzed
            last_task_stmt = (
                select(LogAnalysisTask)
                .where(
                    LogAnalysisTask.business_line_id == biz.id,
                    LogAnalysisTask.task_type == "scheduled",
                    LogAnalysisTask.created_at > (now - cooldown),
                )
                .limit(1)
            )
            last_result = await session.execute(last_task_stmt)
            if last_result.scalar_one_or_none():
                logger.debug("patrol_cooldown_skip", business_line=biz.name)
                skipped += 1
                continue

            # Dispatch independent patrol task for this business line
            patrol_single_business_line.delay(biz.id)
            dispatched += 1
            logger.info("patrol_dispatched", business_line=biz.name, biz_id=biz.id)

    logger.info(
        "scheduled_patrol_dispatcher_done",
        dispatched=dispatched,
        skipped_cooldown=skipped,
        total_business_lines=dispatched + skipped,
    )


@celery_app.task(
    bind=True,
    name="logmind.domain.alert.tasks.patrol_single_business_line",
    max_retries=3,
    default_retry_delay=60,
)
def patrol_single_business_line(self, business_line_id: str):
    """
    Independent patrol task for a single business line.

    Creates an analysis task and dispatches it. Runs in its own
    Celery worker slot, so failures are isolated.
    """
    from sqlalchemy.exc import DBAPIError, OperationalError

    logger.info("patrol_single_started", biz_id=business_line_id)
    try:
        run_async(_patrol_single(business_line_id))
    except (ConnectionResetError, ConnectionError, OSError, DBAPIError, OperationalError) as exc:
        logger.warning(
            "patrol_single_retrying",
            biz_id=business_line_id,
            retry=self.request.retries + 1,
            error=str(exc),
        )
        raise self.retry(exc=exc)


async def _patrol_single(business_line_id: str):
    """Execute patrol for one business line: anomaly check → create task if needed."""
    from logmind.core.config import get_settings
    from logmind.core.database import get_db_context
    from logmind.domain.analysis.models import LogAnalysisTask
    from logmind.domain.analysis.tasks import run_analysis_task
    from logmind.domain.anomaly.detector import anomaly_detector
    from logmind.domain.anomaly.predictor import trend_predictor
    from logmind.domain.tenant.models import BusinessLine

    settings = get_settings()
    now = datetime.now(timezone.utc)

    async with get_db_context() as session:
        biz = await session.get(BusinessLine, business_line_id)
        if not biz or not biz.is_active:
            logger.warning("patrol_biz_not_found_or_inactive", biz_id=business_line_id)
            return

        from sqlalchemy import select
        # A durable successful scan is the watermark, not wall-clock Beat time.
        previous = await session.scalar(select(LogAnalysisTask).where(
            LogAnalysisTask.business_line_id == biz.id,
            LogAnalysisTask.tenant_id == biz.tenant_id,
            LogAnalysisTask.task_type == "scheduled",
            LogAnalysisTask.status != "failed",
        ).order_by(LogAnalysisTask.time_to.desc()).limit(1))
        since = now - timedelta(minutes=settings.effective_anomaly_window_minutes)
        if previous and previous.time_to:
            last = previous.time_to.replace(tzinfo=timezone.utc) if previous.time_to.tzinfo is None else previous.time_to
            since = min(since, last - timedelta(minutes=settings.analysis_patrol_overlap_minutes))
        since = max(since, now - timedelta(minutes=settings.analysis_patrol_max_catchup_minutes))
        # Inspect content as well as configured language: mislabelled C# sites
        # must not miss exceptions. The parser requires .NET/SDK evidence.
        anomaly = await anomaly_detector.detect(
            index_pattern=biz.es_index_pattern,
            window_minutes=settings.effective_anomaly_window_minutes,
            severity_threshold=biz.severity_threshold,
            since=since,
            until=now,
            inspect_concrete_faults=settings.analysis_concrete_fault_enabled,
        )
        metadata = {
            "trigger": anomaly.trigger,
            "current_errors": anomaly.current_errors,
            "concrete_faults": anomaly.concrete_faults,
            "evidence_refs": anomaly.evidence_refs,
            "detection_failed": anomaly.detection_failed,
            "shadow": anomaly.trigger == "concrete_exception" and settings.analysis_concrete_fault_shadow,
        }
        if anomaly.detection_failed:
            session.add(LogAnalysisTask(
                tenant_id=biz.tenant_id, business_line_id=biz.id,
                task_type="scheduled", status="failed", time_from=since, time_to=now,
                query_params=json.dumps({"patrol": metadata}),
                error_message=anomaly.detail, completed_at=now,
            ))
            await session.commit()
            return

        if anomaly.is_anomaly:
            logger.info(
                "patrol_anomaly_triggered",
                business_line=biz.name,
                level=anomaly.level,
                z_score=anomaly.z_score,
                current_errors=anomaly.current_errors,
                detail=anomaly.detail,
            )

            # Create patrol task with anomaly metadata
            task = LogAnalysisTask(
                tenant_id=biz.tenant_id,
                business_line_id=biz.id,
                task_type="scheduled",
                status="pending",
                time_from=min(since, now - timedelta(minutes=settings.effective_lookback_minutes)),
                time_to=now,
                query_params=json.dumps({"patrol": metadata, "severity": "error" if anomaly.concrete_faults else biz.severity_threshold}),
            )
            session.add(task)
            await session.flush()
            await session.commit()

            run_analysis_task.delay(task.id)
            logger.info(
                "patrol_task_created",
                business_line=biz.name,
                task_id=task.id,
                anomaly_level=anomaly.level,
                z_score=anomaly.z_score,
            )
            return  # Realtime anomaly handled — skip predictive check

        session.add(LogAnalysisTask(
            tenant_id=biz.tenant_id, business_line_id=biz.id,
            task_type="scheduled", status="completed", time_from=since, time_to=now,
            query_params=json.dumps({"patrol": metadata}),
            log_count=anomaly.current_errors, completed_at=now,
            error_message="未触发诊断：未发现数量突增或明确异常",
        ))
        await session.commit()

        # ── Predictive Alerting ───────────────────────────
        # Z-Score is normal now, but check if trend is rising toward threshold
        prediction = await trend_predictor.predict(
            index_pattern=biz.es_index_pattern,
            severity_threshold=biz.severity_threshold,
        )

        if prediction.predicted_level in ("warning", "critical") and prediction.trend_direction == "rising":
            logger.warning(
                "patrol_predictive_alert",
                business_line=biz.name,
                predicted_level=prediction.predicted_level,
                trend_slope=prediction.trend_slope,
                predicted_errors_30m=prediction.predicted_errors_30m,
                confidence=prediction.confidence,
                detail=prediction.detail,
            )
            await _fire_predictive_alert(biz, prediction, now, session)
        else:
            logger.info(
                "patrol_skipped_no_anomaly",
                business_line=biz.name,
                z_score=anomaly.z_score,
                current_errors=anomaly.current_errors,
                baseline_mean=anomaly.baseline_mean,
                predicted_level=prediction.predicted_level,
                trend_direction=prediction.trend_direction,
            )


async def _fire_predictive_alert(biz, prediction, now, session):
    """
    Write a predictive AlertHistory record when trend forecasting signals
    an upcoming threshold breach. Does NOT trigger AI analysis — this is
    a lightweight early-warning signal only.

    Deduplication: skip if a predictive alert for this business line was
    already fired in the last 60 minutes to avoid alert storms.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from logmind.domain.alert.models import AlertHistory

    # Dedup: skip if we already fired a predictive alert recently
    recent_cutoff = now - timedelta(minutes=60)
    existing = (
        await session.execute(
            select(AlertHistory).where(
                AlertHistory.business_line_id == biz.id,
                AlertHistory.alert_type == "predictive",
                AlertHistory.fired_at >= recent_cutoff,
            ).limit(1)
        )
    ).scalar_one_or_none()

    if existing:
        logger.debug(
            "predictive_alert_dedup_skip",
            business_line=biz.name,
            last_fired=existing.fired_at.isoformat(),
        )
        return

    severity = prediction.predicted_level  # warning / critical
    priority = "P1" if severity == "critical" else "P2"
    confidence_pct = int(prediction.confidence * 100)

    message = (
        f"[预测告警] {biz.name} 错误趋势持续上升，"
        f"预计未来30分钟约 {prediction.predicted_errors_30m:.0f} 条错误"
        f"（当前 {prediction.current_rate:.0f}/h，基线 {prediction.baseline_mean:.0f}/h）。"
        f"置信度 {confidence_pct}%。{prediction.detail}"
    )

    # Send predictive webhook notification
    notify_success = False
    try:
        from logmind.domain.alert.channels.webhook import notify_predictive_alert
        notify_success = await notify_predictive_alert(
            business_line=biz.name,
            severity=severity,
            priority=priority,
            predicted_errors_30m=prediction.predicted_errors_30m,
            current_rate=prediction.current_rate,
            baseline_mean=prediction.baseline_mean,
            confidence_pct=confidence_pct,
            detail=prediction.detail,
            webhook_url=biz.webhook_url,
        )
    except Exception as ne:
        logger.error("predictive_alert_notification_failed", error=str(ne))

    import json
    notify_res = {"status": "sent" if notify_success else "failed"}

    alert_record = AlertHistory(
        alert_rule_id=None,
        analysis_task_id=None,
        tenant_id=biz.tenant_id,
        business_line_id=biz.id,
        status="fired",
        severity=severity,
        message=message,
        notify_result=json.dumps(notify_res),
        fired_at=now,
        priority=priority,
        alert_type="predictive",
    )
    session.add(alert_record)
    await session.flush()

    logger.info(
        "predictive_alert_fired",
        business_line=biz.name,
        severity=severity,
        predicted_errors_30m=prediction.predicted_errors_30m,
        confidence=prediction.confidence,
        alert_id=alert_record.id,
        notify_status=notify_res["status"],
    )
