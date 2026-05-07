"""
Dashboard Builder Router — CRUD for custom dashboards.

Each dashboard is a JSON layout of widgets stored in DB.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.shared.base_model import Base, UUIDPrimaryKeyMixin, TimestampMixin
from logmind.shared.base_repository import BaseRepository
from sqlalchemy import Column, String, Text, JSON, Boolean

logger = get_logger(__name__)
router = APIRouter(prefix="/dashboards/custom", tags=["DashboardBuilder"])


# ── Model ────────────────────────────────────────────────
class CustomDashboard(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A user-defined custom dashboard layout."""
    __tablename__ = "custom_dashboards"

    tenant_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(100), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String(500), default="")
    layout = Column(JSON, default=list)   # [{id, type, title, x, y, w, h, config}]
    is_default = Column(Boolean, default=False)


dashboard_repo = BaseRepository(CustomDashboard)


# ── Schemas ──────────────────────────────────────────────
class WidgetConfig(BaseModel):
    id: str = ""
    type: str = "number"  # number / line_chart / bar_chart / log_list / status_matrix / alert_list / markdown
    title: str = "Widget"
    x: int = 0
    y: int = 0
    w: int = 4
    h: int = 3
    config: dict = {}  # type-specific: {metric, index_pattern, query, level, limit, content, ...}


class SaveDashboardRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    layout: list[WidgetConfig] = []


# ── Routes ───────────────────────────────────────────────
@router.get("")
async def list_dashboards(db: DBSession, user: CurrentUser):
    """List user's custom dashboards."""
    dashboards = await dashboard_repo.get_all(
        db, tenant_id=user.tenant_id, filters={"user_id": user.sub}, limit=50
    )
    return {
        "dashboards": [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "widget_count": len(d.layout or []),
                "is_default": d.is_default,
                "updated_at": str(d.updated_at),
            }
            for d in dashboards
        ]
    }


@router.post("")
async def create_dashboard(req: SaveDashboardRequest, db: DBSession, user: CurrentUser):
    """Create a new custom dashboard."""
    dash = CustomDashboard(
        id=str(uuid.uuid4()),
        tenant_id=user.tenant_id,
        user_id=user.sub,
        name=req.name,
        description=req.description,
        layout=[w.model_dump() for w in req.layout],
    )
    db.add(dash)
    await db.commit()
    return {"id": dash.id, "status": "created"}


@router.get("/{dashboard_id}")
async def get_dashboard(dashboard_id: str, db: DBSession, user: CurrentUser):
    """Get a dashboard with full layout."""
    dash = await dashboard_repo.get_by_id(db, dashboard_id)
    if not dash or dash.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return {
        "id": dash.id,
        "name": dash.name,
        "description": dash.description,
        "layout": dash.layout or [],
        "is_default": dash.is_default,
        "updated_at": str(dash.updated_at),
    }


@router.put("/{dashboard_id}")
async def update_dashboard(dashboard_id: str, req: SaveDashboardRequest, db: DBSession, user: CurrentUser):
    """Update dashboard layout."""
    dash = await dashboard_repo.get_by_id(db, dashboard_id)
    if not dash or dash.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    dash.name = req.name
    dash.description = req.description
    dash.layout = [w.model_dump() for w in req.layout]
    await db.commit()
    return {"ok": True}


@router.delete("/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, db: DBSession, user: CurrentUser):
    """Delete a dashboard."""
    dash = await dashboard_repo.get_by_id(db, dashboard_id)
    if not dash or dash.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    await db.delete(dash)
    await db.commit()
    return {"ok": True}
