"""
Migration: Add field_mapping and language columns to business_line table.

- field_mapping: Configurable field name mapping for varied log formats
- language: Development language for language-specific log parsing (java/csharp/python/go/other)
"""

import asyncio

from logmind.core.database import get_db_context
from logmind.core.logging import get_logger, setup_logging

logger = get_logger(__name__)

MIGRATION_SQLS_PG = [
    "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS field_mapping TEXT NOT NULL DEFAULT '{}';",
    "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS language VARCHAR(20) NOT NULL DEFAULT 'java';",
]

MIGRATION_SQLS_MYSQL = [
    "ALTER TABLE business_line ADD COLUMN field_mapping TEXT NOT NULL DEFAULT '{}';",
    "ALTER TABLE business_line ADD COLUMN language VARCHAR(20) NOT NULL DEFAULT 'java';",
]


async def migrate():
    """Run migration to add field_mapping and language columns."""
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

    sqls = MIGRATION_SQLS_PG if settings.database_dialect == "postgresql" else MIGRATION_SQLS_MYSQL

    async with get_db_context() as session:
        from sqlalchemy import text
        for sql in sqls:
            try:
                await session.execute(text(sql))
                col_name = "field_mapping" if "field_mapping" in sql else "language"
                print(f"✅ Added column: {col_name}")
                logger.info("migration_column_added", column=col_name, table="business_line")
            except Exception as e:
                err_str = str(e).lower()
                if "already exists" in err_str or "duplicate column" in err_str:
                    col_name = "field_mapping" if "field_mapping" in sql else "language"
                    print(f"⏭️  Column {col_name} already exists, skipping")
                    logger.info("migration_skipped", column=col_name, reason="already_exists")
                else:
                    print(f"❌ Migration failed: {e}")
                    logger.error("migration_failed", error=str(e))
                    raise

    print("✅ Migration completed")


def main():
    setup_logging(log_level="INFO", json_format=False)
    print("🔄 Running migration: add_field_mapping + language")
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
