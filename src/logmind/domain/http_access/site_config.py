"""Tenant-owned governance for globally discovered HTTP access sites.

This model intentionally has no relationship to ``business_line``: access-log
patrol discovers hosts from the two shared gateway indices instead.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from logmind.shared.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class HttpAccessSiteConfig(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "http_access_site_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site", name="uq_http_access_site_tenant_site"),
        Index("ix_http_access_site_tenant_mode", "tenant_id", "monitoring_mode"),
    )

    site: Mapped[str] = mapped_column(String(253), nullable=False)
    # JSON array, e.g. ["nginx", "ingress"], kept MySQL-compatible.
    sources: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    environment: Mapped[str] = mapped_column(String(16), default="production", nullable=False)
    role: Mapped[str] = mapped_column(String(24), default="general", nullable=False)
    # observe = collect/baseline only; enabled = eligible to notify; disabled = silent.
    monitoring_mode: Mapped[str] = mapped_column(String(16), default="observe", nullable=False)
    enable_4xx: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_latency: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_traffic_drop: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(24), default="normal", nullable=False)
    owner: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
