"""
Agent Tools — ES Query Tools for AI Agent

Defines tool schemas (OpenAI Function Calling format) and execution functions
that let the AI agent autonomously query Elasticsearch during analysis.

Tools:
  - search_logs: Free-form ES log search with AI-crafted filters
  - get_log_context: Get surrounding logs for a specific timestamp
  - count_error_patterns: Aggregate error counts by type/domain
  - list_available_indices: Discover searchable indices
  - search_knowledge_base: RAG knowledge base vector search
  - search_similar_incidents: Find historically similar error analyses
  - search_cross_service_logs: Cross-business-line error correlation
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from logmind.core.logging import get_logger
from logmind.domain.log.service import log_service

logger = get_logger(__name__)

# ── Tool Schemas (OpenAI Function Calling format) ────────

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": (
                "在 Elasticsearch 中搜索日志。可以自由指定时间范围、关键词、"
                "日志级别、域名等条件。用于深入调查特定错误模式或查找关联日志。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词（异常类名、错误消息片段等）",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["error", "warning", "info", "debug"],
                        "description": "日志级别过滤",
                    },
                    "time_from": {
                        "type": "string",
                        "description": "起始时间 (ISO 8601 格式，如 2026-04-17T00:00:00Z)",
                    },
                    "time_to": {
                        "type": "string",
                        "description": "结束时间 (ISO 8601 格式)",
                    },
                    "domain": {
                        "type": "string",
                        "description": "站点域名 (gy.domain 字段)",
                    },
                    "size": {
                        "type": "integer",
                        "description": "返回日志条数（默认 20，最大 50）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_log_context",
            "description": (
                "查看某个时间点前后的日志上下文。输入一个时间戳，"
                "返回该时间点前后各 N 条日志，帮助理解错误发生的完整场景。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "timestamp": {
                        "type": "string",
                        "description": "目标时间戳 (ISO 8601 格式)",
                    },
                    "window_minutes": {
                        "type": "integer",
                        "description": "前后时间窗口（分钟），默认 5",
                    },
                    "size": {
                        "type": "integer",
                        "description": "返回日志条数，默认 30",
                    },
                },
                "required": ["timestamp"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_error_patterns",
            "description": (
                "按异常类型、域名或时间段聚合统计错误数量。"
                "帮助判断某个错误是偶发还是频发、是否集中在某个服务。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time_from": {
                        "type": "string",
                        "description": "起始时间 (ISO 8601 格式)",
                    },
                    "time_to": {
                        "type": "string",
                        "description": "结束时间 (ISO 8601 格式)",
                    },
                    "group_by": {
                        "type": "string",
                        "enum": ["filetype", "domain", "time_histogram"],
                        "description": "聚合维度：按日志文件类型、域名或时间直方图",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_available_indices",
            "description": (
                "列出 Elasticsearch 中可搜索的索引。"
                "帮助发现其他相关服务的日志索引。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "索引名称模式（支持通配符），默认 *",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "查阅内部的 RAG 知识库（SOP、历史故障报告、排查手册等）。"
                "当遇到未知的报错或需要人工经验时，可搜索此知识库。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用于进行向量匹配的搜索语句，例如 '如何处理 Redis 连接池耗尽' 或具体的堆栈片段。",
                    },
                    "kb_id": {
                        "type": "string",
                        "description": "（可选）特定的知识库 UUID。如果不提供则搜索全局知识库。",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_similar_incidents",
            "description": (
                "搜索历史上与当前错误模式相似的 AI 分析记录。"
                "帮助参考过去的根因分析结论和修复建议，避免重复分析。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "error_pattern": {
                        "type": "string",
                        "description": "错误模式描述（如异常类名+核心堆栈信息，或错误消息关键词）",
                    },
                },
                "required": ["error_pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_cross_service_logs",
            "description": (
                "跨业务线搜索其他服务的错误日志（同一租户内）。"
                "当怀疑当前服务的错误是由上游/下游服务故障引起时使用。"
                "例如：发现大量连接超时，怀疑是依赖的数据库服务或缓存服务出了问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词（如异常类名、错误消息片段）",
                    },
                    "service_name": {
                        "type": "string",
                        "description": "（可选）目标服务/业务线名称关键词，用于缩小范围",
                    },
                    "minutes_back": {
                        "type": "integer",
                        "description": "向前查看的分钟数（默认30分钟）",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
]

# ── New tools (v5.1) ─────────────────────────────────────
AGENT_TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "get_alerts",
            "description": (
                "查询最近的告警历史记录。可按严重度过滤。"
                "用于了解当前服务是否有活跃告警，以及告警的频率和模式。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_back": {
                        "type": "integer",
                        "description": "查看最近多少小时的告警（默认24小时）",
                    },
                    "severity": {
                        "type": "string",
                        "description": "按严重度过滤（critical/warning，可选）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_health",
            "description": (
                "查询当前服务的健康状态，包括错误率、请求量、错误趋势。"
                "用于判断服务整体是否异常，是偶发错误还是系统性故障。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_back": {
                        "type": "integer",
                        "description": "统计最近多少小时（默认6小时）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_time_windows",
            "description": (
                "对比两个时间窗口的错误分布差异。"
                "用于判断错误是否突增，以及错误模式是否发生变化。"
                "默认对比：最近1小时 vs 前1小时。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window_hours": {
                        "type": "number",
                        "description": "每个窗口的小时数（默认1小时）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_error_chain",
            "description": (
                "追踪错误的上下游调用链。搜索与指定错误关键词关联的"
                "其他服务日志，尝试找到错误的源头或传播路径。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "error_keyword": {
                        "type": "string",
                        "description": "错误关键词（如异常类名、错误码）",
                    },
                    "minutes_back": {
                        "type": "integer",
                        "description": "向前追溯分钟数（默认30分钟）",
                    },
                },
                "required": ["error_keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_analysis_task",
            "description": (
                "当你无法通过现有信息确定根因时，创建一个深度分析任务。"
                "任务会异步运行完整的 AI 分析 pipeline，适合复杂问题。"
                "注意：这是兜底手段，优先通过其他工具直接排查。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "创建任务的原因说明",
                    },
                },
                "required": ["reason"],
            },
        },
    },
])


# ── Tool Execution ───────────────────────────────────────

async def execute_tool(
    tool_name: str,
    arguments: dict,
    es_index_pattern: str,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    tenant_id: str = "",
    business_line_id: str = "",
    related_services: dict | None = None,
    language: str | None = None,
) -> str:
    """
    Execute an agent tool and return the result as a string.

    All tools are bounded by the business line's index pattern
    to prevent cross-tenant data leaks.
    """
    try:
        if tool_name == "search_logs":
            return await _exec_search_logs(
                arguments, es_index_pattern, time_from, time_to,
                business_line_id=business_line_id,
                language=language,
            )
        elif tool_name == "get_log_context":
            return await _exec_get_log_context(
                arguments,
                es_index_pattern,
                business_line_id=business_line_id,
                language=language,
            )
        elif tool_name == "count_error_patterns":
            return await _exec_count_error_patterns(
                arguments,
                es_index_pattern,
                time_from,
                time_to,
                business_line_id=business_line_id,
                language=language,
            )
        elif tool_name == "list_available_indices":
            return await _exec_list_indices(arguments, es_index_pattern)
        elif tool_name == "search_knowledge_base":
            return await _exec_search_knowledge_base(arguments, tenant_id=tenant_id)
        elif tool_name == "search_similar_incidents":
            return await _exec_search_similar_incidents(
                arguments,
                es_index_pattern,
                tenant_id=tenant_id,
                business_line_id=business_line_id,
            )
        elif tool_name == "search_cross_service_logs":
            related_patterns = await _load_related_index_patterns(related_services, tenant_id)
            return await _exec_search_cross_service_logs(
                arguments, es_index_pattern, related_index_patterns=related_patterns
            )
        elif tool_name == "get_alerts":
            return await _exec_get_alerts(
                arguments, tenant_id=tenant_id, business_line_id=business_line_id
            )
        elif tool_name == "get_service_health":
            return await _exec_get_service_health(
                arguments,
                es_index_pattern,
                time_from,
                time_to,
                business_line_id=business_line_id,
                language=language,
            )
        elif tool_name == "compare_time_windows":
            return await _exec_compare_time_windows(
                arguments,
                es_index_pattern,
                time_to,
                business_line_id=business_line_id,
                language=language,
            )
        elif tool_name == "trace_error_chain":
            related_patterns = await _load_related_index_patterns(related_services, tenant_id)
            return await _exec_trace_error_chain(
                arguments, es_index_pattern, time_to,
                related_index_patterns=related_patterns,
            )
        elif tool_name == "create_analysis_task":
            return await _exec_create_analysis_task(arguments, es_index_pattern)
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
    except Exception as e:
        logger.warning("agent_tool_error", tool=tool_name, error=str(e))
        return json.dumps({"error": str(e)})


def _flatten_related_service_ids(related_services: dict | None) -> list[str]:
    """Return configured upstream/downstream business-line IDs in stable order."""
    if not isinstance(related_services, dict):
        return []

    ids: list[str] = []
    for key in ("upstream", "downstream"):
        values = related_services.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if value and value not in ids:
                ids.append(str(value))
    return ids


async def _load_related_index_patterns(
    related_services: dict | None,
    tenant_id: str = "",
) -> list[str]:
    """Load ES index patterns for explicitly configured related services only."""
    related_ids = _flatten_related_service_ids(related_services)
    if not related_ids:
        return []

    try:
        from sqlalchemy import select

        from logmind.core.database import get_db_context
        from logmind.domain.tenant.models import BusinessLine

        async with get_db_context() as session:
            stmt = select(BusinessLine).where(
                BusinessLine.id.in_(related_ids),
                BusinessLine.is_active == True,  # noqa: E712
            )
            if tenant_id:
                stmt = stmt.where(BusinessLine.tenant_id == tenant_id)
            result = await session.execute(stmt)
            patterns = []
            for biz in result.scalars().all():
                if biz.es_index_pattern and biz.es_index_pattern not in patterns:
                    patterns.append(biz.es_index_pattern)
            return patterns
    except Exception as e:
        logger.warning("related_index_patterns_load_failed", error=str(e))
        return []


def _join_index_patterns(patterns: list[str]) -> str:
    """Join non-empty ES index patterns without duplicates."""
    seen: list[str] = []
    for pattern in patterns:
        for part in str(pattern or "").split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
    return ",".join(seen)


async def _exec_search_logs(
    args: dict,
    index_pattern: str,
    default_from,
    default_to,
    business_line_id: str = "",
    language: str | None = None,
) -> str:
    """Execute search_logs tool."""
    from logmind.domain.log.schemas import LogQueryRequest
    from logmind.domain.analysis.sensitive_masker import mask_sensitive

    size = min(args.get("size", 20), 50)  # Cap at 50

    # Parse time range
    t_from = _parse_time(args.get("time_from")) or default_from
    t_to = _parse_time(args.get("time_to")) or default_to

    if not t_from or not t_to:
        return json.dumps({"error": "time_from and time_to are required"})

    request = LogQueryRequest(
        index_pattern=index_pattern,
        time_from=t_from,
        time_to=t_to,
        query=args.get("query", ""),
        severity=args.get("severity"),
        domain=args.get("domain"),
        business_line_id=business_line_id or None,
        language=language,
        size=size,
    )

    result = await log_service.search_logs(request)

    # Format for AI consumption (compact) — mask sensitive data
    logs = []
    for log in result.logs:
        logs.append({
            "timestamp": log.timestamp,
            "level": log.level,
            "message": mask_sensitive(log.message[:800]),
            "domain": log.domain,
            "filetype": log.filetype,
        })

    return json.dumps({
        "total_hits": result.total,
        "returned": len(logs),
        "logs": logs,
    }, ensure_ascii=False, default=str)


async def _exec_get_log_context(
    args: dict,
    index_pattern: str,
    business_line_id: str = "",
    language: str | None = None,
) -> str:
    """Execute get_log_context tool."""
    from logmind.domain.log.schemas import LogQueryRequest
    from logmind.domain.analysis.sensitive_masker import mask_sensitive

    ts = _parse_time(args.get("timestamp"))
    if not ts:
        return json.dumps({"error": "timestamp is required"})

    window = args.get("window_minutes", 5)
    size = min(args.get("size", 30), 50)
    # Context must include the successful operations immediately before/after
    # an error. Sensitive values are masked before returning to the model.
    severity = args.get("severity")

    request = LogQueryRequest(
        index_pattern=index_pattern,
        time_from=ts - timedelta(minutes=window),
        time_to=ts + timedelta(minutes=window),
        severity=severity,
        business_line_id=business_line_id or None,
        language=language,
        size=size,
    )

    result = await log_service.search_logs(request)

    # Mask sensitive data before returning to Agent/LLM
    logs = []
    for log in result.logs:
        logs.append({
            "timestamp": log.timestamp,
            "level": log.level,
            "message": mask_sensitive(log.message[:500]),
            "domain": log.domain,
            "filetype": log.filetype,
        })

    return json.dumps({
        "center_timestamp": ts.isoformat(),
        "window_minutes": window,
        "severity_filter": severity,
        "total_hits": result.total,
        "logs": logs,
    }, ensure_ascii=False, default=str)


async def _exec_count_error_patterns(
    args: dict,
    index_pattern: str,
    default_from,
    default_to,
    *,
    business_line_id: str = "",
    language: str | None = None,
) -> str:
    """Execute count_error_patterns tool."""

    t_from = _parse_time(args.get("time_from")) or default_from
    t_to = _parse_time(args.get("time_to")) or default_to

    if not t_from or not t_to:
        return json.dumps({"error": "time range is required"})

    stats = await log_service.get_log_stats(
        index_pattern,
        t_from,
        t_to,
        severity="error",
        business_line_id=business_line_id,
        language=language,
    )

    group_by = args.get("group_by", "filetype")

    result = {
        "error_count": stats.total_logs,
        "time_range": f"{t_from.isoformat()} ~ {t_to.isoformat()}",
    }

    if group_by == "filetype":
        result["by_filetype"] = [{"type": a.key, "count": a.count} for a in stats.by_filetype]
    elif group_by == "domain":
        result["by_domain"] = [{"domain": a.key, "count": a.count} for a in stats.by_domain]
    elif group_by == "time_histogram":
        result["time_histogram"] = stats.time_histogram[:50]  # Cap buckets

    # Always include level distribution
    result["by_level"] = [{"level": a.key, "count": a.count} for a in stats.by_level]

    return json.dumps(result, ensure_ascii=False, default=str)


async def _exec_list_indices(args: dict, index_pattern: str) -> str:
    """Execute list_available_indices tool."""
    pattern = index_pattern.strip() if index_pattern else ""
    if not pattern:
        return json.dumps({"error": "index pattern is required"})

    indices = await log_service.list_indices(pattern)

    return json.dumps({
        "count": len(indices),
        "indices": [
            {"name": idx.name, "docs_count": idx.docs_count, "size": idx.size}
            for idx in indices[:30]  # Cap at 30
        ],
    }, ensure_ascii=False, default=str)


async def _resolve_knowledge_base_id(tenant_id: str, requested_kb_id: str) -> str | None:
    """Resolve and authorize a tenant-owned knowledge base for Agent search."""
    if not tenant_id:
        return None

    from sqlalchemy import select

    from logmind.core.database import get_db_context
    from logmind.domain.rag.models import KnowledgeBase

    async with get_db_context() as session:
        if requested_kb_id and requested_kb_id != "default":
            kb = await session.get(KnowledgeBase, requested_kb_id)
            if kb and kb.tenant_id == tenant_id and kb.is_active:
                return kb.id
            return None

        result = await session.execute(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.is_active == True,  # noqa: E712
            )
            .order_by(KnowledgeBase.created_at.desc())
            .limit(1)
        )
        kb = result.scalar_one_or_none()
        return kb.id if kb else None


async def _exec_search_knowledge_base(args: dict, tenant_id: str = "") -> str:
    """Execute search_knowledge_base tool (with Embedding Redis cache)."""
    from logmind.domain.analysis.semantic_dedup import cached_embed
    from logmind.core.config import get_settings
    from logmind.domain.log.service import log_service

    query = args.get("query")
    if not query:
        return json.dumps({"error": "query is required"})

    kb_id = args.get("kb_id", "default")
    settings = get_settings()

    try:
        kb_id = await _resolve_knowledge_base_id(tenant_id, str(kb_id or "default"))
        if not kb_id:
            return "未找到当前租户可用的知识库。"

        # Embed the query (with Redis cache — avoids repeated API calls)
        query_vector = await cached_embed(
            text=query,
            redis_url=settings.redis_url,
            cache_ttl=settings.analysis_embedding_cache_ttl_seconds,
            tenant_id=tenant_id,
        )
        if query_vector is None:
            return json.dumps({"error": "Embedding provider not available"})

        # Search ES
        results = await log_service.knn_search(kb_id, query_vector, k=3)

        if not results:
            return "未找到相关的知识库文档。"

        formatted_results = []
        for i, res in enumerate(results):
            score = res.get("score", 0)
            metadata = res.get("metadata", {})
            content = res.get("content", "")
            formatted_results.append(
                f"--- 文档 {i + 1} (相关度: {score:.2f}) ---\n"
                f"来源: {metadata.get('filename', '未知')}\n"
                f"内容片段:\n{content}\n"
            )

        return "\n".join(formatted_results)

    except Exception as e:
        logger.error("search_knowledge_base_error", error=str(e))
        return json.dumps({"error": f"Search failed: {str(e)}"})


async def _exec_search_similar_incidents(
    args: dict,
    index_pattern: str,
    tenant_id: str = "",
    business_line_id: str = "",
) -> str:
    """Execute search_similar_incidents tool — find historically similar analyses."""
    from logmind.domain.analysis.semantic_dedup import cached_embed
    from logmind.core.config import get_settings
    from logmind.domain.log.service import log_service

    error_pattern = args.get("error_pattern")
    if not error_pattern:
        return json.dumps({"error": "error_pattern is required"})

    settings = get_settings()

    try:
        # Embed the error pattern (with Redis cache)
        query_vector = await cached_embed(
            text=error_pattern,
            redis_url=settings.redis_url,
            cache_ttl=settings.analysis_embedding_cache_ttl_seconds,
            tenant_id=tenant_id,
        )
        if query_vector is None:
            return json.dumps({"error": "Embedding provider not available"})

        if business_line_id:
            matches = await log_service.knn_search_analysis_history(
                business_line_id=business_line_id,
                query_vector=query_vector,
                k=3,
                min_score=0.75,
            )
            if not matches:
                return "未找到与当前错误模式相似的历史分析记录。"

            formatted = []
            for i, match in enumerate(matches):
                formatted.append(
                    f"--- 历史事件 {i + 1} (相似度: {match.get('score', 0):.2f}) ---\n"
                    f"严重级别: {match.get('severity', 'unknown')}\n"
                    f"分析时间: {match.get('created_at', '未知')}\n"
                    f"错误签名: {match.get('error_signature', '')[:100]}\n"
                    f"历史结论:\n{match.get('analysis_content', '无内容')[:800]}\n"
                )
            return "\n".join(formatted)

        # Fallback for legacy/manual calls without business context.
        index_name = "logmind-analysis-vectors"
        exists = await log_service.es.indices.exists(index=index_name)
        if not exists:
            return "暂无历史分析记录。系统将在后续分析中逐步积累。"

        from datetime import timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        resp = await log_service.es.search(
            index=index_name,
            knn={
                "field": "embedding",
                "query_vector": query_vector,
                "k": 3,
                "num_candidates": 50,
                "filter": {
                    "range": {"ttl_expire_at": {"gte": now_iso}}
                },
            },
            source=["analysis_content", "severity", "error_signature", "task_id", "created_at"],
        )

        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            return "未找到与当前错误模式相似的历史分析记录。"

        formatted = []
        for i, hit in enumerate(hits):
            src = hit["_source"]
            score = hit["_score"]
            formatted.append(
                f"--- 历史事件 {i + 1} (相似度: {score:.2f}) ---\n"
                f"严重级别: {src.get('severity', 'unknown')}\n"
                f"分析时间: {src.get('created_at', '未知')}\n"
                f"错误签名: {src.get('error_signature', '')[:100]}\n"
                f"历史结论:\n{src.get('analysis_content', '无内容')[:800]}\n"
            )

        return "\n".join(formatted)

    except Exception as e:
        logger.error("search_similar_incidents_error", error=str(e))
        return json.dumps({"error": f"Search failed: {str(e)}"})


async def _exec_search_cross_service_logs(
    args: dict,
    current_index_pattern: str,
    related_index_patterns: list[str] | None = None,
) -> str:
    """Search error logs across other business lines in the same tenant."""
    from logmind.domain.log.schemas import LogQueryRequest
    from logmind.domain.log.service import log_service

    keyword = args.get("keyword")
    if not keyword:
        return json.dumps({"error": "keyword is required"})

    minutes_back = args.get("minutes_back", 30)
    related_index_patterns = related_index_patterns or []

    try:
        index_str = _join_index_patterns(related_index_patterns)
        if not index_str:
            return "未配置可搜索的关联服务索引。"

        time_from = datetime.now(timezone.utc) - timedelta(minutes=minutes_back)
        time_to = datetime.now(timezone.utc)

        # A1 fix: search_logs requires LogQueryRequest, not kwargs
        request = LogQueryRequest(
            index_pattern=index_str,
            time_from=time_from,
            time_to=time_to,
            query=keyword,
            severity="error",
            size=10,
        )
        result = await log_service.search_logs(request)

        if not result.logs:
            return f"在关联服务中未发现与 '{keyword}' 相关的错误日志。"

        # Format results — mask sensitive data before returning to Agent/LLM
        from logmind.domain.analysis.sensitive_masker import mask_sensitive

        formatted = [f"跨服务搜索结果（关键词: {keyword}，搜索范围: 已配置关联服务）：\n"]
        for i, log in enumerate(result.logs[:10]):
            formatted.append(
                f"--- [{i+1}] 来源: {log.domain or '未知'} ---\n"
                f"时间: {log.timestamp}\n"
                f"级别: {log.level}\n"
                f"内容: {mask_sensitive(log.message[:200])}\n"
            )

        return "\n".join(formatted)

    except Exception as e:
        logger.error("search_cross_service_error", error=str(e))
        return json.dumps({"error": f"Cross-service search failed: {str(e)}"})


# ── Helpers ──────────────────────────────────────────────

def _parse_time(value: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp string."""
    if not value:
        return None
    try:
        from dateutil.parser import parse
        return parse(value)
    except Exception:
        return None


