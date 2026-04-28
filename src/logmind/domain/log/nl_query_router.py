"""
Natural Language Log Query — Text-to-DSL

Converts human-readable log queries into ES DSL using the existing LLM provider.
Example: "最近1小时xxx站点的超时错误" → ES query with proper filters.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.provider.base import ChatMessage, ChatRequest
from logmind.domain.provider.manager import provider_manager

logger = get_logger(__name__)

router = APIRouter(prefix="/logs", tags=["Logs"])

NL_SYSTEM_PROMPT = """你是 LogMind 日志搜索助手。用户会用自然语言描述他们想搜索的日志，你需要把它转换成结构化的搜索参数。

可用的搜索字段:
- query: 关键词搜索（异常类名、错误消息等）
- severity: 日志级别 (error | warning | info | debug | critical)
- time_from / time_to: ISO 8601 时间范围
- domain: 站点域名（gy.domain 字段）
- size: 返回条数（默认 50，最大 200）

时间快捷语法理解:
- "最近1小时" → time_from = 当前时间 - 1小时
- "今天" → time_from = 今天00:00
- "昨天" → time_from = 昨天00:00, time_to = 今天00:00
- "最近3天" → time_from = 当前时间 - 3天

当前时间: {current_time}

请严格返回以下 JSON 格式，不要包含任何其他文字:
```json
{
  "query": "搜索关键词",
  "severity": "error",
  "time_from": "2026-04-28T00:00:00Z",
  "time_to": "2026-04-28T23:59:59Z",
  "domain": "",
  "size": 50,
  "explanation": "对用户意图的一句话解释"
}
```

示例:
- 输入: "最近1小时超时错误" → severity=error, query="timeout OR 超时", time 最近1小时
- 输入: "昨天xxx站点的NullPointerException" → domain=xxx, query="NullPointerException", time 昨天
- 输入: "今天所有警告日志" → severity=warning, time 今天
"""


class NLQueryRequest(BaseModel):
    """Natural language log query request."""
    question: str = Field(..., min_length=2, max_length=500, description="自然语言查询")


class NLQueryResponse(BaseModel):
    """Parsed query parameters from NL input."""
    query: str = ""
    severity: str = "error"
    time_from: str = ""
    time_to: str = ""
    domain: str = ""
    size: int = 50
    explanation: str = ""
    raw_llm_output: str = ""


@router.post("/natural-query", response_model=NLQueryResponse)
async def natural_language_query(
    req: NLQueryRequest,
    session: DBSession,
    user: CurrentUser,
):
    """
    Convert a natural language log query into structured ES search parameters.

    Uses the tenant's default AI provider to parse user intent.
    Returns structured parameters that the frontend can review and execute.
    """
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    system_prompt = NL_SYSTEM_PROMPT.replace("{current_time}", current_time)

    chat_request = ChatRequest(
        messages=[
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=req.question),
        ],
        temperature=0.1,  # Low temp for structured output
        max_tokens=512,
    )

    try:
        response, provider_id = await provider_manager.chat_with_fallback(
            session=session,
            tenant_id=user.tenant_id,
            request=chat_request,
        )

        # Parse LLM response — extract JSON
        raw_output = response.content.strip()
        # Handle markdown code block wrapping
        if "```json" in raw_output:
            raw_output = raw_output.split("```json")[-1].split("```")[0].strip()
        elif "```" in raw_output:
            raw_output = raw_output.split("```")[1].split("```")[0].strip()

        parsed = json.loads(raw_output)

        return NLQueryResponse(
            query=parsed.get("query", ""),
            severity=parsed.get("severity", "error"),
            time_from=parsed.get("time_from", ""),
            time_to=parsed.get("time_to", ""),
            domain=parsed.get("domain", ""),
            size=min(parsed.get("size", 50), 200),
            explanation=parsed.get("explanation", ""),
            raw_llm_output=response.content[:500],
        )

    except json.JSONDecodeError:
        logger.warning("nl_query_parse_failed", question=req.question, raw=raw_output[:200])
        raise HTTPException(
            status_code=422,
            detail=f"AI 返回格式解析失败，请尝试更明确的描述。原始输出: {raw_output[:200]}",
        )
    except Exception as e:
        logger.error("nl_query_failed", question=req.question, error=str(e))
        raise HTTPException(status_code=500, detail=f"自然语言解析失败: {str(e)}")
