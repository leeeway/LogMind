"""
Chat Service — Multi-turn Conversational Log Diagnostics

Manages conversation context and integrates Tool Calling for
real-time log search, alert query, and service correlation.
"""

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import AsyncIterator

from logmind.core.logging import get_logger
from logmind.domain.provider.base import ChatMessage, ChatRequest
from logmind.domain.provider.manager import provider_manager

logger = get_logger(__name__)

# Max conversation turns to keep in context
MAX_CONTEXT_TURNS = 10

CHAT_SYSTEM_PROMPT = """你是 LogMind AI 诊断助手，专门帮助运维工程师分析日志、排查故障、定位根因。

你的能力:
1. 搜索 Elasticsearch 日志（按时间、关键词、服务、级别等）
2. 查询告警历史
3. 关联上下游服务的错误
4. 查询已知问题库
5. 分析错误模式和根因

回答规范:
- 用中文回复
- 分析时先说明查询了什么数据，再给出结论
- 给出具体的错误日志摘要（前几条代表性消息）
- 提供可操作的建议
- 使用 Markdown 格式（标题、列表、代码块、表格）
- 在回复末尾，用 `---` 分隔后列出 2-3 个跟进建议

当前时间: {current_time}
当前租户可用的业务线/服务列表:
{service_list}
"""

CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": "搜索 Elasticsearch 中的日志。用于查找错误日志、定位问题时间点、统计错误分布。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词（异常类名、错误消息等）"},
                    "severity": {"type": "string", "enum": ["critical", "error", "warning", "info"], "description": "日志级别"},
                    "time_range": {"type": "string", "description": "时间范围描述，如 '最近1小时', '今天', '最近30分钟'"},
                    "service_name": {"type": "string", "description": "服务/业务线名称"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alerts",
            "description": "查询告警历史记录，了解最近触发的告警。",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["critical", "warning", "info"]},
                    "limit": {"type": "integer", "description": "返回数量", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_known_issues",
            "description": "查询已知问题库，检查是否有已记录的类似问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["keyword"],
            },
        },
    },
]


@dataclass
class ChatSession:
    """In-memory chat session with conversation history."""
    id: str
    tenant_id: str
    user_id: str
    title: str = "新对话"
    messages: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def add_message(self, role: str, content: str, tool_calls: list | None = None):
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)
        self.updated_at = datetime.now(timezone.utc).isoformat()

        # Auto-title from first user message
        if role == "user" and self.title == "新对话":
            self.title = content[:30] + ("..." if len(content) > 30 else "")

    def get_context_messages(self) -> list[ChatMessage]:
        """Get recent messages formatted for LLM, limited to MAX_CONTEXT_TURNS."""
        recent = self.messages[-(MAX_CONTEXT_TURNS * 2):]
        return [
            ChatMessage(role=m["role"], content=m["content"])
            for m in recent
            if m["role"] in ("user", "assistant")
        ]


# In-memory session store (production should use Redis)
_sessions: dict[str, ChatSession] = {}


