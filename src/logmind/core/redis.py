"""
Redis Connection Pool

Provides async Redis client for caching, rate limiting, and session storage.
"""

from functools import lru_cache

import redis.asyncio as aioredis

from logmind.core.config import get_settings


@lru_cache
def get_redis_pool() -> aioredis.ConnectionPool:
    """Create a cached Redis connection pool."""
    settings = get_settings()
    return aioredis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=50,
        decode_responses=True,
    )


def get_redis_client() -> aioredis.Redis:
    """Get a Redis client from the connection pool."""
    pool = get_redis_pool()
    return aioredis.Redis(connection_pool=pool)


async def close_redis() -> None:
    """Close the Redis connection pool on shutdown."""
    pool = get_redis_pool()
    await pool.aclose()
