"""
Shared SQLAlchemy Base Model & Mixins

Provides:
- Base: declarative base for all ORM models
- TimestampMixin: created_at / updated_at
- TenantMixin: tenant_id for row-level multi-tenant isolation
- UUIDPrimaryKeyMixin: UUID primary key

Compatible with both PostgreSQL and MySQL.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""
    pass


class UUIDPrimaryKeyMixin:
    """UUID primary key mixin — compatible with PG and MySQL."""
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )


class TimestampMixin:
    """Auto-managed created_at and updated_at timestamps."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class TenantMixin:
    """Multi-tenant row-level isolation mixin."""
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