# ── New Tool Implementations (v5.1) ─────────────────────

async def _exec_get_alerts(
    args: dict,
    tenant_id: str = "",
    business_line_id: str = "",
) -> str:
    """Query recent alert history from PostgreSQL."""
    from logmind.core.database import get_db_context
    from logmind.domain.alert.models import AlertHistory
    from logmind.domain.analysis.models import LogAnalysisTask
    from sqlalchemy import or_, select

    hours_back = min(args.get("hours_back", 24), 72)
    severity_filter = args.get("severity")
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    async with get_db_context() as session:
        stmt = (
            select(AlertHistory)
            .outerjoin(LogAnalysisTask, AlertHistory.analysis_task_id == LogAnalysisTask.id)
            .where(AlertHistory.fired_at >= since)
            .order_by(AlertHistory.fired_at.desc())
            .limit(20)
        )
        if tenant_id:
            stmt = stmt.where(AlertHistory.tenant_id == tenant_id)
        if business_line_id:
            stmt = stmt.where(
                or_(
                    AlertHistory.business_line_id == business_line_id,
                    LogAnalysisTask.business_line_id == business_line_id,
                )
            )
        if severity_filter:
            stmt = stmt.where(AlertHistory.severity == severity_filter)

        result = await session.execute(stmt)
        alerts = result.scalars().all()

    if not alerts:
        return f"最近 {hours_back} 小时内无告警记录。"

    lines = [f"最近 {hours_back} 小时告警记录（共 {len(alerts)} 条）：\n"]
    for a in alerts:
        time_str = a.fired_at.strftime("%m-%d %H:%M") if a.fired_at else "?"
        lines.append(
            f"- [{time_str}] [{a.severity}] {a.priority or ''} {a.message[:150]}"
        )
    return "\n".join(lines)


