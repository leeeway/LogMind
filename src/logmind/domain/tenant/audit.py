"""
Audit Log — Model & Service

Records all critical user actions for security and compliance.
"""

from sqlalchemy import String, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from logmind.shared.base_model import Base, UUIDPrimaryKeyMixin
from datetime import datetime, timezone


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Audit trail for user actions."""

    __tablename__ = "audit_log"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(50), default="")
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "alert.ack", "issue.resolve"
    resource_type: Mapped[str] = mapped_column(String(50), default="")  # e.g. "alert", "known_issue"
    resource_id: Mapped[str] = mapped_column(String(100), default="")
    details: Mapped[str] = mapped_column(Text, default="{}")  # JSON details
    ip_address: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_audit_tenant_time", "tenant_id", "created_at"),
    )
