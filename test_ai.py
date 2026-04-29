import asyncio
from logmind.core.database import get_db_context
from logmind.domain.provider.manager import provider_manager
from logmind.domain.provider.base import ChatRequest, ChatMessage

async def main():
    tenant_id = "d663f2a4-7de5-43f2-ae5d-5e530ced4840"
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="hello")]
    )
    
    async with get_db_context() as session:
        try:
            resp, prov_id = await provider_manager.chat_with_fallback(session, tenant_id, request)
            print("Success:", resp)
        except Exception as e:
            print("Failed:", repr(e))

asyncio.run(main())
