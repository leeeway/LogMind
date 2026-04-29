"""
Error DNA — Pattern fingerprinting, clustering & mutation detection

Extracts error signatures from AnalysisResult content, clusters by similarity,
tracks pattern lifecycle, and detects mutations (new variants of known patterns).
"""

import re
import hashlib
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fastapi import APIRouter, Query
from pydantic import BaseModel

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult

logger = get_logger(__name__)

router = APIRouter(prefix="/error-dna", tags=["ErrorDNA"])


# ── Signature extraction ─────────────────────────────────

# Java: java.lang.NullPointerException at com.xxx.Service.method(Service.java:42)
_JAVA_EX = re.compile(
    r"([\w$.]+(?:Exception|Error|Throwable))"
    r"(?:.*?at\s+([\w$.]+\.\w+)\(([^)]+)\))?"
)
# C#: System.NullReferenceException at Namespace.Class.Method() in File.cs:line 42
_CSHARP_EX = re.compile(
    r"([\w.]+(?:Exception|Error))"
    r"(?:.*?at\s+([\w.]+\.[\w<>]+)\(\))?"
)


def _extract_signature(content: str) -> tuple[str, str]:
    """Extract error DNA signature from analysis content.
    Returns (exception_class, top_frame) tuple."""
    # Try Java first
    m = _JAVA_EX.search(content)
    if m:
        return m.group(1), m.group(2) or ""
    # Try C#
    m = _CSHARP_EX.search(content)
    if m:
        return m.group(1), m.group(2) or ""
    # Fallback: first ERROR/FATAL line
    for line in content.split("\n"):
        line_lower = line.lower()
        if "error" in line_lower or "exception" in line_lower or "fatal" in line_lower:
            # Use first 120 chars as signature
            clean = re.sub(r"\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}:\d{2}", "", line)
            clean = re.sub(r"\b\d+\b", "N", clean).strip()[:120]
            return clean, ""
    return content[:80], ""


