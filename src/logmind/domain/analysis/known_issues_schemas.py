"""
Known Issues — Pydantic Schemas

Request/response schemas for the Known Issue Library CRUD API.
Known issues are stored in ES `logmind-analysis-vectors` index,
not in the relational database.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from logmind.shared.base_schema import BaseSchema


# ── Response Schemas ─────────────────────────────────────

class KnownIssueResponse(BaseSchema):
    """Single known issue from the vector index."""
    id: str = Field(..., description="ES document ID")
    business_line_id: str = ""
    error_signature: str = ""
    analysis_content: str = ""
    severity: str = "info"
    task_id: str = ""

    # State management
    status: str = Field("open", description="open / resolved / ignored")
    hit_count: int = Field(1, description="Cumulative match count")
    first_seen: str | None = Field(None, description="First time this issue was seen")
    last_seen: str | None = Field(None, description="Last time this issue was matched")
    resolved_at: str | None = Field(None, description="When issue was marked resolved")
    feedback_quality: str | None = Field(None, description="verified / poor / null")

    # Metadata
    created_at: str | None = None
    ttl_expire_at: str | None = None


class KnownIssueDetail(KnownIssueResponse):
    """Extended detail with business line name."""
    business_line_name: str = ""


class KnownIssueListResponse(BaseSchema):
    """Paginated response for known issues list."""
    items: list[KnownIssueResponse]
    total: int
    page: int
    page_size: int


# ── Request Schemas ──────────────────────────────────────

class KnownIssueStatusUpdate(BaseModel):
    """Update the status of a known issue."""
    status: str = Field(
        ...,
        pattern=r"^(open|resolved|ignored)$",
        description="New status: open / resolved / ignored",
    )
    comment: str | None = Field(
        None, max_length=500,
        description="Optional reason for status change",
    )
