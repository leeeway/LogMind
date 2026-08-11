"""Tenant-owned governance for globally discovered HTTP access sites.

This model intentionally has no relationship to ``business_line``: access-log
patrol discovers hosts from the two shared gateway indices instead.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
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
    diagnostic_business_line_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    repository_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    deployment_service_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class HttpAccessIncidentRecord(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """Durable lifecycle for one HTTP anomaly fingerprint."""

    __tablename__ = "http_access_incident"
    __table_args__ = (
        UniqueConstraint("tenant_id", "fingerprint", name="uq_http_access_incident_fingerprint"),
        Index("ix_http_access_incident_tenant_status", "tenant_id", "status"),
    )

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    site: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    sources: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    route_key: Mapped[str] = mapped_column(String(520), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_impact: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    current_impact: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    peak_impact: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    notification_pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notification_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    diagnosis_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    feedback: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    feedback_comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    handled_by: Mapped[str] = mapped_column(String(100), default="", nullable=False)


class HttpAccessLearningRule(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """Human-confirmed or conservative automatic suppression rule."""

    __tablename__ = "http_access_learning_rule"
    __table_args__ = (
        UniqueConstraint("tenant_id", "fingerprint", name="uq_http_access_learning_fingerprint"),
    )

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    site: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="operator", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GitRepositoryConfig(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """Read-only GitLab repository connection; credentials live outside DB."""

    __tablename__ = "git_repository_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", "clone_url", name="uq_git_repository_tenant_url"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    clone_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(32), default="main", nullable=False)
    credential_ref: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    last_sync_error: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_commit_sha: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    cache_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class GitDeploymentRevision(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """CI-provided deployed commit mapping used for exact-code diagnosis."""

    __tablename__ = "git_deployment_revision"
    __table_args__ = (
        Index("ix_git_deploy_tenant_service_time", "tenant_id", "service_name", "deployed_at"),
    )

    repository_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    branch: Mapped[str] = mapped_column(String(32), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_commit_sha: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    image_version: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    environment: Mapped[str] = mapped_column(String(32), default="production", nullable=False)
    operator: Mapped[str] = mapped_column(String(100), default="CI/CD", nullable=False)
    deployed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
