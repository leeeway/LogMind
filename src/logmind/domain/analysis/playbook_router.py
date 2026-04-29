"""
Diagnostic Playbook — Auto-generated resolution procedures from history

Mines historical AnalysisResult content and alert resolution patterns
to generate step-by-step diagnostic playbooks for common error patterns.
"""

import re
import hashlib
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.alert.models import AlertHistory

logger = get_logger(__name__)

router = APIRouter(prefix="/playbooks", tags=["Playbooks"])


# ── Signature extraction (reuse from error_dna) ─────────

_JAVA_EX = re.compile(r"([\w$.]+(?:Exception|Error|Throwable))")
_CSHARP_EX = re.compile(r"([\w.]+(?:Exception|Error))")


def _extract_error_class(content: str) -> str:
    m = _JAVA_EX.search(content)
    if m:
        return m.group(1)
    m = _CSHARP_EX.search(content)
    if m:
        return m.group(1)
    for line in content.split("\n"):
        if "error" in line.lower() or "exception" in line.lower():
            clean = re.sub(r"\d+", "N", line.strip()[:80])
            return clean
    return content[:60]


def _extract_steps(content: str) -> list[str]:
    """Extract diagnostic/resolution steps from AI analysis content."""
    steps = []
    # Look for numbered lists, bullet points, sections like "建议", "原因", "修复"
    patterns = [
        r"(?:建议|修复|解决|处理|排查|操作)[：:]\s*(.+)",
        r"(?:\d+[.)、])\s*(.+)",
        r"[-•]\s*(.+)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, content)
        for m in matches:
            step = m.strip()
            if len(step) > 10 and step not in steps:
                steps.append(step[:200])

    # If no structured steps found, extract key sentences
    if not steps:
        sentences = re.split(r'[。\n]', content)
        for s in sentences:
            s = s.strip()
            if len(s) > 15 and any(kw in s for kw in ["建议", "检查", "确认", "修复", "排查", "重启", "回滚"]):
                steps.append(s[:200])

    return steps[:8]  # max 8 steps


def _pattern_hash(error_class: str) -> str:
    return hashlib.md5(error_class.encode()).hexdigest()[:12]


# ── Response Models ──────────────────────────────────────

class PlaybookStep(BaseModel):
    step_number: int
    action: str
    source: str = "AI"      # AI / manual


class PlaybookCase(BaseModel):
    task_id: str
    date: str
    severity: str
    resolution_time_min: float | None
    feedback_score: int | None


class Playbook(BaseModel):
    pattern_id: str
    error_class: str
    step_count: int
    usage_count: int         # how many times this pattern appeared
    success_rate: float      # % of cases with positive feedback
    avg_resolution_min: float
    steps: list[PlaybookStep]
    recent_cases: list[PlaybookCase]


class PlaybookListResponse(BaseModel):
    playbooks: list[Playbook]
    total: int


class PlaybookSuggestion(BaseModel):
    matched_playbook: Playbook | None
    similarity: str          # exact / partial / none
    message: str


# ── Endpoints ────────────────────────────────────────────

@router.get("", response_model=PlaybookListResponse)
async def list_playbooks(
    session: DBSession,
    user: CurrentUser,
    days: int = Query(30, ge=7, le=90),
):
    """List auto-generated diagnostic playbooks from historical resolutions."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Get all analysis results with their tasks
    stmt = (
        select(
            AnalysisResult.content,
            AnalysisResult.severity,
            AnalysisResult.confidence_score,
            AnalysisResult.feedback_score,
            AnalysisResult.task_id,
            AnalysisResult.created_at,
            LogAnalysisTask.business_line_id,
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            LogAnalysisTask.created_at >= since,
            AnalysisResult.severity.in_(["critical", "warning"]),
        )
        .order_by(AnalysisResult.created_at.desc())
    )
    rows = list((await session.execute(stmt)).all())

    # Get resolution times from AlertHistory
    mttr_stmt = (
        select(
            AlertHistory.analysis_task_id,
            func.extract("epoch", AlertHistory.resolved_at).label("resolved_epoch"),
            func.extract("epoch", AlertHistory.fired_at).label("fired_epoch"),
        )
        .select_from(AlertHistory)
        .where(
            AlertHistory.tenant_id == user.tenant_id,
            AlertHistory.fired_at >= since,
            AlertHistory.resolved_at != None,  # noqa: E711
            AlertHistory.analysis_task_id != None,  # noqa: E711
        )
    )
    mttr_map = {}
    for r in (await session.execute(mttr_stmt)).all():
        if r.analysis_task_id and r.resolved_epoch and r.fired_epoch:
            mttr_map[r.analysis_task_id] = (r.resolved_epoch - r.fired_epoch) / 60

    # Cluster by error class
    clusters: dict[str, dict] = {}
    for content, severity, conf, feedback, task_id, created_at, biz_id in rows:
        error_class = _extract_error_class(content)
        ph = _pattern_hash(error_class)

        if ph not in clusters:
            clusters[ph] = {
                "error_class": error_class,
                "steps_pool": [],
                "cases": [],
                "feedbacks": [],
            }

        c = clusters[ph]
        steps = _extract_steps(content)
        c["steps_pool"].extend(steps)

        resolution_min = mttr_map.get(task_id)
        c["cases"].append({
            "task_id": task_id,
            "date": created_at.isoformat() if created_at else "",
            "severity": severity,
            "resolution_time_min": round(resolution_min, 1) if resolution_min else None,
            "feedback_score": feedback,
        })
        if feedback is not None:
            c["feedbacks"].append(feedback)

    # Build playbooks
    playbooks = []
    for ph, c in clusters.items():
        if len(c["cases"]) < 2:
            continue

        # Deduplicate steps by similarity
        unique_steps = []
        seen = set()
        for s in c["steps_pool"]:
            key = s[:40]  # rough dedup key
            if key not in seen:
                seen.add(key)
                unique_steps.append(s)

        if not unique_steps:
            continue

        positive = sum(1 for f in c["feedbacks"] if f and f > 0)
        total_fb = sum(1 for f in c["feedbacks"] if f is not None)
        success_rate = positive / max(total_fb, 1) * 100

        resolution_times = [
            case["resolution_time_min"]
            for case in c["cases"]
            if case["resolution_time_min"] is not None
        ]
        avg_resolution = sum(resolution_times) / len(resolution_times) if resolution_times else 0

        playbooks.append(Playbook(
            pattern_id=ph,
            error_class=c["error_class"],
            step_count=len(unique_steps),
            usage_count=len(c["cases"]),
            success_rate=round(success_rate, 1),
            avg_resolution_min=round(avg_resolution, 1),
            steps=[
                PlaybookStep(step_number=i+1, action=s)
                for i, s in enumerate(unique_steps[:8])
            ],
            recent_cases=[
                PlaybookCase(**case)
                for case in c["cases"][:5]
            ],
        ))

    playbooks.sort(key=lambda p: p.usage_count, reverse=True)

    return PlaybookListResponse(playbooks=playbooks[:30], total=len(playbooks))


@router.get("/suggest", response_model=PlaybookSuggestion)
async def suggest_playbook(
    session: DBSession,
    user: CurrentUser,
    alert_message: str = Query(..., min_length=5),
):
    """Suggest a diagnostic playbook based on alert message."""
    # Extract error class from the alert message
    target_class = _extract_error_class(alert_message)
    target_hash = _pattern_hash(target_class)

    # Get playbooks and find match
    result = await list_playbooks(session, user, days=30)

    # Exact match
    for pb in result.playbooks:
        if pb.pattern_id == target_hash:
            return PlaybookSuggestion(
                matched_playbook=pb,
                similarity="exact",
                message=f"✅ 找到精确匹配的诊断剧本: {pb.error_class} ({pb.usage_count} 次历史处理)",
            )

    # Partial match (same exception class prefix)
    target_prefix = target_class.split(".")[-1][:15] if "." in target_class else target_class[:15]
    for pb in result.playbooks:
        pb_prefix = pb.error_class.split(".")[-1][:15] if "." in pb.error_class else pb.error_class[:15]
        if target_prefix.lower() == pb_prefix.lower():
            return PlaybookSuggestion(
                matched_playbook=pb,
                similarity="partial",
                message=f"🔍 找到相似的诊断剧本: {pb.error_class} (部分匹配)",
            )

    return PlaybookSuggestion(
        matched_playbook=None,
        similarity="none",
        message=f"📭 暂无匹配的诊断剧本。随着更多告警的解决，系统将自动学习和生成新的剧本。",
    )
