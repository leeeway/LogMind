"""
Chat Domain — ORM Models

Persistent chat sessions, messages, and diagnostic evidence.
Replaces the in-memory _sessions dict for production use.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from logmind.shared.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ChatConversation(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A persistent chat session."""

    __tablename__ = "chat_conversation"

    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / archived

    messages = relationship("ChatMessageRecord", back_populates="conversation", lazy="selectin", order_by="ChatMessageRecord.seq")
    evidences = relationship("DiagnosticEvidence", back_populates="conversation", lazy="noload")


class ChatMessageRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single message in a chat conversation."""

    __tablename__ = "chat_message"

    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_conversation.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user / assistant / system
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata (JSON): suggested_actions, tool_calls summary, etc.
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    # Evidence IDs referenced in this message (JSON array of evidence IDs)
    evidence_refs: Mapped[str] = mapped_column(Text, default="[]")

    conversation = relationship("ChatConversation", back_populates="messages")


class DiagnosticEvidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A piece of evidence collected during AI diagnosis.

    Each tool call that returns meaningful data generates one evidence record.
    Evidence can be referenced by messages and diagnostic clues.
    """

    __tablename__ = "diagnostic_evidence"

    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_conversation.id"), nullable=False, index=True
    )

    # Short sequential label within conversation: E-1, E-2, ...
    label: Mapped[str] = mapped_column(String(10), nullable=False)

    # Tool invocation details
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_args: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    es_index_pattern: Mapped[str] = mapped_column(String(500), default="")
    time_from: Mapped[str] = mapped_column(String(40), default="")
    time_to: Mapped[str] = mapped_column(String(40), default="")

    # Result summary
    source_service: Mapped[str] = mapped_column(String(100), default="")
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    result_preview: Mapped[str] = mapped_column(Text, default="")  # First 1000 chars
    sampled_logs: Mapped[str] = mapped_column(Text, default="[]")  # JSON: top 5 log entries

    # Classification
    evidence_type: Mapped[str] = mapped_column(
        String(30), default="tool_result"
    )  # tool_result / search_clue / trace_segment / prediction

    conversation = relationship("ChatConversation", back_populates="evidences")
