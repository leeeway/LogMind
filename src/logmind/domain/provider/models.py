"""
Provider ORM Model — ProviderConfig
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from logmind.shared.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ProviderConfig(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """
    AI Provider configuration — stored per tenant.
    API keys are encrypted at rest using Fernet.
    """

    __tablename__ = "provider_config"

    provider_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # openai/claude/gemini/deepseek/ollama/subapi
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    api_base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    default_model: Mapped[str] = mapped_column(String(100), nullable=False)

    # Model parameters (JSON text for MySQL compat)
    model_params: Mapped[str] = mapped_column(Text, default="{}")

    # Load balancing & rate limiting
    priority: Mapped[int] = mapped_column(Integer, default=0)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, default=60)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
