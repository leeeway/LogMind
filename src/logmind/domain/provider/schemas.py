"""
Provider Pydantic Schemas
"""

from datetime import datetime

from pydantic import BaseModel, Field

from logmind.shared.base_schema import BaseSchema


class ProviderConfigCreate(BaseModel):
    provider_type: str = Field(
        ..., pattern=r"^(openai|claude|gemini|deepseek|ollama|subapi)$"
    )
    name: str = Field(..., max_length=100)
    api_base_url: str = Field(..., max_length=500)
    api_key: str = Field(default="", description="Will be encrypted at rest")
    default_model: str = Field(..., max_length=100)
    model_params: dict = Field(
        default_factory=lambda: {"temperature": 0.3, "max_tokens": 4096}
    )
    priority: int = Field(0, description="Higher = preferred for load balancing")
    rate_limit_rpm: int = Field(60, ge=1)


class ProviderConfigUpdate(BaseModel):
    name: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    model_params: dict | None = None
    priority: int | None = None
    rate_limit_rpm: int | None = None
    is_active: bool | None = None


class ProviderConfigResponse(BaseSchema):
    id: str
    tenant_id: str
    provider_type: str
    name: str
    api_base_url: str
    default_model: str
    model_params: str  # JSON string
    priority: int
    rate_limit_rpm: int
    is_active: bool
    created_at: datetime
    # Note: api_key is NEVER returned


class ProviderHealthResponse(BaseSchema):
    provider_id: str
    provider_type: str
    name: str
    is_healthy: bool
    error: str | None = None


class RegisteredProvidersResponse(BaseSchema):
    providers: list[str]
