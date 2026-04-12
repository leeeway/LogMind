"""
Shared Pydantic Base Schemas

Provides common schema patterns for API request/response models.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """Base schema with ORM mode enabled."""
    model_config = ConfigDict(from_attributes=True)


class IDSchema(BaseSchema):
    """Schema with UUID ID."""
    id: str


class TimestampSchema(BaseSchema):
    """Schema with timestamps."""
    created_at: datetime
    updated_at: datetime


class PaginatedResponse(BaseSchema):
    """Standard paginated response wrapper."""
    items: list
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def create(
        cls, items: list, total: int, page: int, page_size: int
    ) -> "PaginatedResponse":
        pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )


class MessageResponse(BaseSchema):
    """Simple message response."""
    message: str
    success: bool = True


class ErrorResponse(BaseSchema):
    """Standard error response."""
    error: str
    detail: str | None = None
    code: str | None = None
