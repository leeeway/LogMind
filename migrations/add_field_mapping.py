"""
Migration: Add field_mapping, language, ai_enabled, webhook_url columns to business_line table.

- field_mapping: Configurable field name mapping for varied log formats
- language: Development language for language-specific log parsing
- ai_enabled: Toggle for AI model usage per business line
- webhook_url: Per-business-line webhook notification URL
"""

import asyncio

from logmind.core.database import get_db_context
from logmind.core.logging import get_logger, setup_logging

logger = get_logger(__name__)

MIGRATION_SQLS_PG = [
    "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS field_mapping TEXT NOT NULL DEFAULT '{}';",
    "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS language VARCHAR(20) NOT NULL DEFAULT 'java';",
    "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS ai_enabled BOOLEAN NOT NULL DEFAULT TRUE;",
    "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS webhook_url VARCHAR(500) NOT NULL DEFAULT '';",
]

MIGRATION_SQLS_MYSQL = [
    "ALTER TABLE business_line ADD COLUMN field_mapping TEXT NOT NULL DEFAULT '{}';",
    "ALTER TABLE business_line ADD COLUMN language VARCHAR(20) NOT NULL DEFAULT 'java';",
    "ALTER TABLE business_line ADD COLUMN ai_enabled BOOLEAN NOT NULL DEFAULT TRUE;",
    "ALTER TABLE business_line ADD COLUMN webhook_url VARCHAR(500) NOT NULL DEFAULT '';",
]


def _col_name_from_sql(sql: str) -> str:
    """Extract column name from ALTER TABLE ADD COLUMN statement."""
    import re
    m = re.search(r"ADD COLUMN\s+(?:IF NOT EXISTS\s+)?(\w+)", sql, re.IGNORECASE)
    return m.group(1) if m else "unknown"


async def migrate():
    """Run migration to add new columns."""
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
            col_name = _col_name_from_sql(sql)
            try:
                await session.execute(text(sql))
                print(f"✅ Added column: {col_name}")
                logger.info("migration_column_added", column=col_name, table="business_line")
            except Exception as e:
                err_str = str(e).lower()
                if "already exists" in err_str or "duplicate column" in err_str:
                    print(f"⏭️  Column {col_name} already exists, skipping")
                    logger.info("migration_skipped", column=col_name, reason="already_exists")
                else:
                    print(f"❌ Migration failed for {col_name}: {e}")
                    logger.error("migration_failed", column=col_name, error=str(e))
                    raise

    print("✅ Migration completed")


def main():
    setup_logging(log_level="INFO", json_format=False)
    print("🔄 Running migration: business_line columns")
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
