"""
Analysis Domain — Pydantic Schemas
"""

from datetime import datetime

from pydantic import BaseModel, Field

from logmind.shared.base_schema import BaseSchema


class AnalysisTaskCreate(BaseModel):
    """Request to create a manual analysis task."""
    business_line_id: str
    provider_config_id: str | None = None
    prompt_template_id: str | None = None
    time_from: datetime
    time_to: datetime
    query: str = ""
    severity: str | None = Field(
        None,
        description="Severity filter. Default uses business line threshold",
    )
    extra_filters: dict = Field(default_factory=dict)


class AnalysisResultResponse(BaseSchema):
    id: str
    task_id: str
    result_type: str
    content: str
    severity: str
    confidence_score: float
    structured_data: str
    created_at: datetime


class AnalysisTaskResponse(BaseSchema):
    id: str
    tenant_id: str
    business_line_id: str
    task_type: str
    status: str
    log_count: int
    token_usage: int
    cost_usd: float
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    results: list[AnalysisResultResponse] = []


class AnalysisTaskSummary(BaseSchema):
    """Lightweight task summary for list views."""
    id: str
    business_line_id: str
    task_type: str
    status: str
    log_count: int
    token_usage: int
    created_at: datetime
    completed_at: datetime | None
