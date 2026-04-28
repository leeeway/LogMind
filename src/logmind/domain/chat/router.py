"""
Chat Router — Conversational AI Diagnostics

SSE-based streaming chat endpoint for real-time AI diagnosis.
"""

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.chat.service import chat_service
from logmind.domain.tenant.models import BusinessLine
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])
biz_repo = BaseRepository(BusinessLine)


class CreateSessionRequest(BaseModel):
    pass


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class SessionResponse(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


@router.post("/sessions", response_model=SessionResponse)
async def create_session(session: DBSession, user: CurrentUser):
    """Create a new chat session."""
    session_id = str(uuid.uuid4())
    chat_session = chat_service.get_or_create_session(
        session_id=session_id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )
    return SessionResponse(
        id=chat_session.id,
        title=chat_session.title,
        message_count=0,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
    )


@router.get("/sessions")
async def list_sessions(user: CurrentUser):
    """List chat sessions for current user."""
    sessions = chat_service.list_sessions(user.tenant_id, user.user_id)
    return {"sessions": sessions}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user: CurrentUser):
    """Get a chat session with all messages."""
    s = chat_service.get_session(session_id)
    if not s or s.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": s.id,
        "title": s.title,
        "messages": s.messages,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: CurrentUser):
    """Delete a chat session."""
    s = chat_service.get_session(session_id)
    if not s or s.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    chat_service.delete_session(session_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    req: SendMessageRequest,
    db: DBSession,
    user: CurrentUser,
):
    """
    Send a message and receive AI response via SSE stream.

    Response is a Server-Sent Events stream with events:
      - {"type": "tool_call", "name": "...", "args": {...}}
      - {"type": "tool_result", "name": "...", "result": "..."}
      - {"type": "token", "content": "..."}
      - {"type": "done"}
      - {"type": "error", "message": "..."}
    """
    chat_session = chat_service.get_or_create_session(
        session_id=session_id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )

    # Build service list context
    biz_lines = await biz_repo.get_all(db, tenant_id=user.tenant_id)
    service_list = "\n".join(
        f"- {b.name} (索引: {b.es_index_pattern}, 语言: {b.language})"
        for b in biz_lines
    ) or "暂无配置的业务线"

    return StreamingResponse(
        chat_service.chat_stream(
            session=chat_session,
            user_message=req.content,
            db_session=db,
            service_list=service_list,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
