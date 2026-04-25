"""
RAG Domain — API Router

Knowledge Base management: CRUD for knowledge bases and documents.
Supports text document upload with async embedding + indexing.
"""

import hashlib
import json

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.rag.models import KBDocument, KnowledgeBase
from logmind.domain.rag.schemas import (
    KBDocumentResponse,
    KBDocumentUpload,
    KnowledgeBaseCreate,
    KnowledgeBaseDetail,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/knowledge-base", tags=["Knowledge Base"])


# ── Knowledge Base CRUD ──────────────────────────────────

@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    session: DBSession,
    user: CurrentUser,
):
    """Create a new knowledge base for the current tenant."""
    kb = KnowledgeBase(
        tenant_id=user.tenant_id,
        name=payload.name,
        description=payload.description,
        chunk_size=payload.chunk_size,
        chunk_overlap=payload.chunk_overlap,
        vector_index_name="",  # Will be set on first document indexing
        is_active=True,
    )
    session.add(kb)
    await session.flush()
    await session.refresh(kb)

    logger.info("kb_created", kb_id=kb.id, name=kb.name)

    return KnowledgeBaseResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        is_active=kb.is_active,
        document_count=0,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(
    session: DBSession,
    user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List all knowledge bases for the current tenant."""
    stmt = (
        select(KnowledgeBase)
        .where(KnowledgeBase.tenant_id == user.tenant_id)
        .order_by(KnowledgeBase.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.execute(stmt)
    kbs = result.scalars().all()

    responses = []
    for kb in kbs:
        # Count documents
        doc_count_stmt = (
            select(func.count())
            .select_from(KBDocument)
            .where(KBDocument.kb_id == kb.id)
        )
        doc_count_result = await session.execute(doc_count_stmt)
        doc_count = doc_count_result.scalar_one()

        responses.append(KnowledgeBaseResponse(
            id=kb.id,
            name=kb.name,
            description=kb.description,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
            is_active=kb.is_active,
            document_count=doc_count,
            created_at=kb.created_at,
            updated_at=kb.updated_at,
        ))

    return responses


@router.get("/{kb_id}", response_model=KnowledgeBaseDetail)
async def get_knowledge_base(
    kb_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """Get knowledge base details including document list."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # Load documents
    doc_stmt = (
        select(KBDocument)
        .where(KBDocument.kb_id == kb.id)
        .order_by(KBDocument.created_at.desc())
    )
    doc_result = await session.execute(doc_stmt)
    docs = doc_result.scalars().all()

    doc_responses = [
        KBDocumentResponse(
            id=doc.id,
            kb_id=doc.kb_id,
            filename=doc.filename,
            status=doc.status,
            chunk_count=doc.chunk_count,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
        for doc in docs
    ]

    return KnowledgeBaseDetail(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        is_active=kb.is_active,
        document_count=len(doc_responses),
        created_at=kb.created_at,
        updated_at=kb.updated_at,
        documents=doc_responses,
    )


@router.put("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: str,
    payload: KnowledgeBaseUpdate,
    session: DBSession,
    user: CurrentUser,
):
    """Update knowledge base settings."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(kb, field, value)

    await session.flush()
    await session.refresh(kb)

    doc_count_stmt = (
        select(func.count())
        .select_from(KBDocument)
        .where(KBDocument.kb_id == kb.id)
    )
    doc_count_result = await session.execute(doc_count_stmt)
    doc_count = doc_count_result.scalar_one()

    return KnowledgeBaseResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        is_active=kb.is_active,
        document_count=doc_count,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


@router.delete("/{kb_id}", status_code=204)
async def delete_knowledge_base(
    kb_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """Delete a knowledge base and all its documents."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # Delete all documents first
    doc_stmt = select(KBDocument).where(KBDocument.kb_id == kb.id)
    doc_result = await session.execute(doc_stmt)
    for doc in doc_result.scalars().all():
        await session.delete(doc)

    await session.delete(kb)
    await session.flush()

    # Clean up ES index (best effort)
    try:
        from logmind.domain.log.service import log_service
        index_name = f"logmind-kb-{kb_id}"
        exists = await log_service.es.indices.exists(index=index_name)
        if exists:
            await log_service.es.indices.delete(index=index_name)
            logger.info("kb_es_index_deleted", index=index_name)
    except Exception as e:
        logger.warning("kb_es_index_cleanup_failed", error=str(e))

    logger.info("kb_deleted", kb_id=kb_id)


# ── Document Management ──────────────────────────────────

@router.post("/{kb_id}/documents", response_model=KBDocumentResponse, status_code=201)
async def upload_document(
    kb_id: str,
    payload: KBDocumentUpload,
    session: DBSession,
    user: CurrentUser,
):
    """
    Upload a text document to the knowledge base.

    The document content will be chunked and indexed asynchronously.
    Check the document status via GET to monitor indexing progress.
    """
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    if not kb.is_active:
        raise HTTPException(status_code=400, detail="Knowledge base is not active")

    # Compute content hash for dedup
    content_hash = hashlib.sha256(payload.content.encode()).hexdigest()

    # Check for duplicate document in this knowledge base
    from sqlalchemy import select as sa_select
    dup_stmt = (
        sa_select(KBDocument)
        .where(KBDocument.kb_id == kb.id, KBDocument.content_hash == content_hash)
    )
    dup_result = await session.execute(dup_stmt)
    existing_doc = dup_result.scalars().first()
    if existing_doc:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate document: content already exists as '{existing_doc.filename}' "
                   f"(id={existing_doc.id}). Use DELETE + re-upload to replace.",
        )

    # Store raw text in metadata_json for the indexer task
    metadata = payload.metadata.copy()
    metadata["raw_text"] = payload.content

    doc = KBDocument(
        kb_id=kb.id,
        filename=payload.filename,
        content_hash=content_hash,
        status="pending",
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )
    session.add(doc)
    await session.flush()
    await session.refresh(doc)

    # Dispatch async indexing task
    from logmind.domain.rag.tasks import index_document
    index_document.delay(doc.id)

    logger.info("kb_doc_uploaded", doc_id=doc.id, filename=payload.filename, kb_id=kb_id)

    return KBDocumentResponse(
        id=doc.id,
        kb_id=doc.kb_id,
        filename=doc.filename,
        status=doc.status,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("/{kb_id}/documents", response_model=list[KBDocumentResponse])
async def list_documents(
    kb_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """List all documents in a knowledge base."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    stmt = (
        select(KBDocument)
        .where(KBDocument.kb_id == kb.id)
        .order_by(KBDocument.created_at.desc())
    )
    result = await session.execute(stmt)
    docs = result.scalars().all()

    return [
        KBDocumentResponse(
            id=doc.id,
            kb_id=doc.kb_id,
            filename=doc.filename,
            status=doc.status,
            chunk_count=doc.chunk_count,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
        for doc in docs
    ]


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    kb_id: str,
    doc_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """Delete a document from the knowledge base."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    doc = await session.get(KBDocument, doc_id)
    if not doc or doc.kb_id != kb.id:
        raise HTTPException(status_code=404, detail="Document not found")

    await session.delete(doc)
    await session.flush()

    logger.info("kb_doc_deleted", doc_id=doc_id, kb_id=kb_id)
