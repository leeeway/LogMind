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
from logmind.domain.log.service import LogService

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


# ── Tool Execution ───────────────────────────────────────

async def execute_tool(
    tool_name: str,
    arguments: dict,
    es_index_pattern: str,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> str:
    """
    Execute an agent tool and return the result as a string.

    All tools are bounded by the business line's index pattern
    to prevent cross-tenant data leaks.
    """
    try:
        if tool_name == "search_logs":
            return await _exec_search_logs(arguments, es_index_pattern, time_from, time_to)
        elif tool_name == "get_log_context":
            return await _exec_get_log_context(arguments, es_index_pattern)
        elif tool_name == "count_error_patterns":
            return await _exec_count_error_patterns(arguments, es_index_pattern, time_from, time_to)
        elif tool_name == "list_available_indices":
            return await _exec_list_indices(arguments)
        elif tool_name == "search_knowledge_base":
            return await _exec_search_knowledge_base(arguments)
        elif tool_name == "search_similar_incidents":
            return await _exec_search_similar_incidents(arguments, es_index_pattern)
        elif tool_name == "search_cross_service_logs":
            return await _exec_search_cross_service_logs(arguments, es_index_pattern)
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
    except Exception as e:
        logger.warning("agent_tool_error", tool=tool_name, error=str(e))
        return json.dumps({"error": str(e)})


async def _exec_search_logs(args: dict, index_pattern: str, default_from, default_to) -> str:
    """Execute search_logs tool."""
    from logmind.domain.log.schemas import LogQueryRequest

    service = LogService()
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
        size=size,
    )

    result = await service.search_logs(request)

    # Format for AI consumption (compact)
    logs = []
    for log in result.logs:
        logs.append({
            "timestamp": log.timestamp,
            "level": log.level,
            "message": log.message[:500],
            "domain": log.domain,
            "filetype": log.filetype,
        })

    return json.dumps({
        "total_hits": result.total,
        "returned": len(logs),
        "logs": logs,
    }, ensure_ascii=False, default=str)


async def _exec_get_log_context(args: dict, index_pattern: str) -> str:
    """Execute get_log_context tool."""
    from logmind.domain.log.schemas import LogQueryRequest

    ts = _parse_time(args.get("timestamp"))
    if not ts:
        return json.dumps({"error": "timestamp is required"})

    window = args.get("window_minutes", 5)
    size = min(args.get("size", 30), 50)

    service = LogService()
    request = LogQueryRequest(
        index_pattern=index_pattern,
        time_from=ts - timedelta(minutes=window),
        time_to=ts + timedelta(minutes=window),
        size=size,
    )

    result = await service.search_logs(request)

    logs = []
    for log in result.logs:
        logs.append({
            "timestamp": log.timestamp,
            "level": log.level,
            "message": log.message[:500],
            "domain": log.domain,
        })

    return json.dumps({
        "center_timestamp": ts.isoformat(),
        "window_minutes": window,
        "total_hits": result.total,
        "logs": logs,
    }, ensure_ascii=False, default=str)


async def _exec_count_error_patterns(args: dict, index_pattern: str, default_from, default_to) -> str:
    """Execute count_error_patterns tool."""
    service = LogService()

    t_from = _parse_time(args.get("time_from")) or default_from
    t_to = _parse_time(args.get("time_to")) or default_to

    if not t_from or not t_to:
        return json.dumps({"error": "time range is required"})

    stats = await service.get_log_stats(index_pattern, t_from, t_to)

    group_by = args.get("group_by", "filetype")

    result = {
        "total_logs": stats.total_logs,
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


async def _exec_list_indices(args: dict) -> str:
    """Execute list_available_indices tool."""
    service = LogService()
    pattern = args.get("pattern", "*")

    indices = await service.list_indices(pattern)

    return json.dumps({
        "count": len(indices),
        "indices": [
            {"name": idx.name, "docs_count": idx.docs_count, "size": idx.size}
            for idx in indices[:30]  # Cap at 30
        ],
    }, ensure_ascii=False, default=str)


async def _exec_search_knowledge_base(args: dict) -> str:
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
        # Embed the query (with Redis cache — avoids repeated API calls)
        query_vector = await cached_embed(
            text=query,
            redis_url=settings.redis_url,
            cache_ttl=settings.analysis_embedding_cache_ttl_seconds,
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


async def _exec_search_similar_incidents(args: dict, index_pattern: str) -> str:
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
        )
        if query_vector is None:
            return json.dumps({"error": "Embedding provider not available"})

        # Search all business lines with a lower threshold for broader results
        # Note: we search globally (no biz_id filter) since the agent may
        # want to see incidents across services
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


async def _exec_search_cross_service_logs(args: dict, current_index_pattern: str) -> str:
    """Search error logs across other business lines in the same tenant."""
    from logmind.domain.log.schemas import LogQueryRequest
    from logmind.domain.log.service import log_service

    keyword = args.get("keyword")
    if not keyword:
        return json.dumps({"error": "keyword is required"})

    service_name = args.get("service_name", "")
    minutes_back = args.get("minutes_back", 30)

    try:
        # Discover all available indices (excluding current business line's)
        all_indices = await log_service.list_indices("*")

        # Filter out current business line's indices
        current_patterns = [p.strip() for p in current_index_pattern.split(",")]
        other_indices = []
        for idx_info in all_indices:
            # A2 fix: list_indices returns ESIndexInfo objects, use .name attribute
            idx_name = idx_info.name
            # Skip system/KB/vector indices
            if idx_name.startswith(".") or idx_name.startswith("logmind-"):
                continue
            # Skip current business line indices
            is_current = False
            for pat in current_patterns:
                pat_base = pat.replace("*", "")
                if pat_base and idx_name.startswith(pat_base):
                    is_current = True
                    break
            if not is_current:
                # If service_name filter provided, only include matching indices
                if service_name and service_name.lower() not in idx_name.lower():
                    continue
                other_indices.append(idx_name)

        if not other_indices:
            return "未找到其他可搜索的服务索引。"

        # Search across other indices (limit to 5 indices to control cost)
        search_indices = other_indices[:5]
        index_str = ",".join(search_indices)

        time_from = datetime.now(timezone.utc) - timedelta(minutes=minutes_back)
        time_to = datetime.now(timezone.utc)

        # A1 fix: search_logs requires LogQueryRequest, not kwargs
        svc = LogService()
        request = LogQueryRequest(
            index_pattern=index_str,
            time_from=time_from,
            time_to=time_to,
            query=keyword,
            severity="error",
            size=10,
        )
        result = await svc.search_logs(request)

        if not result.logs:
            return f"在其他 {len(search_indices)} 个服务中未发现与 '{keyword}' 相关的错误日志。"

        # Format results using LogQueryResponse.logs (LogEntry objects)
        formatted = [f"跨服务搜索结果（关键词: {keyword}，搜索范围: {len(search_indices)} 个服务索引）：\n"]
        for i, log in enumerate(result.logs[:10]):
            formatted.append(
                f"--- [{i+1}] 来源: {log.domain or '未知'} ---\n"
                f"时间: {log.timestamp}\n"
                f"级别: {log.level}\n"
                f"内容: {log.message[:200]}\n"
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
