"""
Daily Standup — API Router

Endpoints for generating and retrieving AI-powered daily standup summaries.
"""

from datetime import datetime, timezone, timedelta

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

    target = None
    if date:
        try:
            target = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            target = None

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

    target = None
    if date:
        try:
            target = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            target = None

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
    from logmind.domain.dashboard.standup_generator import generate_standup_report
    from logmind.domain.alert.channels.webhook import send_webhook
    from logmind.core.database import get_session
    from logmind.domain.tenant.models import BusinessLine
    from sqlalchemy import select

    target = None
    if date:
        try:
            target = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    result = await generate_standup_report(user.tenant_id, target)

    # Collect webhook URLs from business lines
    async with get_session() as session:
        stmt = select(BusinessLine.webhook_url).where(
            BusinessLine.tenant_id == user.tenant_id,
            BusinessLine.is_active == True,  # noqa: E712
            BusinessLine.webhook_url != None,  # noqa: E711
            BusinessLine.webhook_url != "",
        ).distinct()
        rows = (await session.execute(stmt)).scalars().all()

    sent_count = 0
    summary_text = result["ai_summary"]
    title = f"📋 LogMind 每日站会 — {result['date']}"

    for url in set(rows):
        try:
            # Try DingTalk markdown format first, fallback to text
            body = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": f"# {title}\n\n{summary_text}",
                },
            }
            await send_webhook(url, body)
            sent_count += 1
        except Exception as e:
            logger.warning("standup_share_failed", url=url[:30], error=str(e))

    return {"ok": True, "sent_count": sent_count, "date": result["date"]}
