"""
Rate Limiter — Redis-based Sliding Window Rate Limiting

Provides per-tenant, per-route rate limiting using Redis sorted sets
with a sliding window algorithm.

Usage:
    Registered as FastAPI middleware in main.py.
    Rate limits are defined per route prefix:
      - /api/v1/analysis/*  → 10 req/min (AI-intensive)
      - /api/v1/*           → 60 req/min (general)
      - /health*            → unlimited (probes)

    Exceeding the limit returns HTTP 429 with Retry-After header.
"""

import time

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from logmind.core.logging import get_logger
from logmind.core.security import decode_access_token

logger = get_logger(__name__)

# Route prefix → (max_requests, window_seconds)
_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/v1/analysis": (10, 60),       # 10 req/min for analysis
    "/api/v1/rag":      (10, 60),       # 10 req/min for RAG
    "/api/v1":          (60, 60),        # 60 req/min general
}

# Paths exempt from rate limiting
_EXEMPT_PATHS = {"/api/v1/health", "/api/v1/health/live", "/docs", "/openapi.json", "/redoc"}


def _get_rate_limit(path: str) -> tuple[int, int] | None:
    """Find the most specific rate limit for a given path."""
    if path in _EXEMPT_PATHS:
        return None
    for prefix, limit in _RATE_LIMITS.items():
        if path.startswith(prefix):
            return limit
    return None


def _get_rate_limit_identity(scope: Scope) -> str:
    """Resolve rate-limit identity from request state, JWT tenant, then client IP."""
    state = scope.get("state") or {}
    tenant_id = state.get("tenant_id") if isinstance(state, dict) else getattr(state, "tenant_id", None)
    if tenant_id:
        return str(tenant_id)

    for name, value in scope.get("headers", []):
        if name.lower() != b"authorization":
            continue
        auth_value = value.decode("latin1")
        scheme, _, token = auth_value.partition(" ")
        if scheme.lower() != "bearer" or not token:
            continue
        try:
            payload = decode_access_token(token)
            jwt_tenant_id = payload.get("tenant_id")
            if jwt_tenant_id:
                return str(jwt_tenant_id)
        except Exception:
            break

    client = scope.get("client")
    if client:
        return str(client[0])
    return "anonymous"


class RateLimitMiddleware:
    """
    Redis-based sliding window rate limiter.

    Uses sorted sets with timestamps as scores for accurate
    sliding window counting. Each tenant gets its own rate
    limit bucket per route prefix.

    Falls through gracefully if Redis is unavailable (open policy).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path
        limit_config = _get_rate_limit(path)

        if limit_config is None:
            await self.app(scope, receive, send)
            return

        max_requests, window_seconds = limit_config

        # Extract tenant identity from state/JWT, then fall back to client IP.
        tenant_id = _get_rate_limit_identity(scope)

        # Determine rate limit bucket key
        # Use the matched prefix, not the full path
        bucket_prefix = next(
            (p for p in _RATE_LIMITS if path.startswith(p)), "/api/v1"
        )
        bucket_key = f"logmind:ratelimit:{tenant_id}:{bucket_prefix}"

        try:
            is_allowed, remaining, retry_after = await self._check_rate_limit(
                bucket_key, max_requests, window_seconds
            )
        except Exception as e:
            # Redis failure → open policy (allow request)
            logger.warning("rate_limit_check_failed", error=str(e))
            await self.app(scope, receive, send)
            return

        if not is_allowed:
            logger.warning(
                "rate_limit_exceeded",
                tenant_id=tenant_id,
                path=path,
                bucket=bucket_prefix,
                limit=max_requests,
                window=window_seconds,
            )
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "detail": f"Maximum {max_requests} requests per {window_seconds}s",
                    "retry_after": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )
            await response(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-RateLimit-Limit"] = str(max_requests)
                headers["X-RateLimit-Remaining"] = str(remaining)
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    async def _check_rate_limit(
        key: str, max_requests: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """
        Sliding window rate limit check using Redis sorted set.

        Returns: (is_allowed, remaining_requests, retry_after_seconds)
        """
        from logmind.core.redis import get_redis_client

        r = get_redis_client()
        now = time.time()
        window_start = now - window_seconds

        pipe = r.pipeline()
        # Remove expired entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Count current window entries
        pipe.zcard(key)
        # Add current request
        pipe.zadd(key, {f"{now}": now})
        # Set TTL on the key
        pipe.expire(key, window_seconds + 1)
        results = await pipe.execute()

        current_count = results[1]  # zcard result

        if current_count >= max_requests:
            # Get the oldest entry to calculate retry-after
            oldest = await r.zrange(key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(oldest[0][1] + window_seconds - now) + 1
            else:
                retry_after = window_seconds
            return False, 0, max(retry_after, 1)

        remaining = max_requests - current_count - 1  # -1 for current request
        return True, max(remaining, 0), 0