def _signature_hash(exc_class: str, frame: str) -> str:
    """Generate short hash for a signature."""
    raw = f"{exc_class}|{frame}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _levenshtein_ratio(s1: str, s2: str) -> float:
    """Simple edit distance similarity ratio (0-1). Optimized for short strings."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    # For performance, truncate to 100 chars
    s1, s2 = s1[:100], s2[:100]
    len1, len2 = len(s1), len(s2)

    matrix = list(range(len2 + 1))
    for i in range(len1):
        prev = matrix[:]
        matrix[0] = i + 1
        for j in range(len2):
            cost = 0 if s1[i] == s2[j] else 1
            matrix[j + 1] = min(prev[j + 1] + 1, matrix[j] + 1, prev[j] + cost)

    dist = matrix[len2]
    return 1.0 - dist / max(len1, len2)


# ── Response models ──────────────────────────────────────

class ErrorPattern(BaseModel):
    pattern_id: str
    signature: str  # exception class + frame
    representative_error: str  # shortened content
    occurrence_count: int
    first_seen: str
    last_seen: str
    affected_services: list[str]
    severity: str  # most common severity
    trend: str  # rising / falling / stable


class PatternTimeline(BaseModel):
    pattern_id: str
    signature: str
    daily_counts: list[dict]  # [{date, count}]
    service_distribution: list[dict]  # [{service, count}]
    total_occurrences: int


class Mutation(BaseModel):
    original_pattern_id: str
    original_signature: str
    mutated_signature: str
    similarity: float
    first_seen: str
    occurrence_count: int
    sample_content: str


class ErrorDNAResponse(BaseModel):
    patterns: list[ErrorPattern]
    total_patterns: int
    total_occurrences: int


class MutationResponse(BaseModel):
    mutations: list[Mutation]
    total: int


# ── Endpoints ────────────────────────────────────────────

@router.get("/patterns", response_model=ErrorDNAResponse)
async def get_error_patterns(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(7, ge=1, le=30),
    min_count: int = Query(2, ge=1),
):
    """Get clustered error patterns with lifecycle data."""
    from sqlalchemy import select

    since = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            AnalysisResult.content,
            AnalysisResult.severity,
            AnalysisResult.created_at,
            LogAnalysisTask.business_line_id,
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            AnalysisResult.severity.in_(["critical", "warning"]),
        )
        .order_by(AnalysisResult.created_at)
    )
    rows = (await session.execute(stmt)).all()

    # Extract signatures and cluster
    clusters: dict[str, dict] = {}  # hash -> cluster_data
    biz_names_cache: dict[str, str] = {}

    for content, severity, created_at, biz_id in rows:
        exc_class, frame = _extract_signature(content)
        sig_hash = _signature_hash(exc_class, frame)

        if sig_hash not in clusters:
            clusters[sig_hash] = {
                "signature": f"{exc_class}" + (f" @ {frame}" if frame else ""),
                "representative": content[:200],
                "count": 0,
                "first_seen": created_at,
                "last_seen": created_at,
                "services": set(),
                "severities": [],
                "daily": defaultdict(int),
            }

        c = clusters[sig_hash]
        c["count"] += 1
        c["last_seen"] = max(c["last_seen"], created_at)
        c["services"].add(biz_id or "unknown")
        c["severities"].append(severity)
        day_key = created_at.strftime("%Y-%m-%d")
        c["daily"][day_key] += 1

    # Resolve business line names
    from logmind.domain.tenant.models import BusinessLine

    all_biz_ids = set()
    for c in clusters.values():
        all_biz_ids.update(c["services"])
    all_biz_ids.discard("unknown")

    if all_biz_ids:
        biz_stmt = select(BusinessLine.id, BusinessLine.name).where(
            BusinessLine.id.in_(list(all_biz_ids))
        )
        biz_rows = (await session.execute(biz_stmt)).all()
        biz_names_cache = {r.id: r.name for r in biz_rows}

    # Build response
    patterns = []
    for sig_hash, c in clusters.items():
        if c["count"] < min_count:
            continue

        # Trend: compare last 2 days vs first 2 days
        daily_sorted = sorted(c["daily"].items())
        if len(daily_sorted) >= 2:
            first_half = sum(v for _, v in daily_sorted[:len(daily_sorted)//2])
            second_half = sum(v for _, v in daily_sorted[len(daily_sorted)//2:])
            if second_half > first_half * 1.3:
                trend = "rising"
            elif second_half < first_half * 0.7:
                trend = "falling"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Most common severity
        sev_count = defaultdict(int)
        for s in c["severities"]:
            sev_count[s] += 1
        top_severity = max(sev_count, key=sev_count.get)

        service_names = [biz_names_cache.get(bid, bid) for bid in c["services"]]

        patterns.append(ErrorPattern(
            pattern_id=sig_hash,
            signature=c["signature"],
            representative_error=c["representative"],
            occurrence_count=c["count"],
            first_seen=c["first_seen"].isoformat(),
            last_seen=c["last_seen"].isoformat(),
            affected_services=service_names,
            severity=top_severity,
            trend=trend,
        ))

    patterns.sort(key=lambda p: p.occurrence_count, reverse=True)

    return ErrorDNAResponse(
        patterns=patterns[:50],
        total_patterns=len(patterns),
        total_occurrences=sum(p.occurrence_count for p in patterns),
    )


@router.get("/patterns/{pattern_id}/timeline", response_model=PatternTimeline)
async def get_pattern_timeline(
    pattern_id: str,
    session: DBSession,
    user: CurrentUser,
    days: int = Query(14, ge=1, le=30),
):
    """Get daily occurrence timeline for a specific error pattern."""
    from sqlalchemy import select

    since = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            AnalysisResult.content,
            AnalysisResult.created_at,
            LogAnalysisTask.business_line_id,
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            AnalysisResult.severity.in_(["critical", "warning"]),
        )
    )
    rows = (await session.execute(stmt)).all()

    # Filter by pattern_id
    daily: dict[str, int] = defaultdict(int)
    svc_dist: dict[str, int] = defaultdict(int)
    matched_sig = ""
    total = 0

    for content, created_at, biz_id in rows:
        exc_class, frame = _extract_signature(content)
        sig_hash = _signature_hash(exc_class, frame)
        if sig_hash != pattern_id:
            continue

        if not matched_sig:
            matched_sig = f"{exc_class}" + (f" @ {frame}" if frame else "")

        total += 1
        daily[created_at.strftime("%Y-%m-%d")] += 1
        svc_dist[biz_id or "unknown"] += 1

    # Resolve service names
    from logmind.domain.tenant.models import BusinessLine

    biz_ids = [k for k in svc_dist if k != "unknown"]
    biz_names = {}
    if biz_ids:
        biz_rows = (await session.execute(
            select(BusinessLine.id, BusinessLine.name).where(BusinessLine.id.in_(biz_ids))
        )).all()
        biz_names = {r.id: r.name for r in biz_rows}

    return PatternTimeline(
        pattern_id=pattern_id,
        signature=matched_sig,
        daily_counts=[{"date": d, "count": c} for d, c in sorted(daily.items())],
        service_distribution=[
            {"service": biz_names.get(s, s), "count": c}
            for s, c in sorted(svc_dist.items(), key=lambda x: -x[1])
        ],
        total_occurrences=total,
    )


@router.get("/mutations", response_model=MutationResponse)
async def get_mutations(
    session: DBSession,
    user: CurrentUser,
    similarity_threshold: float = Query(0.6, ge=0.3, le=0.95),
):
    """Detect error mutations — new variants of known patterns in last 24h."""
    from sqlalchemy import select

    now = datetime.now(timezone.utc)
    recent_since = now - timedelta(hours=24)
    historical_since = now - timedelta(days=7)

    # Historical patterns (2-7 days ago)
    hist_stmt = (
        select(AnalysisResult.content, AnalysisResult.created_at)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= historical_since,
            LogAnalysisTask.created_at < recent_since,
            AnalysisResult.severity.in_(["critical", "warning"]),
        )
    )
    hist_rows = (await session.execute(hist_stmt)).all()

    known_sigs: dict[str, tuple[str, str]] = {}  # hash -> (exc_class, frame)
    for content, _ in hist_rows:
        exc, frame = _extract_signature(content)
        h = _signature_hash(exc, frame)
        if h not in known_sigs:
            known_sigs[h] = (exc, frame)

    # Recent patterns (last 24h)
    recent_stmt = (
        select(AnalysisResult.content, AnalysisResult.created_at)
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= recent_since,
            AnalysisResult.severity.in_(["critical", "warning"]),
        )
    )
    recent_rows = (await session.execute(recent_stmt)).all()

    recent_sigs: dict[str, dict] = {}
    for content, created_at in recent_rows:
        exc, frame = _extract_signature(content)
        h = _signature_hash(exc, frame)
        if h in known_sigs:
            continue  # Exact match, not a mutation
        if h not in recent_sigs:
            recent_sigs[h] = {
                "exc": exc, "frame": frame,
                "first_seen": created_at, "count": 0, "sample": content[:200],
            }
        recent_sigs[h]["count"] += 1

    # Find mutations: new patterns similar to known ones
    mutations = []
    for new_hash, new_data in recent_sigs.items():
        new_sig = f"{new_data['exc']}|{new_data['frame']}"
        best_match = None
        best_sim = 0.0

        for known_hash, (k_exc, k_frame) in known_sigs.items():
            known_sig = f"{k_exc}|{k_frame}"
            sim = _levenshtein_ratio(new_sig, known_sig)
            if sim > best_sim and sim >= similarity_threshold:
                best_sim = sim
                best_match = (known_hash, k_exc, k_frame)

        if best_match:
            mutations.append(Mutation(
                original_pattern_id=best_match[0],
                original_signature=f"{best_match[1]}" + (f" @ {best_match[2]}" if best_match[2] else ""),
                mutated_signature=f"{new_data['exc']}" + (f" @ {new_data['frame']}" if new_data['frame'] else ""),
                similarity=round(best_sim, 2),
                first_seen=new_data["first_seen"].isoformat(),
                occurrence_count=new_data["count"],
                sample_content=new_data["sample"],
            ))

    mutations.sort(key=lambda m: m.occurrence_count, reverse=True)

    return MutationResponse(mutations=mutations[:20], total=len(mutations))
