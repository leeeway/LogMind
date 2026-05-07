import asyncio
from logmind.domain.dashboard.standup_generator import generate_standup_report

async def main():
    try:
        res = await generate_standup_report("dummy_tenant")
        print("Success:", res)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
