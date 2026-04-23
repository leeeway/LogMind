"""
Alert Domain — ORM Models

Models: AlertRule, AlertHistory
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from logmind.shared.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AlertRule(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """Alert rule definition."""

    __tablename__ = "alert_rule"

    business_line_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    rule_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # keyword / pattern / ai_anomaly / threshold
    conditions: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    notify_channels: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    cron_expression: Mapped[str] = mapped_column(String(50), default="*/30 * * * *")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AlertHistory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Alert firing history."""

    __tablename__ = "alert_history"

    alert_rule_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    analysis_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(20), default="fired"
    )  # fired / acknowledged / resolved
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notify_result: Mapped[str] = mapped_column(Text, default="{}")  # JSON

    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    priority: Mapped[str] = mapped_column(String(10), default="P2")  # P0 / P1 / P2
