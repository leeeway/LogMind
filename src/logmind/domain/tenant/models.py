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

    # Development language — determines log format parsing strategy
    # java: gy.filetype-based level, Java stack traces (at ..., Caused by:)
    # csharp: NLog/log4net format, .NET stack traces, sys.log.txt filetype
    # python: Python traceback format
    # go: Go panic/runtime stack format
    # other: Generic format, level extracted from message
    language: Mapped[str] = mapped_column(
        String(20), default="java"
    )  # java / csharp / python / go / other

    # Configurable field mapping for varied log formats (JSON text)
    # Defines how to map source-specific fields to LogMind standard fields.
    # Example for GYYX Filebeat format:
    # {
    #   "level_field": "gy.filetype",
    #   "level_mapping": {"info.log": "info", "error.log": "error", ...},
    #   "domain_field": "gy.domain",
    #   "pod_field": "gy.podname",
    #   "branch_field": "gy.branch",
    #   "version_field": "image.version",
    #   "filetype_field": "gy.filetype"
    # }
    field_mapping: Mapped[str] = mapped_column(Text, default="{}")

    # AI model toggle — when False, skip AI inference and directly send
    # error log notifications via webhook. Useful for cost control or
    # when AI provider is not configured.
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Per-business-line webhook URL for notifications.
    # Overrides the global wechat_webhook_url from settings.
    # Supports WeChat Work, DingTalk, Feishu, or any webhook accepting JSON POST.
    webhook_url: Mapped[str] = mapped_column(String(500), default="")

    # ── Priority Decision Engine ───────────────────────
    # Business importance weight (1-10)
    # 10 = core revenue (authentication/payment), 5 = normal, 1 = internal tool
    business_weight: Mapped[int] = mapped_column(Integer, default=5)

    # Whether this is a critical path (login/payment/registration)
    # Core paths get +10 priority score boost
    is_core_path: Mapped[bool] = mapped_column(Boolean, default=False)

    # Estimated DAU for impact assessment
    estimated_dau: Mapped[int] = mapped_column(Integer, default=0)

    # Night silence policy
    # "always": notify any priority immediately
    # "p0_only": at night, only P0 notifies immediately, P1/P2 delayed to morning
    # "silent": fully silent at night, all delayed to morning
    night_policy: Mapped[str] = mapped_column(String(20), default="p0_only")

    # Night hours window (HH:MM-HH:MM, local time based on Celery timezone)
    night_hours: Mapped[str] = mapped_column(String(20), default="22:00-08:00")

    # Auto-remediation Runbook config (JSON)
    # Phase B: {"actions": [{"type": "webhook", "url": "...", "trigger_on": ["P0"]}]}
    auto_remediation_config: Mapped[str] = mapped_column(Text, default="{}")

    # Cross-service correlation — upstream and downstream service dependencies (JSON text)
    # Format: {"upstream": ["<biz_line_id>", ...], "downstream": ["<biz_line_id>", ...]}
    # When this service has errors, the pipeline auto-checks related services
    # for correlated failures in the same time window.
    related_services: Mapped[str] = mapped_column(Text, default="{}")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="business_lines", foreign_keys=[tenant_id])
