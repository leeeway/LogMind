"""
Incident Models — Incident tracking and War Room collaboration.
"""

from sqlalchemy import Column, String, Text, DateTime, Integer, JSON, ForeignKey
from sqlalchemy.sql import func

from logmind.shared.base_model import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Incident(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An incident / outage being tracked."""
    __tablename__ = "incidents"

    tenant_id = Column(String(36), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    severity = Column(String(10), default="P2")  # P0/P1/P2/P3
    status = Column(String(20), default="investigating")  # investigating / identified / monitoring / resolved
    assignee = Column(String(100), default="")
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, default=0)
    related_alert_ids = Column(JSON, default=list)
    related_task_ids = Column(JSON, default=list)
    tags = Column(JSON, default=list)
    postmortem = Column(Text, default="")


class IncidentEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Timeline event within an incident."""
    __tablename__ = "incident_events"

    incident_id = Column(String(36), ForeignKey("incidents.id"), nullable=False, index=True)
    event_type = Column(String(20), nullable=False)  # alert / action / message / ai / status_change
    content = Column(Text, nullable=False)
    user = Column(String(100), default="system")
    event_metadata = Column(JSON, default=dict)
