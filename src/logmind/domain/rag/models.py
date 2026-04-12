"""
RAG Domain — ORM Models

Models: KnowledgeBase, KBDocument
"""

from sqlalchemy import Boolean, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from logmind.shared.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class KnowledgeBase(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """RAG Knowledge Base."""

    __tablename__ = "knowledge_base"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    embedding_provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vector_index_name: Mapped[str] = mapped_column(String(200), default="")
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=200)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    documents = relationship("KBDocument", back_populates="knowledge_base", lazy="selectin")


class KBDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Document within a knowledge base."""

    __tablename__ = "kb_document"

    kb_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_base.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), default="")  # MinIO path
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending / processing / indexed / failed
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
