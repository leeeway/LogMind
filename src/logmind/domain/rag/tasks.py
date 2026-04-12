# RAG tasks placeholder
from logmind.core.celery_app import celery_app
from logmind.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(name="logmind.domain.rag.tasks.index_document")
def index_document(document_id: str):
    """Index a RAG document — Phase 4 implementation."""
    logger.info("rag_index_document", document_id=document_id, status="not_implemented")
