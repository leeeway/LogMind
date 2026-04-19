"""
Analysis Domain — API Router
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.domain.analysis.models import LogAnalysisTask
from logmind.domain.analysis.schemas import (
    AnalysisTaskCreate,
    AnalysisTaskResponse,
    AnalysisTaskSummary,
    StageMetric,
    TaskTraceResponse,
    ToolCallRecord,
)
from logmind.domain.analysis.tasks import run_analysis_task
from logmind.shared.base_repository import BaseRepository
from logmind.shared.base_schema import MessageResponse, PaginatedResponse
from logmind.shared.pagination import PaginationParams, get_pagination

router = APIRouter(prefix="/analysis", tags=["Analysis"])
task_repo = BaseRepository(LogAnalysisTask)


@router.post("/tasks", response_model=AnalysisTaskResponse, status_code=201)
async def create_analysis_task(
    req: AnalysisTaskCreate, session: DBSession, user: CurrentUser
):
    """
    Create and trigger a manual log analysis task.
    Only analyzes ERROR/CRITICAL severity by default to control AI costs.
    """
    query_params = {
        "query": req.query,
        "severity": req.severity,
        "extra_filters": req.extra_filters,
    }

    task = LogAnalysisTask(
        tenant_id=user.tenant_id,
        business_line_id=req.business_line_id,
        provider_config_id=req.provider_config_id,
        prompt_template_id=req.prompt_template_id,
        task_type="manual",
        status="pending",
        query_params=json.dumps(query_params),
        time_from=req.time_from,
        time_to=req.time_to,
    )
    task = await task_repo.create(session, task)
    await session.commit()

    # Dispatch to Celery worker
    run_analysis_task.delay(task.id)

    return AnalysisTaskResponse.model_validate(task)


@router.get("/tasks", response_model=PaginatedResponse)
async def list_analysis_tasks(
    session: DBSession,
    user: CurrentUser,
    business_line_id: str | None = None,
    task_status: str | None = None,
    pagination: PaginationParams = Depends(get_pagination),
):
    """List analysis tasks for current tenant."""
    filters = {}
    if business_line_id:
        filters["business_line_id"] = business_line_id
    if task_status:
        filters["status"] = task_status

    items = await task_repo.get_all(
        session,
        tenant_id=user.tenant_id,
        offset=pagination.offset,
        limit=pagination.limit,
        filters=filters,
    )
    total = await task_repo.count(
        session, tenant_id=user.tenant_id, filters=filters
    )
    return PaginatedResponse.create(
        items=[AnalysisTaskSummary.model_validate(t) for t in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/tasks/{task_id}", response_model=AnalysisTaskResponse)
async def get_analysis_task(task_id: str, session: DBSession, user: CurrentUser):
    """Get a specific analysis task with its results."""
    task = await task_repo.get_by_id(session, task_id, tenant_id=user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return AnalysisTaskResponse.model_validate(task)


@router.get("/tasks/{task_id}/trace", response_model=TaskTraceResponse)
async def get_task_trace(task_id: str, session: DBSession, user: CurrentUser):
    """
    Get the full execution trace for an analysis task.

    Returns:
      - Per-stage timing metrics (duration_ms, status, error)
      - Agent tool call chain (tool_name, arguments, result_preview, duration_ms)

    Useful for:
      - Debugging why a task was slow or failed
      - Understanding what tools the Agent used and in what order
      - Performance monitoring and optimization
    """
    task = await task_repo.get_by_id(session, task_id, tenant_id=user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Parse stage_metrics from JSON
    try:
        stages_raw = json.loads(task.stage_metrics or "[]")
    except (json.JSONDecodeError, TypeError):
        stages_raw = []

    stages = [StageMetric(**s) for s in stages_raw if isinstance(s, dict)]
    total_duration = sum(s.duration_ms for s in stages)

    # Build tool call records from relationship
    tool_call_items = [
        ToolCallRecord(
            id=tc.id,
            step=tc.step,
            tool_name=tc.tool_name,
            arguments=tc.arguments or "{}",
            result_preview=tc.result_preview or "",
            result_length=tc.result_length or 0,
            duration_ms=tc.duration_ms or 0,
            success=tc.success,
            created_at=tc.created_at,
        )
        for tc in sorted(task.tool_calls, key=lambda x: (x.step, x.created_at))
    ]

    # Extract errors from error_message
    errors = []
    if task.error_message:
        errors = [e.strip() for e in task.error_message.split(";") if e.strip()]

    return TaskTraceResponse(
        task_id=task.id,
        status=task.status,
        total_duration_ms=total_duration,
        stages=stages,
        tool_calls=tool_call_items,
        errors=errors,
    )


@router.put("/results/{result_id}/feedback", response_model=MessageResponse)
async def submit_result_feedback(
    result_id: str,
    session: DBSession,
    user: CurrentUser,
    score: int = 1,
    comment: str | None = None,
):
    """
    Submit feedback on an analysis result for self-learning.

    - score=1: This analysis was helpful/accurate ✅
      → Vector library: mark as "verified", extend TTL to 365 days
    - score=-1: This analysis was inaccurate/unhelpful ❌
      → Vector library: mark as "poor", excluded from future KNN matches

    Feedback closes the self-learning loop:
    good conclusions persist longer, bad conclusions stop propagating.
    """
    import json

    from logmind.domain.analysis.models import AnalysisResult

    result = await session.get(AnalysisResult, result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Analysis result not found")

    # Verify the result belongs to the user's tenant
    task = await task_repo.get_by_id(session, result.task_id, tenant_id=user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Analysis result not found")

    if score not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="Score must be -1, 0, or 1")

    result.feedback_score = score
    result.feedback_comment = comment
    await session.flush()

    # ── Propagate feedback to ES vector library ──────────
    # Find the linked vector entry via structured_data
    feedback_result = f"Feedback recorded: score={score}"
    try:
        structured = json.loads(result.structured_data or "{}")
        historical_task_id = structured.get("historical_task_id") or task.id

        if historical_task_id:
            from logmind.domain.log.service import log_service

            if score == 1:
                # Positive: mark as verified, extend TTL to 365 days
                await _update_vector_feedback(
                    historical_task_id, "verified"
                )
                feedback_result += " | Vector marked as verified (TTL=365d)"
            elif score == -1:
                # Negative: mark as poor, excluded from future matches
                await _update_vector_feedback(
                    historical_task_id, "poor"
                )
                feedback_result += " | Vector marked as poor (excluded from future matches)"
    except Exception as e:
        # Non-critical: DB feedback is saved even if vector update fails
        feedback_result += f" | Vector update failed: {e}"

    return MessageResponse(message=feedback_result)


async def _update_vector_feedback(task_id: str, quality: str):
    """
    Update feedback_quality in the analysis vector index.

    Searches for the vector entry by task_id and updates its quality.
    """
    from logmind.domain.log.service import log_service

    index_name = "logmind-analysis-vectors"
    try:
        es = log_service.es
        # Find the vector entry by task_id
        resp = await es.search(
            index=index_name,
            query={"term": {"task_id": task_id}},
            source=False,
            size=1,
        )
        hits = resp.get("hits", {}).get("hits", [])
        if hits:
            doc_id = hits[0]["_id"]
            # Update feedback_quality only; status is managed via Known Issues API
            await log_service.update_analysis_vector_status(
                doc_id=doc_id,
                status=None,  # Don't change status through feedback
                feedback_quality=quality,
            )
    except Exception:
        pass  # Best-effort

