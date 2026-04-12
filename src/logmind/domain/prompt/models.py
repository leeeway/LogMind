"""
Prompt Template — ORM Model
"""

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from logmind.shared.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PromptTemplate(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """
    AI Prompt template — configurable, versioned.

    Templates use Jinja2 syntax with variable interpolation.
    Variables are validated against a JSON Schema.
    """

    __tablename__ = "prompt_template"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # log_analysis / anomaly_detect / root_cause / summary
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    description: Mapped[str] = mapped_column(Text, default="")

    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)

    # JSON Schema defining expected variables (stored as JSON text)
    variables_schema: Mapped[str] = mapped_column(Text, default="{}")

    # Additional metadata (JSON text)
    extra_metadata: Mapped[str] = mapped_column("metadata", Text, default="{}")

    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
