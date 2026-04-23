"""
Migration: Add all v1.4-v1.9 schema changes.

Changes:
1. log_analysis_task: Add stage_metrics column
2. agent_tool_call: Create new table for Agent tool call tracing
3. business_line: Add priority engine columns (v1.4)
4. analysis_result: Add feedback columns (v1.3)

Idempotent: safe to run multiple times (IF NOT EXISTS / IF NOT EXISTS).
"""

import asyncio

from logmind.core.database import get_db_context
from logmind.core.logging import get_logger, setup_logging

logger = get_logger(__name__)

MIGRATION_SQLS_PG = [
    # ── 1. log_analysis_task: stage_metrics ──
    (
        "stage_metrics column",
        "ALTER TABLE log_analysis_task ADD COLUMN IF NOT EXISTS stage_metrics TEXT NOT NULL DEFAULT '[]';",
    ),

    # ── 2. agent_tool_call table ──
    (
        "agent_tool_call table",
        """
        CREATE TABLE IF NOT EXISTS agent_tool_call (
            id VARCHAR(36) PRIMARY KEY,
            task_id VARCHAR(36) NOT NULL REFERENCES log_analysis_task(id),
            step INTEGER NOT NULL,
            tool_name VARCHAR(100) NOT NULL,
            arguments TEXT NOT NULL DEFAULT '{}',
            result_preview TEXT NOT NULL DEFAULT '',
            result_length INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            success BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """,
    ),
    (
        "agent_tool_call index",
        "CREATE INDEX IF NOT EXISTS ix_agent_tool_call_task_id ON agent_tool_call (task_id);",
    ),

    # ── 3. business_line: priority engine columns (v1.4) ──
    (
        "business_weight column",
        "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS business_weight INTEGER NOT NULL DEFAULT 5;",
    ),
    (
        "is_core_path column",
        "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS is_core_path BOOLEAN NOT NULL DEFAULT FALSE;",
    ),
    (
        "estimated_dau column",
        "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS estimated_dau INTEGER NOT NULL DEFAULT 0;",
    ),
    (
        "night_policy column",
        "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS night_policy VARCHAR(20) NOT NULL DEFAULT 'p0_only';",
    ),
    (
        "night_hours column",
        "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS night_hours VARCHAR(20) NOT NULL DEFAULT '23:00-08:00';",
    ),
    (
        "auto_remediation_config column",
        "ALTER TABLE business_line ADD COLUMN IF NOT EXISTS auto_remediation_config TEXT NOT NULL DEFAULT '{}';",
    ),

    # ── 4. analysis_result: feedback columns (v1.3) ──
    (
        "feedback_score column",
        "ALTER TABLE analysis_result ADD COLUMN IF NOT EXISTS feedback_score INTEGER DEFAULT NULL;",
    ),
    (
        "feedback_comment column",
        "ALTER TABLE analysis_result ADD COLUMN IF NOT EXISTS feedback_comment TEXT DEFAULT NULL;",
    ),
]


async def migrate():
    """Run migration to apply all v1.4-v1.9 schema changes."""
    # Import models so tables are registered
    import logmind.domain.tenant.models  # noqa: F401
    import logmind.domain.provider.models  # noqa: F401
    import logmind.domain.prompt.models  # noqa: F401
    import logmind.domain.analysis.models  # noqa: F401
    import logmind.domain.alert.models  # noqa: F401
    import logmind.domain.rag.models  # noqa: F401

    from logmind.core.database import init_db
    await init_db()

    async with get_db_context() as session:
        from sqlalchemy import text

        total = len(MIGRATION_SQLS_PG)
        for i, (desc, sql) in enumerate(MIGRATION_SQLS_PG, 1):
            try:
                await session.execute(text(sql.strip()))
                print(f"✅ [{i}/{total}] {desc}")
                logger.info("migration_applied", step=i, description=desc)
            except Exception as e:
                err_str = str(e).lower()
                if "already exists" in err_str or "duplicate" in err_str:
                    print(f"⏭️  [{i}/{total}] {desc} — already exists, skipping")
                else:
                    print(f"❌ [{i}/{total}] {desc} — FAILED: {e}")
                    logger.error("migration_failed", step=i, description=desc, error=str(e))
                    raise

    print(f"\n✅ Migration completed — all {total} steps applied")


def main():
    setup_logging(log_level="INFO", json_format=False)
    print("🔄 Running migration: v1.4-v1.9 schema changes")
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
