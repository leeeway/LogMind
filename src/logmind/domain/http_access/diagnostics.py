"""Evidence enrichment for confirmed, novel HTTP incidents."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import select

from logmind.core.config import get_settings
from logmind.core.database import get_db_context
from logmind.core.logging import get_logger
from logmind.domain.analysis.sensitive_masker import mask_sensitive
from logmind.domain.http_access.models import AccessIncident
from logmind.domain.http_access.repository import RepositoryError, git_repository_service
from logmind.domain.http_access.site_config import (
    GitDeploymentRevision,
    GitRepositoryConfig,
    HttpAccessIncidentRecord,
    HttpAccessSiteConfig,
)
from logmind.domain.log.schemas import LogQueryRequest

logger = get_logger(__name__)

_STACK_SYMBOL_RE = re.compile(
    r"(?:at\s+)?(?:[A-Za-z_][\w`+]*\.){1,8}([A-Za-z_][\w`+]*)\s*(?:\(|:)",
)


async def enrich_incident_diagnostics(
    tenant_id: str,
    incidents: list[AccessIncident],
    site_configs: dict[str, HttpAccessSiteConfig],
    *,
    time_from: datetime,
    time_to: datetime,
) -> None:
    """Attach app-log, knowledge and exact deployed-code evidence in place."""
    for incident in incidents:
        config = site_configs.get(incident.site)
        if not config:
            continue
        stack_symbols: list[str] = []
        if config.diagnostic_business_line_id:
            evidence, stack_symbols = await _application_evidence(
                config.diagnostic_business_line_id,
                time_from=time_from - timedelta(minutes=5),
                time_to=time_to,
            )
            incident.diagnostic_evidence.extend(evidence)
            await _analysis_history_evidence(
                tenant_id,
                config.diagnostic_business_line_id,
                incident,
                evidence,
            )
        await _historical_experience(tenant_id, incident)
        await _knowledge_evidence(tenant_id, incident)
        if config.repository_id:
            finding = await _code_evidence(
                tenant_id,
                config.repository_id,
                incident,
                stack_symbols=stack_symbols,
                before=time_to,
                service_name=config.deployment_service_name,
            )
            if finding:
                incident.code_findings.append(finding)
        await _persist_diagnosis(tenant_id, incident)


async def _application_evidence(
    business_line_id: str,
    *,
    time_from: datetime,
    time_to: datetime,
) -> tuple[list[str], list[str]]:
    from logmind.domain.log.service import log_service
    from logmind.domain.tenant.models import BusinessLine

    async with get_db_context() as session:
        business = await session.get(BusinessLine, business_line_id)
        if not business or not business.is_active:
            return [], []
        try:
            filters = json.loads(business.default_filters or "{}")
        except (TypeError, json.JSONDecodeError):
            filters = {}
        if not isinstance(filters, dict):
            filters = {}
        request = LogQueryRequest(
            index_pattern=business.es_index_pattern,
            time_from=time_from,
            time_to=time_to,
            severity="error",
            language=business.language,
            business_line_id=business.id,
            extra_filters=filters,
            size=200,
        )
    try:
        response = await log_service.search_logs(request)
    except Exception as exc:
        logger.warning("http_access_app_log_enrichment_failed", error=str(exc))
        return [], []
    messages: list[str] = []
    symbols: list[str] = []
    seen: set[str] = set()
    for log in response.logs:
        message = mask_sensitive(str(log.message or ""))
        metadata = " ".join(
            value
            for value in (
                f"实例={str(log.pod_name or log.host_name)[:120]}"
                if log.pod_name or log.host_name
                else "",
                f"版本={str(log.image_version)[:120]}" if log.image_version else "",
            )
            if value
        )
        compact = " ".join(f"{metadata} {message}".split())[:1200]
        signature = re.sub(r"\b\d+\b", "{n}", compact.lower())
        if not compact or signature in seen:
            continue
        seen.add(signature)
        messages.append(compact)
        symbols.extend(_STACK_SYMBOL_RE.findall(compact))
        if len(messages) >= 20:
            break
    return messages, list(dict.fromkeys(symbols))[:20]


async def _knowledge_evidence(tenant_id: str, incident: AccessIncident) -> None:
    from logmind.domain.analysis.agent_tools import _exec_search_knowledge_base

    dominant_status = (
        max(incident.status_counts, key=incident.status_counts.get)
        if incident.status_counts
        else ""
    )
    query = " ".join(
        value
        for value in (
            incident.site,
            incident.kind,
            incident.route_key,
            f"status={dominant_status}",
            *incident.diagnostic_evidence[:2],
        )
        if value
    )[:1800]
    try:
        result = await _exec_search_knowledge_base({"query": query}, tenant_id=tenant_id)
    except Exception as exc:
        logger.warning("http_access_knowledge_enrichment_failed", error=str(exc))
        return
    if not result or result.startswith("未找到") or '"error"' in result[:80]:
        return
    incident.knowledge_sources.append(f"租户知识库({_result_confidence(result)})")
    incident.diagnostic_evidence.append("知识库参考: " + mask_sensitive(result)[:1600])


async def _analysis_history_evidence(
    tenant_id: str,
    business_line_id: str,
    incident: AccessIncident,
    application_evidence: list[str],
) -> None:
    from logmind.domain.analysis.agent_tools import _exec_search_similar_incidents

    pattern = " ".join(
        [incident.site, incident.route_key, incident.kind, *application_evidence[:2]]
    )[:1800]
    if not pattern.strip():
        return
    try:
        result = await _exec_search_similar_incidents(
            {"error_pattern": pattern},
            index_pattern="",
            tenant_id=tenant_id,
            business_line_id=business_line_id,
        )
    except Exception as exc:
        logger.warning("http_access_history_enrichment_failed", error=str(exc))
        return
    if not result or result.startswith(("未找到", "暂无")) or '"error"' in result[:80]:
        return
    incident.knowledge_sources.append(f"应用历史分析({_result_confidence(result)})")
    incident.diagnostic_evidence.append("历史分析参考: " + mask_sensitive(result)[:1600])


async def _historical_experience(
    tenant_id: str,
    incident: AccessIncident,
) -> None:
    """Reuse only operator-verified incidents; raw logs/source are excluded."""
    async with get_db_context() as session:
        result = await session.execute(
            select(HttpAccessIncidentRecord)
            .where(
                HttpAccessIncidentRecord.tenant_id == tenant_id,
                HttpAccessIncidentRecord.site == incident.site,
                HttpAccessIncidentRecord.kind == incident.kind,
                HttpAccessIncidentRecord.route_key == incident.route_key,
                HttpAccessIncidentRecord.feedback.in_(["valid", "resolved"]),
            )
            .order_by(HttpAccessIncidentRecord.updated_at.desc())
            .limit(3)
        )
        records = list(result.scalars().all())
    if not records:
        return
    summaries: list[str] = []
    for record in records:
        comment = mask_sensitive(record.feedback_comment or "")[:500]
        diagnosis = _safe_diagnosis_summary(record.diagnosis_json)
        if comment or diagnosis:
            summaries.append(
                f"历史已验证事件({record.feedback}): "
                + "；".join(value for value in (comment, diagnosis) if value)
            )
    if summaries:
        incident.knowledge_sources.append("已验证HTTP历史经验(人工确认)")
        incident.diagnostic_evidence.extend(summaries[:3])


def _result_confidence(value: str) -> str:
    match = re.search(r"(?:相关度|相似度):\s*([01](?:\.\d+)?)", value)
    return match.group(1) if match else "辅助证据"


def _safe_diagnosis_summary(raw: str) -> str:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(value, dict):
        return ""
    # Only retain already-structured conclusions. Application logs and source
    # snippets from an old event are intentionally not copied forward.
    candidates = value.get("conclusion") or value.get("summary") or ""
    return mask_sensitive(str(candidates))[:800]


async def _code_evidence(
    tenant_id: str,
    repository_id: str,
    incident: AccessIncident,
    *,
    stack_symbols: list[str],
    before: datetime,
    service_name: str = "",
) -> dict | None:
    async with get_db_context() as session:
        repository = await session.get(GitRepositoryConfig, repository_id)
        if not repository or repository.tenant_id != tenant_id or not repository.is_active:
            return None
        revision_query = select(GitDeploymentRevision).where(
            GitDeploymentRevision.tenant_id == tenant_id,
            GitDeploymentRevision.repository_id == repository_id,
            GitDeploymentRevision.environment == "production",
            GitDeploymentRevision.deployed_at <= before,
        )
        if service_name:
            revision_query = revision_query.where(
                GitDeploymentRevision.service_name == service_name
            )
        result = await session.execute(
            revision_query.order_by(GitDeploymentRevision.deployed_at.desc()).limit(1)
        )
        revision = result.scalar_one_or_none()
        if not revision:
            return None
    try:
        evidence = await git_repository_service.locate(
            repository,
            commit_sha=revision.commit_sha,
            previous_commit_sha=revision.previous_commit_sha,
            route_key=incident.route_key,
            stack_symbols=stack_symbols,
        )
    except RepositoryError as exc:
        logger.warning(
            "http_access_code_enrichment_failed",
            repository_id=repository_id,
            error=str(exc),
        )
        return None
    finding = evidence.to_dict(include_source=get_settings().http_access_source_to_ai_enabled)
    for snippet in finding.get("snippets", []):
        if "content" in snippet:
            snippet["content"] = mask_sensitive(str(snippet["content"]))[:8000]
    return finding


async def _persist_diagnosis(tenant_id: str, incident: AccessIncident) -> None:
    async with get_db_context() as session:
        result = await session.execute(
            select(HttpAccessIncidentRecord).where(
                HttpAccessIncidentRecord.tenant_id == tenant_id,
                HttpAccessIncidentRecord.fingerprint == incident.fingerprint,
            )
        )
        record = result.scalar_one_or_none()
        if record:
            record.diagnosis_json = json.dumps(
                {
                    "application_evidence": incident.diagnostic_evidence[:20],
                    "knowledge_sources": incident.knowledge_sources[:10],
                    "code_findings": incident.code_findings[:5],
                },
                ensure_ascii=False,
            )