async def _exec_get_service_health(
    args: dict,
    index_pattern: str,
    default_from,
    default_to,
    *,
    business_line_id: str = "",
    language: str | None = None,
) -> str:
    """Query service health metrics from ES: error rate, total count, hourly trend."""
    from logmind.core.elasticsearch import get_es_client
    from logmind.domain.log.service import build_severity_filter

    hours_back = min(args.get("hours_back", 6), 24)
    now = default_to or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours_back)

    es = get_es_client()
    error_filter = await build_severity_filter(
        "error",
        business_line_id=business_line_id,
        language=language,
    )

    # Total + error count
    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": since.isoformat(), "lte": now.isoformat()}}},
                ]
            }
        },
        "aggs": {
            "total": {"value_count": {"field": "@timestamp"}},
            "by_level": {
                "terms": {"field": "level.keyword", "size": 10}
            },
            "errors": {
                "filter": error_filter,
            },
            "hourly": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": "1h",
                },
                "aggs": {
                    "errors": {
                        "filter": error_filter
                    }
                }
            }
        }
    }

    try:
        resp = await es.search(index=index_pattern, body=body)
        aggs = resp.get("aggregations", {})

        total = aggs.get("total", {}).get("value", 0)
        level_buckets = aggs.get("by_level", {}).get("buckets", [])
        level_dist = {b["key"]: b["doc_count"] for b in level_buckets}
        error_count = aggs.get("errors", {}).get("doc_count", 0)
        error_rate = round(error_count / max(total, 1) * 100, 2)

        hourly = aggs.get("hourly", {}).get("buckets", [])
        trend_lines = []
        for h in hourly[-hours_back:]:
            hr = h.get("key_as_string", "")[-8:-3]
            err = h.get("errors", {}).get("doc_count", 0)
            trend_lines.append(f"  {hr}: {err} errors")

        return (
            f"服务健康概况（最近 {hours_back} 小时）\n"
            f"- 总日志: {total}\n"
            f"- 错误数: {error_count} (错误率: {error_rate}%)\n"
            f"- 级别分布: {level_dist}\n"
            f"- 每小时错误趋势:\n" + "\n".join(trend_lines)
        )
    except Exception as e:
        return json.dumps({"error": f"Service health query failed: {str(e)}"})


