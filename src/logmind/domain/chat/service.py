"""
Chat Service v4.0 — Agentic Multi-turn Diagnosis

Manages conversation context and integrates real Agent Tools
with multi-round ReAct reasoning for autonomous log diagnosis.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import AsyncIterator

from logmind.core.logging import get_logger
from logmind.domain.provider.base import ChatMessage, ChatRequest
from logmind.domain.provider.manager import provider_manager

logger = get_logger(__name__)

# ── Configuration ────────────────────────────────────────
MAX_CONTEXT_TURNS = 10
MAX_TOOL_ROUNDS = 5  # Maximum ReAct reasoning loops
DIAGNOSIS_STAGES = ["侦察", "聚焦", "关联", "验证", "结论"]
ACCOUNT_FIELD_CANDIDATES = [
    "userId.keyword",
    "userId",
    "userName.keyword",
    "userName",
    "userid.keyword",
    "userid",
    "user_id.keyword",
    "user_id",
    "username.keyword",
    "username",
    "memberId.keyword",
    "memberId",
    "account.keyword",
    "account",
    "accountNo.keyword",
    "accountNo",
    "account_no.keyword",
    "account_no",
    "accountno.keyword",
    "accountno",
    "member_id.keyword",
    "member_id",
    "memberid.keyword",
    "memberid",
    "operator.keyword",
    "operator",
]

# ── Correlation ID extraction patterns ─────────────────
CORRELATION_ID_PATTERNS = [
    # traceId / requestId / correlationId
    re.compile(r'(?:traceId|TraceId|trace_id|trace-id)[=:\s]+([A-Za-z0-9\-_]{8,64})', re.IGNORECASE),
    re.compile(r'(?:requestId|RequestId|request_id|request-id|reqId|req_id)[=:\s]+([A-Za-z0-9\-_]{8,64})', re.IGNORECASE),
    re.compile(r'(?:correlationId|CorrelationId|correlation_id)[=:\s]+([A-Za-z0-9\-_]{8,64})', re.IGNORECASE),
    re.compile(r'(?:spanId|SpanId|span_id)[=:\s]+([A-Za-z0-9\-_]{8,64})', re.IGNORECASE),
    # Business IDs
    re.compile(r'(?:orderNo|orderId|order_no|order_id|订单号)[=:\s：]+([A-Za-z0-9\-_]{6,32})', re.IGNORECASE),
    re.compile(r'(?:transactionId|transId|trans_id|交易号|流水号)[=:\s：]+([A-Za-z0-9\-_]{6,32})', re.IGNORECASE),
    re.compile(r'(?:paymentId|payment_id|支付单号)[=:\s：]+([A-Za-z0-9\-_]{6,32})', re.IGNORECASE),
]

# Patterns to detect service call relationships in log messages
SERVICE_CALL_PATTERNS = [
    re.compile(r'(?:调用|请求|call|invoke|request)\s*[：:]\s*(\S+)', re.IGNORECASE),
    re.compile(r'(?:HTTP|http)\s+(?:GET|POST|PUT|DELETE)\s+(\S+)', re.IGNORECASE),
    re.compile(r'(?:Feign|feign|RestTemplate|HttpClient)\s*[：:→>]\s*(\S+)', re.IGNORECASE),
]

# Time proximity threshold for inferring causal links (seconds)
TRACE_TIME_PROXIMITY_SECONDS = 5

# ── System Prompt (ReAct Reasoning Template) ─────────────
CHAT_SYSTEM_PROMPT = """你是 LogMind AI 诊断助手 — 一个高级 SRE 级别的自主排查 Agent。

## 你的核心能力
你可以主动调用工具查询真实的 Elasticsearch 日志、告警记录、知识库等数据源。
你不会凭空捏造数据，而是通过多轮工具调用，像资深工程师一样逐步排查问题。

## 可用工具
### 日志查询类
- `search_logs` — 搜索 ES 日志（关键词、级别、时间范围、域名）
- `get_log_context` — 查看某个时间点前后的日志上下文
- `count_error_patterns` — 按类型/域名/时间聚合统计错误
- `list_available_indices` — 发现可搜索的 ES 索引

### 知识与历史
- `search_knowledge_base` — 搜索 RAG 知识库（SOP、故障报告）
- `search_similar_incidents` — 查找历史相似故障分析
- `search_cross_service_logs` — 跨服务日志关联

### 诊断辅助
- `get_alerts` — 查询告警历史
- `get_service_health` — 查询服务健康状态（错误率/QPS/趋势）
- `compare_time_windows` — 对比两个时间窗口的日志差异
- `trace_error_chain` — 追踪错误的上下游调用链
- `query_account_activity` — 查询账号在一段时间内的操作轨迹
- `query_operation_timeline` — 跨多个业务线按时间梳理账号/关键词相关操作链路
- `trace_linked_operations` — 智能链路追踪：自动提取 traceId/requestId/订单号等关联标识，跨服务追踪完整调用链路并按链路分组
- `predict_service_trend` — 预测服务未来30分钟的错误趋势（上升/平稳/下降），判断问题是否在恶化
- `create_analysis_task` — 创建深度分析任务

## ReAct 推理规范
每次排查请遵循以下推理框架，最多 {max_rounds} 轮：

**第1轮 — 侦察**: 先搜索日志或查询告警，了解问题全貌
**第2轮 — 聚焦**: 根据第1轮结果，缩小范围深入查看具体错误
**第3轮 — 关联**: 检查上下游服务是否有关联错误
**第4轮 — 验证**: 查阅知识库或历史相似故障确认根因
**第5轮 — 兜底**: 如果还无法确认，创建深度分析任务

每轮只调用必要的工具。如果信息已经足够，提前结束推理。

## 时区规范
- 用户所在时区为 **UTC+8（北京时间）**
- 当前北京时间: {current_time}
- 调用 search_logs 时，time_from 和 time_to 请使用 ISO 8601 格式并带上 +08:00 时区，例如: 2026-04-29T05:00:00+08:00
- 向用户展示时间时，统一使用北京时间（不要显示 UTC 时间）
- "最近几小时" 指从当前北京时间往前推算

## 搜索技巧
- 搜索中文关键词时，直接用原始中文词即可，如 “截断”、”异常”、”超时”
- 搜索 Java 异常时可用异常类名，如 “SQLServerException”、”NullPointerException”
- 使用 search_logs 时建议传入 domain 参数来指定站点精确过滤
- 如果第一次搜索无结果，尝试换关键词或去掉 severity 过滤
- 如果用户提到”账号/用户/手机号/会员号最近做了什么”，优先调用 `query_operation_timeline`
- 如果用户要求”整个业务线/多个业务线/相关链路按时间点梳理”，优先调用 `query_operation_timeline`
- 如果用户要求”查链路/追踪调用链/完整流程/trace/看请求经过了哪些服务”，优先调用 `trace_linked_operations`
- 如果用户提供了 traceId/requestId/订单号 等具体关联标识，优先调用 `trace_linked_operations`
- `trace_linked_operations` 会自动从日志中提取关联ID并跨服务追踪，返回按链路分组的结果

## 回答规范
- 用中文回复
- 先说明你做了哪些查询（工具调用摘要）
- 然后给出分析结论（根因、影响范围、严重程度）
- 最后提供可操作的修复建议
- 使用 Markdown 格式（标题、列表、代码块、表格）
- 在回复末尾用 `---` 分隔后列出 2-3 个跟进建议，每条单独一行
- 如果查到具体日志，用代码块展示关键条目
- 所有时间显示为北京时间

