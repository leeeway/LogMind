"""
Analysis Comparison — Diff Two Analysis Tasks

Compares two analysis tasks (or time windows) to identify:
  - new_errors: errors in B but not in A
  - resolved_errors: errors in A but not in B
  - worsened: severity upgraded or frequency increased
  - improved: severity downgraded or frequency decreased

Uses AnalysisResult records for structured comparison.
Error matching is based on normalized error signatures
(first 80 chars of content after stripping timestamps/IDs).

Thread Safety:
  Stateless — all data loaded per request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


# ── Result Types ─────────────────────────────────────────

@dataclass
class ErrorEntry:
    """Normalized representation of an analysis result for comparison."""

    result_type: str
    severity: str
    content: str
    confidence: float
    signature: str  # Normalized key for matching

    def to_dict(self) -> dict:
        return {
            "result_type": self.result_type,
            "severity": self.severity,
            "content": self.content[:500],
            "confidence": self.confidence,
        }


@dataclass
class ComparisonResult:
    """Result of comparing two analysis tasks."""

    task_a_id: str
    task_b_id: str
    task_a_time: str
    task_b_time: str
    new_errors: list[dict] = field(default_factory=list)
    resolved_errors: list[dict] = field(default_factory=list)
    worsened: list[dict] = field(default_factory=list)
    improved: list[dict] = field(default_factory=list)
    unchanged: int = 0
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "task_a_id": self.task_a_id,
            "task_b_id": self.task_b_id,
            "task_a_time": self.task_a_time,
            "task_b_time": self.task_b_time,
            "new_errors": self.new_errors,
            "resolved_errors": self.resolved_errors,
            "worsened": self.worsened,
            "improved": self.improved,
            "unchanged": self.unchanged,
            "summary": self.summary,
        }


# ── Normalization ────────────────────────────────────────

# Patterns to strip for normalization (timestamps, UUIDs, hex IDs, line numbers)
_STRIP_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[\.\d]*Z?"),  # timestamps
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I),  # UUIDs
    re.compile(r"0x[0-9a-fA-F]+"),  # hex addresses
    re.compile(r":\d+"),  # port/line numbers
    re.compile(r"\b\d{5,}\b"),  # long numbers (IDs, counts)
]

_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def normalize_signature(content: str) -> str:
    """
    Create a stable signature from analysis content for matching.

    Strips volatile data (timestamps, UUIDs, numbers) so that
    "same error at different times" matches as the same error.
    """
    text = content.strip()
    for pattern in _STRIP_PATTERNS:
        text = pattern.sub("", text)
    # Collapse whitespace and truncate
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120].lower()


def _entries_from_results(results: list[dict]) -> list[ErrorEntry]:
    """Convert raw analysis result dicts to normalized ErrorEntry list."""
    entries = []
    for r in results:
        content = r.get("content", "")
        entries.append(ErrorEntry(
            result_type=r.get("result_type", "anomaly"),
            severity=r.get("severity", "info"),
            content=content,
            confidence=r.get("confidence_score", 0.5),
            signature=normalize_signature(content),
        ))
    return entries


# ── Core Comparison Logic ────────────────────────────────

def compare_analyses(
    results_a: list[dict],
    results_b: list[dict],
    *,
    task_a_id: str = "",
    task_b_id: str = "",
    task_a_time: str = "",
    task_b_time: str = "",
) -> ComparisonResult:
    """
    Compare two sets of analysis results and produce a structured diff.

    Args:
        results_a: Analysis results from the earlier (baseline) task.
        results_b: Analysis results from the later (current) task.
        task_a_id: ID of baseline task.
        task_b_id: ID of current task.
        task_a_time: Human-readable time of task A.
        task_b_time: Human-readable time of task B.

    Returns:
        ComparisonResult with new/resolved/worsened/improved entries.
    """
    entries_a = _entries_from_results(results_a)
    entries_b = _entries_from_results(results_b)

    # Build signature → entry maps
    map_a: dict[str, ErrorEntry] = {}
    for e in entries_a:
        if e.signature:  # skip empty
            map_a[e.signature] = e

    map_b: dict[str, ErrorEntry] = {}
    for e in entries_b:
        if e.signature:
            map_b[e.signature] = e

    sigs_a = set(map_a.keys())
    sigs_b = set(map_b.keys())

    # New errors: in B but not in A
    new_sigs = sigs_b - sigs_a
    new_errors = [map_b[s].to_dict() for s in new_sigs]

    # Resolved errors: in A but not in B
    resolved_sigs = sigs_a - sigs_b
    resolved_errors = [map_a[s].to_dict() for s in resolved_sigs]

    # Common: check for severity/confidence changes
    common_sigs = sigs_a & sigs_b
    worsened = []
    improved = []
    unchanged = 0

    for sig in common_sigs:
        ea = map_a[sig]
        eb = map_b[sig]
        rank_a = _SEVERITY_RANK.get(ea.severity, 0)
        rank_b = _SEVERITY_RANK.get(eb.severity, 0)

        if rank_b > rank_a:
            worsened.append({
                **eb.to_dict(),
                "previous_severity": ea.severity,
                "change": "severity_upgrade",
            })
        elif rank_b < rank_a:
            improved.append({
                **eb.to_dict(),
                "previous_severity": ea.severity,
                "change": "severity_downgrade",
            })
        elif eb.confidence > ea.confidence + 0.15:
            worsened.append({
                **eb.to_dict(),
                "previous_confidence": ea.confidence,
                "change": "confidence_increase",
            })
        elif eb.confidence < ea.confidence - 0.15:
            improved.append({
                **eb.to_dict(),
                "previous_confidence": ea.confidence,
                "change": "confidence_decrease",
            })
        else:
            unchanged += 1

    # Build summary
    parts = []
    if new_errors:
        parts.append(f"{len(new_errors)} 个新增错误模式")
    if resolved_errors:
        parts.append(f"{len(resolved_errors)} 个已修复问题")
    if worsened:
        parts.append(f"{len(worsened)} 个恶化项")
    if improved:
        parts.append(f"{len(improved)} 个改善项")
    if unchanged:
        parts.append(f"{unchanged} 个未变化")
    summary = "，".join(parts) if parts else "两次分析结果完全一致"

    return ComparisonResult(
        task_a_id=task_a_id,
        task_b_id=task_b_id,
        task_a_time=task_a_time,
        task_b_time=task_b_time,
        new_errors=new_errors,
        resolved_errors=resolved_errors,
        worsened=worsened,
        improved=improved,
        unchanged=unchanged,
        summary=summary,
    )
