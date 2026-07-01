"""Tenant-scoped access helpers."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from logmind.domain.tenant.models import BusinessLine


async def get_active_business_line_or_404(
    session: AsyncSession,
    tenant_id: str,
    business_line_id: str | None,
) -> BusinessLine:
    """Load an active business line owned by the tenant, or raise 404/422."""
    if not business_line_id:
        raise HTTPException(
            status_code=422,
            detail="business_line_id is required",
        )

    biz = await session.get(BusinessLine, business_line_id)
    if not biz or biz.tenant_id != tenant_id or not biz.is_active:
        raise HTTPException(status_code=404, detail="Business line not found")

    return biz


async def list_active_business_lines(
    session: AsyncSession,
    tenant_id: str,
) -> list[BusinessLine]:
    """Return all active business lines for a tenant."""
    stmt = (
        select(BusinessLine)
        .where(
            BusinessLine.tenant_id == tenant_id,
            BusinessLine.is_active == True,  # noqa: E712
        )
        .order_by(BusinessLine.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
