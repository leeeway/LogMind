"""
Migration: Add alert ACK/resolve tracking fields

New columns on alert_history:
  - acked_at: timestamp when on-call acknowledged
  - acked_by: username of acknowledger
  - resolved_by: username of resolver
  - priority: P0/P1/P2 alert priority level
"""

import asyncio
from sqlalchemy import text
from logmind.core.database import engine


async def migrate():
    """Add alert feedback columns if they don't exist."""
    async with engine.begin() as conn:
        # Check which columns already exist
        check = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'alert_history'
        """))
        existing = {row[0] for row in check.fetchall()}

        migrations = []
        if "acked_at" not in existing:
            migrations.append(
                "ALTER TABLE alert_history ADD COLUMN acked_at TIMESTAMPTZ"
            )
        if "acked_by" not in existing:
            migrations.append(
                "ALTER TABLE alert_history ADD COLUMN acked_by VARCHAR(100)"
            )
        if "resolved_by" not in existing:
            migrations.append(
                "ALTER TABLE alert_history ADD COLUMN resolved_by VARCHAR(100)"
            )
        if "priority" not in existing:
            migrations.append(
                "ALTER TABLE alert_history ADD COLUMN priority VARCHAR(10) DEFAULT 'P2'"
            )

        for sql in migrations:
            await conn.execute(text(sql))
            print(f"  ✅ {sql}")

        if not migrations:
            print("  ℹ️  All columns already exist, nothing to migrate.")


if __name__ == "__main__":
    asyncio.run(migrate())
