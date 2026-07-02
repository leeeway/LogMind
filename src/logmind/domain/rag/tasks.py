import asyncio
import json

from logmind.core.async_task import run_async
from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


async def _async_index_document(document_id: str):
    from logmind.core.database import get_db_context
    from logmind.domain.rag.models import KBDocument, KnowledgeBase
    from logmind.domain.log.service import log_service
    from logmind.domain.provider.manager import provider_manager
    from logmind.domain.provider.base import EmbeddingRequest

    async with get_db_context() as session:
        # 1. Fetch document and KB
        doc: KBDocument = await session.get(KBDocument, document_id)
        if not doc:
            logger.error("kb_doc_not_found", doc_id=document_id)
            return

        kb: KnowledgeBase = await session.get(KnowledgeBase, doc.kb_id)
        if not kb:
            doc.status = "failed"
            await session.flush()
            return
            
        doc.status = "processing"
        await session.flush()
        
        try:
            # 2. Extract content (For now, from metadata_json 'raw_text' or fallback)
            meta = json.loads(doc.metadata_json or "{}")
            text = meta.get("raw_text", f"Dummy content for {doc.filename}")
            
            # Simple text chunking by characters
            chunk_size = kb.chunk_size or 1000
            overlap = kb.chunk_overlap or 200
            
            chunks = []
            start = 0
            step = max(chunk_size - overlap, 1)
            while start < len(text):
                chunks.append(text[start:start + chunk_size])
                start += step
                if start >= len(text):
                    break
                    
            if not chunks:
                doc.status = "indexed"
                doc.chunk_count = 0
                await session.flush()
                return
                
            # 3. Get embeddings via preferred provider (e.g. OpenAI)
            provider = await provider_manager.get_provider(
                session,
                kb.tenant_id,
                kb.embedding_provider_id,
            )
                
            req = EmbeddingRequest(texts=chunks)
            resp = await provider.embed(req)
            
            # 4. Prepare ES index and bulk insert
            index_name = await log_service.create_kb_index_if_not_exists(kb.id)
            
            es_chunks = []
            for i, (chunk_text, embedding) in enumerate(zip(chunks, resp.embeddings)):
                es_chunks.append({
                    "doc_id": doc.id,
                    "kb_id": kb.id,
                    "content": chunk_text,
                    "chunk_index": i,
                    "embedding": embedding,
                    "metadata": {"filename": doc.filename}
                })
                
            await log_service.insert_chunks(index_name, es_chunks)
            
            # 5. Mark as done
            doc.status = "indexed"
            doc.chunk_count = len(chunks)
            await session.flush()
            
            logger.info("rag_index_success", doc_id=document_id, chunks=len(chunks))
            
        except Exception as e:
            logger.error("rag_index_failed", doc_id=document_id, error=str(e))
            doc.status = "failed"
            await session.flush()


@celery_app.task(name="logmind.domain.rag.tasks.index_document")
def index_document(document_id: str):
    """Index a RAG document (Chunking -> Embedding -> ES)."""
    run_async(_async_index_document(document_id))


async def _async_index_document_chunks(
    content: str,
    filename: str,
    tenant_id: str,
    knowledge_base_id: str | None = None,
    metadata: dict | None = None,
) -> str | None:
    """Create a KB document from raw text chunks, then index it after commit."""
    import hashlib

    from sqlalchemy import select

    from logmind.core.database import get_db_context
    from logmind.domain.rag.models import KBDocument, KnowledgeBase

    if not content.strip() or not filename.strip() or not tenant_id:
        logger.warning("index_document_chunks_invalid_input", tenant_id=tenant_id, filename=filename)
        return None

    metadata = dict(metadata or {})
    metadata["raw_text"] = content
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    doc_id: str | None = None

    async with get_db_context() as session:
        kb = None
        if knowledge_base_id:
            kb = await session.get(KnowledgeBase, knowledge_base_id)
            if not kb or kb.tenant_id != tenant_id or not kb.is_active:
                logger.warning(
                    "index_document_chunks_kb_not_found",
                    tenant_id=tenant_id,
                    kb_id=knowledge_base_id,
                )
                return None
        else:
            result = await session.execute(
                select(KnowledgeBase)
                .where(
                    KnowledgeBase.tenant_id == tenant_id,
                    KnowledgeBase.name == "故障复盘知识库",
                    KnowledgeBase.is_active == True,  # noqa: E712
                )
                .limit(1)
            )
            kb = result.scalar_one_or_none()
            if not kb:
                kb = KnowledgeBase(
                    tenant_id=tenant_id,
                    name="故障复盘知识库",
                    description="由 AI 故障复盘自动沉淀的知识库",
                    vector_index_name="",
                    is_active=True,
                )
                session.add(kb)
                await session.flush()

        duplicate = await session.execute(
            select(KBDocument)
            .where(
                KBDocument.kb_id == kb.id,
                KBDocument.content_hash == content_hash,
            )
            .limit(1)
        )
        existing = duplicate.scalar_one_or_none()
        if existing:
            logger.info("index_document_chunks_duplicate", doc_id=existing.id, kb_id=kb.id)
            return existing.id

        doc = KBDocument(
            kb_id=kb.id,
            filename=filename,
            content_hash=content_hash,
            status="pending",
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        session.add(doc)
        await session.flush()
        doc_id = doc.id

    if doc_id:
        await _async_index_document(doc_id)
    return doc_id


@celery_app.task(name="logmind.domain.rag.tasks.index_document_chunks")
def index_document_chunks(
    content: str,
    filename: str,
    tenant_id: str,
    knowledge_base_id: str | None = None,
    metadata: dict | None = None,
):
    """Create and index a raw text document, used by AI postmortem auto-learning."""
    run_async(
        _async_index_document_chunks(
            content=content,
            filename=filename,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            metadata=metadata,
        )
    )
