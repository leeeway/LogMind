"""
Tenant Domain — Pydantic Schemas
"""

import json
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from logmind.shared.base_schema import BaseSchema


# ── Tenant ───────────────────────────────────────────────
class TenantCreate(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=50, pattern=r"^[a-z0-9-]+$")
    description: str = ""
    quota_tokens_daily: int = Field(1000000, ge=0)


class TenantUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    quota_tokens_daily: int | None = None
    is_active: bool | None = None


class TenantResponse(BaseSchema):
    id: str
    name: str
    slug: str
    description: str
    quota_tokens_daily: int
    is_active: bool
    created_at: datetime


# ── User ─────────────────────────────────────────────────
class UserCreate(BaseModel):
    username: str = Field(..., max_length=50)
    email: str = Field(..., max_length=200)
    password: str = Field(..., min_length=8)
    role: str = Field("viewer", pattern=r"^(admin|analyst|viewer)$")


class UserResponse(BaseSchema):
    id: str
    tenant_id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseSchema):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ── BusinessLine ─────────────────────────────────────────
class BusinessLineCreate(BaseModel):
    name: str = Field(..., max_length=100)
    description: str = ""
    es_index_pattern: str = Field(
        ...,
        max_length=500,
        description="ES index pattern(s), comma-separated. e.g. 'site-a-*,site-b-*'",
    )
    log_parse_config: dict = Field(default_factory=dict)
    default_filters: dict = Field(default_factory=dict)
    severity_threshold: str = Field("error", pattern=r"^(debug|info|warning|error|critical)$")
    language: str = Field(
        "java",
        pattern=r"^(java|csharp|python|go|other)$",
        description="Development language: java/csharp/python/go/other. "
        "Determines log parsing strategy and stack trace detection.",
    )
    field_mapping: dict = Field(
        default_factory=dict,
        description="Field mapping for varied log formats. "
        'e.g. {"level_field": "gy.filetype", "domain_field": "gy.domain", ...}',
    )
    ai_enabled: bool = Field(
        True,
        description="Enable AI model analysis. When False, only sends error log notifications via webhook.",
    )
    webhook_url: str = Field(
        "",
        max_length=500,
        description="Webhook URL for notifications. Overrides global setting. "
        "Supports WeChat Work, DingTalk, Feishu webhooks.",
    )


class BusinessLineUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    es_index_pattern: str | None = None
    log_parse_config: dict | None = None
    default_filters: dict | None = None
    severity_threshold: str | None = None
    language: str | None = Field(None, pattern=r"^(java|csharp|python|go|other)$")
    field_mapping: dict | None = None
    ai_enabled: bool | None = None
    webhook_url: str | None = None
    is_active: bool | None = None


class BusinessLineResponse(BaseSchema):
    id: str
    tenant_id: str
    name: str
    description: str
    es_index_pattern: str
    severity_threshold: str
    language: str = "java"
    field_mapping: dict = Field(default_factory=dict)
    ai_enabled: bool = True
    webhook_url: str = ""
    is_active: bool
    created_at: datetime

    @field_validator("field_mapping", mode="before")
    @classmethod
    def parse_json_field(cls, v):
        """Auto-deserialize JSON string from DB into dict."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return v