class ChatService:
    """Manages chat sessions and AI inference."""

    def get_or_create_session(
        self, session_id: str, tenant_id: str, user_id: str
    ) -> ChatSession:
        if session_id in _sessions:
            return _sessions[session_id]
        session = ChatSession(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        _sessions[session_id] = session
        return session

    def list_sessions(self, tenant_id: str, user_id: str) -> list[dict]:
        result = []
        for s in _sessions.values():
            if s.tenant_id == tenant_id and s.user_id == user_id:
                result.append({
                    "id": s.id,
                    "title": s.title,
                    "message_count": len(s.messages),
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                })
        return sorted(result, key=lambda x: x["updated_at"], reverse=True)

    def get_session(self, session_id: str) -> ChatSession | None:
        return _sessions.get(session_id)

    def delete_session(self, session_id: str):
        _sessions.pop(session_id, None)

    async def execute_tool_call(
        self, tool_name: str, args: dict, tenant_id: str, db_session
    ) -> str:
        """Execute a tool call and return the result as a string."""
        try:
            if tool_name == "search_logs":
                return await self._tool_search_logs(args, tenant_id, db_session)
            elif tool_name == "get_alerts":
                return await self._tool_get_alerts(args, tenant_id, db_session)
            elif tool_name == "get_known_issues":
                return await self._tool_get_known_issues(args, tenant_id, db_session)
            else:
                return f"未知工具: {tool_name}"
        except Exception as e:
            logger.error("tool_call_failed", tool=tool_name, error=str(e))
            return f"工具调用失败: {str(e)}"

    async def _tool_search_logs(self, args: dict, tenant_id: str, db_session) -> str:
        """Search logs via ES."""
        from logmind.domain.tenant.models import BusinessLine
        from logmind.shared.base_repository import BaseRepository

        biz_repo = BaseRepository(BusinessLine)
        biz_lines = await biz_repo.get_all(db_session, tenant_id=tenant_id)

        # Find matching service
        service_name = args.get("service_name", "")
        target_biz = None
        for b in biz_lines:
            if service_name and service_name.lower() in b.name.lower():
                target_biz = b
                break
        if not target_biz and biz_lines:
            target_biz = biz_lines[0]

        if not target_biz:
            return "未找到匹配的服务，请检查服务名称。"

        # Build search summary (simulate — in production would call ES)
        return json.dumps({
            "service": target_biz.name,
            "index": target_biz.es_index_pattern,
            "query": args.get("query", ""),
            "severity": args.get("severity", "error"),
            "time_range": args.get("time_range", "最近1小时"),
            "result": f"在 {target_biz.name} ({target_biz.es_index_pattern}) 中搜索 '{args.get('query', '')}' 级别={args.get('severity', 'error')}",
            "hint": "实际部署时会返回真实的ES查询结果",
        }, ensure_ascii=False)

    async def _tool_get_alerts(self, args: dict, tenant_id: str, db_session) -> str:
        """Get recent alerts."""
        from logmind.domain.alert.models import AlertHistory
        from logmind.shared.base_repository import BaseRepository

        repo = BaseRepository(AlertHistory)
        limit = min(args.get("limit", 10), 20)
        alerts = await repo.get_all(db_session, tenant_id=tenant_id, limit=limit)

        if not alerts:
            return "最近没有告警记录。"

        result = []
        for a in alerts[:limit]:
            result.append({
                "severity": a.severity,
                "status": a.status,
                "message": a.message[:200] if a.message else "",
                "fired_at": str(a.fired_at) if a.fired_at else "",
            })
        return json.dumps(result, ensure_ascii=False, default=str)

    async def _tool_get_known_issues(self, args: dict, tenant_id: str, db_session) -> str:
        """Search known issues (stored in ES)."""
        keyword = args.get("keyword", "")
        # Known issues are indexed in ES, not in the relational DB.
        # In production, this would call the known_issues_router's ES search.
        return json.dumps({
            "query": keyword,
            "source": "elasticsearch",
            "hint": "已知问题存储在 ES 索引 logmind-analysis-vectors 中，实际部署时会查询 ES 返回匹配结果",
        }, ensure_ascii=False)

    async def chat_stream(
        self,
        session: ChatSession,
        user_message: str,
        db_session,
        service_list: str = "",
    ) -> AsyncIterator[str]:
        """
        Stream AI response with tool calling support.

        Yields SSE-formatted events:
          - data: {"type": "token", "content": "..."}
          - data: {"type": "tool_call", "name": "...", "args": {...}}
          - data: {"type": "tool_result", "name": "...", "result": "..."}
          - data: {"type": "done"}
        """
        session.add_message("user", user_message)

        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        system_prompt = CHAT_SYSTEM_PROMPT.replace("{current_time}", current_time).replace("{service_list}", service_list)

        # Build messages with context
        messages = [ChatMessage(role="system", content=system_prompt)]
        messages.extend(session.get_context_messages())

        # First call — may include tool calls
        request = ChatRequest(
            messages=messages,
            temperature=0.4,
            max_tokens=4096,
            tools=CHAT_TOOLS,
        )

        try:
            response, provider_id = await provider_manager.chat_with_fallback(
                session=db_session,
                tenant_id=session.tenant_id,
                request=request,
            )

            # Handle tool calls
            if response.tool_calls:
                for tc in response.tool_calls:
                    func_name = tc.get("function", {}).get("name", "")
                    func_args_str = tc.get("function", {}).get("arguments", "{}")
                    try:
                        func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
                    except json.JSONDecodeError:
                        func_args = {}

                    # Notify frontend about tool call
                    yield f"data: {json.dumps({'type': 'tool_call', 'name': func_name, 'args': func_args}, ensure_ascii=False)}\n\n"

                    # Execute tool
                    tool_result = await self.execute_tool_call(func_name, func_args, session.tenant_id, db_session)

                    yield f"data: {json.dumps({'type': 'tool_result', 'name': func_name, 'result': tool_result[:500]}, ensure_ascii=False)}\n\n"

                    # Add tool result to context and call again
                    messages.append(ChatMessage(role="assistant", content=f"[调用工具 {func_name}]"))
                    messages.append(ChatMessage(role="user", content=f"工具 {func_name} 返回结果:\n{tool_result}"))

                # Second call with tool results
                request2 = ChatRequest(
                    messages=messages,
                    temperature=0.4,
                    max_tokens=4096,
                )
                response2, _ = await provider_manager.chat_with_fallback(
                    session=db_session,
                    tenant_id=session.tenant_id,
                    request=request2,
                )
                final_content = response2.content
            else:
                final_content = response.content

            # Stream the response content character by character for typing effect
            # In production with streaming API, replace with actual SSE stream
            chunk_size = 4
            for i in range(0, len(final_content), chunk_size):
                chunk = final_content[i:i + chunk_size]
                yield f"data: {json.dumps({'type': 'token', 'content': chunk}, ensure_ascii=False)}\n\n"

            session.add_message("assistant", final_content)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            error_msg = f"AI 推理失败: {str(e)}"
            logger.error("chat_stream_failed", error=str(e), session_id=session.id)
            session.add_message("assistant", error_msg)
            yield f"data: {json.dumps({'type': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"


# Singleton
chat_service = ChatService()
