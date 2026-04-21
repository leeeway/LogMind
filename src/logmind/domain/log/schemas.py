"""
Log Domain — Pydantic Schemas
"""

from datetime import datetime

from pydantic import BaseModel, Field

from logmind.shared.base_schema import BaseSchema


class LogQueryRequest(BaseModel):
    """ES log query parameters."""
    index_pattern: str = Field(..., description="ES index pattern(s)")
    time_from: datetime
    time_to: datetime
    query: str = Field("", description="Free text search query")
    severity: str | None = Field(
        None, description="Filter by severity: debug/info/warning/error/critical"
    )
    namespace: str | None = None
    pod_name: str | None = None
    container_name: str | None = None
    domain: str | None = Field(None, description="Filter by gy.domain (site domain)")
    filetype: str | None = Field(
        None, description="Filter by gy.filetype (e.g. error.log, info.log, sys.log.txt)"
    )
    language: str | None = Field(
        None, description="Business line language (java/csharp). "
        "When set, enables language-specific severity filtering on message content."
    )
    extra_filters: dict = Field(default_factory=dict)
    size: int = Field(5000, ge=1, le=10000)
    sort_order: str = Field("desc", pattern=r"^(asc|desc)$")


class LogEntry(BaseSchema):
    """Single log entry from ES."""
    id: str
    timestamp: str
    level: str = ""
    message: str
    source: dict = Field(default_factory=dict)
    kubernetes: dict = Field(default_factory=dict)
    raw: dict = Field(default_factory=dict)

    # GYYX gy.* business metadata
    domain: str = Field("", description="Site domain from gy.domain")
    pod_name: str = Field("", description="Pod name from gy.podname")
    branch: str = Field("", description="Code branch from gy.branch")
    image_version: str = Field("", description="Image version from image.version")
    filetype: str = Field("", description="Log file type from gy.filetype")
    host_name: str = Field("", description="Host name from host.name (esp. for VM-deployed C# services)")


class LogQueryResponse(BaseSchema):
    """Log query response."""
    total: int
    logs: list[LogEntry]
    took_ms: int


class LogAggregation(BaseSchema):
    """Log aggregation result."""
    key: str
    count: int


class LogStatsResponse(BaseSchema):
    """Log statistics."""
    total_logs: int
    by_level: list[LogAggregation]
    by_namespace: list[LogAggregation]
    by_domain: list[LogAggregation] = Field(default_factory=list)
    by_filetype: list[LogAggregation] = Field(default_factory=list)
    time_histogram: list[dict]


class ESIndexInfo(BaseSchema):
    """ES index information."""
    name: str
    docs_count: int
    size: str
    status: str
