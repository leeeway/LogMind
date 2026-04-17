"""
Semantic Dedup Stage — Vector-Level Error Deduplication (Phase 3)

Replaces the coarse MD5 fingerprint approach with embedding-based
semantic similarity matching. Errors that are semantically equivalent
(even with different line numbers or call paths) are identified as
duplicates and their previous AI analysis conclusions are reused.

Flow:
  1. Extract error signature from processed logs (exception class + core stack)
  2. Embed the signature (with Redis caching to avoid repeated API calls)
  3. KNN search the analysis-vectors index for semantically similar past analyses
  4. If match found (cosine > threshold) → reuse historical conclusions, skip LLM
  5. If no match → continue normal Agent flow; results will be indexed later
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)

# Exception class name pattern (Java + C#)
_EXCEPTION_CLASS_RE = re.compile(r"([\w.]+(?:Exception|Error|Throwable|Fault))")

# Stack trace "at" line — extract class and method only (strip line numbers)
_AT_LINE_RE = re.compile(r"at\s+([\w.$]+)\(")


def extract_error_signature(processed_logs: str, language: str = "java") -> str:
    """
    Extract a stable error signature from processed log text.

    Strategy:
      - Collect all unique exception class names
      - Collect unique "at ClassName.method" frames (without line numbers)
      - Combine into a stable signature that is resilient to minor variations

    This signature is then embedded for vector comparison, so minor differences
    in line numbers or call order won't affect the similarity score.
    """
    if not processed_logs:
        return ""

    exception_classes = set()
    stack_methods = []

    for line in processed_logs.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Extract exception class names
        for match in _EXCEPTION_CLASS_RE.finditer(stripped):
            exception_classes.add(match.group(1))

        # Extract method names from stack frames (line-number-agnostic)
        at_match = _AT_LINE_RE.search(stripped)
        if at_match and len(stack_methods) < 10:
            method = at_match.group(1)
            if method not in stack_methods:
                stack_methods.append(method)

    # If no structured exception found, use first 300 chars of error messages
    if not exception_classes:
        # Extract ERROR lines only
        error_lines = []
        for line in processed_logs.split("\n"):
            if "[ERROR]" in line.upper() or "[FATAL]" in line.upper():
                # Strip timestamp prefix for stability
                msg = re.sub(r"^\[.*?\]\s*\[.*?\]\s*(\[.*?\]\s*)?", "", line)
                error_lines.append(msg.strip()[:150])
                if len(error_lines) >= 3:
                    break
        if error_lines:
            return "ERRORS: " + " | ".join(error_lines)
        return processed_logs[:300]

    # Build stable signature
    parts = []
    parts.append("EXCEPTIONS: " + ", ".join(sorted(exception_classes)))
    if stack_methods:
        parts.append("STACK: " + " → ".join(stack_methods[:8]))

    return " | ".join(parts)


async def cached_embed(
    text: str,
    redis_url: str,
    cache_ttl: int = 3600,
) -> list[float] | None:
    """
    Embed text using OpenAI, with Redis caching to avoid repeated API calls.

    Returns the embedding vector, or None if embedding fails.
    """
    cache_key = f"logmind:emb_cache:{hashlib.md5(text.encode()).hexdigest()}"

    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        # Check cache first
        cached = await r.get(cache_key)
        if cached:
            logger.info("embedding_cache_hit", key=cache_key[:40])
            await r.aclose()
            return json.loads(cached)
    except Exception as e:
        logger.warning("embedding_cache_read_error", error=str(e))
        r = None

    # Cache miss — call embedding API
    try:
        from logmind.domain.provider.base import EmbeddingRequest
        from logmind.domain.provider.manager import provider_manager
        from logmind.core.database import get_db_context

        # We need a db session to get provider config
        async with get_db_context() as session:
            from sqlalchemy import select
            from logmind.domain.provider.models import ProviderConfig

            # Find any active provider that supports embeddings (prefer openai)
            stmt = (
                select(ProviderConfig)
                .where(
                    ProviderConfig.is_active == True,
                    ProviderConfig.provider_type.in_(["openai", "subapi", "deepseek"]),
                )
                .order_by(ProviderConfig.priority.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            config = result.scalar_one_or_none()

            if not config:
                logger.warning("no_embedding_provider_found")
                return None

            provider = provider_manager._create_or_get_cached(config)

        req = EmbeddingRequest(texts=[text])
        resp = await provider.embed(req)
        vector = resp.embeddings[0]

        # Write to cache
        if r:
            try:
                await r.setex(cache_key, cache_ttl, json.dumps(vector))
            except Exception:
                pass
            finally:
                await r.aclose()

        return vector

    except Exception as e:
        logger.error("embedding_failed", error=str(e))
        if r:
            await r.aclose()
        return None


class SemanticDedupStage(PipelineStage):
    """
    Pipeline stage: vector-level semantic deduplication.

    Checks if the current error pattern has been analyzed before by
    comparing embedding vectors. If a semantically similar historical
    analysis is found, reuses those conclusions and skips LLM inference.

    Non-critical — if Redis/ES/Embedding fails, all logs pass through.
    """

    name = "semantic_dedup"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        settings = get_settings()

        if not settings.analysis_semantic_dedup_enabled:
            logger.info("semantic_dedup_disabled", task_id=ctx.task_id)
            return ctx

        if not ctx.processed_logs or ctx.processed_logs.startswith("(No logs"):
            return ctx

        try:
            # 1. Extract error signature (line-number-agnostic)
            error_sig = extract_error_signature(ctx.processed_logs, ctx.language)
            if not error_sig or len(error_sig) < 20:
                logger.info("semantic_dedup_sig_too_short", task_id=ctx.task_id)
                return ctx

            ctx.error_signature = error_sig

            # 2. Embed the signature (with Redis cache)
            vector = await cached_embed(
                text=error_sig,
                redis_url=settings.redis_url,
                cache_ttl=settings.analysis_embedding_cache_ttl_seconds,
            )
            if vector is None:
                logger.warning("semantic_dedup_embed_failed", task_id=ctx.task_id)
                return ctx

            # 3. KNN search for similar historical analyses
            from logmind.domain.log.service import log_service

            matches = await log_service.knn_search_analysis_history(
                business_line_id=ctx.business_line_id,
                query_vector=vector,
                k=1,
                min_score=settings.analysis_semantic_dedup_threshold,
            )

            if matches:
                match = matches[0]
                logger.info(
                    "semantic_dedup_hit",
                    score=match["score"],
                    historical_task=match.get("task_id", "")[:8],
                    task_id=ctx.task_id,
                )

                # Reuse historical analysis conclusions
                ctx.analysis_results = [{
                    "result_type": "root_cause",
                    "content": (
                        f"[语义去重命中] 本次错误模式与历史分析任务 {match.get('task_id', '')[:8]}... "
                        f"高度相似（相似度: {match['score']:.2f}），以下为历史分析结论：\n\n"
                        f"{match['analysis_content']}"
                    ),
                    "severity": match.get("severity", "warning"),
                    "confidence_score": match["score"],
                    "structured_data": json.dumps({
                        "dedup_source": "semantic",
                        "historical_task_id": match.get("task_id", ""),
                        "similarity_score": match["score"],
                    }, ensure_ascii=False),
                }]
                ctx.semantic_dedup_hit = True

                ctx.log_metadata["semantic_dedup_hit"] = True
                ctx.log_metadata["semantic_dedup_score"] = match["score"]
                ctx.log_metadata["semantic_dedup_historical_task"] = match.get("task_id", "")
            else:
                logger.info("semantic_dedup_miss", task_id=ctx.task_id)
                ctx.semantic_dedup_hit = False

        except Exception as e:
            # Non-critical: if anything fails, let all logs through
            logger.warning("semantic_dedup_error", error=str(e), task_id=ctx.task_id)

        return ctx
