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
    - score=-1: This analysis was inaccurate/unhelpful ❌

    Feedback is used to improve future analysis quality:
    - Positive feedback reinforces the analysis memory
    - Negative feedback flags the historical conclusion for review
    """
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

    return MessageResponse(message=f"Feedback recorded: score={score}")

