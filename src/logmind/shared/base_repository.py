"""
Generic CRUD Repository

Base repository with common database operations, tenant-scoped by default.
"""

from typing import Any, Generic, TypeVar

from sqlalchemy import select, func, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from logmind.shared.base_model import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Generic async CRUD repository.

    All queries are scoped to tenant_id when the model has a tenant_id column.
    """

    def __init__(self, model: type[ModelType]):
        self.model = model

    def _has_tenant(self) -> bool:
        return hasattr(self.model, "tenant_id")

    def _apply_tenant_filter(self, stmt, tenant_id: str | None):
        if tenant_id and self._has_tenant():
            stmt = stmt.where(self.model.tenant_id == tenant_id)
        return stmt

    async def get_by_id(
        self, session: AsyncSession, id: str, tenant_id: str | None = None
    ) -> ModelType | None:
        stmt = select(self.model).where(self.model.id == id)
        stmt = self._apply_tenant_filter(stmt, tenant_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(
        self,
        session: AsyncSession,
        tenant_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
        order_by: str = "created_at",
        descending: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> list[ModelType]:
        stmt = select(self.model)
        stmt = self._apply_tenant_filter(stmt, tenant_id)

        # Apply additional filters
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key) and value is not None:
                    stmt = stmt.where(getattr(self.model, key) == value)

        # Ordering
        if hasattr(self.model, order_by):
            col = getattr(self.model, order_by)
            stmt = stmt.order_by(col.desc() if descending else col.asc())

        stmt = stmt.offset(offset).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        session: AsyncSession,
        tenant_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(self.model)
        stmt = self._apply_tenant_filter(stmt, tenant_id)
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key) and value is not None:
                    stmt = stmt.where(getattr(self.model, key) == value)
        result = await session.execute(stmt)
        return result.scalar_one()

    async def create(
        self, session: AsyncSession, obj: ModelType
    ) -> ModelType:
        session.add(obj)
        await session.flush()
        await session.refresh(obj)
        return obj

    async def update_by_id(
        self,
        session: AsyncSession,
        id: str,
        values: dict[str, Any],
        tenant_id: str | None = None,
    ) -> bool:
        stmt = (
            update(self.model)
            .where(self.model.id == id)
            .values(**values)
        )
        stmt = self._apply_tenant_filter(stmt, tenant_id)
        result = await session.execute(stmt)
        return result.rowcount > 0

    async def delete_by_id(
        self,
        session: AsyncSession,
        id: str,
        tenant_id: str | None = None,
    ) -> bool:
        stmt = delete(self.model).where(self.model.id == id)
        stmt = self._apply_tenant_filter(stmt, tenant_id)
        result = await session.execute(stmt)
        return result.rowcount > 0
