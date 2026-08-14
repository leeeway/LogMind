import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL").replace("+asyncpg", "")

async def main():
    conn = await asyncpg.connect(db_url)
    columns_to_add = [
        "ALTER TABLE business_line ADD COLUMN language VARCHAR(20) NOT NULL DEFAULT 'java'",
        "ALTER TABLE business_line ADD COLUMN field_mapping TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE business_line ADD COLUMN ai_enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE business_line ADD COLUMN webhook_url VARCHAR(500) NOT NULL DEFAULT ''",
        "ALTER TABLE business_line ADD COLUMN noise_patterns TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE business_line ADD COLUMN min_notify_priority VARCHAR(10) NULL",
    ]
    for col_query in columns_to_add:
        try:
            await conn.execute(col_query)
            print(f"Executed: {col_query}")
        except asyncpg.exceptions.DuplicateColumnError:
            print(f"Column already exists for: {col_query}")
        except Exception as e:
            print(f"Error executing {col_query}: {e}")

    # Create discovered_index table
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS discovered_index (
        tenant_id VARCHAR(36) NOT NULL REFERENCES tenant(id),
        index_name VARCHAR(500) NOT NULL,
        index_pattern VARCHAR(500) NOT NULL,
        doc_count BIGINT DEFAULT 0,
        first_seen TIMESTAMP WITH TIME ZONE NOT NULL,
        last_seen TIMESTAMP WITH TIME ZONE NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        business_line_id VARCHAR(36) NULL,
        id VARCHAR(36) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        PRIMARY KEY (id),
        UNIQUE (tenant_id, index_pattern)
    )
    """
    try:
        await conn.execute(create_table_sql)
        print("Created table: discovered_index")
    except Exception as e:
        print(f"Table discovered_index: {e}")

    await conn.close()
    
asyncio.run(main())