当前北京时间: {current_time}
当前租户可用的业务线/服务列表:
{service_list}
"""

# ── Tool Schemas (merged from agent_tools + new tools) ───
# Import real tool schemas from agent_tools
from logmind.domain.analysis.agent_tools import AGENT_TOOLS

CHAT_TOOLS = AGENT_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "get_alerts",
            "description": "查询告警历史记录，了解最近触发的告警、未解决的告警。",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["critical", "warning", "info"]},
                    "status": {"type": "string", "enum": ["fired", "acknowledged", "resolved"], "description": "告警状态"},
                    "limit": {"type": "integer", "description": "返回数量", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_health",
            "description": (
                "直接查询 ES 日志获取指定服务的实时健康状态：错误/告警日志数量、最近的报错样本、"
                "filetype 分布。用于快速判断某个服务是否有异常。返回的 recent_errors 包含最新的错误日志摘要。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "服务/业务线名称"},
                    "hours": {"type": "integer", "description": "查看时间范围（小时），默认 6", "default": 6},
                },
                "required": ["service_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_time_windows",
            "description": (
                "对比两个时间窗口的错误分布差异。例如对比今天和昨天、本小时和上小时。"
                "帮助判断问题是新出现的还是一直存在的。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "服务名称"},
                    "window_a": {"type": "string", "description": "第一个窗口描述，如 '最近1小时'"},
                    "window_b": {"type": "string", "description": "第二个窗口描述，如 '昨天同一时段'"},
                },
                "required": ["service_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_error_chain",
            "description": (
                "追踪一个错误消息的上下游服务调用链。"
                "当发现某个错误可能是由其他服务引起的时候使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "error_message": {"type": "string", "description": "错误消息关键词"},
                    "source_service": {"type": "string", "description": "错误发生的服务"},
                },
                "required": ["error_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_account_activity",
            "description": (
                "查询某个账号/用户在指定时间范围内的操作轨迹。"
                "用于回答“某个账号最近1小时做了哪些操作”这类动态问题。"
                "会尽量从 userId、userid、username、account、member_id、operator 和 message 中匹配。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "账号、用户ID、手机号或会员号"},
                    "hours": {"type": "integer", "description": "查看最近多少小时，默认 1", "default": 1},
                    "service_name": {"type": "string", "description": "可选，指定服务名称缩小范围"},
                    "action_keyword": {"type": "string", "description": "可选，按动作关键词过滤，如 登录 / 下单 / 支付"},
                    "size": {"type": "integer", "description": "最多返回多少条记录，默认 20，最大 50", "default": 20},
                },
                "required": ["account"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_operation_timeline",
            "description": (
                "跨多个业务线按时间线梳理账号、关键词、异常相关的操作链路。"
                "适合回答“整个业务线里这个账号做了什么”“按时间点梳理相关链路操作”这类问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "可选，账号、用户ID、手机号或会员号"},
                    "keyword": {"type": "string", "description": "可选，动作关键词或异常关键词"},
                    "hours": {"type": "integer", "description": "查看最近多少小时，默认 1", "default": 1},
                    "service_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选，指定多个业务线名称；不传则按 scope 自动选择",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["single", "selected", "core", "all"],
                        "description": "查询范围：单服务/多选服务/核心业务线/全部业务线，默认 core",
                    },
                    "include_related": {
                        "type": "boolean",
                        "description": "是否自动扩展所选业务线的上下游 related_services，默认 true",
                        "default": True,
                    },
                    "size": {"type": "integer", "description": "最多返回多少条记录，默认 40，最大 100", "default": 40},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "predict_service_trend",
            "description": (
                "预测指定服务未来30分钟的错误趋势。基于过去24小时的历史数据，"
                "使用加权线性回归分析趋势方向（上升/平稳/下降）和预计错误量。"
                "用于判断问题是否在恶化、是否需要提前干预。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "服务/业务线名称"},
                },
                "required": ["service_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_linked_operations",
            "description": (
                "智能链路追踪：输入账号或关键词，自动从日志中提取 traceId/requestId/订单号等关联标识，"
                "跨多个服务追踪同一操作流的完整调用链路。返回按链路分组的时间线，标注因果关系和错误节点。"
                "适合回答「查这个账号的完整链路」「追踪这个请求的调用链」「看看这个订单经过了哪些服务」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "账号、用户ID、手机号或会员号"},
                    "keyword": {"type": "string", "description": "可选，关键词（如订单号、traceId、异常关键词）"},
                    "hours": {"type": "integer", "description": "查看最近多少小时，默认 1", "default": 1},
                    "service_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选，指定业务线范围；不传则自动选择核心+关联业务线",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["single", "selected", "core", "all"],
                        "description": "查询范围，默认 core",
                    },
                    "size": {"type": "integer", "description": "最多返回多少条原始记录，默认 60", "default": 60},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_analysis_task",
            "description": (
                "创建一个深度分析任务，由 LogMind 分析引擎进行全面的日志诊断。"
                "当对话中无法快速定位根因时使用。分析完成后用户可在分析中心查看结果。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "要分析的服务"},
                    "description": {"type": "string", "description": "问题描述"},
                },
                "required": ["service_name", "description"],
            },
        },
    },
]


@dataclass
class ChatSession:
    """Chat session with conversation history, backed by DB persistence."""
    id: str
    tenant_id: str
    user_id: str
    title: str = "新对话"
    messages: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    _evidence_counter: int = field(default=0, repr=False)

    def add_message(
        self,
        role: str,
        content: str,
        tool_calls: list | None = None,
        metadata: dict | None = None,
    ):
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if metadata:
            msg["metadata"] = metadata
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

    def next_evidence_label(self) -> str:
        """Generate next evidence label: E-1, E-2, ..."""
        self._evidence_counter += 1
        return f"E-{self._evidence_counter}"


# In-memory cache (write-through to DB)
_sessions: dict[str, ChatSession] = {}


async def _persist_session(session: ChatSession, db_session) -> None:
    """Write-through: persist session state to DB."""
    from logmind.domain.chat.models import ChatConversation, ChatMessageRecord
    from sqlalchemy import select

    stmt = select(ChatConversation).where(ChatConversation.id == session.id)
    result = await db_session.execute(stmt)
    conv = result.scalar_one_or_none()

    if not conv:
        conv = ChatConversation(
            id=session.id,
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            title=session.title,
        )
        db_session.add(conv)
    else:
        conv.title = session.title

    # Sync messages: only add new ones
    existing_count_stmt = select(ChatMessageRecord).where(
        ChatMessageRecord.conversation_id == session.id
    )
    existing_result = await db_session.execute(existing_count_stmt)
    existing_count = len(existing_result.scalars().all())

    for idx, msg in enumerate(session.messages[existing_count:], start=existing_count):
        record = ChatMessageRecord(
            conversation_id=session.id,
            seq=idx,
            role=msg["role"],
            content=msg["content"],
            metadata_json=json.dumps(msg.get("metadata") or {}, ensure_ascii=False),
            evidence_refs=json.dumps(msg.get("evidence_refs") or [], ensure_ascii=False),
        )
        db_session.add(record)

    await db_session.flush()


async def _load_session_from_db(session_id: str, db_session) -> ChatSession | None:
    """Load a session from DB into memory cache."""
    from logmind.domain.chat.models import ChatConversation
    from sqlalchemy import select

    stmt = select(ChatConversation).where(ChatConversation.id == session_id)
    result = await db_session.execute(stmt)
    conv = result.scalar_one_or_none()
    if not conv:
        return None

    messages = []
    for msg_record in (conv.messages or []):
        msg = {
            "role": msg_record.role,
            "content": msg_record.content,
            "timestamp": msg_record.created_at.isoformat() if msg_record.created_at else "",
        }
        try:
            meta = json.loads(msg_record.metadata_json) if msg_record.metadata_json else {}
            if meta:
                msg["metadata"] = meta
        except (json.JSONDecodeError, TypeError):
            pass
        messages.append(msg)

    session = ChatSession(
        id=conv.id,
        tenant_id=conv.tenant_id,
        user_id=conv.user_id,
        title=conv.title,
        messages=messages,
        created_at=conv.created_at.isoformat() if conv.created_at else "",
        updated_at=conv.updated_at.isoformat() if conv.updated_at else "",
    )
    _sessions[session_id] = session
    return session


def _extract_error_core(msg: str, max_len: int = 500) -> str:
    """
    Smart extract error core from Java/C# log messages.

    Instead of blind truncation (which often cuts off key info like
    'SQLServerException: 将截断字符串或二进制数据'), this extracts:
    1. The exception description (method + error summary)
    2. Root cause Exception class + message
    3. SQL statement if present

    Example input (961 chars):
      [2026-04-29 11:12:32] [http-nio...] ... 执行GyyxUser_LoginLog方法发生异常,
      参数:userId:xxx,clientIP:xxx,ex:org.springframework.dao.DataIntegrityViolationException:
      ### Error updating database. Cause: com.microsoft.sqlserver.jdbc.SQLServerException:
      将截断字符串或二进制数据。 ### SQL: INSERT INTO ...

    Output (~200 chars):
      执行GyyxUser_LoginLog方法发生异常 | Cause: SQLServerException: 将截断字符串或二进制数据。
      | SQL: INSERT INTO community_login_log(...)
    """
    if not msg or len(msg) <= max_len:
        return msg

    import re
    parts = []

    # 1. Extract the error description (after "] - " prefix in Java logs)
    desc_match = re.search(r'\] - (.+?)(?:,参数:|,ex:|$)', msg)
    if desc_match:
        parts.append(desc_match.group(1).strip())

    # 2. Extract Cause / root exception (most important part!)
    # Look for patterns like "Cause: com.xxx.SomeException: error message"
    cause_matches = re.findall(
        r'(?:Cause|Caused by|caused by)[:\s]+(?:[\w.]+\.)?(\w+Exception[:\s]+[^\n#]+)',
        msg
    )
    if cause_matches:
        # Take the most specific cause (usually the last one)
        for cause in cause_matches:
            cause_clean = cause.strip().rstrip(';').strip()
            if cause_clean and cause_clean not in str(parts):
                parts.append(f"Cause: {cause_clean}")

    # 3. Extract SQL statement if present
    sql_match = re.search(r'###\s*SQL:\s*(.+?)(?:\s*###|$)', msg)
    if sql_match:
        sql = sql_match.group(1).strip()[:150]
        parts.append(f"SQL: {sql}")

    # 4. If we extracted structured parts, join them
    if parts:
        result = " | ".join(parts)
        return result[:max_len]

    # Fallback: just return more chars than before
    return msg[:max_len]


class ChatService:
    """Manages chat sessions and AI inference with multi-round ReAct reasoning."""

    @staticmethod
    def _extract_domain_from_index_pattern(index_pattern: str) -> str | None:
        if ".gyyx.cn" not in index_pattern:
            return None
        domain = index_pattern.rstrip("*").rstrip("-")
        for prefix in ("master-", "develop-", ".ds-master-", ".ds-develop-"):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
                break
        if ".gyyx.cn" not in domain:
            return None
        return domain.split(".gyyx.cn")[0] + ".gyyx.cn"

    @staticmethod
    def _service_lookup_keys(value: str | None) -> set[str]:
        """Build normalized lookup keys for service names, domains and index patterns."""
        if not value:
            return set()

        keys: set[str] = set()
        for raw_part in str(value).split(","):
            raw = raw_part.strip().lower()
            if not raw:
                continue

            compact = raw.replace("*", "").strip()
            keys.add(raw)
            keys.add(compact)

            normalized = compact
            normalized = re.sub(r"^\.ds-", "", normalized)
            normalized = re.sub(r"^(master|develop|prod|stage|release)-", "", normalized)
            if ".gyyx.cn" in normalized:
                normalized = normalized.split(".gyyx.cn", 1)[0] + ".gyyx.cn"
            keys.add(normalized)

            if ".gyyx.cn" in compact:
                domain = compact.split(".gyyx.cn", 1)[0] + ".gyyx.cn"
                keys.add(domain)

        return {key for key in keys if key}

    async def _load_business_lines(self, tenant_id: str, db_session) -> list:
        from logmind.domain.tenant.models import BusinessLine
        from logmind.shared.base_repository import BaseRepository

        biz_repo = BaseRepository(BusinessLine)
        items: list = []
        offset = 0
        page_size = 500

        while True:
            page = await biz_repo.get_all(
                db_session,
                tenant_id=tenant_id,
                offset=offset,
                limit=page_size,
                filters={"is_active": True},
            )
            items.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
            if len(items) >= 5000:
                logger.warning("business_line_load_capped", tenant_id=tenant_id, count=len(items))
                break

        return items

    def _resolve_business_line(self, biz_lines: list, service_name: str):
        if not service_name:
            return None

        query = service_name.strip().lower()
        if not query:
            return None
        query_keys = self._service_lookup_keys(query)

        for biz in biz_lines:
            name = (biz.name or "").lower()
            if query in name:
                return biz

        for biz in biz_lines:
            candidate_keys = set()
            candidate_keys.update(self._service_lookup_keys(biz.es_index_pattern or ""))
            candidate_keys.update(
                self._service_lookup_keys(
                    self._extract_domain_from_index_pattern(biz.es_index_pattern or "")
                )
            )
            for query_key in query_keys or {query}:
                for candidate_key in candidate_keys:
                    if query_key in candidate_key or candidate_key in query_key:
                        return biz

        for biz in biz_lines:
            domain = self._extract_domain_from_index_pattern(biz.es_index_pattern or "") or ""
            if query in domain.lower():
                return biz

        return None

    def _resolve_business_lines(self, biz_lines: list, service_names: list[str] | None, scope: str) -> list:
        if service_names:
            resolved = []
            seen_ids: set[str] = set()
            for service_name in service_names:
                biz = self._resolve_business_line(biz_lines, service_name)
                if biz and biz.id not in seen_ids:
                    resolved.append(biz)
                    seen_ids.add(biz.id)
            if resolved:
                return resolved

        active_lines = [biz for biz in biz_lines if getattr(biz, "is_active", True)]
        if scope == "all":
            return active_lines or biz_lines
        if scope == "core":
            core_lines = [biz for biz in active_lines if getattr(biz, "is_core_path", False)]
            return core_lines or active_lines[:8] or biz_lines[:8]
        return active_lines[:8] or biz_lines[:8]

    def _expand_related_business_lines(self, base_lines: list, all_lines: list) -> list:
        if not base_lines:
            return []
        by_id = {biz.id: biz for biz in all_lines}
        resolved = []
        seen_ids: set[str] = set()

        def add_biz(biz):
            if biz and biz.id not in seen_ids:
                resolved.append(biz)
                seen_ids.add(biz.id)

        for biz in base_lines:
            add_biz(biz)
            try:
                related = json.loads(biz.related_services) if biz.related_services else {}
            except (json.JSONDecodeError, TypeError):
                related = {}
            for related_id in (related.get("upstream", []) + related.get("downstream", [])):
                add_biz(by_id.get(related_id))

        return resolved

    @staticmethod
    def _to_beijing_time(value: str) -> str:
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            beijing_tz = timezone(timedelta(hours=8))
            return dt.astimezone(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value

    @staticmethod
    def _summarize_action_message(message: str) -> str:
        if not message:
            return ""
        condensed = " ".join(message.split())
        return condensed[:180] + ("..." if len(condensed) > 180 else "")

    @staticmethod
    def _extract_identity_value(source: dict) -> str:
        for key in ("userId", "userid", "user_id", "userName", "username", "account", "accountNo", "memberId", "member_id", "operator"):
            value = source.get(key)
            if value:
                return str(value)
        return ""

    def _extract_context_entities(self, text: str, biz_lines: list) -> dict[str, str]:
        context: dict[str, str] = {}
        normalized = text or ""

        account_match = re.search(
            r"(?:账号|账户|用户|userId|userid|memberId|memberid|手机号|手机号码)[：:\s]*([A-Za-z0-9_\-@.]{3,})",
            normalized,
            flags=re.IGNORECASE,
        )
        if not account_match:
            account_match = re.search(r"\b([1-9]\d{7,18})\b", normalized)
        if account_match:
            context["account"] = account_match.group(1)

        hours_match = re.search(r"最近\s*(\d+)\s*(小时|h)", normalized)
        if hours_match:
            context["hours"] = hours_match.group(1)

        keyword_match = re.search(r"(?:关键词|关键字|异常|操作|行为|查)\s*[：:]\s*([^\s，。；,;]{2,30})", normalized)
        if keyword_match:
            context["keyword"] = keyword_match.group(1)

        # Extract correlation IDs (traceId, orderId, etc.)
        trace_id_match = re.search(
            r"(?:traceId|trace_id|requestId|request_id|reqId)[=:\s：]+([A-Za-z0-9\-_]{8,64})",
            normalized, flags=re.IGNORECASE,
        )
        if trace_id_match:
            context["keyword"] = trace_id_match.group(1)

        order_id_match = re.search(
            r"(?:订单号|orderNo|orderId|order_no|交易号|transactionId|流水号)[=:\s：]+([A-Za-z0-9\-_]{6,32})",
            normalized, flags=re.IGNORECASE,
        )
        if order_id_match:
            context["keyword"] = order_id_match.group(1)

        for biz in biz_lines:
            if biz.name and biz.name in normalized:
                context["service_name"] = biz.name
                break
            domain = self._extract_domain_from_index_pattern(biz.es_index_pattern or "")
            if domain and domain in normalized:
                context["service_name"] = biz.name
                break

        if re.search(r"(整个业务线|多个业务线|全链路|相关链路|全部业务线)", normalized):
            context["scope"] = "all"

        return context

    @staticmethod
    def _extract_lookback_seconds(text: str) -> tuple[int, str]:
        normalized = text or ""
        match = re.search(
            r"(?:最近|最新|近|过去)\s*(\d+)\s*(分钟|分|min|minute|m|小时|时|h|hour|天|日|d|day)",
            normalized,
            flags=re.IGNORECASE,
        )
        if not match:
            if re.search(r"(?:最近|最新|近|过去)", normalized):
                return 3600, "最近 1 小时"
            return 3600, "最近 1 小时"

        value = max(1, int(match.group(1)))
        unit = match.group(2).lower()
        if unit in {"分钟", "分", "min", "minute", "m"}:
            return value * 60, f"最近 {value} 分钟"
        if unit in {"天", "日", "d", "day"}:
            return value * 86400, f"最近 {value} 天"
        return value * 3600, f"最近 {value} 小时"

    @staticmethod
    def _strip_log_keyword(value: str) -> str:
        cleaned = (value or "").strip().strip("\"'“”‘’`")
        cleaned = re.sub(r"(?:的)?(?:数据|日志|记录|内容|结果)$", "", cleaned).strip()
        cleaned = cleaned.strip("，,。；; ")
        return cleaned

    @classmethod
    def _extract_log_keyword(cls, text: str) -> str:
        normalized = text or ""
        patterns = [
            (
                r"(?:所有日志|全部日志|日志|message|内容)?\s*"
                r"(?:包含|含有|匹配|关键字|关键词)\s*[：:]?\s*[\"'“”‘’`]?"
                r"(.+?)(?:[\"'“”‘’`]?"
                r"(?:的?(?:数据|日志|记录|内容|结果)|[,，。；;]|\s+并|\s+然后|$))"
            ),
            (
                r"(?:搜索|搜|查询|查找|查)\s*[\"'“”‘’`]?"
                r"(.+?)(?:[\"'“”‘’`]?"
                r"(?:的?(?:数据|日志|记录|内容|结果)|[,，。；;]|\s+并|\s+然后|$))"
            ),
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if match:
                keyword = cls._strip_log_keyword(match.group(1))
                if keyword and not re.search(r"^(最近|最新|业务线|所有|全部)$", keyword):
                    return keyword
        return ""

    def _extract_service_query_from_text(self, text: str, biz_lines: list) -> str:
        normalized = text or ""
        domain_match = re.search(
            r"((?:\.ds-)?(?:(?:master|develop|prod|stage|release)-)?[A-Za-z0-9_.-]+\.gyyx\.cn)",
            normalized,
            flags=re.IGNORECASE,
        )
        if domain_match:
            return domain_match.group(1).strip()

        business_line_match = re.search(r"业务线\s*[：:]?\s*([^\s，,。；;]+)", normalized)
        if business_line_match:
            candidate = business_line_match.group(1)
            candidate = re.split(r"(?:所有日志|全部日志|日志|包含|关键字|关键词|最近|最新)", candidate)[0]
            if candidate:
                return candidate.strip()

        for biz in biz_lines:
            if biz.name and biz.name in normalized:
                return biz.name
        return ""

    @staticmethod
    def _extract_order_ids(message: str) -> list[str]:
        ids: list[str] = []
        patterns = [
            (
                r"(?:订单号|orderNo|orderId|order_no|order_id|交易号|transactionId|流水号)"
                r"[=:\s：]+([A-Za-z0-9\-_]{6,64})"
            ),
            r"\b((?:AliQr|ORD|ORDER|PAY|TXN|TRADE)[A-Za-z0-9\-_]{6,64})\b",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, message or "", flags=re.IGNORECASE):
                value = match.group(1).strip().strip("，,。；;")
                if value and value not in ids:
                    ids.append(value)
        return ids

    def _extract_direct_log_search_intent(self, text: str, biz_lines: list) -> dict | None:
        normalized = text or ""
        if not re.search(
            r"(日志|log|数据|包含|关键字|关键词|导出|搜索|查询|查找)",
            normalized,
            re.IGNORECASE,
        ):
            return None

        service_query = self._extract_service_query_from_text(normalized, biz_lines)
        target = self._resolve_business_line(biz_lines, service_query) if service_query else None
        keyword = self._extract_log_keyword(normalized)

        if not target or not keyword:
            return None

        lookback_seconds, time_label = self._extract_lookback_seconds(normalized)
        wants_export = bool(re.search(r"(导出|下载|csv|订单号|order)", normalized, re.IGNORECASE))
        size = 1000 if wants_export else 100
        size_match = re.search(r"(?:返回|显示|前)\s*(\d{1,5})\s*(?:条|行)?", normalized)
        if size_match:
            size = int(size_match.group(1))
        size = min(max(size, 1), 5000)

        severity = None
        if "所有日志" not in normalized and "全部日志" not in normalized:
            if re.search(r"(错误|异常|error|exception|失败)", normalized, re.IGNORECASE):
                severity = "error"
            elif re.search(r"(告警|警告|warning|warn)", normalized, re.IGNORECASE):
                severity = "warning"

        return {
            "service_query": service_query,
            "service_name": target.name,
            "index_pattern": target.es_index_pattern,
            "keyword": keyword,
            "lookback_seconds": lookback_seconds,
            "hours": max(1, (lookback_seconds + 3599) // 3600),
            "time_label": time_label,
            "size": size,
            "severity": severity,
            "wants_export": wants_export,
        }

    async def _execute_direct_log_search(
        self,
        intent: dict,
        session: ChatSession,
        db_session,
    ) -> tuple[dict, str]:
        from logmind.domain.log.schemas import LogQueryRequest
        from logmind.domain.log.service import log_service
        from logmind.domain.chat.models import DiagnosticEvidence
        import uuid as _uuid

        now = datetime.now(timezone.utc)
        since = now - timedelta(seconds=int(intent.get("lookback_seconds") or 3600))
        request = LogQueryRequest(
            index_pattern=intent["index_pattern"],
            time_from=since,
            time_to=now,
            query=intent["keyword"],
            severity=intent.get("severity"),
            size=intent.get("size") or 100,
            sort_order="desc",
        )
        result = await log_service.search_logs(request)

        logs: list[dict] = []
        order_rows: list[dict] = []
        seen_order_ids: set[str] = set()
        for log in result.logs:
            host = log.host_name or (log.raw.get("host", {}) or {}).get("name", "")
            entry = {
                "id": log.id,
                "timestamp": self._to_beijing_time(log.timestamp),
                "level": log.level,
                "message": log.message,
                "domain": log.domain,
                "filetype": log.filetype,
                "host": host,
            }
            logs.append(entry)
            for order_id in self._extract_order_ids(log.message):
                if order_id in seen_order_ids:
                    continue
                seen_order_ids.add(order_id)
                order_rows.append({
                    "order_id": order_id,
                    "timestamp": entry["timestamp"],
                    "service": intent["service_name"],
                    "domain": entry["domain"],
                    "host": entry["host"],
                    "filetype": entry["filetype"],
                })

        error_count = sum(
            1 for item in logs if item.get("level", "").lower() in {"error", "critical"}
        )
        payload = {
            "service": intent["service_name"],
            "index_pattern": intent["index_pattern"],
            "keyword": intent["keyword"],
            "time_range": intent["time_label"],
            "total_hits": result.total,
            "returned": len(logs),
            "error_count": error_count,
            "logs": logs,
            "order_ids": [row["order_id"] for row in order_rows],
            "order_export_rows": order_rows,
            "took_ms": result.took_ms,
        }

        evidence_label = session.next_evidence_label()
        try:
            evidence = DiagnosticEvidence(
                id=str(_uuid.uuid4()),
                conversation_id=session.id,
                label=evidence_label,
                tool_name="search_logs",
                tool_args=json.dumps(intent, ensure_ascii=False)[:2000],
                es_index_pattern=intent["index_pattern"],
                source_service=intent["service_name"],
                hit_count=result.total,
                error_count=error_count,
                result_preview=json.dumps(payload, ensure_ascii=False, default=str)[:1000],
                evidence_type="tool_result",
            )
            db_session.add(evidence)
        except Exception as e:
            logger.warning("direct_log_evidence_failed", error=str(e))

        return payload, evidence_label

    @staticmethod
    def _csv_escape(value: str) -> str:
        text = "" if value is None else str(value)
        if any(ch in text for ch in [",", "\"", "\n", "\r"]):
            return "\"" + text.replace("\"", "\"\"") + "\""
        return text

    def _format_direct_log_search_answer(self, result: dict) -> str:
        sample_logs = result.get("logs", [])[:5]
        order_rows = result.get("order_export_rows", [])
        order_preview = order_rows[:200]

        lines = [
            "## 查询结果",
            (
                f"已查询 `{result.get('service')}`（索引 `{result.get('index_pattern')}`）"
                f"{result.get('time_range')}内包含 `{result.get('keyword')}` 的日志。"
            ),
            (
                f"命中 {result.get('total_hits', 0)} 条，本次返回 "
                f"{result.get('returned', 0)} 条，耗时 {result.get('took_ms', 0)}ms。"
            ),
        ]

        if order_rows:
            csv_lines = ["order_id,timestamp,service,domain,host,filetype"]
            for row in order_preview:
                csv_lines.append(",".join([
                    self._csv_escape(row.get("order_id", "")),
                    self._csv_escape(row.get("timestamp", "")),
                    self._csv_escape(row.get("service", "")),
                    self._csv_escape(row.get("domain", "")),
                    self._csv_escape(row.get("host", "")),
                    self._csv_escape(row.get("filetype", "")),
                ]))
            lines.extend([
                "",
                "## 订单号导出",
                f"从返回日志中提取到 {len(order_rows)} 个唯一订单号。",
                "```csv",
                "\n".join(csv_lines),
                "```",
            ])
            if len(order_rows) > len(order_preview):
                lines.append(
                    f"上面展示前 {len(order_preview)} 个订单号；"
                    "可用跟进动作按相同条件继续生成更窄的 CSV。"
                )
        else:
            lines.extend([
                "",
                "## 订单号导出",
                "这批返回日志里没有提取到订单号字段。",
            ])

        if sample_logs:
            log_lines = []
            for item in sample_logs:
                message = " ".join((item.get("message") or "").split())
                if len(message) > 700:
                    message = message[:700] + "..."
                log_lines.append(
                    f"[{item.get('timestamp')}] [{item.get('level')}] "
                    f"{item.get('host') or item.get('domain') or '-'} {message}"
                )
            lines.extend([
                "",
                "## 样本日志",
                "```text",
                "\n".join(log_lines),
                "```",
            ])
        else:
            lines.extend([
                "",
                "## 样本日志",
                "未查到匹配日志。建议确认业务线、时间窗口或关键词是否一致。",
            ])

        lines.extend([
            "",
            "---",
            f"扩大时间窗口继续查 `{result.get('keyword')}`",
            f"只导出 `{result.get('service')}` 的订单号 CSV",
            "按 UserAgent 或主机维度聚合这批命中日志",
        ])
        return "\n".join(lines)

    @staticmethod
    def _looks_like_account_activity_request(text: str) -> bool:
        normalized = text or ""
        account_hint = re.search(
            r"(账号|账户|用户|userId|userid|memberId|memberid|手机号|手机号码|会员号)",
            normalized,
            flags=re.IGNORECASE,
        )
        action_hint = re.search(
            r"(操作|做了什么|轨迹|记录|登录|激活|下单|支付|行为)",
            normalized,
            flags=re.IGNORECASE,
        )
        return bool(account_hint and action_hint)

    @staticmethod
    def _looks_like_trace_request(text: str) -> bool:
        """Detect if user is asking for trace/linked operations analysis."""
        normalized = text or ""
        trace_hint = re.search(
            r"(链路|调用链|完整流程|追踪|trace|traceId|requestId|"
            r"经过了哪些服务|跨服务追踪|关联链路|请求链|调用路径|全链路)",
            normalized,
            flags=re.IGNORECASE,
        )
        return bool(trace_hint)

    @staticmethod
    def _looks_like_multi_agent_request(text: str) -> bool:
        """Detect if user is asking for comprehensive multi-agent analysis."""
        normalized = text or ""
        return bool(re.search(
            r"(全面分析|深度诊断|彻底排查|完整分析|所有可能|多角度|综合排查|全方位)",
            normalized,
            flags=re.IGNORECASE,
        ))

    @staticmethod
    def _looks_like_smart_search(text: str) -> bool:
        """Detect if user input is a short search query (account/order/keyword)."""
        normalized = (text or "").strip()
        # Short input (under 30 chars) that looks like an identifier or keyword
        if len(normalized) > 60 or len(normalized) < 2:
            return False
        # Pure identifier (no Chinese sentence structure)
        if re.match(r"^[A-Za-z0-9_\-@.]{3,64}$", normalized):
            return True
        # Short keyword (1-3 Chinese words, no sentence)
        if re.match(r"^[一-鿿]{2,8}$", normalized):
            return True
        # "搜索/查/搜 XXX" pattern
        if re.match(r"^(搜索|搜|查|查询|找)\s*.{2,30}$", normalized):
            return True
        return False

    # ── Smart Search Engine ──────────────────────────────

    KEYWORD_SYNONYMS = {
        "NPE": ["NullPointerException", "NPE", "空指针", "null pointer"],
        "OOM": ["OutOfMemoryError", "OOM", "内存溢出", "heap space", "out of memory"],
        "超时": ["超时", "timeout", "Timeout", "timed out", "TimeoutException", "SocketTimeout"],
        "连接": ["连接", "connection", "Connection refused", "连接超时", "ConnectionException"],
        "死锁": ["死锁", "deadlock", "Deadlock", "lock wait timeout"],
        "SQL": ["SQLServerException", "SQLException", "SQL Error", "数据库异常"],
        "截断": ["截断", "truncat", "将截断字符串或二进制数据", "String or binary data would be truncated"],
        "权限": ["权限", "permission", "denied", "unauthorized", "403", "Forbidden"],
        "404": ["404", "Not Found", "找不到", "路由不存在"],
        "500": ["500", "Internal Server Error", "服务器内部错误"],
    }

    @staticmethod
    def _classify_search_input(query: str) -> dict:
        """Classify user search input into type + expanded keywords."""
        query = query.strip()
        if not query:
            return {"type": "keyword", "value": query, "expanded_keywords": [query]}

        # Pure numeric 8-18 digits → account/phone
        if re.match(r"^\d{8,18}$", query):
            return {"type": "account", "value": query, "expanded_keywords": [query]}

        # traceId/requestId format (hex or uuid-like)
        if re.match(r"^[0-9a-f\-]{16,64}$", query, re.IGNORECASE):
            return {"type": "trace_id", "value": query, "expanded_keywords": [query]}

        # Order/transaction ID patterns
        if re.match(r"^(ORD|TXN|PAY|ORDER|TRADE)[_\-]?\w{4,}", query, re.IGNORECASE):
            return {"type": "order_id", "value": query, "expanded_keywords": [query]}

        # Error keyword detection
        if re.search(r"(Exception|Error|异常|失败|错误|Fault|Panic)", query, re.IGNORECASE):
            return {"type": "error", "value": query, "expanded_keywords": [query]}

        # Check synonym expansion
        for key, synonyms in ChatService.KEYWORD_SYNONYMS.items():
            if query.lower() in [s.lower() for s in synonyms] or query.lower() == key.lower():
                return {"type": "error", "value": query, "expanded_keywords": synonyms}

        return {"type": "keyword", "value": query, "expanded_keywords": [query]}

    async def _smart_search(
        self,
        query: str,
        hours: int,
        scope: str,
        tenant_id: str,
        db_session,
    ) -> dict:
        """
        Smart search: classify input, search across all business lines,
        generate diagnostic clues.
        """
        import asyncio
        from logmind.domain.log.service import log_service

        classification = self._classify_search_input(query)
        input_type = classification["type"]
        expanded = classification["expanded_keywords"]

        biz_lines = await self._load_business_lines(tenant_id, db_session)
        targets = self._resolve_business_lines(biz_lines, None, scope)
        targets = self._expand_related_business_lines(targets, biz_lines)

        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=hours)

        # Parallel search across all target services
        all_hits: list[dict] = []
        services_scanned: list[str] = []
        error_entries: list[dict] = []

        for target in targets:
            should_clauses = []
            for kw in expanded:
                should_clauses.append({"match_phrase": {"message": kw}})
            if input_type == "account":
                for field_name in ACCOUNT_FIELD_CANDIDATES:
                    should_clauses.append({"term": {field_name: query}})

            body = {
                "query": {
                    "bool": {
                        "must": [{"bool": {"should": should_clauses, "minimum_should_match": 1}}],
                        "filter": [{"range": {"@timestamp": {"gte": since.isoformat(), "lte": now.isoformat()}}}],
                    }
                },
                "sort": [{"@timestamp": {"order": "desc"}}],
                "size": 20,
                "_source": [
                    "@timestamp", "message", "gy.domain", "gy.filetype",
                    "gy.hostname", "host.name",
                    "userId", "userid", "user_id", "userName", "username",
                    "account", "accountNo", "memberId", "member_id", "operator",
                ],
            }

            try:
                result = await log_service.es.search(index=target.es_index_pattern, body=body)
                hits = result.get("hits", {}).get("hits", [])
                if hits:
                    services_scanned.append(target.name)
                for hit in hits:
                    source = hit.get("_source", {})
                    gy_info = source.get("gy", {}) or {}
                    entry = {
                        "time": self._to_beijing_time(source.get("@timestamp", "")),
                        "service": target.name,
                        "domain": gy_info.get("domain", ""),
                        "filetype": gy_info.get("filetype", ""),
                        "host": gy_info.get("hostname") or source.get("host", {}).get("name", ""),
                        "identity": self._extract_identity_value(source),
                        "action": self._summarize_action_message(source.get("message", "")),
                        "level": self._infer_log_level({"filetype": gy_info.get("filetype", ""), "action": source.get("message", "")}),
                    }
                    all_hits.append(entry)
                    if entry["level"] == "error":
                        error_entries.append(entry)
            except Exception as e:
                logger.warning("smart_search_service_failed", service=target.name, error=str(e))

        all_hits.sort(key=lambda x: x.get("time", ""), reverse=True)

        # Generate diagnostic clues
        clues = self._generate_diagnostic_clues(all_hits, error_entries, services_scanned, hours)

        return {
            "query": query,
            "input_type": input_type,
            "expanded_keywords": expanded,
            "time_range": f"最近 {hours} 小时",
            "scope": scope,
            "total_hits": len(all_hits),
            "error_count": len(error_entries),
            "services_involved": services_scanned,
            "services_total": len(targets),
            "clues": clues,
            "timeline": all_hits[:30],
            "summary": (
                f"搜索 \"{query}\" 在 {len(targets)} 个业务线中命中 {len(all_hits)} 条记录"
                f"（{len(error_entries)} 条错误），涉及 {len(services_scanned)} 个服务。"
            ),
        }

    def _generate_diagnostic_clues(
        self,
        all_hits: list[dict],
        error_entries: list[dict],
        services_scanned: list[str],
        hours: int,
    ) -> list[dict]:
        """Generate structured diagnostic clues from search results."""
        clues: list[dict] = []

        if not all_hits:
            clues.append({
                "severity": "info",
                "title": "未找到相关记录",
                "detail": f"在最近 {hours} 小时内未搜索到匹配的日志。",
                "affected_services": [],
                "time_range": "",
                "suggestion": "尝试扩大时间范围或更换关键词。",
            })
            return clues

        # Clue 1: Error concentration by service
        error_by_service: dict[str, int] = {}
        for entry in error_entries:
            svc = entry.get("service", "unknown")
            error_by_service[svc] = error_by_service.get(svc, 0) + 1

        for svc, count in sorted(error_by_service.items(), key=lambda x: -x[1]):
            if count >= 3:
                sample = next((e for e in error_entries if e.get("service") == svc), {})
                clues.append({
                    "severity": "critical" if count >= 10 else "warning",
                    "title": f"{svc} 出现 {count} 条错误",
                    "detail": f"错误样本: {sample.get('action', '')[:120]}",
                    "affected_services": [svc],
                    "time_range": "",
                    "suggestion": f"建议深入排查 {svc} 的错误日志，检查是否有配置变更或资源瓶颈。",
                })

        # Clue 2: Time burst detection
        if error_entries:
            time_buckets: dict[str, int] = {}
            for entry in error_entries:
                t = entry.get("time", "")[:16]  # group by minute
                if t:
                    time_buckets[t] = time_buckets.get(t, 0) + 1

            burst_minutes = [(t, c) for t, c in time_buckets.items() if c >= 3]
            if burst_minutes:
                burst_minutes.sort(key=lambda x: -x[1])
                top_burst = burst_minutes[0]
                clues.append({
                    "severity": "warning",
                    "title": f"错误集中爆发于 {top_burst[0]}",
                    "detail": f"该分钟内出现 {top_burst[1]} 条错误，可能是瞬时故障或批量操作触发。",
                    "affected_services": list(error_by_service.keys())[:3],
                    "time_range": top_burst[0],
                    "suggestion": "对比该时间点前后的日志，查看是否有部署或配置变更。",
                })

        # Clue 3: Multi-service cascade
        if len(error_by_service) >= 3:
            clues.append({
                "severity": "critical",
                "title": f"疑似级联故障 — {len(error_by_service)} 个服务同时报错",
                "detail": f"涉及服务: {', '.join(list(error_by_service.keys())[:5])}",
                "affected_services": list(error_by_service.keys()),
                "time_range": "",
                "suggestion": "多服务同时报错通常由上游依赖故障引起，建议追踪链路定位源头。",
            })

        # Clue 4: Correlation IDs found
        cid_count = 0
        for entry in all_hits[:20]:
            cids = self._extract_correlation_ids(entry.get("action", ""))
            if cids:
                cid_count += 1
        if cid_count >= 2:
            clues.append({
                "severity": "info",
                "title": f"发现 {cid_count} 条记录包含关联标识",
                "detail": "日志中包含 traceId/requestId/orderId 等关联标识，可进一步追踪完整链路。",
                "affected_services": services_scanned[:3],
                "time_range": "",
                "suggestion": "使用链路追踪功能查看完整调用链路和错误传播路径。",
            })

        # Clue 5: No errors found (positive signal)
        if not error_entries and all_hits:
            clues.append({
                "severity": "info",
                "title": f"找到 {len(all_hits)} 条记录，无错误",
                "detail": "所有匹配记录均为正常级别日志，未发现异常。",
                "affected_services": services_scanned,
                "time_range": "",
                "suggestion": "如果仍怀疑有问题，可尝试扩大时间范围或搜索相关错误关键词。",
            })

        # Sort by severity
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        clues.sort(key=lambda c: severity_order.get(c["severity"], 9))

        return clues[:6]

    def _build_suggested_actions(self, user_message: str, final_content: str, biz_lines: list) -> list[dict]:
        context = self._extract_context_entities(f"{user_message}\n{final_content}", biz_lines)
        actions: list[dict] = []

        def add_action(label: str, prompt: str, kind: str = "follow_up"):
            if not prompt.strip():
                return
            if any(existing["prompt"] == prompt for existing in actions):
                return
            actions.append({"label": label, "prompt": prompt, "kind": kind})

        account = context.get("account")
        service_name = context.get("service_name")
        hours = context.get("hours", "1")

        if account:
            add_action(
                "继续查账号轨迹",
                f"请继续查询账号 {account} 最近 {hours} 小时的完整操作轨迹，并按时间线列出关键动作。",
            )
            add_action(
                "跨业务线查链路",
                f"请在整个业务线范围内按时间线梳理账号 {account} 最近 {hours} 小时的相关操作链路，并标记涉及服务。",
                kind="diagnose",
            )
            add_action(
                "查账号异常",
                f"请检查账号 {account} 最近 {hours} 小时是否伴随异常、失败或告警，并指出影响服务。",
            )

        if service_name:
            add_action(
                "查看服务健康",
                f"请检查 {service_name} 最近 6 小时的服务健康状态，并总结错误趋势。",
                kind="diagnose",
            )
            add_action(
                "创建深度分析",
                f"请为 {service_name} 当前问题创建一个深度分析任务，并说明分析目标。",
                kind="task",
            )

        add_action("查询最近告警", "请查询最近 1 小时的重要告警，并按严重程度排序。", kind="diagnose")

        follow_ups = []
        divider_idx = final_content.rfind("---")
        if divider_idx > -1:
            for line in final_content[divider_idx + 3:].splitlines():
                cleaned = re.sub(r"^[\s\-\d.*]+", "", line).strip()
                if 4 <= len(cleaned) <= 80 and not cleaned.startswith("#"):
                    follow_ups.append(cleaned)
        for item in follow_ups[:3]:
            add_action(item[:16], item)

        return actions[:4]

    def _infer_diagnosis_path(self, user_message: str) -> str:
        text = user_message.lower()
        if self._looks_like_multi_agent_request(user_message):
            return "multi_agent"
        if self._looks_like_trace_request(user_message):
            return "trace"
        if self._looks_like_account_activity_request(user_message):
            return "account_replay"
        if self._looks_like_smart_search(user_message):
            return "smart_search"
        if any(word in text for word in ["error", "exception", "timeout", "错误", "异常", "失败", "超时", "告警"]):
            return "service_error"
        return "general"

    def _stage_payload(
        self,
        case_state: dict,
        stage: str,
        status: str = "running",
        summary: str = "",
    ) -> dict:
        stage_index = DIAGNOSIS_STAGES.index(stage) if stage in DIAGNOSIS_STAGES else 0
        case_state.update({
            "stage": stage,
            "stage_index": stage_index,
            "status": status,
        })
        if summary:
            case_state["stage_summary"] = summary
        return {
            "type": "diagnosis_state",
            "stage": stage,
            "stage_index": stage_index,
            "total_stages": len(DIAGNOSIS_STAGES),
            "status": status,
            "summary": summary or case_state.get("stage_summary", ""),
            "question": case_state.get("question", ""),
            "confidence": case_state.get("confidence", 0),
            "path": case_state.get("path", "general"),
        }

    def _round_stage(self, round_num: int) -> str:
        if round_num <= 1:
            return "侦察"
        if round_num == 2:
            return "聚焦"
        if round_num == 3:
            return "关联"
        if round_num == 4:
            return "验证"
        return "结论"

    def _safe_parse_tool_result(self, result: str) -> dict:
        try:
            parsed = json.loads(result)
            return parsed if isinstance(parsed, dict) else {"items": parsed}
        except (TypeError, json.JSONDecodeError):
            return {"raw": result}

    def _infer_result_counts(self, parsed: dict) -> tuple[int, int]:
        hit_count = parsed.get("count", 0) or parsed.get("total_hits", 0)
        for key in ("timeline", "activities", "alerts", "trace_segments", "results", "items"):
            value = parsed.get(key)
            if not hit_count and isinstance(value, list):
                hit_count = len(value)
        error_count = parsed.get("error_count", 0) or parsed.get("failed_count", 0)
        if not error_count:
            for key in ("alerts", "timeline", "activities"):
                value = parsed.get(key)
                if isinstance(value, list):
                    error_count += sum(
                        1
                        for item in value
                        if isinstance(item, dict)
                        and str(item.get("severity") or item.get("level") or item.get("status") or "").lower()
                        in {"critical", "error", "warning", "failed", "failure"}
                    )
        return int(hit_count or 0), int(error_count or 0)

    def _summarize_tool_result_structured(
        self,
        tool_name: str,
        args: dict,
        result: str,
        evidence_label: str = "",
    ) -> dict:
        parsed = self._safe_parse_tool_result(result)
        hit_count, error_count = self._infer_result_counts(parsed)
        supporting: list[str] = []
        counter: list[str] = []
        confidence_delta = 8

        if evidence_label:
            if parsed.get("error") or hit_count == 0:
                counter.append(evidence_label)
            else:
                supporting.append(evidence_label)

        target = (
            args.get("service_name")
            or args.get("domain")
            or args.get("account")
            or args.get("keyword")
            or "当前问题"
        )

        if parsed.get("error"):
            hypothesis = f"{target} 的工具查询失败，当前只能保持低置信度判断。"
            summary = str(parsed.get("error"))[:160]
            confidence_delta = -8
        elif hit_count == 0:
            hypothesis = f"暂未找到 {target} 的直接异常证据，需要扩大时间窗或更换关键词确认。"
            summary = "该轮查询未返回可引用证据。"
            confidence_delta = -4
        elif tool_name in {"trace_linked_operations", "trace_error_chain", "search_cross_service_logs"}:
            hypothesis = f"已形成 {target} 的跨服务链路证据，优先检查错误传播路径和首个异常节点。"
            summary = f"发现 {hit_count} 条链路/关联记录，其中 {error_count} 条指向异常。"
            confidence_delta = 18 if error_count else 10
        elif tool_name in {"query_operation_timeline", "query_account_activity"}:
            hypothesis = f"已定位到 {target} 的操作时间线，可按时间顺序回放触发点。"
            summary = f"时间线命中 {hit_count} 条记录，异常相关 {error_count} 条。"
            confidence_delta = 14 if error_count else 7
        elif tool_name in {"get_alerts", "get_service_health", "count_error_patterns"}:
            hypothesis = f"{target} 的告警/健康数据正在支持当前异常判断。"
            summary = f"命中 {hit_count} 条健康或告警证据，异常计数 {error_count}。"
            confidence_delta = 16 if error_count else 8
        elif tool_name in {"search_knowledge_base", "search_similar_incidents"}:
            hypothesis = f"已找到可对照的历史知识，适合进入根因验证。"
            summary = f"知识检索命中 {hit_count} 条可参考材料。"
            confidence_delta = 12
        else:
            hypothesis = f"已获取 {target} 的日志证据，正在判断是否集中于同一根因。"
            summary = f"命中 {hit_count} 条记录，异常相关 {error_count} 条。"
            confidence_delta = 12 if error_count else 6

        return {
            "hypothesis": hypothesis,
            "summary": summary,
            "supporting_evidence": supporting,
            "counter_evidence": counter,
            "confidence_delta": confidence_delta,
            "hit_count": hit_count,
            "error_count": error_count,
        }

    def _apply_hypothesis_update(self, case_state: dict, update: dict) -> dict:
        support = list(dict.fromkeys(
            list(case_state.get("supporting_evidence", [])) + update.get("supporting_evidence", [])
        ))
        counter = list(dict.fromkeys(
            list(case_state.get("counter_evidence", [])) + update.get("counter_evidence", [])
        ))
        confidence = max(5, min(95, int(case_state.get("confidence", 15)) + int(update.get("confidence_delta", 0))))
        if update.get("hit_count", 0) == 0:
            confidence = min(confidence, 35)
        evidence_summaries = list(case_state.get("evidence_summaries", []))
        if update.get("summary"):
            evidence_summaries.append({
                "label": (update.get("supporting_evidence") or update.get("counter_evidence") or [""])[0],
                "summary": update["summary"],
            })
        case_state.update({
            "hypothesis": update.get("hypothesis") or case_state.get("hypothesis", ""),
            "confidence": confidence,
            "supporting_evidence": support,
            "counter_evidence": counter,
            "evidence_summaries": evidence_summaries[-8:],
            "impact_scope": self._infer_impact_scope(update, case_state),
            "missing_confirmations": self._infer_missing_confirmations(update, case_state),
        })
        return {
            "type": "hypothesis_update",
            "hypothesis": case_state["hypothesis"],
            "confidence": confidence,
            "supporting_evidence": support,
            "counter_evidence": counter,
            "evidence_summaries": case_state["evidence_summaries"],
            "impact_scope": case_state["impact_scope"],
            "missing_confirmations": case_state["missing_confirmations"],
        }

    def _infer_impact_scope(self, update: dict, case_state: dict) -> str:
        if case_state.get("path") == "trace" and update.get("hit_count", 0) > 0:
            return "涉及跨服务调用链，需以首个异常节点为准。"
        if update.get("error_count", 0) >= 10:
            return "错误较集中，建议按服务级故障优先处理。"
        if update.get("error_count", 0) > 0:
            return "已发现异常证据，影响范围仍需继续确认。"
        return case_state.get("impact_scope") or "暂无明确影响范围。"

    def _infer_missing_confirmations(self, update: dict, case_state: dict) -> list[str]:
        gaps: list[str] = []
        if update.get("hit_count", 0) == 0:
            gaps.append("扩大时间窗口或更换关键词，确认是否存在遗漏证据")
        if case_state.get("confidence", 0) < 55:
            gaps.append("补充一条反向查询，排除偶发噪声或误报")
        if case_state.get("path") in {"trace", "service_error"} and update.get("error_count", 0) > 0:
            gaps.append("确认首个异常节点是否早于下游报错")
        if case_state.get("path") == "account_replay":
            gaps.append("确认账号关键动作前后是否存在失败响应或风控拦截")
        if not gaps:
            gaps.append("确认变更、发布或依赖状态是否与异常时间点重合")
        return gaps[:3]

    def _build_decision_actions(
        self,
        user_message: str,
        biz_lines: list,
        diagnosis_path: str,
        case_state: dict,
        final_content: str = "",
    ) -> list[dict]:
        actions: list[dict] = []
        context = self._extract_context_entities(f"{user_message}\n{final_content}", biz_lines)
        service_name = context.get("service_name") or "当前服务"
        account = context.get("account")
        hours = context.get("hours", "1")

        def add(action_id: str, label: str, prompt: str, kind: str, description: str = ""):
            if any(item["id"] == action_id for item in actions):
                return
            actions.append({
                "id": action_id,
                "label": label,
                "prompt": prompt,
                "kind": kind,
                "description": description,
            })

        if diagnosis_path == "trace":
            add(
                "continue-trace",
                "继续查链路",
                f"请沿着当前证据继续追踪 {service_name} 的上下游链路，找出首个异常服务和传播路径。",
                "diagnose",
                "复用链路追踪能力继续收敛根因。",
            )
        if diagnosis_path == "account_replay" or account:
            target = account or "该账号"
            add(
                "account-replay",
                "账号回放",
                f"请按时间线回放账号 {target} 最近 {hours} 小时的关键操作，并标记失败、告警和关联服务。",
                "follow_up",
                "继续用时间线确认触发点。",
            )
        add(
            "create-analysis",
            "创建深度分析",
            f"请为 {service_name} 当前问题创建一个深度分析任务，目标是验证根因假设：{case_state.get('hypothesis', '')}",
            "task",
            "证据不足或影响扩大时升级为异步分析。",
        )
        add(
            "open-incident",
            "进入故障作战室",
            "请基于当前诊断结论创建或进入故障作战室，并保留证据链。",
            "incident",
            "用于升级协同处理。",
        )
        add(
            "postmortem-draft",
            "生成复盘草稿",
            (
                "请基于当前诊断案件生成一份故障复盘草稿，包含时间线、影响范围、根因假设、"
                "已确认/待确认证据、短期止血和长期改进。"
            ),
            "follow_up",
            "把诊断链转成可复用的复盘资产。",
        )
        add(
            "copy-report",
            "复制诊断报告",
            "",
            "copy",
            "复制结论、证据链和建议动作。",
        )
        return actions[:5]

    def _format_expert_answer(self, content: str, case_state: dict, actions: list[dict]) -> str:
        if "## 结论摘要" in content or "# 结论摘要" in content:
            return content
        evidence = case_state.get("evidence_summaries", [])
        evidence_lines = "\n".join(
            f"- {item.get('label') or '未编号'}: {item.get('summary', '')}" for item in evidence
        ) or "- 暂无直接证据，需要继续确认。"
        action_lines = "\n".join(
            f"- {action['label']}: {action.get('description') or action.get('prompt') or '可复制当前报告。'}"
            for action in actions
            if action.get("kind") != "copy"
        ) or "- 继续补充查询以验证当前假设。"
        confirmations = case_state.get("missing_confirmations") or [
            "首个异常出现的准确时间点",
            "异常是否集中在单服务、单账号、单链路或单部署批次",
            "当前证据中的反例是否能排除",
        ]
        confirm_lines = "\n".join(f"- {item}" for item in confirmations)
        return (
            f"## 结论摘要\n{content.strip()}\n\n"
            f"## 证据链\n{evidence_lines}\n\n"
            f"## 影响范围\n{case_state.get('impact_scope') or '暂无明确影响范围。'}\n\n"
            f"## 置信度\n{case_state.get('confidence', 0)}% - "
            f"{case_state.get('hypothesis', '仍在收集证据。')}\n\n"
            f"## 建议动作\n{action_lines}\n\n"
            f"## 我还需要确认什么\n{confirm_lines}"
        )

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

    async def get_or_create_session_persistent(
        self, session_id: str, tenant_id: str, user_id: str, db_session
    ) -> ChatSession:
        """Get from cache or load from DB; create if not exists."""
        if session_id in _sessions:
            return _sessions[session_id]
        # Try loading from DB
        loaded = await _load_session_from_db(session_id, db_session)
        if loaded:
            return loaded
        # Create new
        session = ChatSession(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        _sessions[session_id] = session
        await _persist_session(session, db_session)
        return session

    async def list_sessions_persistent(self, tenant_id: str, user_id: str, db_session) -> list[dict]:
        """List sessions from DB."""
        from logmind.domain.chat.models import ChatConversation
        from sqlalchemy import select, func
        from logmind.domain.chat.models import ChatMessageRecord

        stmt = (
            select(ChatConversation)
            .where(
                ChatConversation.tenant_id == tenant_id,
                ChatConversation.user_id == user_id,
                ChatConversation.status == "active",
            )
            .order_by(ChatConversation.updated_at.desc())
            .limit(50)
        )
        result = await db_session.execute(stmt)
        conversations = result.scalars().all()

        sessions = []
        for conv in conversations:
            msg_count = len(conv.messages) if conv.messages else 0
            sessions.append({
                "id": conv.id,
                "title": conv.title,
                "message_count": msg_count,
                "created_at": conv.created_at.isoformat() if conv.created_at else "",
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else "",
            })
        return sessions

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

    async def get_session_persistent(self, session_id: str, db_session) -> ChatSession | None:
        """Get session from cache or DB."""
        if session_id in _sessions:
            return _sessions[session_id]
        return await _load_session_from_db(session_id, db_session)

    def get_session(self, session_id: str) -> ChatSession | None:
        return _sessions.get(session_id)

    async def delete_session_persistent(self, session_id: str, db_session) -> None:
        """Mark session as archived in DB and remove from cache."""
        from logmind.domain.chat.models import ChatConversation
        from sqlalchemy import select

        _sessions.pop(session_id, None)
        stmt = select(ChatConversation).where(ChatConversation.id == session_id)
        result = await db_session.execute(stmt)
        conv = result.scalar_one_or_none()
        if conv:
            conv.status = "archived"
            await db_session.flush()

    def delete_session(self, session_id: str):
        _sessions.pop(session_id, None)

    # ── Real Tool Execution ──────────────────────────────
    async def execute_tool_call(
        self, tool_name: str, args: dict, tenant_id: str, db_session,
        es_index_pattern: str = "*",
    ) -> str:
        """Execute a tool call using real Agent Tools or built-in tools."""
        try:
            # Agent tools (real ES queries)
            agent_tool_names = {
                "search_logs", "get_log_context", "count_error_patterns",
                "list_available_indices", "search_knowledge_base",
                "search_similar_incidents", "search_cross_service_logs",
            }

            if tool_name in agent_tool_names:
                from logmind.domain.analysis.agent_tools import execute_tool
                now = datetime.now(timezone.utc)
                # Respect explicit time_from/time_to first.
                if args.get("time_from") or args.get("time_to"):
                    time_from = None
                    time_to = None
                else:
                    hours = args.get("hours", 6)
                    if isinstance(hours, str):
                        try:
                            hours = int(hours)
                        except ValueError:
                            hours = 6
                    hours = min(max(hours, 1), 24)
                    time_from = now - timedelta(hours=hours)
                    time_to = now
                return await execute_tool(
                    tool_name=tool_name,
                    arguments=args,
                    es_index_pattern=es_index_pattern,
                    time_from=time_from,
                    time_to=time_to,
                )

            # Built-in tools
            if tool_name == "get_alerts":
                return await self._tool_get_alerts(args, tenant_id, db_session)
            elif tool_name == "get_service_health":
                return await self._tool_get_service_health(args, tenant_id, db_session)
            elif tool_name == "compare_time_windows":
                return await self._tool_compare_time_windows(args, tenant_id, db_session)
            elif tool_name == "trace_error_chain":
                return await self._tool_trace_error_chain(args, tenant_id, db_session)
            elif tool_name == "query_account_activity":
                return await self._tool_query_account_activity(
                    args, tenant_id, db_session, es_index_pattern
                )
            elif tool_name == "query_operation_timeline":
                return await self._tool_query_operation_timeline(
                    args, tenant_id, db_session
                )
            elif tool_name == "trace_linked_operations":
                return await self._tool_trace_linked_operations(
                    args, tenant_id, db_session
                )
            elif tool_name == "predict_service_trend":
                return await self._tool_predict_service_trend(
                    args, tenant_id, db_session
                )
            elif tool_name == "create_analysis_task":
                return await self._tool_create_analysis_task(args, tenant_id, db_session)
            else:
                return json.dumps({"error": f"未知工具: {tool_name}"})
        except Exception as e:
            logger.error("tool_call_failed", tool=tool_name, error=str(e))
            return json.dumps({"error": f"工具调用失败: {str(e)}"})

    async def execute_tool_call_with_evidence(
        self,
        tool_name: str,
        args: dict,
        tenant_id: str,
        db_session,
        session: "ChatSession",
        es_index_pattern: str = "*",
    ) -> tuple[str, str]:
        """
        Execute tool call and create a DiagnosticEvidence record.
        Returns (tool_result, evidence_label).
        """
        result = await self.execute_tool_call(
            tool_name, args, tenant_id, db_session, es_index_pattern
        )

        # Generate evidence record
        label = session.next_evidence_label()

        try:
            from logmind.domain.chat.models import DiagnosticEvidence
            import uuid as _uuid

            # Parse result to extract hit/error counts
            hit_count = 0
            error_count = 0
            try:
                parsed = json.loads(result)
                hit_count = parsed.get("count", 0) or parsed.get("total_hits", 0) or len(parsed.get("timeline", []))
                error_count = parsed.get("error_count", 0)
                if not hit_count and isinstance(parsed.get("activities"), list):
                    hit_count = len(parsed["activities"])
                if not hit_count and isinstance(parsed.get("alerts"), list):
                    hit_count = len(parsed["alerts"])
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

            evidence = DiagnosticEvidence(
                id=str(_uuid.uuid4()),
                conversation_id=session.id,
                label=label,
                tool_name=tool_name,
                tool_args=json.dumps(args, ensure_ascii=False)[:2000],
                es_index_pattern=es_index_pattern,
                source_service=args.get("service_name", "") or args.get("domain", ""),
                hit_count=hit_count,
                error_count=error_count,
                result_preview=result[:1000],
                evidence_type="tool_result",
            )
            db_session.add(evidence)
        except Exception as e:
            logger.warning("evidence_creation_failed", tool=tool_name, error=str(e))

        return result, label

    async def _tool_get_alerts(self, args: dict, tenant_id: str, db_session) -> str:
        """Get recent alerts."""
        from logmind.domain.alert.models import AlertHistory
        from logmind.shared.base_repository import BaseRepository

        repo = BaseRepository(AlertHistory)
        limit = min(args.get("limit", 10), 20)
        alerts = await repo.get_all(db_session, tenant_id=tenant_id, limit=limit)

        if not alerts:
            return json.dumps({"message": "最近没有告警记录。", "count": 0}, ensure_ascii=False)

        result = []
        for a in alerts[:limit]:
            result.append({
                "severity": a.severity,
                "status": a.status,
                "message": a.message[:200] if a.message else "",
                "fired_at": str(a.fired_at) if a.fired_at else "",
            })
        return json.dumps({"alerts": result, "count": len(result)}, ensure_ascii=False, default=str)

    async def _tool_get_service_health(self, args: dict, tenant_id: str, db_session) -> str:
        """Get service health status — queries ES directly for real-time metrics."""
        service_name = args.get("service_name", "")
        hours = min(args.get("hours", 6), 24)
        biz_lines = await self._load_business_lines(tenant_id, db_session)

        # Find matching service (fuzzy)
        target = self._resolve_business_line(biz_lines, service_name)

        if not target:
            return json.dumps({
                "error": f"未找到服务 '{service_name}'",
                "available_services": [b.name for b in biz_lines[:10]],
            }, ensure_ascii=False)

        # ── Query ES directly for real-time health ────────────
        index_pattern = target.es_index_pattern if target.es_index_pattern else "*"

        # Extract gy.domain from index pattern or field_mapping
        # Pattern like "master-stage-account-login-service.gyyx.cn*"
        # → domain = "stage-account-login-service.gyyx.cn"
        domain_filter = None
        try:
            fm = json.loads(target.field_mapping) if target.field_mapping else {}
            domain_filter = fm.get("domain")
        except (json.JSONDecodeError, TypeError):
            pass

        if not domain_filter:
            domain_filter = self._extract_domain_from_index_pattern(index_pattern)

        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=hours)

        try:
            from logmind.domain.log.service import log_service

            # 1) Count total + error logs by filetype
            filter_clauses = [
                {"range": {"@timestamp": {"gte": since.isoformat(), "lte": now.isoformat()}}},
            ]
            if domain_filter:
                filter_clauses.append({"term": {"gy.domain.keyword": domain_filter}})

            body = {
                "size": 0,
                "query": {"bool": {"filter": filter_clauses}},
                "aggs": {
                    "by_filetype": {
                        "terms": {"field": "gy.filetype.keyword", "size": 20}
                    },
                    "error_logs": {
                        "filter": {
                            "bool": {
                                "should": [
                                    {"term": {"gy.filetype.keyword": "error.log"}},
                                    {"term": {"gy.filetype.keyword": "warn.log"}},
                                    {"match_phrase": {"message": "Exception"}},
                                    {"match_phrase": {"message": "异常"}},
                                    {"match_phrase": {"message": "[ERROR]"}},
                                    {"match_phrase": {"message": "[FATAL]"}},
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    },
                    "recent_errors": {
                        "filter": {
                            "bool": {
                                "should": [
                                    {"term": {"gy.filetype.keyword": "error.log"}},
                                    {"term": {"gy.filetype.keyword": "warn.log"}},
                                    {"match_phrase": {"message": "Exception"}},
                                    {"match_phrase": {"message": "异常"}},
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                        "aggs": {
                            "top_errors": {
                                "top_hits": {
                                    "size": 5,
                                    "sort": [{"@timestamp": {"order": "desc"}}],
                                    "_source": ["message", "@timestamp", "gy.filetype"],
                                }
                            }
                        }
                    }
                }
            }

            result = await log_service.es.search(index=index_pattern, body=body)

            total_hits = result["hits"]["total"]["value"]
            error_count = result["aggregations"]["error_logs"]["doc_count"]
            filetype_buckets = result["aggregations"]["by_filetype"]["buckets"]
            recent_error_hits = result["aggregations"]["recent_errors"]["top_errors"]["hits"]["hits"]

            # Format filetype distribution
            filetype_dist = {b["key"]: b["doc_count"] for b in filetype_buckets}

            # Format recent error samples — smart extraction
            recent_errors = []
            for hit in recent_error_hits:
                src = hit["_source"]
                msg = src.get("message", "")
                # Smart extract: find Exception/Cause messages instead of blind truncation
                extracted = _extract_error_core(msg)
                recent_errors.append({
                    "time": src.get("@timestamp", ""),
                    "filetype": src.get("gy", {}).get("filetype", ""),
                    "message": extracted,
                })

            # Determine status
            if error_count > 50:
                status = "critical"
            elif error_count > 10:
                status = "warning"
            elif error_count > 0:
                status = "attention"
            else:
                status = "healthy"

            return json.dumps({
                "service": target.name,
                "index_pattern": index_pattern,
                "domain": domain_filter or "N/A",
                "time_range": f"过去 {hours} 小时",
                "total_logs": total_hits,
                "error_and_warning_logs": error_count,
                "filetype_distribution": filetype_dist,
                "status": status,
                "recent_errors": recent_errors,
                "diagnosis_hint": (
                    f"发现 {error_count} 条错误/告警日志。"
                    + (f"最新错误摘要: {recent_errors[0]['message'][:200]}" if recent_errors else "")
                ),
            }, ensure_ascii=False, default=str)

        except Exception as e:
            logger.warning("es_health_query_failed", service=service_name, error=str(e))
            # Fallback: return basic info + hint to use search_logs
            return json.dumps({
                "service": target.name,
                "index_pattern": index_pattern,
                "time_range": f"过去 {hours} 小时",
                "error": f"ES 查询异常: {str(e)[:100]}",
                "fallback_hint": "请使用 search_logs 工具直接查询日志",
            }, ensure_ascii=False)

    async def _tool_compare_time_windows(self, args: dict, tenant_id: str, db_session) -> str:
        """Compare error distributions between two time windows."""
        service_name = args.get("service_name", "")
        biz_lines = await self._load_business_lines(tenant_id, db_session)
        target = self._resolve_business_line(biz_lines, service_name)

        if not target:
            return json.dumps({"error": f"未找到服务 '{service_name}'"}, ensure_ascii=False)

        # Use agent_tools search_logs for both windows
        from logmind.domain.analysis.agent_tools import execute_tool
        now = datetime.now(timezone.utc)

        result_a = await execute_tool(
            "count_error_patterns", {"group_by": "filetype"},
            target.es_index_pattern,
            now - timedelta(hours=1), now,
        )
        result_b = await execute_tool(
            "count_error_patterns", {"group_by": "filetype"},
            target.es_index_pattern,
            now - timedelta(hours=2), now - timedelta(hours=1),
        )

        return json.dumps({
            "service": target.name,
            "window_a": {"label": args.get("window_a", "最近1小时"), "data": json.loads(result_a)},
            "window_b": {"label": args.get("window_b", "上一小时"), "data": json.loads(result_b)},
        }, ensure_ascii=False)

    async def _tool_trace_error_chain(self, args: dict, tenant_id: str, db_session) -> str:
        """Trace error across services."""
        from logmind.domain.analysis.agent_tools import execute_tool

        error_message = args.get("error_message", "")
        source = args.get("source_service", "")

        # Cross-service search
        result = await execute_tool(
            "search_cross_service_logs",
            {"keyword": error_message, "service_name": source, "minutes_back": 30},
            "*",
        )
        return result

    async def _collect_operation_timeline(
        self,
        tenant_id: str,
        db_session,
        *,
        account: str = "",
        keyword: str = "",
        hours: int = 1,
        service_names: list[str] | None = None,
        scope: str = "core",
        include_related: bool = True,
        size: int = 40,
    ) -> dict:
        from logmind.domain.log.service import log_service

        biz_lines = await self._load_business_lines(tenant_id, db_session)
        targets = self._resolve_business_lines(biz_lines, service_names, scope)
        if include_related and scope in {"single", "selected", "core"}:
            targets = self._expand_related_business_lines(targets, biz_lines)
        if not targets:
            return {
                "time_range": f"最近 {hours} 小时",
                "services_scanned": [],
                "count": 0,
                "timeline": [],
                "summary": "没有可查询的业务线。",
            }

        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=hours)
        size_per_service = max(10, min(30, size // max(len(targets), 1) + 8))

        timeline: list[dict] = []
        scanned_services: list[str] = []
        related_expanded: list[str] = []

        if include_related and len(targets) > len(service_names or []):
            related_expanded = [
                biz.name for biz in targets
                if not service_names or biz.name not in service_names
            ]

        for target in targets:
            should_clauses = []
            if account:
                should_clauses.extend([
                    {"match_phrase": {"message": account}},
                    {
                        "query_string": {
                            "query": f"*{account}*",
                            "fields": ["message"],
                            "analyze_wildcard": True,
                        }
                    },
                ])
                for field in ACCOUNT_FIELD_CANDIDATES:
                    should_clauses.append({"term": {field: account}})
            if keyword:
                should_clauses.extend([
                    {"match_phrase": {"message": keyword}},
                    {
                        "query_string": {
                            "query": f"*{keyword}*",
                            "fields": ["message"],
                            "analyze_wildcard": True,
                        }
                    },
                ])

            if not should_clauses:
                continue

            body = {
                "query": {
                    "bool": {
                        "must": [{
                            "bool": {
                                "should": should_clauses,
                                "minimum_should_match": 1,
                            }
                        }],
                        "filter": [{
                            "range": {"@timestamp": {"gte": since.isoformat(), "lte": now.isoformat()}}
                        }],
                    }
                },
                "sort": [{"@timestamp": {"order": "asc"}}],
                "size": size_per_service,
                "_source": [
                    "@timestamp",
                    "message",
                    "gy.domain",
                    "gy.filetype",
                    "gy.hostname",
                    "host.name",
                    "userId",
                    "userid",
                    "user_id",
                    "userName",
                    "username",
                    "account",
                    "accountNo",
                    "memberId",
                    "member_id",
                    "operator",
                ],
            }

            result = await log_service.es.search(index=target.es_index_pattern, body=body)
            hits = result.get("hits", {}).get("hits", [])
            if hits:
                scanned_services.append(target.name)
            for hit in hits:
                source = hit.get("_source", {})
                gy_info = source.get("gy", {}) or {}
                timeline.append({
                    "time": self._to_beijing_time(source.get("@timestamp", "")),
                    "service": target.name,
                    "domain": gy_info.get("domain", "") or self._extract_domain_from_index_pattern(target.es_index_pattern or "") or "",
                    "filetype": gy_info.get("filetype", ""),
                    "host": gy_info.get("hostname") or source.get("host", {}).get("name", ""),
                    "identity": self._extract_identity_value(source),
                    "action": self._summarize_action_message(source.get("message", "")),
                })

        timeline.sort(key=lambda item: item["time"])
        timeline = timeline[:size]

        summary_parts = []
        if account:
            summary_parts.append(f"账号 {account}")
        if keyword:
            summary_parts.append(f"关键词 {keyword}")
        scope_label = "全部业务线" if scope == "all" else "核心业务线" if scope == "core" else "指定业务线"

        return {
            "account": account,
            "keyword": keyword,
            "time_range": f"最近 {hours} 小时",
            "scope": scope,
            "scope_label": scope_label,
            "services_scanned": scanned_services or [service.name for service in targets],
            "related_expanded": related_expanded,
            "count": len(timeline),
            "timeline": timeline,
            "summary": (
                f"{' / '.join(summary_parts) or '条件'} 在 {scope_label} 中命中 {len(timeline)} 条记录，"
                f"涉及 {len(scanned_services or targets)} 个业务线。"
            ),
        }

    # ── Trace Correlation Engine ─────────────────────────

    @staticmethod
    def _extract_correlation_ids(message: str) -> list[str]:
        """Extract traceId, requestId, orderId etc. from a log message."""
        ids = []
        for pattern in CORRELATION_ID_PATTERNS:
            for match in pattern.finditer(message or ""):
                value = match.group(1).strip()
                if value and len(value) >= 6:
                    prefix = pattern.pattern.split("(")[0].split("|")[0]
                    prefix = re.sub(r'[^a-zA-Z]', '', prefix)[:10]
                    ids.append(f"{prefix}={value}")
        return ids

    @staticmethod
    def _detect_service_calls(message: str) -> list[str]:
        """Detect service call targets mentioned in a log message."""
        targets = []
        for pattern in SERVICE_CALL_PATTERNS:
            for match in pattern.finditer(message or ""):
                target = match.group(1).strip().rstrip(",;。）)")
                if target and len(target) > 2:
                    targets.append(target)
        return targets

    @staticmethod
    def _infer_log_level(entry: dict) -> str:
        """Infer log level from filetype or message content."""
        filetype = (entry.get("filetype") or "").lower()
        if "error" in filetype:
            return "error"
        if "warn" in filetype:
            return "warning"

        action = (entry.get("action") or "").lower()
        if any(kw in action for kw in ("exception", "error", "失败", "异常", "fatal", "严重")):
            return "error"
        if any(kw in action for kw in ("warn", "warning", "告警", "超时", "timeout")):
            return "warning"
        return "info"

    def _group_into_trace_segments(
        self, timeline: list[dict], service_topology: dict[str, dict]
    ) -> tuple[list[dict], list[dict]]:
        """
        Group flat timeline entries into trace segments using correlation IDs
        and time-proximity + service dependency inference.
        """
        # Step 1: Enrich entries with correlation IDs and level
        for entry in timeline:
            entry["correlation_ids"] = self._extract_correlation_ids(entry.get("action", ""))
            entry["call_targets"] = self._detect_service_calls(entry.get("action", ""))
            entry["level"] = self._infer_log_level(entry)

        # Step 2: Group by shared correlation IDs
        id_to_entries: dict[str, list[int]] = {}
        for idx, entry in enumerate(timeline):
            for cid in entry["correlation_ids"]:
                id_to_entries.setdefault(cid, []).append(idx)

        # Build union-find for merging entries that share any correlation ID
        parent = list(range(len(timeline)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for indices in id_to_entries.values():
            for i in range(1, len(indices)):
                union(indices[0], indices[i])

        # Step 3: Time-proximity inference for uncorrelated entries
        uncorrelated_indices = [i for i in range(len(timeline)) if not timeline[i]["correlation_ids"]]
        for i in uncorrelated_indices:
            entry_i = timeline[i]
            time_i = entry_i.get("time", "")
            service_i = entry_i.get("service", "")
            downstream_i = set(service_topology.get(service_i, {}).get("downstream", []))

            for j in range(len(timeline)):
                if i == j or find(i) == find(j):
                    continue
                entry_j = timeline[j]
                service_j = entry_j.get("service", "")
                time_j = entry_j.get("time", "")

                if service_j not in downstream_i and service_i not in set(
                    service_topology.get(service_j, {}).get("downstream", [])
                ):
                    continue

                try:
                    dt_i = datetime.strptime(time_i, "%Y-%m-%d %H:%M:%S")
                    dt_j = datetime.strptime(time_j, "%Y-%m-%d %H:%M:%S")
                    delta = abs((dt_j - dt_i).total_seconds())
                    if delta <= TRACE_TIME_PROXIMITY_SECONDS:
                        union(i, j)
                except ValueError:
                    continue

        # Step 4: Collect groups
        groups: dict[int, list[int]] = {}
        for i in range(len(timeline)):
            root = find(i)
            groups.setdefault(root, []).append(i)

        # Step 5: Build trace segments
        segments = []
        uncorrelated = []
        seg_counter = 0

        for root, indices in sorted(groups.items(), key=lambda x: timeline[x[1][0]].get("time", "")):
            entries = [timeline[i] for i in sorted(indices, key=lambda i: timeline[i].get("time", ""))]

            if len(entries) == 1 and not entries[0]["correlation_ids"]:
                uncorrelated.append(entries[0])
                continue

            seg_counter += 1
            all_cids = []
            for e in entries:
                all_cids.extend(e["correlation_ids"])
            unique_cids = list(dict.fromkeys(all_cids))

            has_error = any(e["level"] == "error" for e in entries)
            start_time = entries[0].get("time", "")
            end_time = entries[-1].get("time", "")

            duration_ms = 0
            try:
                dt_start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                dt_end = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
                duration_ms = int((dt_end - dt_start).total_seconds() * 1000)
            except ValueError:
                pass

            error_summary = ""
            if has_error:
                error_entries = [e for e in entries if e["level"] == "error"]
                if error_entries:
                    first_err = error_entries[0]
                    error_summary = f"{first_err.get('service', '')} — {first_err.get('action', '')[:80]}"

            nodes = []
            for e in entries:
                nodes.append({
                    "time": e.get("time", ""),
                    "service": e.get("service", ""),
                    "action": e.get("action", ""),
                    "level": e["level"],
                    "host": e.get("host", ""),
                    "domain": e.get("domain", ""),
                    "identity": e.get("identity", ""),
                    "call_targets": e.get("call_targets", []),
                })

            segments.append({
                "segment_id": f"seg_{seg_counter}",
                "correlation_ids": unique_cids,
                "start_time": start_time,
                "end_time": end_time,
                "duration_ms": duration_ms,
                "has_error": has_error,
                "error_summary": error_summary,
                "node_count": len(nodes),
                "nodes": nodes,
            })

        return segments, uncorrelated

    def _build_service_topology(self, targets: list, all_lines: list) -> dict[str, dict]:
        """Build service topology map from BusinessLine.related_services."""
        by_id = {biz.id: biz for biz in all_lines}
        topology: dict[str, dict] = {}

        for biz in targets:
            try:
                related = json.loads(biz.related_services) if biz.related_services else {}
            except (json.JSONDecodeError, TypeError):
                related = {}

            upstream_names = [by_id[uid].name for uid in related.get("upstream", []) if uid in by_id]
            downstream_names = [by_id[did].name for did in related.get("downstream", []) if did in by_id]

            topology[biz.name] = {
                "upstream": upstream_names,
                "downstream": downstream_names,
            }

        return topology

    async def _trace_linked_operations(
        self,
        tenant_id: str,
        db_session,
        *,
        account: str = "",
        keyword: str = "",
        hours: int = 1,
        service_names: list[str] | None = None,
        scope: str = "core",
        size: int = 60,
    ) -> dict:
        """
        Advanced trace linking: collect timeline, extract correlation IDs,
        do secondary queries to fill gaps, then group into trace segments.
        """
        from logmind.domain.log.service import log_service

        biz_lines = await self._load_business_lines(tenant_id, db_session)
        targets = self._resolve_business_lines(biz_lines, service_names, scope)
        targets = self._expand_related_business_lines(targets, biz_lines)

        # Step 1: Collect raw timeline
        timeline_data = await self._collect_operation_timeline(
            tenant_id, db_session,
            account=account, keyword=keyword, hours=hours,
            service_names=service_names, scope=scope,
            include_related=True, size=size,
        )
        timeline = timeline_data.get("timeline", [])

        if not timeline:
            return {
                "account": account,
                "keyword": keyword,
                "time_range": f"最近 {hours} 小时",
                "trace_segments": [],
                "uncorrelated_entries": [],
                "service_topology": {},
                "summary": "未找到相关日志记录。",
            }

        # Step 2: Extract correlation IDs from initial results
        initial_cids: set[str] = set()
        for entry in timeline:
            cids = self._extract_correlation_ids(entry.get("action", ""))
            entry["_cids"] = cids
            initial_cids.update(cids)

        # Step 3: Secondary query — search for correlation IDs in other services
        if initial_cids:
            cid_values = [cid.split("=", 1)[1] for cid in initial_cids if "=" in cid]
            unique_values = list(set(cid_values))[:10]

            if unique_values:
                now = datetime.now(timezone.utc)
                since = now - timedelta(hours=hours)

                for target in targets:
                    already_has = any(e.get("service") == target.name for e in timeline)
                    if already_has:
                        continue

                    should_clauses = []
                    for val in unique_values:
                        should_clauses.append({"match_phrase": {"message": val}})

                    body = {
                        "query": {
                            "bool": {
                                "must": [{"bool": {"should": should_clauses, "minimum_should_match": 1}}],
                                "filter": [{"range": {"@timestamp": {"gte": since.isoformat(), "lte": now.isoformat()}}}],
                            }
                        },
                        "sort": [{"@timestamp": {"order": "asc"}}],
                        "size": 15,
                        "_source": [
                            "@timestamp", "message", "gy.domain", "gy.filetype",
                            "gy.hostname", "host.name",
                            "userId", "userid", "user_id", "userName", "username",
                            "account", "accountNo", "memberId", "member_id", "operator",
                        ],
                    }

                    try:
                        result = await log_service.es.search(index=target.es_index_pattern, body=body)
                        hits = result.get("hits", {}).get("hits", [])
                        for hit in hits:
                            source = hit.get("_source", {})
                            gy_info = source.get("gy", {}) or {}
                            timeline.append({
                                "time": self._to_beijing_time(source.get("@timestamp", "")),
                                "service": target.name,
                                "domain": gy_info.get("domain", "") or self._extract_domain_from_index_pattern(target.es_index_pattern or "") or "",
                                "filetype": gy_info.get("filetype", ""),
                                "host": gy_info.get("hostname") or source.get("host", {}).get("name", ""),
                                "identity": self._extract_identity_value(source),
                                "action": self._summarize_action_message(source.get("message", "")),
                            })
                    except Exception as e:
                        logger.warning("trace_secondary_query_failed", service=target.name, error=str(e))

                timeline.sort(key=lambda item: item.get("time", ""))

        # Step 4: Build topology and group into segments
        service_topology = self._build_service_topology(targets, biz_lines)
        segments, uncorrelated = self._group_into_trace_segments(timeline, service_topology)

        # Step 5: Build summary
        error_count = sum(1 for s in segments if s["has_error"])
        summary = (
            f"账号 {account or keyword} 最近 {hours} 小时共追踪到 {len(segments)} 条链路"
            f"（{error_count} 条包含错误），"
            f"涉及 {len(timeline_data.get('services_scanned', []))} 个业务线。"
        )
        if uncorrelated:
            summary += f" 另有 {len(uncorrelated)} 条未关联记录。"

        return {
            "account": account,
            "keyword": keyword,
            "time_range": f"最近 {hours} 小时",
            "trace_segments": segments,
            "uncorrelated_entries": uncorrelated[:10],
            "service_topology": service_topology,
            "summary": summary,
        }

    async def _tool_query_account_activity(
        self, args: dict, tenant_id: str, db_session, es_index_pattern: str
    ) -> str:
        """Query recent activity for a specific account across likely identity fields."""
        account = str(args.get("account", "")).strip()
        if not account:
            return json.dumps({"error": "缺少 account 参数"}, ensure_ascii=False)

        hours = args.get("hours", 1)
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            hours = 1
        hours = min(max(hours, 1), 24)

        size = args.get("size", 20)
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 20
        size = min(max(size, 5), 50)

        service_name = str(args.get("service_name", "")).strip()
        action_keyword = str(args.get("action_keyword", "")).strip()
        timeline_data = await self._collect_operation_timeline(
            tenant_id,
            db_session,
            account=account,
            keyword=action_keyword,
            hours=hours,
            service_names=[service_name] if service_name else None,
            scope="single" if service_name else "core",
            include_related=True,
            size=size,
        )
        return json.dumps({
            "account": account,
            "service": service_name,
            "time_range": timeline_data["time_range"],
            "count": timeline_data["count"],
            "activities": timeline_data["timeline"],
            "services_scanned": timeline_data["services_scanned"],
            "summary": timeline_data["summary"],
        }, ensure_ascii=False)

    async def _tool_query_operation_timeline(self, args: dict, tenant_id: str, db_session) -> str:
        account = str(args.get("account", "")).strip()
        keyword = str(args.get("keyword", "")).strip()
        if not account and not keyword:
            return json.dumps({"error": "至少提供 account 或 keyword 之一"}, ensure_ascii=False)

        hours = args.get("hours", 1)
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            hours = 1
        hours = min(max(hours, 1), 24)

        size = args.get("size", 40)
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 40
        size = min(max(size, 10), 100)

        service_names = args.get("service_names") or []
        if isinstance(service_names, str):
            service_names = [name.strip() for name in service_names.split(",") if name.strip()]

        timeline_data = await self._collect_operation_timeline(
            tenant_id,
            db_session,
            account=account,
            keyword=keyword,
            hours=hours,
            service_names=service_names,
            scope=str(args.get("scope", "core")),
            include_related=bool(args.get("include_related", True)),
            size=size,
        )
        return json.dumps(timeline_data, ensure_ascii=False)

    async def _tool_trace_linked_operations(self, args: dict, tenant_id: str, db_session) -> str:
        """Execute trace_linked_operations tool — advanced trace correlation."""
        account = str(args.get("account", "")).strip()
        keyword = str(args.get("keyword", "")).strip()
        if not account and not keyword:
            return json.dumps({"error": "至少提供 account 或 keyword 之一"}, ensure_ascii=False)

        hours = args.get("hours", 1)
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            hours = 1
        hours = min(max(hours, 1), 24)

        size = args.get("size", 60)
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 60
        size = min(max(size, 10), 100)

        service_names = args.get("service_names") or []
        if isinstance(service_names, str):
            service_names = [name.strip() for name in service_names.split(",") if name.strip()]

        result = await self._trace_linked_operations(
            tenant_id, db_session,
            account=account,
            keyword=keyword,
            hours=hours,
            service_names=service_names or None,
            scope=str(args.get("scope", "core")),
            size=size,
        )
        return json.dumps(result, ensure_ascii=False)

    async def _tool_create_analysis_task(self, args: dict, tenant_id: str, db_session) -> str:
        """Create a deep analysis task."""
        service_name = args.get("service_name", "")
        description = args.get("description", "")
        biz_lines = await self._load_business_lines(tenant_id, db_session)
        target = self._resolve_business_line(biz_lines, service_name)

        if not target:
            return json.dumps({"error": f"未找到服务 '{service_name}'"}, ensure_ascii=False)

        return json.dumps({
            "status": "created",
            "message": f"已创建深度分析任务: {description}",
            "service": target.name,
            "hint": "请前往「分析中心」查看分析进度和结果",
        }, ensure_ascii=False)

    async def _tool_predict_service_trend(self, args: dict, tenant_id: str, db_session) -> str:
        """Predict service error trend for the next 30 minutes."""
        from logmind.domain.anomaly.predictor import trend_predictor

        service_name = args.get("service_name", "")
        biz_lines = await self._load_business_lines(tenant_id, db_session)
        target = self._resolve_business_line(biz_lines, service_name)

        if not target:
            return json.dumps({"error": f"未找到服务 '{service_name}'"}, ensure_ascii=False)

        result = await trend_predictor.predict(
            index_pattern=target.es_index_pattern,
            severity_threshold=target.severity_threshold or "error",
        )

        return json.dumps({
            "service": target.name,
            "predicted_errors_30m": result.predicted_errors_30m,
            "predicted_level": result.predicted_level,
            "trend_direction": result.trend_direction,
            "trend_slope": result.trend_slope,
            "confidence": result.confidence,
            "current_rate": result.current_rate,
            "baseline_mean": result.baseline_mean,
            "detail": result.detail,
        }, ensure_ascii=False)

    # ── Multi-round ReAct Streaming ──────────────────────

    async def chat_stream(
        self,
        session: ChatSession,
        user_message: str,
        db_session,
        service_list: str = "",
    ) -> AsyncIterator[str]:
        """
        Stream AI response with multi-round ReAct tool calling.

        Yields SSE-formatted events:
          - data: {"type": "thinking", "round": N, "content": "..."}
          - data: {"type": "tool_call", "round": N, "name": "...", "args": {...}}
          - data: {"type": "tool_result", "round": N, "name": "...", "result": "...", "summary": "..."}
          - data: {"type": "step_done", "round": N, "total_rounds": MAX}
          - data: {"type": "token", "content": "..."}
          - data: {"type": "done", "total_rounds": N}
          - data: {"type": "error", "message": "..."}
        """
        session.add_message("user", user_message)

        # Show Beijing time (UTC+8) to match user's timezone
        beijing_tz = timezone(timedelta(hours=8))
        current_time = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M (北京时间)")
        system_prompt = (
            CHAT_SYSTEM_PROMPT
            .replace("{current_time}", current_time)
            .replace("{service_list}", service_list)
            .replace("{max_rounds}", str(MAX_TOOL_ROUNDS))
        )

        # Resolve ES index pattern for the tenant
        biz_lines = await self._load_business_lines(session.tenant_id, db_session)
        default_index = biz_lines[0].es_index_pattern if biz_lines else "*"

        # Build messages with context
        messages = [ChatMessage(role="system", content=system_prompt)]
        messages.extend(session.get_context_messages())

        diagnosis_path = self._infer_diagnosis_path(user_message)
        case_state = {
            "question": user_message,
            "path": diagnosis_path,
            "stage": "侦察",
            "stage_index": 0,
            "status": "running",
            "hypothesis": "正在侦察问题现场，等待第一批证据。",
            "confidence": 15,
            "supporting_evidence": [],
            "counter_evidence": [],
            "evidence_summaries": [],
            "actions": [],
            "impact_scope": "待确认",
            "missing_confirmations": ["等待第一批工具证据确认问题范围"],
        }
        yield self._sse(
            self._stage_payload(case_state, "侦察", summary="开始收集问题上下文和可用证据。")
        )

        # ── Deterministic Log Retrieval Path ─────────────────
        # Explicit requests like "最近1小时业务线X所有日志包含Y，导出订单号"
        # should not depend on LLM tool-argument guessing.
        direct_log_intent = self._extract_direct_log_search_intent(user_message, biz_lines)
        if direct_log_intent:
            tool_args = {
                "service_name": direct_log_intent["service_name"],
                "index_pattern": direct_log_intent["index_pattern"],
                "query": direct_log_intent["keyword"],
                "time_range": direct_log_intent["time_label"],
                "severity": direct_log_intent.get("severity"),
                "size": direct_log_intent["size"],
            }
            yield self._sse({
                "type": "tool_call",
                "round": 0,
                "name": "search_logs",
                "args": tool_args,
            })

            direct_result, evidence_label = await self._execute_direct_log_search(
                direct_log_intent,
                session=session,
                db_session=db_session,
            )
            direct_result_json = json.dumps(direct_result, ensure_ascii=False, default=str)

            yield self._sse({
                "type": "tool_result",
                "round": 0,
                "name": "search_logs",
                "result": direct_result_json[:2000],
                "summary": (
                    f"命中 {direct_result['total_hits']} 条，返回 {direct_result['returned']} 条，"
                    f"提取订单号 {len(direct_result.get('order_ids', []))} 个。"
                ),
                "evidence_label": evidence_label,
            })

            direct_update = self._summarize_tool_result_structured(
                "search_logs",
                {
                    "service_name": direct_log_intent["service_name"],
                    "query": direct_log_intent["keyword"],
                },
                json.dumps(
                    {
                        "total_hits": direct_result["total_hits"],
                        "error_count": direct_result["error_count"],
                        "logs": direct_result["logs"][:10],
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                evidence_label,
            )
            yield self._sse(self._apply_hypothesis_update(case_state, direct_update))
            yield self._sse(
                self._stage_payload(
                    case_state,
                    "结论",
                    status="done",
                    summary="日志检索完成，输出可导出的结果。",
                )
            )

            decision_actions = [
                {
                    "id": "export-order-ids",
                    "label": "导出订单号",
                    "prompt": (
                        f"按相同条件继续导出 {direct_log_intent['service_name']} "
                        f"{direct_log_intent['time_label']}包含 "
                        f"{direct_log_intent['keyword']} 的订单号 CSV。"
                    ),
                    "kind": "follow_up",
                    "description": "只保留订单号、时间、服务、主机等导出字段。",
                },
                {
                    "id": "aggregate-user-agent",
                    "label": "聚合 UserAgent",
                    "prompt": (
                        f"按 UserAgent 和主机聚合 {direct_log_intent['service_name']} "
                        f"{direct_log_intent['time_label']}包含 "
                        f"{direct_log_intent['keyword']} 的命中日志。"
                    ),
                    "kind": "diagnose",
                    "description": "确认是否集中来自某类客户端或入口。",
                },
                {
                    "id": "copy-report",
                    "label": "复制诊断报告",
                    "prompt": "",
                    "kind": "copy",
                    "description": "复制当前查询结论、样本日志和导出结果。",
                },
            ]
            case_state["actions"] = decision_actions
            yield self._sse({"type": "decision_actions", "actions": decision_actions})

            final_content = self._format_direct_log_search_answer(direct_result)
            suggested_actions = [
                {
                    "label": "扩大时间窗口",
                    "prompt": (
                        f"查询 {direct_log_intent['service_name']} "
                        "最近 6 小时所有日志包含 "
                        f"{direct_log_intent['keyword']} 的数据，并导出订单号。"
                    ),
                    "kind": "follow_up",
                },
                {
                    "label": "按主机聚合",
                    "prompt": (
                        f"按 host.name 聚合 {direct_log_intent['service_name']} "
                        f"{direct_log_intent['time_label']}包含 "
                        f"{direct_log_intent['keyword']} 的日志。"
                    ),
                    "kind": "diagnose",
                },
            ]

            for i in range(0, len(final_content), 4):
                yield self._sse({"type": "token", "content": final_content[i:i + 4]})

            yield self._sse({"type": "suggested_actions", "actions": suggested_actions})
            session.add_message(
                "assistant",
                final_content,
                metadata={"suggested_actions": suggested_actions, "diagnosis_case": case_state},
            )
            yield self._sse({"type": "done", "total_rounds": 0, "mode": "direct_log_search"})
            return

        # ── Smart Search Path ────────────────────────────────
        if self._looks_like_smart_search(user_message):
            search_query = re.sub(r"^(搜索|搜|查|查询|找)\s*", "", user_message.strip())
            search_result = await self._smart_search(
                query=search_query,
                hours=1,
                scope="all",
                tenant_id=session.tenant_id,
                db_session=db_session,
            )

            yield self._sse({
                "type": "search_clues",
                "clues": search_result["clues"],
                "summary": search_result["summary"],
                "total_hits": search_result["total_hits"],
                "error_count": search_result["error_count"],
                "services_involved": search_result["services_involved"],
                "input_type": search_result["input_type"],
            })
            search_update = {
                "hypothesis": (
                    f"智能搜索已命中 {search_result['total_hits']} 条记录，"
                    f"其中 {search_result['error_count']} 条错误，可作为初始诊断方向。"
                    if search_result["total_hits"] else
                    "智能搜索暂无直接命中，需要扩大范围或换关键词。"
                ),
                "summary": search_result["summary"],
                "supporting_evidence": [],
                "counter_evidence": [],
                "confidence_delta": 12 if search_result["error_count"] else (-4 if not search_result["total_hits"] else 5),
                "hit_count": search_result["total_hits"],
                "error_count": search_result["error_count"],
            }
            yield self._sse(self._apply_hypothesis_update(case_state, search_update))
            yield self._sse(self._stage_payload(case_state, "聚焦", summary="已完成智能搜索，准备聚焦关键线索。"))

            # Inject search results as context for the LLM
            messages.append(ChatMessage(
                role="assistant",
                content=f"[智能搜索 \"{search_query}\" 完成]",
            ))
            messages.append(ChatMessage(
                role="user",
                content=(
                    f"搜索结果如下，请基于此分析并给出诊断建议：\n"
                    f"命中 {search_result['total_hits']} 条记录，{search_result['error_count']} 条错误，"
                    f"涉及服务: {', '.join(search_result['services_involved'][:5])}\n"
                    f"诊断线索:\n" +
                    "\n".join(f"- [{c['severity']}] {c['title']}: {c['detail']}" for c in search_result["clues"][:4]) +
                    f"\n\n时间线前5条:\n" +
                    "\n".join(f"- {e['time']} [{e['service']}] {e['action'][:80]}" for e in search_result["timeline"][:5])
                ),
            ))

        elif self._looks_like_trace_request(user_message):
            extracted = self._extract_context_entities(user_message, biz_lines)
            account = extracted.get("account", "")
            keyword = extracted.get("keyword", "")
            if account or keyword:
                preflight_args: dict[str, str | int] = {
                    "account": account,
                    "keyword": keyword,
                    "hours": int(extracted.get("hours", "1")),
                    "size": 60,
                    "scope": extracted.get("scope", "core"),
                }
                if extracted.get("service_name"):
                    preflight_args["service_names"] = [extracted["service_name"]]
                    preflight_args["scope"] = "selected"
                yield self._sse({
                    "type": "tool_call",
                    "round": 0,
                    "name": "trace_linked_operations",
                    "args": preflight_args,
                })
                preflight_result, evidence_label = await self.execute_tool_call_with_evidence(
                    "trace_linked_operations",
                    preflight_args,
                    session.tenant_id,
                    db_session,
                    session=session,
                    es_index_pattern=default_index,
                )
                preflight_update = self._summarize_tool_result_structured(
                    "trace_linked_operations", preflight_args, preflight_result, evidence_label
                )
                yield self._sse(self._apply_hypothesis_update(case_state, preflight_update))
                yield self._sse(self._stage_payload(case_state, "关联", summary="已预查询链路证据，进入关联分析。"))
                yield self._sse({
                    "type": "tool_result",
                    "round": 0,
                    "name": "trace_linked_operations",
                    "result": preflight_result[:2000],
                    "summary": preflight_result[:200] + ("..." if len(preflight_result) > 200 else ""),
                    "evidence_label": evidence_label,
                })
                messages.append(ChatMessage(
                    role="assistant",
                    content=f"[预查询工具 trace_linked_operations({json.dumps(preflight_args, ensure_ascii=False)})]",
                ))
                messages.append(ChatMessage(
                    role="user",
                    content=f"预查询结果如下，请基于此继续分析并按需补充工具调用：\n{preflight_result[:4000]}",
                ))
        elif self._looks_like_account_activity_request(user_message):
            extracted = self._extract_context_entities(user_message, biz_lines)
            account = extracted.get("account")
            if account:
                scope = extracted.get("scope", "core")
                preflight_args: dict[str, str | int] = {
                    "account": account,
                    "hours": int(extracted.get("hours", "1")),
                    "size": 20,
                    "scope": scope,
                }
                if extracted.get("service_name"):
                    preflight_args["service_names"] = [extracted["service_name"]]
                    preflight_args["scope"] = "selected"
                if extracted.get("keyword"):
                    preflight_args["keyword"] = extracted["keyword"]
                yield self._sse({
                    "type": "tool_call",
                    "round": 0,
                    "name": "query_operation_timeline",
                    "args": preflight_args,
                })
                preflight_result, evidence_label = await self.execute_tool_call_with_evidence(
                    "query_operation_timeline",
                    preflight_args,
                    session.tenant_id,
                    db_session,
                    session=session,
                    es_index_pattern=default_index,
                )
                preflight_update = self._summarize_tool_result_structured(
                    "query_operation_timeline", preflight_args, preflight_result, evidence_label
                )
                yield self._sse(self._apply_hypothesis_update(case_state, preflight_update))
                yield self._sse(self._stage_payload(case_state, "聚焦", summary="已预查询账号/对象时间线，准备聚焦触发点。"))
                yield self._sse({
                    "type": "tool_result",
                    "round": 0,
                    "name": "query_operation_timeline",
                    "result": preflight_result[:2000],
                    "summary": preflight_result[:200] + ("..." if len(preflight_result) > 200 else ""),
                    "evidence_label": evidence_label,
                })
                messages.append(ChatMessage(
                    role="assistant",
                    content=f"[预查询工具 query_operation_timeline({json.dumps(preflight_args, ensure_ascii=False)})]",
                ))
                messages.append(ChatMessage(
                    role="user",
                    content=f"预查询结果如下，请基于此继续分析并按需补充工具调用：\n{preflight_result[:3000]}",
                ))

        # ── Multi-Agent Path ────────────────────────────────
        if self._looks_like_multi_agent_request(user_message):
            from logmind.domain.chat.multi_agent import MultiAgentOrchestrator, AGENT_ROLES

            orchestrator = MultiAgentOrchestrator(self)
            agent_names = orchestrator.select_agents(user_message)
            yield self._sse(self._stage_payload(case_state, "关联", summary="启动多 Agent 分支并行核对证据。"))

            yield self._sse({
                "type": "multi_agent_start",
                "agents": [{"name": n, "display_name": AGENT_ROLES[n].display_name} for n in agent_names],
            })

            findings, synthesis = await orchestrator.orchestrate(
                user_message=user_message,
                tenant_id=session.tenant_id,
                db_session=db_session,
                default_index=default_index,
                biz_lines=biz_lines,
                service_list=service_list,
            )

            for finding in findings:
                yield self._sse({
                    "type": "agent_done",
                    "agent": finding.agent_name,
                    "display_name": finding.display_name,
                    "status": finding.status,
                    "summary": finding.summary[:300],
                    "tool_calls": finding.tool_calls[:5],
                })

            agent_summary = "；".join(f"{finding.display_name}: {finding.summary[:80]}" for finding in findings[:3])
            multi_update = {
                "hypothesis": agent_summary or "多 Agent 已完成分支诊断，当前结论需要结合最终汇总判断。",
                "summary": agent_summary or "多 Agent 分支返回空摘要。",
                "supporting_evidence": [],
                "counter_evidence": [],
                "confidence_delta": 18 if agent_summary else 4,
                "hit_count": len(findings),
                "error_count": sum(1 for finding in findings if finding.status == "error"),
            }
            yield self._sse(self._apply_hypothesis_update(case_state, multi_update))
            yield self._sse(self._stage_payload(case_state, "结论", status="done", summary="多 Agent 诊断完成，生成汇总结论。"))
            decision_actions = self._build_decision_actions(
                user_message, biz_lines, diagnosis_path, case_state, synthesis
            )
            case_state["actions"] = decision_actions
            yield self._sse({"type": "decision_actions", "actions": decision_actions})
            synthesis = self._format_expert_answer(synthesis, case_state, decision_actions)

            # Stream synthesis
            chunk_size = 4
            for i in range(0, len(synthesis), chunk_size):
                yield self._sse({"type": "token", "content": synthesis[i:i + chunk_size]})

            suggested_actions = self._build_suggested_actions(user_message, synthesis, biz_lines)
            metadata = {"suggested_actions": suggested_actions, "diagnosis_case": case_state}
            session.add_message("assistant", synthesis, metadata=metadata)
            yield self._sse({"type": "done", "total_rounds": 0, "mode": "multi_agent"})
            return

        try:
            total_rounds = 0

            for round_num in range(1, MAX_TOOL_ROUNDS + 1):
                # Notify frontend: thinking
                yield self._sse(self._stage_payload(
                    case_state,
                    self._round_stage(round_num),
                    summary=f"第 {round_num} 轮正在{self._round_stage(round_num)}。",
                ))
                yield self._sse({"type": "thinking", "round": round_num, "content": f"第 {round_num}/{MAX_TOOL_ROUNDS} 轮推理..."})

                # Call LLM with tools
                request = ChatRequest(
                    messages=messages,
                    temperature=0.3,
                    max_tokens=4096,
                    tools=CHAT_TOOLS,
                )

                response, provider_id = await provider_manager.chat_with_fallback(
                    session=db_session,
                    tenant_id=session.tenant_id,
                    request=request,
                )

                total_rounds = round_num

                # Check for tool calls
                if response.tool_calls:
                    for tc in response.tool_calls:
                        func_name = tc.get("function", {}).get("name", "")
                        func_args_str = tc.get("function", {}).get("arguments", "{}")
                        try:
                            func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
                        except json.JSONDecodeError:
                            func_args = {}

                        # Notify frontend: tool_call
                        yield self._sse({
                            "type": "tool_call", "round": round_num,
                            "name": func_name, "args": func_args,
                        })

                        # Execute tool with real agent tools
                        # Resolve index pattern from service_name or domain arg
                        tool_index = default_index
                        svc_name = func_args.get("service_name", "") or func_args.get("domain", "")
                        matched_biz = self._resolve_business_line(biz_lines, svc_name)
                        if matched_biz:
                            tool_index = matched_biz.es_index_pattern

                        # For search_logs: inject exact gy.domain if we matched a biz line
                        # AI often passes imprecise domain like "login" — resolve to exact value
                        if matched_biz and func_name == "search_logs":
                            exact_domain = self._extract_domain_from_index_pattern(
                                matched_biz.es_index_pattern or ""
                            )
                            if exact_domain:
                                func_args["domain"] = exact_domain

                        tool_result, evidence_label = await self.execute_tool_call_with_evidence(
                            func_name, func_args, session.tenant_id,
                            db_session, session=session, es_index_pattern=tool_index,
                        )

                        # Generate result summary (first 200 chars)
                        summary = tool_result[:200] + ("..." if len(tool_result) > 200 else "")

                        # Notify frontend: tool_result
                        yield self._sse({
                            "type": "tool_result", "round": round_num,
                            "name": func_name,
                            "result": tool_result[:2000],
                            "summary": summary,
                            "evidence_label": evidence_label,
                        })
                        structured_update = self._summarize_tool_result_structured(
                            func_name, func_args, tool_result, evidence_label
                        )
                        yield self._sse(self._apply_hypothesis_update(case_state, structured_update))

                        # Add tool interaction to message context for next round
                        messages.append(ChatMessage(
                            role="assistant",
                            content=f"[调用工具 {func_name}({json.dumps(func_args, ensure_ascii=False)[:100]})]",
                        ))
                        messages.append(ChatMessage(
                            role="user",
                            content=f"工具 {func_name} 返回结果:\n{tool_result[:3000]}",
                        ))

                    # Notify frontend: step_done
                    yield self._sse({
                        "type": "step_done", "round": round_num,
                        "total_rounds": MAX_TOOL_ROUNDS,
                    })
                    yield self._sse(self._stage_payload(
                        case_state,
                        self._round_stage(min(round_num + 1, MAX_TOOL_ROUNDS)),
                        summary="本轮证据已归档，继续收敛假设。",
                    ))

                    # Continue to next round
                    continue

                else:
                    # No tool calls — LLM is ready to answer
                    final_content = response.content
                    yield self._sse(self._stage_payload(case_state, "结论", status="done", summary="证据足够，正在形成诊断结论。"))
                    decision_actions = self._build_decision_actions(
                        user_message, biz_lines, diagnosis_path, case_state, final_content
                    )
                    case_state["actions"] = decision_actions
                    yield self._sse({"type": "decision_actions", "actions": decision_actions})
                    final_content = self._format_expert_answer(final_content, case_state, decision_actions)
                    suggested_actions = self._build_suggested_actions(user_message, final_content, biz_lines)

                    # Stream the response token by token
                    chunk_size = 4
                    for i in range(0, len(final_content), chunk_size):
                        chunk = final_content[i:i + chunk_size]
                        yield self._sse({"type": "token", "content": chunk})

                    if suggested_actions:
                        yield self._sse({"type": "suggested_actions", "actions": suggested_actions})

                    session.add_message(
                        "assistant",
                        final_content,
                        metadata={"suggested_actions": suggested_actions, "diagnosis_case": case_state},
                    )
                    yield self._sse({"type": "done", "total_rounds": total_rounds})
                    return

            # If we exhausted all rounds, do a final answer call without tools
            yield self._sse({"type": "thinking", "round": MAX_TOOL_ROUNDS, "content": "已收集足够信息，正在生成最终分析报告..."})

            final_request = ChatRequest(
                messages=messages + [ChatMessage(
                    role="user",
                    content="请根据以上所有工具调用结果，给出完整的分析结论和建议。",
                )],
                temperature=0.3,
                max_tokens=4096,
            )
            final_response, _ = await provider_manager.chat_with_fallback(
                session=db_session,
                tenant_id=session.tenant_id,
                request=final_request,
            )
            final_content = final_response.content
            yield self._sse(self._stage_payload(case_state, "结论", status="done", summary="已达到最大推理轮次，输出当前最可信结论。"))
            decision_actions = self._build_decision_actions(
                user_message, biz_lines, diagnosis_path, case_state, final_content
            )
            case_state["actions"] = decision_actions
            yield self._sse({"type": "decision_actions", "actions": decision_actions})
            final_content = self._format_expert_answer(final_content, case_state, decision_actions)
            suggested_actions = self._build_suggested_actions(user_message, final_content, biz_lines)

            chunk_size = 4
            for i in range(0, len(final_content), chunk_size):
                chunk = final_content[i:i + chunk_size]
                yield self._sse({"type": "token", "content": chunk})

            if suggested_actions:
                yield self._sse({"type": "suggested_actions", "actions": suggested_actions})

            session.add_message(
                "assistant",
                final_content,
                metadata={"suggested_actions": suggested_actions, "diagnosis_case": case_state},
            )
            yield self._sse({"type": "done", "total_rounds": total_rounds})

        except Exception as e:
            error_msg = f"AI 推理失败: {str(e)}"
            logger.error("chat_stream_failed", error=str(e), session_id=session.id)
            session.add_message("assistant", error_msg)
            yield self._sse({"type": "error", "message": error_msg})

    @staticmethod
    def _sse(data: dict) -> str:
        """Format as SSE event."""
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# Singleton
chat_service = ChatService()
