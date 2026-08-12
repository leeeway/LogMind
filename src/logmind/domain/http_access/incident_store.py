"""Durable HTTP incident lifecycle and conservative feedback learning."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from logmind.core.database import get_db_context
from logmind.domain.http_access.models import (
    AccessIncident,
    AccessRecovery,
    AccessRouteBaseline,
    AccessRouteMetric,
)
from logmind.domain.http_access.site_config import (
    HttpAccessIncidentRecord,
    HttpAccessLearningRule,
)

_SUPPRESSING_DISPOSITIONS = {"false_positive", "expected"}


async def learn_stable_route_behaviors(
    tenant_id: str | None,
    metrics: list[AccessRouteMetric],
    baselines: dict[tuple[str, str, str], AccessRouteBaseline],
    *,
    observed_at: datetime,
) -> int:
    """Persist conservative expected quick-400 behavior after three days."""
    if not tenant_id or not metrics:
        return 0
    candidates: dict[str, AccessIncident] = {}
    for metric in metrics:
        baseline = baselines.get((metric.source, metric.site, metric.route_key))
        dominant = _dominant_status(metric.status_counts)
        dominant_upstream = _dominant_status(metric.upstream_status_counts)
        if (
            not baseline
            or not baseline.is_ready(min_samples=6, min_days=3)
            or dominant != 400
            or dominant_upstream != 400
            or metric.status_5xx
            or metric.p95_ms >= 1000
            or baseline.rate_4xx <= 0
            or metric.rate_4xx > baseline.rate_4xx * 1.5
            or metric.rate_4xx > baseline.rate_4xx + 0.10
        ):
            continue
        incident = AccessIncident(
            source=metric.source,
            site=metric.site,
            kind="route_4xx",
            priority="P1",
            request_count=metric.request_count,
            current_value=metric.rate_4xx,
            baseline_value=baseline.rate_4xx,
            status_4xx=metric.status_4xx,
            route_key=metric.route_key,
            status_counts=dict(metric.status_counts),
            upstream_status_counts=dict(metric.upstream_status_counts),
            p95_ms=metric.p95_ms,
        )
        candidates[incident.fingerprint] = incident
    if not candidates:
        return 0
    learned = 0
    async with get_db_context() as session:
        result = await session.execute(
            select(HttpAccessLearningRule).where(
                HttpAccessLearningRule.tenant_id == tenant_id,
                HttpAccessLearningRule.fingerprint.in_(candidates),
            )
        )
        existing = {item.fingerprint: item for item in result.scalars().all()}
        for fingerprint, incident in candidates.items():
            rule = existing.get(fingerprint)
            if rule and rule.source != "auto":
                continue
            if rule is None:
                rule = HttpAccessLearningRule(
                    tenant_id=tenant_id,
                    fingerprint=fingerprint,
                    site=incident.site,
                    kind=incident.kind,
                    disposition="expected",
                    source="auto",
                    confidence=0.9,
                    reason="跨3天稳定、上下游均400且P95低于1秒",
                    expires_at=observed_at + timedelta(days=30),
                )
                session.add(rule)
                learned += 1
            else:
                rule.last_hit_at = observed_at
                rule.hit_count += 1
                rule.expires_at = observed_at + timedelta(days=30)
    return learned


def _dominant_status(counts: dict[int, int]) -> int:
    if not counts:
        return 0
    try:
        return int(max(counts, key=counts.get))
    except (TypeError, ValueError):
        return 0


async def filter_learned_incidents(
    tenant_id: str | None,
    incidents: list[AccessIncident],
    *,
    now: datetime,
) -> tuple[list[AccessIncident], int]:
    if not tenant_id or not incidents:
        return incidents, 0
    fingerprints = [item.fingerprint for item in incidents]
    async with get_db_context() as session:
        result = await session.execute(
            select(HttpAccessLearningRule).where(
                HttpAccessLearningRule.tenant_id == tenant_id,
                HttpAccessLearningRule.fingerprint.in_(fingerprints),
            )
        )
        rules = {item.fingerprint: item for item in result.scalars().all()}
        retained: list[AccessIncident] = []
        suppressed = 0
        for incident in incidents:
            rule = rules.get(incident.fingerprint)
            if rule is not None and _breaks_expected_baseline(incident):
                rule.disposition = "review"
                rule.reason = "当前错误率达到历史3倍且增加至少20个百分点，自动退出抑制"
                rule.expires_at = now
                retained.append(incident)
                continue
            if (
                incident.priority == "P0"
                or rule is None
                or rule.disposition not in _SUPPRESSING_DISPOSITIONS
                or (rule.expires_at and _as_aware(rule.expires_at) <= _as_aware(now))
            ):
                retained.append(incident)
                continue
            rule.hit_count += 1
            rule.last_hit_at = now
            suppressed += 1
        return retained, suppressed


def _breaks_expected_baseline(incident: AccessIncident) -> bool:
    """A three-fold/new +20pp surge always escapes an expected-behavior rule."""
    return (
        incident.kind == "route_4xx"
        and incident.baseline_value > 0
        and incident.current_value >= incident.baseline_value * 3
        and incident.current_value >= incident.baseline_value + 0.20
    )


async def sync_incident_records(
    tenant_id: str | None,
    incidents: list[AccessIncident],
    *,
    due_fingerprints: set[str],
    observed_at: datetime,
) -> None:
    if not tenant_id or not incidents:
        return
    fingerprints = [item.fingerprint for item in incidents]
    async with get_db_context() as session:
        result = await session.execute(
            select(HttpAccessIncidentRecord).where(
                HttpAccessIncidentRecord.tenant_id == tenant_id,
                HttpAccessIncidentRecord.fingerprint.in_(fingerprints),
            )
        )
        existing = {item.fingerprint: item for item in result.scalars().all()}
        for incident in incidents:
            record = existing.get(incident.fingerprint)
            evidence = {
                "request_count": incident.request_count,
                "status_4xx": incident.status_4xx,
                "status_5xx": incident.status_5xx,
                "p95_ms": round(incident.p95_ms, 3),
                "successful_count": incident.successful_count,
                "slow_2s_count": incident.slow_2s_count,
                "current_value": round(incident.current_value, 6),
                "baseline_value": round(incident.baseline_value, 6),
                "status_counts": incident.status_counts,
                "upstream_status_counts": incident.upstream_status_counts,
            }
            if record is None:
                record = HttpAccessIncidentRecord(
                    tenant_id=tenant_id,
                    fingerprint=incident.fingerprint,
                    site=incident.site,
                    source=incident.source,
                    sources=json.dumps(incident.sources or [incident.source]),
                    kind=incident.kind,
                    priority=incident.priority,
                    route_key=incident.route_key,
                    status="open",
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    current_impact=incident.impact,
                    peak_impact=incident.impact,
                    notification_pending=incident.fingerprint in due_fingerprints,
                    evidence_json=json.dumps(evidence, ensure_ascii=False),
                )
                session.add(record)
                continue
            record.last_seen_at = observed_at
            record.recovered_at = None
            if record.status in {"resolved", "recovered", "muted"}:
                record.status = "open"
            record.priority = incident.priority
            record.sources = json.dumps(incident.sources or [incident.source])
            record.current_impact = incident.impact
            record.peak_impact = max(record.peak_impact, incident.impact)
            record.notification_pending = (
                record.notification_pending or incident.fingerprint in due_fingerprints
            )
            record.evidence_json = json.dumps(evidence, ensure_ascii=False)


async def mark_notification_delivered(
    tenant_id: str | None,
    incidents: list[AccessIncident],
    *,
    delivered_at: datetime,
) -> None:
    if not tenant_id or not incidents:
        return
    fingerprints = [item.fingerprint for item in incidents]
    impacts = {item.fingerprint: item.impact for item in incidents}
    async with get_db_context() as session:
        result = await session.execute(
            select(HttpAccessIncidentRecord).where(
                HttpAccessIncidentRecord.tenant_id == tenant_id,
                HttpAccessIncidentRecord.fingerprint.in_(fingerprints),
            )
        )
        for record in result.scalars().all():
            record.last_notified_at = delivered_at
            record.last_notified_impact = impacts.get(record.fingerprint, 0)
            record.notification_pending = False
            record.notification_count += 1


async def mark_recovered_records(
    tenant_id: str | None,
    recoveries: list[AccessRecovery],
    *,
    recovered_at: datetime,
) -> None:
    if not tenant_id or not recoveries:
        return
    async with get_db_context() as session:
        for recovery in recoveries:
            conditions = [
                HttpAccessIncidentRecord.tenant_id == tenant_id,
                HttpAccessIncidentRecord.status.in_(["open", "acknowledged"]),
            ]
            if recovery.fingerprint:
                conditions.append(HttpAccessIncidentRecord.fingerprint == recovery.fingerprint)
            else:
                conditions.extend(
                    [
                        HttpAccessIncidentRecord.site == recovery.site,
                        HttpAccessIncidentRecord.kind == recovery.kind,
                        HttpAccessIncidentRecord.route_key == recovery.route_key,
                    ]
                )
            result = await session.execute(select(HttpAccessIncidentRecord).where(*conditions))
            for record in result.scalars().all():
                record.status = "recovered"
                record.recovered_at = recovered_at
                record.notification_pending = False


async def record_feedback(
    session: AsyncSession,
    record: HttpAccessIncidentRecord,
    *,
    action: str,
    comment: str,
    user_id: str,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(UTC)
    result = await session.execute(
        select(HttpAccessLearningRule).where(
            HttpAccessLearningRule.tenant_id == record.tenant_id,
            HttpAccessLearningRule.fingerprint == record.fingerprint,
        )
    )
    rule = result.scalar_one_or_none()
    record.feedback = action
    record.feedback_comment = comment
    record.handled_by = user_id
    if action == "valid":
        record.status = "acknowledged"
    elif action == "resolved":
        record.status = "resolved"
        record.recovered_at = current
        record.notification_pending = False
    elif action in _SUPPRESSING_DISPOSITIONS:
        record.status = "muted"
        record.notification_pending = False
        if rule is None:
            rule = HttpAccessLearningRule(
                tenant_id=record.tenant_id,
                fingerprint=record.fingerprint,
                site=record.site,
                kind=record.kind,
                disposition=action,
                reason=comment,
                expires_at=current + timedelta(days=30),
            )
            session.add(rule)
        else:
            rule.disposition = action
            rule.reason = comment
            rule.expires_at = current + timedelta(days=30)
    if action in {"valid", "resolved"} and rule is not None:
        rule.disposition = "review"
        rule.reason = "人工确认有效，停止学习抑制"
        rule.expires_at = current


def _as_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
