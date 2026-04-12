"""
Analysis Domain — ORM Models

Models: LogAnalysisTask, AnalysisResult
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from logmind.shared.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class LogAnalysisTask(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A single AI log analysis task/job."""

    __tablename__ = "log_analysis_task"

    business_line_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider_config_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prompt_template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    task_type: Mapped[str] = mapped_column(
        String(20), default="manual"
    )  # manual / scheduled / alert_triggered
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending / running / completed / failed

    # Query context
    query_params: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    time_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Results metadata
    log_count: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    results = relationship("AnalysisResult", back_populates="task", lazy="selectin")


class AnalysisResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Individual analysis result/finding from an AI analysis task."""

    __tablename__ = "analysis_result"

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("log_analysis_task.id"), nullable=False, index=True)

    result_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # anomaly / root_cause / summary / suggestion
    content: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), default="info"
    )  # critical / warning / info
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Structured data (JSON text)
    structured_data: Mapped[str] = mapped_column(Text, default="{}")
    source_log_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON array

    # Relationships
    task = relationship("LogAnalysisTask", back_populates="results")
