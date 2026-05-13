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
    """In-memory chat session with conversation history."""
    id: str
    tenant_id: str
    user_id: str
    title: str = "新对话"
    messages: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

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


# In-memory session store (production should use Redis)
_sessions: dict[str, ChatSession] = {}


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

    async def _load_business_lines(self, tenant_id: str, db_session) -> list:
        from logmind.domain.tenant.models import BusinessLine
        from logmind.shared.base_repository import BaseRepository

        biz_repo = BaseRepository(BusinessLine)
        return await biz_repo.get_all(db_session, tenant_id=tenant_id, limit=100)

    def _resolve_business_line(self, biz_lines: list, service_name: str):
        if not service_name:
            return None

        query = service_name.strip().lower()
        if not query:
            return None

        for biz in biz_lines:
            if query in (biz.name or "").lower():
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
            elif tool_name == "create_analysis_task":
                return await self._tool_create_analysis_task(args, tenant_id, db_session)
            else:
                return json.dumps({"error": f"未知工具: {tool_name}"})
        except Exception as e:
            logger.error("tool_call_failed", tool=tool_name, error=str(e))
            return json.dumps({"error": f"工具调用失败: {str(e)}"})

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

        if self._looks_like_trace_request(user_message):
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
                preflight_result = await self.execute_tool_call(
                    "trace_linked_operations",
                    preflight_args,
                    session.tenant_id,
                    db_session,
                    es_index_pattern=default_index,
                )
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
                preflight_result = await self.execute_tool_call(
                    "query_operation_timeline",
                    preflight_args,
                    session.tenant_id,
                    db_session,
                    es_index_pattern=default_index,
                )
                messages.append(ChatMessage(
                    role="assistant",
                    content=f"[预查询工具 query_operation_timeline({json.dumps(preflight_args, ensure_ascii=False)})]",
                ))
                messages.append(ChatMessage(
                    role="user",
                    content=f"预查询结果如下，请基于此继续分析并按需补充工具调用：\n{preflight_result[:3000]}",
                ))

        try:
            total_rounds = 0

            for round_num in range(1, MAX_TOOL_ROUNDS + 1):
                # Notify frontend: thinking
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

                        tool_result = await self.execute_tool_call(
                            func_name, func_args, session.tenant_id,
                            db_session, es_index_pattern=tool_index,
                        )

                        # Generate result summary (first 200 chars)
                        summary = tool_result[:200] + ("..." if len(tool_result) > 200 else "")

                        # Notify frontend: tool_result
                        yield self._sse({
                            "type": "tool_result", "round": round_num,
                            "name": func_name,
                            "result": tool_result[:2000],
                            "summary": summary,
                        })

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

                    # Continue to next round
                    continue

                else:
                    # No tool calls — LLM is ready to answer
                    final_content = response.content
                    suggested_actions = self._build_suggested_actions(
                        user_message, final_content, biz_lines
                    )

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
                        metadata={"suggested_actions": suggested_actions},
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
                metadata={"suggested_actions": suggested_actions},
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
