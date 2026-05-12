"""
SQLAlchemy Async Database Engine & Session Management

Supports both PostgreSQL (asyncpg) and MySQL (aiomysql).
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from logmind.core.config import get_settings
from logmind.core.runtime import is_celery_runtime

settings = get_settings()

_ENGINE_PID: int | None = None
_ENGINE: AsyncEngine | None = None
_SESSION_FACTORY: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    """Create a process-local async engine."""
    runtime_is_celery = is_celery_runtime()
    engine_kwargs: dict = {
        "echo": settings.database_echo,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }

    if runtime_is_celery:
        from sqlalchemy.pool import NullPool

        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs["pool_size"] = settings.database_pool_size
        engine_kwargs["max_overflow"] = settings.database_max_overflow

    return create_async_engine(settings.database_url, **engine_kwargs)


def _ensure_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """
    Ensure the engine/sessionmaker belong to the current process.

    Celery prefork can import modules in the parent process, so module-level
    engines must not be blindly reused after fork.
    """
    global _ENGINE_PID, _ENGINE, _SESSION_FACTORY

    current_pid = os.getpid()
    if _ENGINE is None or _SESSION_FACTORY is None or _ENGINE_PID != current_pid:
        _ENGINE = _build_engine()
        _SESSION_FACTORY = async_sessionmaker(
            _ENGINE,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        _ENGINE_PID = current_pid

    return _ENGINE, _SESSION_FACTORY


def get_engine() -> AsyncEngine:
    """Get the current process-local SQLAlchemy async engine."""
    engine, _ = _ensure_engine()
    return engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the current process-local async session factory."""
    _, session_factory = _ensure_engine()
    return session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a database session per request."""
    async_session_factory = get_session_factory()
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for use outside of FastAPI (e.g., Celery tasks)."""
    async_session_factory = get_session_factory()
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database — create tables if they don't exist."""
    from logmind.shared.base_model import Base  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose database engine on shutdown."""
    global _ENGINE_PID, _ENGINE, _SESSION_FACTORY

    if _ENGINE is not None:
        await _ENGINE.dispose()

    _ENGINE = None
    _SESSION_FACTORY = None
    _ENGINE_PID = None
