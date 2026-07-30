"""Evidence extraction and root-cause candidate ranking for analysis results."""

from __future__ import annotations

import json
from typing import Any


_SEVERITY_SCORE = {"critical": 0.22, "warning": 0.14, "info": 0.05}


def parse_json_object(value: Any) -> dict:
    """Parse persisted JSON text into a dict, returning empty dict on bad data."""
    if isinstance(value, dict):
        return value
    if not value or not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_json_list(value: Any) -> list:
    """Parse persisted JSON text into a list, returning empty list on bad data."""
    if isinstance(value, list):
        return value
    if not value or not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


def _created_at_iso(result: Any) -> str:
    created_at = _get(result, "created_at")
    if hasattr(created_at, "isoformat"):
        return created_at.isoformat()
    return _as_text(created_at)


def _clamp_score(value: float) -> float:
    return round(max(0.05, min(value, 0.99)), 2)


def _append_once(items: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in items:
        items.append(value)


def _candidate_score(
    result: Any,
    structured: dict,
    evidence_count: int,
    *,
    has_upstream: bool = False,
    has_change_point: bool = False,
    has_correlation: bool = False,
    has_history: bool = False,
    has_log_refs: bool = False,
) -> float:
    confidence = float(_get(result, "confidence_score", 0.5) or 0.5)
    severity = _as_text(_get(result, "severity", "info"), "info")
    score = confidence * 0.45 + _SEVERITY_SCORE.get(severity, 0.05)
    score += min(evidence_count, 5) * 0.07
    if has_upstream:
        score += 0.12
    if has_change_point:
        score += 0.10
    if has_correlation:
        score += 0.08
    if has_history:
        score += 0.08
    if has_log_refs:
        score += 0.05
    if structured.get("is_regression"):
        score += 0.12
    return _clamp_score(score)


def build_root_cause_evidence(results: list[Any]) -> dict:
    """
    Build normalized evidence, ranked root-cause candidates, and verification steps.

    This function deliberately works from persisted AnalysisResult-like objects so
    historical tasks can gain evidence views without DB migrations.
    """
    evidence: list[dict] = []
    candidates: list[dict] = []
    next_verifications: list[str] = []
    candidate_keys: set[tuple[str, str]] = set()

    def add_evidence(
        *,
        kind: str,
        title: str,
        detail: str,
        service: str = "",
        severity: str = "info",
        timestamp: str = "",
        score: float = 0.5,
        source: str = "analysis",
        log_refs: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        item = {
            "id": f"E-{len(evidence) + 1}",
            "kind": kind,
            "title": title,
            "detail": detail,
            "service": service,
            "severity": severity,
            "timestamp": timestamp,
            "score": _clamp_score(score),
            "source": source,
            "log_refs": log_refs or [],
            "metadata": metadata or {},
        }
        evidence.append(item)
        return item

    def add_candidate(
        *,
        title: str,
        service: str,
        reason: str,
        severity: str,
        score: float,
        confidence: float,
        evidence_refs: list[str],
        verifications: list[str],
    ) -> None:
        key = (service.lower(), title.lower())
        if key in candidate_keys:
            return
        candidate_keys.add(key)
        candidates.append({
            "id": f"C-{len(candidates) + 1}",
            "title": title[:120],
            "service": service,
            "reason": reason[:500],
            "severity": severity,
            "score": score,
            "confidence": _clamp_score(confidence),
            "evidence_refs": evidence_refs[:10],
            "next_verifications": verifications[:5],
        })

    for result in results:
        structured = parse_json_object(_get(result, "structured_data", "{}"))
        refs = [str(ref)[:200] for ref in parse_json_list(_get(result, "source_log_refs", "[]")) if ref][:20]
        severity = _as_text(_get(result, "severity", "info"), "info")
        confidence = float(_get(result, "confidence_score", 0.5) or 0.5)
        content = _as_text(_get(result, "content", ""))
        result_type = _as_text(_get(result, "result_type", "finding"), "finding")
        timestamp = _created_at_iso(result)

        current_evidence: list[dict] = []

        if refs:
            current_evidence.append(add_evidence(
                kind="log_sample",
                title="原始日志引用",
                detail=f"关联 {len(refs)} 条原始日志，可用于回看结论证据。",
                severity=severity,
                timestamp=timestamp,
                score=0.7,
                source="source_log_refs",
                log_refs=refs[:5],
                metadata={"total_refs": len(refs)},
            ))

        change_points = _as_list(
            structured.get("change_points") or structured.get("change_point_evidence")
        )
        for cp in change_points[:3]:
            if not isinstance(cp, dict):
                continue
            z_score = float(cp.get("z_score") or 0)
            current_evidence.append(add_evidence(
                kind="change_point",
                title="错误率变点",
                detail=(
                    f"错误率从 {cp.get('before_rate', '未知')}/min "
                    f"到 {cp.get('after_rate', '未知')}/min，"
                    f"z-score={cp.get('z_score', '未知')}。"
                ),
                severity=severity,
                timestamp=_as_text(cp.get("timestamp"), timestamp),
                score=min(0.95, 0.45 + z_score / 10),
                source="change_point",
                metadata=cp,
            ))

        correlated_errors = _as_list(
            structured.get("correlated_errors") or structured.get("cross_service_errors")
        )
        for ce in correlated_errors[:5]:
            if not isinstance(ce, dict):
                continue
            service = _as_text(ce.get("service_name") or ce.get("service") or ce.get("service_id"))
            direction = _as_text(ce.get("direction"), "related")
            direction_label = "上游" if direction == "upstream" else "下游" if direction == "downstream" else "关联"
            count = ce.get("error_count", ce.get("count", "未知"))
            samples = _as_list(ce.get("error_samples"))
            sample = _as_text(samples[0]) if samples else ""
            current_evidence.append(add_evidence(
                kind="cross_service",
                title=f"{direction_label}服务异常",
                detail=f"{service or '关联服务'} 在同窗口出现 {count} 条错误。{sample[:180]}",
                service=service,
                severity=severity,
                timestamp=timestamp,
                score=0.78 if direction == "upstream" else 0.65,
                source="cross_service",
                metadata=ce,
            ))

        if structured.get("historical_task_id") or structured.get("dedup_source"):
            similarity = float(structured.get("similarity_score") or confidence)
            historical_task = _as_text(structured.get("historical_task_id"))
            current_evidence.append(add_evidence(
                kind="history_match",
                title="历史相似故障",
                detail=(
                    f"命中历史分析 {historical_task[:8] or '未知'}，"
                    f"相似度 {similarity:.2f}。"
                ),
                severity=severity,
                timestamp=timestamp,
                score=similarity,
                source="semantic_dedup",
                metadata={
                    "historical_task_id": historical_task,
                    "feedback_quality": structured.get("feedback_quality"),
                    "status": structured.get("status"),
                },
            ))

        knowledge_sources = [
            _as_text(source)
            for source in _as_list(structured.get("knowledge_sources"))
            if _as_text(source)
        ]
        if knowledge_sources:
            current_evidence.append(add_evidence(
                kind="knowledge_match",
                title="知识库参考",
                detail=f"分析时已参考：{'、'.join(knowledge_sources[:4])}。",
                severity="info",
                timestamp=timestamp,
                score=0.6,
                source="knowledge_retrieval",
                metadata={"sources": knowledge_sources[:10]},
            ))

        if structured.get("is_regression"):
            current_evidence.append(add_evidence(
                kind="regression",
                title="已解决问题回归",
                detail="该错误模式曾标记为已解决，本次再次出现，需优先核对修复是否失效。",
                severity="critical",
                timestamp=timestamp,
                score=0.9,
                source="known_issue",
                metadata={"resolved_at": structured.get("resolved_at")},
            ))

        if (
            content
            and not current_evidence
            and result_type in {"root_cause", "anomaly", "summary"}
        ):
            current_evidence.append(add_evidence(
                kind="ai_finding",
                title="AI 分析发现",
                detail=content[:220],
                severity=severity,
                timestamp=timestamp,
                score=confidence,
                source="analysis_result",
            ))

        explicit_verifications = [
            _as_text(step)
            for step in (
                _as_list(structured.get("next_verifications"))
                or _as_list(structured.get("verification_steps"))
            )
            if _as_text(step)
        ]
        for step in explicit_verifications:
            _append_once(next_verifications, step)

        root_cause = _as_text(
            structured.get("root_cause")
            or structured.get("probable_root_cause")
            or structured.get("cause")
        )
        upstream_service = _as_text(structured.get("upstream_service"))
        service = _as_text(
            upstream_service
            or structured.get("root_service")
            or structured.get("service")
            or structured.get("service_name")
            or structured.get("affected_service")
        )
        evidence_refs = [item["id"] for item in current_evidence]
        has_change = any(item["kind"] == "change_point" for item in current_evidence)
        has_correlation = any(item["kind"] == "cross_service" for item in current_evidence)
        has_history = any(item["kind"] == "history_match" for item in current_evidence)
        has_refs = any(item["kind"] == "log_sample" for item in current_evidence)

        candidate_score = _candidate_score(
            result,
            structured,
            len(evidence_refs),
            has_upstream=bool(upstream_service),
            has_change_point=has_change,
            has_correlation=has_correlation,
            has_history=has_history,
            has_log_refs=has_refs,
        )

        candidate_verifications = list(explicit_verifications)
        if upstream_service:
            _append_once(candidate_verifications, f"检查 {upstream_service} 在异常前后的错误率和资源状态")
            if not explicit_verifications:
                _append_once(next_verifications, candidate_verifications[-1])
        if has_change:
            _append_once(candidate_verifications, "回看变点前后 5-10 分钟日志，并核对发布或配置变更")
            if not explicit_verifications:
                _append_once(next_verifications, candidate_verifications[-1])
        if has_refs:
            _append_once(candidate_verifications, "打开原始日志引用，确认关键错误栈和请求上下文")
            if not explicit_verifications:
                _append_once(next_verifications, candidate_verifications[-1])

        explicit_candidates = _as_list(structured.get("root_cause_candidates"))
        for item in explicit_candidates:
            if isinstance(item, dict):
                item_service = _as_text(item.get("service") or service)
                title = _as_text(item.get("title") or item.get("name") or item.get("root_cause") or root_cause or content[:80])
                reason = _as_text(item.get("reason") or item.get("detail") or root_cause or content)
                score = float(item.get("score") or candidate_score)
                add_candidate(
                    title=title,
                    service=item_service,
                    reason=reason,
                    severity=severity,
                    score=_clamp_score(score),
                    confidence=confidence,
                    evidence_refs=evidence_refs,
                    verifications=candidate_verifications,
                )
            elif isinstance(item, str):
                add_candidate(
                    title=item,
                    service=service,
                    reason=root_cause or content,
                    severity=severity,
                    score=candidate_score,
                    confidence=confidence,
                    evidence_refs=evidence_refs,
                    verifications=candidate_verifications,
                )

        if root_cause or result_type == "root_cause":
            title = f"{service} 异常" if service else (root_cause or "根因候选")
            add_candidate(
                title=title,
                service=service,
                reason=root_cause or content,
                severity=severity,
                score=candidate_score,
                confidence=confidence,
                evidence_refs=evidence_refs,
                verifications=candidate_verifications,
            )
        elif result_type == "anomaly" and severity in {"critical", "warning"}:
            add_candidate(
                title=service or "异常模式候选",
                service=service,
                reason=content,
                severity=severity,
                score=candidate_score,
                confidence=confidence,
                evidence_refs=evidence_refs,
                verifications=candidate_verifications,
            )

    candidates.sort(key=lambda c: (c["score"], len(c["evidence_refs"])), reverse=True)
    for index, candidate in enumerate(candidates, 1):
        candidate["id"] = f"C-{index}"

    return {
        "evidence": evidence,
        "candidates": candidates,
        "next_verifications": next_verifications[:8],
    }
