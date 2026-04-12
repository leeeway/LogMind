"""
Tenant Domain — ORM Models

Models: Tenant, User, BusinessLine
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from logmind.shared.base_model import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Multi-tenant organization."""

    __tablename__ = "tenant"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[str] = mapped_column(Text, default="{}")  # JSON stored as text for MySQL compat
    quota_tokens_daily: Mapped[int] = mapped_column(Integer, default=1000000)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    users = relationship("User", back_populates="tenant", lazy="selectin")
    business_lines = relationship("BusinessLine", back_populates="tenant", lazy="selectin")


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Platform user — belongs to a tenant."""

    __tablename__ = "user"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id"), nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), default="viewer", nullable=False
    )  # admin / analyst / viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="users", foreign_keys=[tenant_id])


class BusinessLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Business line / service group — maps to ES index patterns.
    Supports arbitrary index naming (per user feedback: indexes named by site, potentially inconsistent).
    """

    __tablename__ = "business_line"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenant.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    # ES index configuration — supports arbitrary patterns
    # Examples: "site-a-*", "prod-webserver-*", "k8s-app-*,k8s-nginx-*"
    es_index_pattern: Mapped[str] = mapped_column(String(500), nullable=False)

    # Log parsing configuration (JSON text for MySQL compat)
    # Defines how to extract timestamp, level, message from varied log formats
    log_parse_config: Mapped[str] = mapped_column(Text, default="{}")

    # Default ES query filters (JSON text)
    # e.g. {"must": [{"term": {"kubernetes.namespace": "production"}}]}
    default_filters: Mapped[str] = mapped_column(Text, default="{}")

    # Severity threshold for AI analysis cost control
    severity_threshold: Mapped[str] = mapped_column(
        String(20), default="error"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="business_lines", foreign_keys=[tenant_id])
