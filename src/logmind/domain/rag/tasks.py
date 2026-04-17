import asyncio
import json

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
            while start < len(text):
                chunks.append(text[start:start + chunk_size])
                start += chunk_size - overlap
                if start >= len(text):
                    break
                    
            if not chunks:
                doc.status = "indexed"
                doc.chunk_count = 0
                await session.flush()
                return
                
            # 3. Get embeddings via preferred provider (e.g. OpenAI)
            provider = provider_manager.get_provider(kb.embedding_provider_id)
            if not provider:
                # Default to the first available (usually openai)
                provider = provider_manager.get_provider("openai")
                
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
    asyncio.run(_async_index_document(document_id))
