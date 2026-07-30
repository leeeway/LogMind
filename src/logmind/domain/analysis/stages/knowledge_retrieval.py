"""Knowledge Retrieval Stage — deterministically preload tenant RAG context."""

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)

_MAX_KNOWLEDGE_BASES = 5
_MAX_MATCHES = 4
_MIN_RELEVANCE_SCORE = 0.6
_MAX_CHUNK_CHARS = 800


class KnowledgeRetrievalStage(PipelineStage):
    """Retrieve relevant internal knowledge before the model starts analysis.

    Agent tool calling remains available for follow-up investigation, but the
    first model request no longer depends on the model deciding to call the
    knowledge-base tool itself.
    """

    name = "knowledge_retrieval"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.tenant_id or not ctx.processed_logs.strip():
            return ctx

        from sqlalchemy import select

        from logmind.core.database import get_db_context
        from logmind.domain.analysis.semantic_dedup import (
            cached_embed,
            extract_error_signature,
        )
        from logmind.domain.log.service import log_service
        from logmind.domain.rag.models import KnowledgeBase

        async with get_db_context() as session:
            result = await session.execute(
                select(
                    KnowledgeBase.id,
                    KnowledgeBase.name,
                    KnowledgeBase.embedding_provider_id,
                )
                .where(
                    KnowledgeBase.tenant_id == ctx.tenant_id,
                    KnowledgeBase.is_active == True,  # noqa: E712
                )
                .order_by(KnowledgeBase.updated_at.desc())
                .limit(_MAX_KNOWLEDGE_BASES)
            )
            knowledge_bases = [
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]) if row[2] else None,
                )
                for row in result.all()
                if row[0]
            ]

        if not knowledge_bases:
            logger.info(
                "knowledge_retrieval_skipped_no_active_kb",
                task_id=ctx.task_id,
            )
            return ctx

        query = ctx.error_signature or extract_error_signature(
            ctx.processed_logs,
            ctx.language,
        )
        query = query.strip()[:1200]
        if not query:
            return ctx

        from logmind.core.config import get_settings

        settings = get_settings()
        matches: list[dict] = []
        vectors_by_provider: dict[str, list[float] | None] = {}
        for kb_id, kb_name, embedding_provider_id in knowledge_bases:
            provider_key = embedding_provider_id or "default"
            if provider_key not in vectors_by_provider:
                vectors_by_provider[provider_key] = await cached_embed(
                    text=query,
                    redis_url=settings.redis_url,
                    cache_ttl=settings.analysis_embedding_cache_ttl_seconds,
                    tenant_id=ctx.tenant_id,
                    provider_config_id=embedding_provider_id,
                )
            query_vector = vectors_by_provider[provider_key]
            if query_vector is None:
                continue
            for match in await log_service.knn_search(kb_id, query_vector, k=2):
                score = float(match.get("score") or 0)
                if score < _MIN_RELEVANCE_SCORE:
                    continue
                matches.append({
                    **match,
                    "score": score,
                    "kb_id": kb_id,
                    "kb_name": kb_name,
                })

        matches.sort(key=lambda item: item["score"], reverse=True)
        matches = matches[:_MAX_MATCHES]
        if not matches:
            logger.info(
                "knowledge_retrieval_no_match",
                kb_count=len(knowledge_bases),
                task_id=ctx.task_id,
            )
            return ctx

        sections: list[str] = []
        sources: list[str] = []
        for index, match in enumerate(matches, start=1):
            metadata = (
                match.get("metadata")
                if isinstance(match.get("metadata"), dict)
                else {}
            )
            filename = str(
                metadata.get("filename")
                or metadata.get("title")
                or "未命名文档"
            )
            source = f"{match['kb_name']} / {filename}"
            if source not in sources:
                sources.append(source)
            content = str(match.get("content") or "").strip()[:_MAX_CHUNK_CHARS]
            sections.append(
                f"{index}. 来源: {source}（相关度 {match['score']:.2f}）\n"
                f"{content}"
            )

        ctx.rag_context = (
            "以下内容来自当前租户的内部知识库，仅作为历史经验参考；"
            "必须用本次日志证据独立验证，禁止直接照抄为根因。\n\n"
            + "\n\n".join(sections)
        )
        ctx.rag_sources = sources
        ctx.log_metadata["knowledge_retrieval_count"] = len(matches)
        ctx.log_metadata["knowledge_sources"] = sources
        logger.info(
            "knowledge_context_retrieved",
            kb_count=len(knowledge_bases),
            match_count=len(matches),
            sources=sources,
            task_id=ctx.task_id,
        )
        return ctx
