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

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from logmind.core.logging import get_logger

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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis-based sliding window rate limiter.

    Uses sorted sets with timestamps as scores for accurate
    sliding window counting. Each tenant gets its own rate
    limit bucket per route prefix.

    Falls through gracefully if Redis is unavailable (open policy).
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        limit_config = _get_rate_limit(path)

        if limit_config is None:
            return await call_next(request)

        max_requests, window_seconds = limit_config

        # Extract tenant identity (from JWT or API key)
        # Fall back to client IP if no tenant context
        tenant_id = getattr(request.state, "tenant_id", None)
        if not tenant_id:
            tenant_id = request.client.host if request.client else "anonymous"

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
            return await call_next(request)

        if not is_allowed:
            logger.warning(
                "rate_limit_exceeded",
                tenant_id=tenant_id,
                path=path,
                bucket=bucket_prefix,
                limit=max_requests,
                window=window_seconds,
            )
            return JSONResponse(
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

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

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
