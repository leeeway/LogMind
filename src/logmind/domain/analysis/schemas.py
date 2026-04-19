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


# ── Observability / Trace Schemas ────────────────────────

class StageMetric(BaseSchema):
    """Timing and status for a single pipeline stage."""
    stage: str
    duration_ms: int = 0
    status: str = "ok"  # ok / skipped / error
    error: str | None = None


class ToolCallRecord(BaseSchema):
    """Record of a single Agent tool invocation."""
    id: str
    step: int
    tool_name: str
    arguments: str = "{}"
    result_preview: str = ""
    result_length: int = 0
    duration_ms: int = 0
    success: bool = True
    created_at: datetime


class TaskTraceResponse(BaseSchema):
    """
    Full execution trace for an analysis task.

    Includes pipeline stage timings and agent tool call chain.
    Used for debugging, performance analysis, and observability.
    """
    task_id: str
    status: str
    total_duration_ms: int = Field(
        0, description="End-to-end pipeline duration (sum of stages)"
    )
    stages: list[StageMetric]
    tool_calls: list[ToolCallRecord]
    errors: list[str] = Field(default_factory=list)

