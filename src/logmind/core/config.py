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
    public_app_url: str = ""

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

    @property
    def effective_patrol_interval_minutes(self) -> int:
        return self.analysis_patrol_interval_minutes

    @property
    def effective_anomaly_window_minutes(self) -> int:
        return self.analysis_anomaly_window_minutes

    @property
    def effective_lookback_minutes(self) -> int:
        return self.analysis_lookback_minutes

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

    # ── Global Nginx / Ingress Access Patrol ─────────────
    # This patrol is intentionally independent from the business_line table.
    http_access_patrol_enabled: bool = False
    http_access_notification_enabled: bool = False
    http_access_recovery_notification_enabled: bool = False
    http_access_ai_enabled: bool = True
    http_access_indexes: str = (
        "nginx-log-json,ingress-nginx-master-external-log"
    )
    http_access_allowed_suffixes: str = "gyyx.cn,tjlong.cn,wyx.cn,costrip.cn"
    http_access_window_minutes: int = 5
    http_access_webhook_url: str = ""
    http_access_tenant_id: str = ""
    http_access_metrics_index: str = "logmind-http-access-metrics-v1"
    http_access_metrics_retention_days: int = 90
    http_access_baseline_days: int = 7
    http_access_dedup_minutes: int = 30
    http_access_max_notification_sites: int = 10
    http_access_sample_size: int = 20

    @property
    def http_access_index_list(self) -> tuple[str, ...]:
        return tuple(
            item.strip()
            for item in self.http_access_indexes.split(",")
            if item.strip()
        )

    @property
    def http_access_allowed_suffix_list(self) -> tuple[str, ...]:
        return tuple(
            item.strip().lower().lstrip(".")
            for item in self.http_access_allowed_suffixes.split(",")
            if item.strip()
        )

    # ── CI/CD Webhook ───────────────────────────────────────
    # HMAC-SHA256 secret for /api/v1/changes/webhook/{tenant_id}.
    # Production rejects unsigned CI webhook requests when this is empty.
    ci_webhook_secret: str = ""

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
    analysis_cooldown_minutes: int = 5  # Legacy name; use analysis_patrol_interval_minutes
    analysis_patrol_interval_minutes: int = 5
    analysis_anomaly_window_minutes: int = 5
    analysis_lookback_minutes: int = 10
    pipeline_error_cooldown_minutes: int = 240
    analysis_fingerprint_enabled: bool = True
    analysis_fingerprint_ttl_hours: int = 6
    analysis_agent_max_steps: int = 5
    analysis_agent_enabled: bool = True

    # ── Adaptive Log Sampling ────────────────────────────
    analysis_sampling_default_budget: int = 150
    analysis_sampling_min_budget: int = 20
    analysis_sampling_max_budget: int = 300

    # ── Change-Point Detection ───────────────────────────
    analysis_changepoint_enabled: bool = True
    analysis_changepoint_threshold: float = 3.0   # Z-score threshold for spike detection
    analysis_changepoint_window_hours: int = 4     # Rolling baseline window

    # ── Semantic Dedup (Phase 3) ──────────────────────────
    analysis_semantic_dedup_enabled: bool = True
    analysis_semantic_dedup_threshold: float = 0.92
    analysis_semantic_dedup_ttl_hours: int = 168  # 7 days (was 24h)
    analysis_embedding_cache_ttl_seconds: int = 3600

    # ── Embedding API Retry Configuration ─────────────────
    embedding_retry_enabled: bool = True
    embedding_retry_max_attempts: int = 3
    embedding_retry_initial_wait: float = 1.0
    embedding_retry_max_wait: float = 10.0
    embedding_retry_multiplier: float = 2.0

    @field_validator("analysis_severity_threshold")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"debug", "info", "warning", "error", "critical"}
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.lower()

    @field_validator(
        "analysis_cooldown_minutes",
        "analysis_patrol_interval_minutes",
        "analysis_anomaly_window_minutes",
        "analysis_lookback_minutes",
        "http_access_window_minutes",
        "http_access_metrics_retention_days",
        "http_access_baseline_days",
        "http_access_dedup_minutes",
        "http_access_max_notification_sites",
        "http_access_sample_size",
    )
    @classmethod
    def validate_positive_minutes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("minute settings must be greater than 0")
        return v

    @field_validator("secret_key", "jwt_secret_key", "encryption_key")
    @classmethod
    def reject_default_keys(cls, v: str, info) -> str:
        """Prevent production deployment with placeholder keys."""
        if (
            v.startswith(("change-me", "dev-"))
            and os.getenv("APP_ENV") == "production"
        ):
            raise ValueError(
                f"{info.field_name} must be changed from default value "
                f"in production! Current value starts with '{v[:12]}...'"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
