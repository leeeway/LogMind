"""
Chat Router — Conversational AI Diagnostics

SSE-based streaming chat endpoint for real-time AI diagnosis.
"""

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.alert.models import AlertHistory
from logmind.domain.analysis.models import LogAnalysisTask
from logmind.domain.chat.service import chat_service, _persist_session

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


class CreateSessionRequest(BaseModel):
    pass


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)


class SessionResponse(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


class RecommendationCard(BaseModel):
    id: str
    title: str
    prompt: str
    reason: str
    priority: str
    kind: str
    tone: str
    metric: str = ""
    expected_path: str = ""
    expected_steps: list[str] = Field(default_factory=list)


class RecommendationsResponse(BaseModel):
    generated_at: str
    window_minutes: int
    items: list[RecommendationCard]


@router.post("/sessions", response_model=SessionResponse)
async def create_session(session: DBSession, user: CurrentUser):
    """Create a new chat session."""
    session_id = str(uuid.uuid4())
    chat_session = await chat_service.get_or_create_session_persistent(
        session_id=session_id,
        tenant_id=user.tenant_id,
        user_id=user.sub,
        db_session=session,
    )
    return SessionResponse(
        id=chat_session.id,
        title=chat_session.title,
        message_count=0,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
    )


@router.get("/sessions")
async def list_sessions(db: DBSession, user: CurrentUser):
    """List chat sessions for current user."""
    sessions = await chat_service.list_sessions_persistent(user.tenant_id, user.sub, db)
    return {"sessions": sessions}


@router.get("/recommendations", response_model=RecommendationsResponse)
async def get_recommendations(
    db: DBSession,
    user: CurrentUser,
    window_minutes: int = 60,
    limit: int = 6,
):
    """Build real-time chat recommendations from recent alerts and active services."""
    window_minutes = min(max(window_minutes, 15), 240)
    limit = min(max(limit, 3), 8)
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    biz_lines = await chat_service._load_business_lines(user.tenant_id, db)
    biz_by_id = {b.id: b for b in biz_lines}
    cards: list[RecommendationCard] = []

    recent_rows = (
        await db.execute(
            select(AlertHistory, LogAnalysisTask.business_line_id)
            .outerjoin(LogAnalysisTask, AlertHistory.analysis_task_id == LogAnalysisTask.id)
            .where(
                AlertHistory.tenant_id == user.tenant_id,
                AlertHistory.fired_at >= since,
            )
            .order_by(AlertHistory.fired_at.desc())
            .limit(60)
        )
    ).all()

    severity_counts = Counter()
    alerts_by_service: dict[str, list[AlertHistory]] = defaultdict(list)
    message_counts: Counter[str] = Counter()

    for alert, biz_id in recent_rows:
        severity_counts[alert.severity or "info"] += 1
        if biz_id:
            alerts_by_service[biz_id].append(alert)
        if alert.message:
            message_counts[alert.message[:80]] += 1

    ranked_services = sorted(
        alerts_by_service.items(),
        key=lambda item: (
            sum(1 for a in item[1] if a.severity == "critical"),
            len(item[1]),
        ),
        reverse=True,
    )

    for biz_id, service_alerts in ranked_services[:3]:
        biz = biz_by_id.get(biz_id)
        if not biz:
            continue
        critical_count = sum(1 for a in service_alerts if a.severity == "critical")
        warning_count = sum(1 for a in service_alerts if a.severity == "warning")
        hottest = next((a for a in service_alerts if a.message), None)
        cards.append(RecommendationCard(
            id=f"service-{biz_id}",
            title=f"{biz.name} 正在升温",
            prompt=f"请分析 {biz.name} 最近 {window_minutes // 60 if window_minutes >= 60 else 1} 小时的异常、告警与影响范围，并给出下一步排查建议。",
            reason=(hottest.message[:60] if hottest and hottest.message else "最近告警频率上升"),
            priority="critical" if critical_count > 0 else "warning",
            kind="service",
            tone="live",
            metric=f"{critical_count} critical / {warning_count} warning",
            expected_path="服务错误诊断",
            expected_steps=["查告警", "看服务健康", "聚合错误模式", "给出升级建议"],
        ))

    for message_text, count in message_counts.most_common(2):
        if count < 2:
            continue
        cards.append(RecommendationCard(
            id=f"pattern-{abs(hash(message_text)) % 100000}",
            title="重复异常值得先问",
            prompt=f"请围绕这类异常继续排查：{message_text}。重点说明最近 {window_minutes} 分钟重复出现的原因、影响服务和修复建议。",
            reason=f"最近 {window_minutes} 分钟重复出现 {count} 次",
            priority="warning",
            kind="pattern",
            tone="spotlight",
            metric=f"{count} 次重复",
            expected_path="异常模式复核",
            expected_steps=["定位重复日志", "对比时间窗口", "确认影响范围", "沉淀降噪规则"],
        ))

    active_biz = [b for b in biz_lines if getattr(b, "is_active", True)]
    core_biz = [b for b in active_biz if getattr(b, "is_core_path", False)]
    fallback_services = core_biz[:2] or active_biz[:2]
    for biz in fallback_services:
        cards.append(RecommendationCard(
            id=f"health-{biz.id}",
            title=f"检查 {biz.name} 健康状态",
            prompt=f"请检查 {biz.name} 最近 1 小时的服务健康、错误趋势和潜在风险。",
            reason="核心业务建议持续巡检",
            priority="info",
            kind="health",
            tone="calm",
            metric="巡检",
            expected_path="服务健康巡检",
            expected_steps=["读取健康指标", "识别错误趋势", "检查最近告警", "输出值班建议"],
        ))

    cards.append(RecommendationCard(
        id="account-activity",
        title="追踪账号关键操作",
        prompt="请帮我查询账号 a8872123 在最近 1 小时做了哪些操作，并按时间线列出关键动作、异常和影响服务。",
        reason="账号轨迹类问题最适合直接发起动态诊断",
        priority="info",
        kind="account",
        tone="action",
        metric="账号",
        expected_path="账号回放",
        expected_steps=["查询操作时间线", "标注失败动作", "关联服务链路", "给出确认点"],
    ))

    deduped: list[RecommendationCard] = []
    seen_titles: set[str] = set()
    for card in cards:
        if card.title in seen_titles:
            continue
        seen_titles.add(card.title)
        deduped.append(card)
        if len(deduped) >= limit:
            break

    if not deduped:
        deduped = [
            RecommendationCard(
                id="fallback-errors",
                title="最近 1 小时有哪些关键错误？",
                prompt="最近 1 小时有哪些关键错误？请按严重程度和影响服务总结。",
                reason="暂无高优先级异常时，先看全局错误最稳妥",
                priority="info",
                kind="fallback",
                tone="calm",
                metric="全局",
                expected_path="全局侦察",
                expected_steps=["扫描关键错误", "按严重度排序", "聚焦服务", "建议下一步"],
            )
        ]

    return RecommendationsResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        window_minutes=window_minutes,
        items=deduped,
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, db: DBSession, user: CurrentUser):
    """Get a chat session with all messages."""
    s = await chat_service.get_session_persistent(session_id, db)
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
async def delete_session(session_id: str, db: DBSession, user: CurrentUser):
    """Delete a chat session."""
    s = await chat_service.get_session_persistent(session_id, db)
    if not s or s.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    await chat_service.delete_session_persistent(session_id, db)
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
    chat_session = await chat_service.get_or_create_session_persistent(
        session_id=session_id,
        tenant_id=user.tenant_id,
        user_id=user.sub,
        db_session=db,
    )

    # Build service list context
    biz_lines = await chat_service._load_business_lines(user.tenant_id, db)
    service_list_items = []
    for b in biz_lines:
        domain = ""
        idx = b.es_index_pattern or ""
        if ".gyyx.cn" in idx:
            d = idx.rstrip("*").rstrip("-")
            for pfx in ("master-", "develop-", ".ds-master-", ".ds-develop-"):
                if d.startswith(pfx):
                    d = d[len(pfx):]
                    break
            if ".gyyx.cn" in d:
                domain = d.split(".gyyx.cn")[0] + ".gyyx.cn"
        domain_hint = f", 域名: {domain}" if domain else ""
        service_list_items.append(
            f"- {b.name} (索引: {b.es_index_pattern}, 语言: {b.language}{domain_hint})"
        )
    service_list = "\n".join(service_list_items) or "暂无配置的业务线"

    async def stream_and_persist():
        async for chunk in chat_service.chat_stream(
            session=chat_session,
            user_message=req.content,
            db_session=db,
            service_list=service_list,
        ):
            yield chunk
        # Persist session after streaming completes
        try:
            await _persist_session(chat_session, db)
            await db.commit()
        except Exception:
            pass

    return StreamingResponse(
        stream_and_persist(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
