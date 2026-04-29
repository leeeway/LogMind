import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL").replace("+asyncpg", "")

async def main():
    conn = await asyncpg.connect(db_url)
    records = await conn.fetch("SELECT * FROM provider_config")
    for r in records:
        print(dict(r))
    await conn.close()

asyncio.run(main())
