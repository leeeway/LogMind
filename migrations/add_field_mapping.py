"""
Migration: Add field_mapping column to business_line table.

Supports configurable field name mapping for varied log formats (e.g. GYYX gy.* fields).
"""

import asyncio

from logmind.core.database import get_db_context
from logmind.core.logging import get_logger, setup_logging

logger = get_logger(__name__)

MIGRATION_SQL_PG = """
ALTER TABLE business_line
ADD COLUMN IF NOT EXISTS field_mapping TEXT NOT NULL DEFAULT '{}';
"""

MIGRATION_SQL_MYSQL = """
ALTER TABLE business_line
ADD COLUMN field_mapping TEXT NOT NULL DEFAULT '{}';
"""


async def migrate():
    """Run migration to add field_mapping column."""
    from logmind.core.config import get_settings

    settings = get_settings()

    # Import models so tables are registered
    import logmind.domain.tenant.models  # noqa: F401
    import logmind.domain.provider.models  # noqa: F401
    import logmind.domain.prompt.models  # noqa: F401
    import logmind.domain.analysis.models  # noqa: F401
    import logmind.domain.alert.models  # noqa: F401
    import logmind.domain.rag.models  # noqa: F401

    from logmind.core.database import init_db
    await init_db()

    sql = MIGRATION_SQL_PG if settings.database_dialect == "postgresql" else MIGRATION_SQL_MYSQL

    async with get_db_context() as session:
        try:
            from sqlalchemy import text
            await session.execute(text(sql))
            print("✅ Migration completed: added field_mapping column to business_line")
            logger.info("migration_completed", column="field_mapping", table="business_line")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("⏭️  Column field_mapping already exists, skipping")
                logger.info("migration_skipped", reason="column_already_exists")
            else:
                print(f"❌ Migration failed: {e}")
                logger.error("migration_failed", error=str(e))
                raise


def main():
    setup_logging(log_level="INFO", json_format=False)
    print("🔄 Running migration: add_field_mapping")
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
