"""
Global Exception Definitions

Custom exceptions for structured error handling across the platform.
"""

from typing import Any


class LogMindError(Exception):
    """Base exception for all LogMind errors."""

    def __init__(self, message: str, detail: Any = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


# ── Auth ─────────────────────────────────────────────────
class AuthenticationError(LogMindError):
    """Invalid or missing authentication credentials."""
    pass


class AuthorizationError(LogMindError):
    """Insufficient permissions."""
    pass


# ── Resource ─────────────────────────────────────────────
class NotFoundError(LogMindError):
    """Requested resource not found."""

    def __init__(self, resource: str, resource_id: str):
        super().__init__(
            message=f"{resource} not found: {resource_id}",
            detail={"resource": resource, "id": resource_id},
        )


class ConflictError(LogMindError):
    """Resource already exists or conflicts."""
    pass


# ── Provider ─────────────────────────────────────────────
class ProviderError(LogMindError):
    """AI Provider error."""

    def __init__(self, provider: str, message: str, detail: Any = None):
        super().__init__(
            message=f"Provider [{provider}] error: {message}",
            detail=detail,
        )


class AllProvidersFailedError(LogMindError):
    """All configured providers failed."""

    def __init__(self, tenant_id: str):
        super().__init__(
            message=f"All providers failed for tenant {tenant_id}",
            detail={"tenant_id": tenant_id},
        )


# ── Pipeline ─────────────────────────────────────────────
class PipelineError(LogMindError):
    """Pipeline stage execution error."""

    def __init__(self, stage: str, original_error: Exception):
        super().__init__(
            message=f"Pipeline stage [{stage}] failed: {original_error}",
            detail={"stage": stage, "error": str(original_error)},
        )


# ── Quota ────────────────────────────────────────────────
class QuotaExceededError(LogMindError):
    """Daily analysis quota exceeded."""

    def __init__(self, tenant_id: str, limit: int):
        super().__init__(
            message=f"Daily analysis quota exceeded for tenant {tenant_id} (limit: {limit})",
            detail={"tenant_id": tenant_id, "limit": limit},
        )


# ── Validation ───────────────────────────────────────────
class ValidationError(LogMindError):
    """Input validation error."""
    pass


class TemplateRenderError(LogMindError):
    """Prompt template rendering error."""
    pass
