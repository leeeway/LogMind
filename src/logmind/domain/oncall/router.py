"""
OnCall Schedule — Smart on-call scheduling & auto-escalation

Features:
  - Schedule management (primary / backup / manager roles)
  - Current on-call lookup by service
  - Escalation policies with timeout-based auto-upgrade
  - Override/swap support
"""

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/oncall", tags=["OnCall"])


# ── In-memory store ──────────────────────────────────────
_schedules: dict[str, list[dict]] = {}  # tenant_id -> [schedule_entries]
_policies: dict[str, list[dict]] = {}   # tenant_id -> [escalation_policies]


class ScheduleEntry(BaseModel):
    id: str = ""
    business_line_id: str = ""
    business_line_name: str = ""
    user_name: str
    user_contact: str = ""  # webhook URL or phone
    role: str = Field(description="primary / backup / manager")
    start_time: str  # ISO
    end_time: str    # ISO
    is_override: bool = False


class ScheduleCreateRequest(BaseModel):
    business_line_id: str
    user_name: str
    user_contact: str = ""
    role: str = "primary"
    start_time: str
    end_time: str


class EscalationPolicy(BaseModel):
    id: str = ""
    business_line_id: str = ""
    business_line_name: str = ""
    levels: list[dict] = Field(default_factory=list)
    # Each level: {level: 1, role: "primary", timeout_minutes: 15, notify_channel: "webhook"}


class EscalationPolicyCreate(BaseModel):
    business_line_id: str
    levels: list[dict]


class CurrentOnCallResponse(BaseModel):
    business_line_id: str
    business_line_name: str
    primary: ScheduleEntry | None = None
    backup: ScheduleEntry | None = None
    manager: ScheduleEntry | None = None


# ── Schedule CRUD ────────────────────────────────────────

@router.get("/schedules")
async def list_schedules(
    session: DBSession,
    user: CurrentUser,
    business_line_id: str | None = None,
    days: int = Query(30, ge=1, le=90),
) -> list[ScheduleEntry]:
    """List on-call schedules."""
    entries = _schedules.get(user.tenant_id, [])
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = []
    for e in entries:
        try:
            end = datetime.fromisoformat(e["end_time"].replace("Z", "+00:00"))
            if end < since:
                continue
        except Exception:
            pass
        if business_line_id and e.get("business_line_id") != business_line_id:
            continue
        result.append(ScheduleEntry(**e))

    result.sort(key=lambda x: x.start_time)
    return result


@router.post("/schedules", response_model=ScheduleEntry)
async def create_schedule(
    req: ScheduleCreateRequest,
    session: DBSession,
    user: CurrentUser,
):
    """Create on-call schedule entry."""
    from logmind.domain.tenant.models import BusinessLine

    # Get business line name
    biz = await session.get(BusinessLine, req.business_line_id)
    biz_name = biz.name if biz else req.business_line_id

    entry = {
        "id": str(uuid.uuid4()),
        "business_line_id": req.business_line_id,
        "business_line_name": biz_name,
        "user_name": req.user_name,
        "user_contact": req.user_contact,
        "role": req.role,
        "start_time": req.start_time,
        "end_time": req.end_time,
        "is_override": False,
    }
    _schedules.setdefault(user.tenant_id, []).append(entry)
    logger.info("oncall_schedule_created", user=req.user_name, role=req.role, biz=biz_name)
    return ScheduleEntry(**entry)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, user: CurrentUser):
    """Delete on-call schedule entry."""
    entries = _schedules.get(user.tenant_id, [])
    _schedules[user.tenant_id] = [e for e in entries if e["id"] != schedule_id]
    return {"ok": True}


@router.post("/override", response_model=ScheduleEntry)
async def create_override(
    req: ScheduleCreateRequest,
    session: DBSession,
    user: CurrentUser,
):
    """Create temporary on-call override (swap)."""
    from logmind.domain.tenant.models import BusinessLine
    biz = await session.get(BusinessLine, req.business_line_id)
    biz_name = biz.name if biz else req.business_line_id

    entry = {
        "id": str(uuid.uuid4()),
        "business_line_id": req.business_line_id,
        "business_line_name": biz_name,
        "user_name": req.user_name,
        "user_contact": req.user_contact,
        "role": req.role,
        "start_time": req.start_time,
        "end_time": req.end_time,
        "is_override": True,
    }
    _schedules.setdefault(user.tenant_id, []).append(entry)
    logger.info("oncall_override_created", user=req.user_name, biz=biz_name)
    return ScheduleEntry(**entry)


# ── Current On-Call ──────────────────────────────────────

@router.get("/current")
async def get_current_oncall(
    session: DBSession,
    user: CurrentUser,
    business_line_id: str | None = None,
) -> list[CurrentOnCallResponse]:
    """Get who is currently on-call for each service."""
    from logmind.domain.tenant.models import BusinessLine
    from sqlalchemy import select

    biz_stmt = select(BusinessLine).where(
        BusinessLine.tenant_id == user.tenant_id,
        BusinessLine.is_active == True,  # noqa: E712
    )
    if business_line_id:
        biz_stmt = biz_stmt.where(BusinessLine.id == business_line_id)
    biz_lines = (await session.execute(biz_stmt)).scalars().all()

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    entries = _schedules.get(user.tenant_id, [])

    result = []
    for biz in biz_lines:
        current = CurrentOnCallResponse(
            business_line_id=biz.id,
            business_line_name=biz.name,
        )
        # Find current on-call for each role (overrides first)
        biz_entries = [e for e in entries if e.get("business_line_id") == biz.id]
        # Sort: overrides first, then by start_time desc
        biz_entries.sort(key=lambda x: (not x.get("is_override", False), x.get("start_time", "")), reverse=True)

        for e in biz_entries:
            try:
                start = datetime.fromisoformat(e["start_time"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(e["end_time"].replace("Z", "+00:00"))
                if start <= now <= end:
                    entry = ScheduleEntry(**e)
                    if e["role"] == "primary" and not current.primary:
                        current.primary = entry
                    elif e["role"] == "backup" and not current.backup:
                        current.backup = entry
                    elif e["role"] == "manager" and not current.manager:
                        current.manager = entry
            except Exception:
                pass

        result.append(current)

    return result


# ── Escalation Policies ──────────────────────────────────

@router.get("/escalation-policies")
async def list_policies(user: CurrentUser) -> list[EscalationPolicy]:
    """List escalation policies."""
    return [EscalationPolicy(**p) for p in _policies.get(user.tenant_id, [])]


@router.post("/escalation-policies", response_model=EscalationPolicy)
async def create_policy(
    req: EscalationPolicyCreate,
    session: DBSession,
    user: CurrentUser,
):
    """Create/update escalation policy for a business line."""
    from logmind.domain.tenant.models import BusinessLine
    biz = await session.get(BusinessLine, req.business_line_id)
    biz_name = biz.name if biz else req.business_line_id

    # Remove existing policy for this business line
    existing = _policies.get(user.tenant_id, [])
    _policies[user.tenant_id] = [p for p in existing if p.get("business_line_id") != req.business_line_id]

    policy = {
        "id": str(uuid.uuid4()),
        "business_line_id": req.business_line_id,
        "business_line_name": biz_name,
        "levels": req.levels,
    }
    _policies.setdefault(user.tenant_id, []).append(policy)
    logger.info("escalation_policy_created", biz=biz_name, levels=len(req.levels))
    return EscalationPolicy(**policy)