async def _exec_compare_time_windows(
    args: dict,
    index_pattern: str,
    default_to,
    *,
    business_line_id: str = "",
    language: str | None = None,
) -> str:
    """Compare error distribution between two time windows."""
    from logmind.core.elasticsearch import get_es_client
    from logmind.domain.log.service import build_severity_filter

    window_hours = min(args.get("window_hours", 1), 6)
    now = default_to or datetime.now(timezone.utc)
    current_start = now - timedelta(hours=window_hours)
    prev_start = current_start - timedelta(hours=window_hours)

    es = get_es_client()
    error_filter = await build_severity_filter(
        "error",
        business_line_id=business_line_id,
        language=language,
    )

    async def _count_errors(start, end):
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": start.isoformat(),
                                    "lt": end.isoformat(),
                                }
                            }
                        },
                        error_filter,
                    ]
                }
            },
            "aggs": {
                "top_errors": {
                    "terms": {"field": "message.keyword", "size": 5}
                }
            }
        }
        resp = await es.search(index=index_pattern, body=body)
        total = resp.get("hits", {}).get("total", {}).get("value", 0)
        top = [
            {"msg": b["key"][:100], "count": b["doc_count"]}
            for b in resp.get("aggregations", {}).get("top_errors", {}).get("buckets", [])
        ]
        return total, top

    try:
        curr_total, curr_top = await _count_errors(current_start, now)
        prev_total, prev_top = await _count_errors(prev_start, current_start)

        change = curr_total - prev_total
        change_pct = round(change / max(prev_total, 1) * 100, 1)
        arrow = "↑" if change > 0 else "↓" if change < 0 else "→"

        curr_top_str = "\n".join(f"  {t['count']}x {t['msg']}" for t in curr_top) or "  无"
        prev_top_str = "\n".join(f"  {t['count']}x {t['msg']}" for t in prev_top) or "  无"

        return (
            f"时间窗口对比（每窗口 {window_hours}h）\n\n"
            f"当前窗口: {curr_total} errors\n{curr_top_str}\n\n"
            f"前一窗口: {prev_total} errors\n{prev_top_str}\n\n"
            f"变化: {arrow} {abs(change)} ({change_pct}%)"
        )
    except Exception as e:
        return json.dumps({"error": f"Compare failed: {str(e)}"})


