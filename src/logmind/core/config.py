"""
LogMind Core Configuration — Pydantic Settings

Supports both PostgreSQL and MySQL via DATABASE_URL.
All sensitive values loaded from environment variables.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────
    app_name: str = "logmind"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    secret_key: str = "change-me-in-production"

    # ── Database ─────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://logmind:logmind@localhost:5432/logmind"
    database_echo: bool = False
    database_pool_size: int = 20
    database_max_overflow: int = 10

    @property
    def database_dialect(self) -> str:
        """Detect database dialect from URL."""
        if "mysql" in self.database_url:
            return "mysql"
        return "postgresql"

    # ── Redis ────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Celery ───────────────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── Elasticsearch ────────────────────────────────────
    es_hosts: str = "http://10.14.3.101:9200"
    es_username: str = ""
    es_password: str = ""
    es_verify_certs: bool = False
    es_request_timeout: int = 30

    @property
    def es_hosts_list(self) -> list[str]:
        return [h.strip() for h in self.es_hosts.split(",")]

    # ── JWT Auth ─────────────────────────────────────────
    jwt_secret_key: str = "change-me-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 480

    # ── Encryption (API Key storage) ─────────────────────
    encryption_key: str = "change-me-use-Fernet-generate-key"

    # ── WeChat Work ──────────────────────────────────────
    wechat_webhook_url: str = ""
    wechat_enabled: bool = False

    # ── MinIO ────────────────────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "logmind"
    minio_secret_key: str = "logmind123456"
    minio_bucket: str = "logmind-docs"
    minio_secure: bool = False

    # ── AI Analysis Cost Control ─────────────────────────
    analysis_severity_threshold: str = "error"
    analysis_max_logs_per_task: int = 500
    analysis_daily_task_limit: int = 100
    analysis_cooldown_minutes: int = 30
    analysis_fingerprint_enabled: bool = True
    analysis_fingerprint_ttl_hours: int = 6
    analysis_agent_max_steps: int = 5
    analysis_agent_enabled: bool = True

    # ── Adaptive Log Sampling ────────────────────────────
    analysis_sampling_default_budget: int = 150
    analysis_sampling_min_budget: int = 20
    analysis_sampling_max_budget: int = 300

    # ── Semantic Dedup (Phase 3) ──────────────────────────
    analysis_semantic_dedup_enabled: bool = True
    analysis_semantic_dedup_threshold: float = 0.92
    analysis_semantic_dedup_ttl_hours: int = 168  # 7 days (was 24h)
    analysis_embedding_cache_ttl_seconds: int = 3600

    @field_validator("analysis_severity_threshold")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"debug", "info", "warning", "error", "critical"}
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.lower()

    @field_validator("secret_key", "jwt_secret_key", "encryption_key")
    @classmethod
    def reject_default_keys(cls, v: str, info) -> str:
        """Prevent production deployment with placeholder keys."""
        if v.startswith("change-me") or v.startswith("dev-"):
            if os.getenv("APP_ENV") == "production":
                raise ValueError(
                    f"{info.field_name} must be changed from default value "
                    f"in production! Current value starts with '{v[:12]}...'"
                )
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
