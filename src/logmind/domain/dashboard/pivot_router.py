"""
Pivot Table — Multi-dimensional analysis API.

Dynamic GROUP BY with configurable rows/cols/metrics.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
from logmind.domain.tenant.models import BusinessLine
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
biz_repo = BaseRepository(BusinessLine)


class PivotCell(BaseModel):
    row: str
    col: str
    value: float


class PivotResponse(BaseModel):
    rows: list[str]
    cols: list[str]
    cells: list[PivotCell]
    row_dimension: str
    col_dimension: str
    metric: str


@router.get("/pivot", response_model=PivotResponse)
async def get_pivot_data(
    session: DBSession,
    user: CurrentUser,
    row_dim: str = Query("service", description="Row dimension: service|severity|type|date"),
    col_dim: str = Query("severity", description="Col dimension: service|severity|type|date"),
    metric: str = Query("count", description="Metric: count|total"),
    days: int = Query(7, ge=1, le=30),
):
    """
    Multi-dimensional pivot table data.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Build dimension expressions
    dim_exprs = {
        "service": LogAnalysisTask.business_line_id,
        "severity": AnalysisResult.severity,
        "type": AnalysisResult.result_type,
        "date": func.date_trunc("day", AnalysisResult.created_at),
    }

    row_expr = dim_exprs.get(row_dim)
    col_expr = dim_exprs.get(col_dim)
    if row_expr is None or col_expr is None:
        row_expr = dim_exprs["service"]
        col_expr = dim_exprs["severity"]

    # Metric
    metric_expr = func.count()

    stmt = (
        select(
            row_expr.label("row_val"),
            col_expr.label("col_val"),
            metric_expr.label("metric_val"),
        )
        .join(LogAnalysisTask, AnalysisResult.task_id == LogAnalysisTask.id)
        .where(
            LogAnalysisTask.tenant_id == user.tenant_id,
            AnalysisResult.created_at >= since,
        )
        .group_by(row_expr, col_expr)
    )

    result = await session.execute(stmt)
    rows_data = result.all()

    # Map service IDs to names
    biz_lines = await biz_repo.get_all(session, tenant_id=user.tenant_id, limit=100)
    biz_map = {b.id: b.name for b in biz_lines}

    def format_dim(val: any, dim: str) -> str:
        if val is None:
            return "未知"
        if dim == "service":
            return biz_map.get(str(val), str(val)[:8])
        if dim == "date" and hasattr(val, "strftime"):
            return val.strftime("%m-%d")
        return str(val)

    row_set: set[str] = set()
    col_set: set[str] = set()
    cells: list[PivotCell] = []

    for row in rows_data:
        r = format_dim(row[0], row_dim)
        c = format_dim(row[1], col_dim)
        v = float(row[2] or 0)
        row_set.add(r)
        col_set.add(c)
        cells.append(PivotCell(row=r, col=c, value=v))

    return PivotResponse(
        rows=sorted(row_set),
        cols=sorted(col_set),
        cells=cells,
        row_dimension=row_dim,
        col_dimension=col_dim,
        metric=metric,
    )
