"""
RAG Domain — Pydantic Schemas

Request/response schemas for Knowledge Base management API.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from logmind.shared.base_schema import BaseSchema


# ── Knowledge Base Schemas ───────────────────────────────

class KnowledgeBaseCreate(BaseModel):
    """Create a new knowledge base."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    chunk_size: int = Field(1000, ge=100, le=10000)
    chunk_overlap: int = Field(200, ge=0, le=2000)


class KnowledgeBaseUpdate(BaseModel):
    """Update an existing knowledge base."""
    name: str | None = None
    description: str | None = None
    chunk_size: int | None = Field(None, ge=100, le=10000)
    chunk_overlap: int | None = Field(None, ge=0, le=2000)
    is_active: bool | None = None


class KnowledgeBaseResponse(BaseSchema):
    id: str
    name: str
    description: str
    chunk_size: int
    chunk_overlap: int
    is_active: bool
    document_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class KnowledgeBaseDetail(KnowledgeBaseResponse):
    documents: list["KBDocumentResponse"] = []


# ── Document Schemas ─────────────────────────────────────

class KBDocumentUpload(BaseModel):
    """Upload a document to the knowledge base (text content)."""
    filename: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1, description="Raw text content of the document")
    metadata: dict = Field(default_factory=dict, description="Optional metadata")


class KBDocumentResponse(BaseSchema):
    id: str
    kb_id: str
    filename: str
    status: str
    chunk_count: int
    created_at: datetime
    updated_at: datetime | None = None
