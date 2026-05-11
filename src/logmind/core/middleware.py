"""
FastAPI Middleware

- Tenant resolution from JWT / API Key
- Request/response structured logging
- CORS
"""

import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger(__name__)


def _init_request_state(scope: Scope) -> None:
    """Ensure request state exists before dependency resolution runs."""
    state = scope.setdefault("state", {})
    state.setdefault("tenant_id", None)
    state.setdefault("user_id", None)
    state.setdefault("user_role", None)


class RequestLoggingMiddleware:
    """Log every request with timing, tenant, and trace context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())[:8]
        start_time = time.perf_counter()
        status_code = 500

        # Bind context for structured logging
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=scope["method"],
            path=scope["path"],
        )

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000

            state = scope.get("state") or {}
            tenant_id = state.get("tenant_id")
            if tenant_id:
                structlog.contextvars.bind_contextvars(tenant_id=tenant_id)

            await logger.ainfo(
                "request_completed",
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
            )


class TenantMiddleware:
    """
    Extract tenant_id from JWT token and inject into request.state.
    For non-authenticated endpoints, tenant_id will be None.
    """

    SKIP_PATHS = {"/api/v1/health", "/docs", "/openapi.json", "/redoc"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Skip middleware for health check and docs
        if scope["path"] in self.SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        # Tenant will be resolved in the dependency layer (dependencies.py)
        # This middleware just initializes the state
        _init_request_state(scope)
        await self.app(scope, receive, send)
