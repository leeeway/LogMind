"""
FastAPI Middleware

- Tenant resolution from JWT / API Key
- Request/response structured logging
- CORS
"""

import time
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with timing, tenant, and trace context."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(uuid.uuid4())[:8]
        start_time = time.perf_counter()

        # Bind context for structured logging
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        # Add request ID to response headers
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Bind tenant if present
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id:
            structlog.contextvars.bind_contextvars(tenant_id=tenant_id)

        await logger.ainfo(
            "request_completed",
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )

        response.headers["X-Request-ID"] = request_id
        return response


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Extract tenant_id from JWT token and inject into request.state.
    For non-authenticated endpoints, tenant_id will be None.
    """

    SKIP_PATHS = {"/api/v1/health", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip middleware for health check and docs
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        # Tenant will be resolved in the dependency layer (dependencies.py)
        # This middleware just initializes the state
        request.state.tenant_id = None
        request.state.user_id = None
        request.state.user_role = None

        return await call_next(request)