async def _exec_trace_error_chain(
    args: dict,
    index_pattern: str,
    default_to,
    related_index_patterns: list[str] | None = None,
) -> str:
    """Trace error chain across related services."""
    from logmind.core.elasticsearch import get_es_client
    from logmind.domain.analysis.sensitive_masker import mask_sensitive

    keyword = args.get("error_keyword", "")
    if not keyword:
        return json.dumps({"error": "error_keyword is required"})

    minutes_back = min(args.get("minutes_back", 30), 120)
    now = default_to or datetime.now(timezone.utc)
    since = now - timedelta(minutes=minutes_back)
    search_index = _join_index_patterns([index_pattern] + (related_index_patterns or []))
    if not search_index:
        return "未配置可搜索的服务索引。"

    es = get_es_client()

    # Search only the current service and explicitly configured related services.
    body = {
        "size": 30,
        "query": {
            "bool": {
                "must": [{"match_phrase": {"message": keyword}}],
                "filter": [
                    {"range": {"@timestamp": {"gte": since.isoformat(), "lte": now.isoformat()}}},
                ],
            }
        },
        "sort": [{"@timestamp": "asc"}],
    }

    try:
        resp = await es.search(index=search_index, body=body)
        hits = resp.get("hits", {}).get("hits", [])

        if not hits:
            return f"未找到与 '{keyword}' 相关的跨服务错误链。"

        # Group by index (service)
        by_service: dict[str, list] = {}
        for hit in hits:
            idx = hit["_index"]
            src = hit["_source"]
            by_service.setdefault(idx, []).append({
                "time": src.get("@timestamp", "")[-12:-1],
                "level": src.get("level", "?"),
                "msg": mask_sensitive(src.get("message", "")[:150]),
            })

        lines = [f"错误链追踪: '{keyword}' (最近 {minutes_back} 分钟, {len(hits)} 条)\n"]
        for svc, logs in sorted(by_service.items()):
            lines.append(f"\n📦 服务: {svc} ({len(logs)} 条)")
            for log in logs[:5]:
                lines.append(f"  {log['time']} [{log['level']}] {log['msg']}")

        return "\n".join(lines)
    except Exception as e:
        return json.dumps({"error": f"Trace failed: {str(e)}"})


async def _exec_create_analysis_task(args: dict, index_pattern: str) -> str:
    """Create a deep analysis task (fallback when direct investigation is insufficient)."""
    reason = args.get("reason", "Agent 请求深度分析")
    return json.dumps({
        "status": "noted",
        "message": (
            f"已记录深度分析需求: {reason}。"
            "建议在最终结论中说明需要创建深度分析任务，"
            "由用户在分析中心手动创建。"
        ),
    }, ensure_ascii=False)
