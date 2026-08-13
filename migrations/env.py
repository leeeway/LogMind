"""
Alembic migration environment.

Supports both PostgreSQL (asyncpg) and MySQL (aiomysql) via async engines.
"""

import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config, create_async_engine

import logmind.domain.alert.models  # noqa: F401
import logmind.domain.analysis.models  # noqa: F401
import logmind.domain.http_access.site_config  # noqa: F401
import logmind.domain.prompt.models  # noqa: F401
import logmind.domain.provider.models  # noqa: F401
import logmind.domain.rag.models  # noqa: F401
import logmind.domain.tenant.models  # noqa: F401
from logmind.core.config import get_settings

# Import all models so Alembic can detect them
from logmind.shared.base_model import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Resolve the same URL the API uses.  Do not rely exclusively on Alembic's
# ConfigParser for an environment URL: a valid database password can contain
# '%', which ConfigParser treats as interpolation and previously caused the
# exception below to be silently swallowed.  The resulting fallback was the
# localhost URL from alembic.ini, even though the API was connected correctly.

database_url: str | None = None
try:
    settings = get_settings()
    database_url = settings.database_url
    # Keep offline mode and Alembic tooling informative. Escape only for the
    # ConfigParser representation; online mode uses database_url directly.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
except Exception as exc:
    # Online migration still has the configured Alembic URL as a fallback.
    # Do not hide this in logs: otherwise a production migration can silently
    # attempt localhost and look like a database outage.
    logging.getLogger(__name__).warning("Unable to load application DATABASE_URL: %s", exc)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    if database_url:
        connectable = create_async_engine(database_url, poolclass=pool.NullPool)
    else:
        connectable = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations online."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
