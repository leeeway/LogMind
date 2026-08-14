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
    await conn.close()
    
asyncio.run(main())
