"""
Daily Standup — API Router

Endpoints for generating and retrieving AI-powered daily standup summaries.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from logmind.core.dependencies import CurrentUser
from logmind.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class StandupResponse(BaseModel):
    date: str
    data: dict
    ai_summary: str
    generated_at: str


@router.get("/standup", response_model=StandupResponse)
async def get_standup(
    user: CurrentUser,
    date: str | None = Query(None, description="YYYY-MM-DD, default yesterday"),
):
    """
    Get daily standup summary for a given date.
    Defaults to yesterday if no date provided.
    """
    from logmind.domain.dashboard.standup_generator import generate_standup_report
    from logmind.domain.dashboard.standup_service import parse_standup_date

    target = parse_standup_date(date)
    result = await generate_standup_report(user.tenant_id, target)
    return StandupResponse(**result)


@router.post("/standup/generate", response_model=StandupResponse)
async def generate_standup(
    user: CurrentUser,
    date: str | None = Query(None, description="YYYY-MM-DD, default yesterday"),
):
    """
    Force-generate a fresh standup summary (bypass cache).
    """
    from logmind.domain.dashboard.standup_generator import generate_standup_report
    from logmind.domain.dashboard.standup_service import parse_standup_date

    target = parse_standup_date(date)
    result = await generate_standup_report(user.tenant_id, target)
    logger.info("standup_generated", date=result["date"], tenant_id=user.tenant_id)
    return StandupResponse(**result)


@router.post("/standup/share")
async def share_standup(
    user: CurrentUser,
    date: str | None = Query(None),
    channel: str = Query("webhook", description="webhook"),
):
    """
    Share standup summary to configured notification channels.
    """
    from logmind.domain.dashboard.standup_service import parse_standup_date, share_standup_for_tenant

    target = parse_standup_date(date)
    return await share_standup_for_tenant(user.tenant_id, target_date=target)
