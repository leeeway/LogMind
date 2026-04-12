"""
Prompt Template — Pydantic Schemas
"""

from datetime import datetime

from pydantic import BaseModel, Field

from logmind.shared.base_schema import BaseSchema


class PromptTemplateCreate(BaseModel):
    name: str = Field(..., max_length=100)
    category: str = Field(
        ..., pattern=r"^(log_analysis|anomaly_detect|root_cause|summary)$"
    )
    version: str = Field("1.0.0", max_length=20)
    description: str = ""
    system_prompt: str
    user_prompt_template: str
    variables_schema: dict = Field(default_factory=dict)
    is_default: bool = False


class PromptTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    user_prompt_template: str | None = None
    variables_schema: dict | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class PromptTemplateResponse(BaseSchema):
    id: str
    tenant_id: str
    name: str
    category: str
    version: str
    description: str
    system_prompt: str
    user_prompt_template: str
    variables_schema: str
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PromptRenderRequest(BaseModel):
    template_id: str
    variables: dict


class PromptRenderResponse(BaseSchema):
    system_prompt: str
    user_prompt: str


class PromptValidateRequest(BaseModel):
    template_str: str


class PromptValidateResponse(BaseSchema):
    is_valid: bool
    errors: list[str]
