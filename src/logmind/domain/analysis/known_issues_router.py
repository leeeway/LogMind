"""
Known Issues — API Router

CRUD endpoints for the AI-managed Known Issue Library.

Known issues live in the ES `logmind-analysis-vectors` index (not in the
relational DB). Each document represents a historically analyzed error
pattern + its AI-generated conclusions. The system automatically creates
entries via `analysis_indexer.py` after each AI analysis.

This router provides human visibility and control:
  - View what the system "remembers"
  - Mark issues as resolved (enables regression detection on recurrence)
  - Ignore noisy issues
  - Delete incorrect entries
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.known_issues_schemas import (
    KnownIssueDetail,
    KnownIssueListResponse,
    KnownIssueResponse,
    KnownIssueStatusUpdate,
)
from logmind.shared.base_schema import MessageResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/known-issues", tags=["Known Issues"])

# ES index name (must match log/service.py and analysis_indexer.py)
_INDEX_NAME = "logmind-analysis-vectors"


# ── List Known Issues ────────────────────────────────────

@router.get("", response_model=KnownIssueListResponse)
async def list_known_issues(
    session: DBSession,
    user: CurrentUser,
    status: str | None = Query(None, description="Filter by status: open/resolved/ignored"),
    severity: str | None = Query(None, description="Filter by severity: critical/warning/info"),
    business_line_id: str | None = Query(None, description="Filter by business line ID"),
    search: str | None = Query(None, description="Full-text search in error_signature"),
    sort_by: str = Query("last_seen", description="Sort field: last_seen/hit_count/created_at/severity"),
    sort_order: str = Query("desc", pattern=r"^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    List known issues with filtering, search, and pagination.

    Known issues are automatically created when the AI analyzes a new error
    pattern. They accumulate hit_count each time the same pattern is seen again.
    """
    from logmind.domain.log.service import log_service

    es = log_service.es
    exists = await es.indices.exists(index=_INDEX_NAME)
    if not exists:
        return KnownIssueListResponse(items=[], total=0, page=page, page_size=page_size)

    # Build query
    must_clauses = []
    filter_clauses = []

    # Tenant isolation: only show issues from business lines belonging to this tenant
    tenant_biz_ids = await _get_tenant_business_line_ids(session, user.tenant_id)
    if not tenant_biz_ids:
        return KnownIssueListResponse(items=[], total=0, page=page, page_size=page_size)

    if business_line_id:
        # Validate access
        if business_line_id not in tenant_biz_ids:
            raise HTTPException(status_code=403, detail="Business line not in your tenant")
        filter_clauses.append({"term": {"business_line_id": business_line_id}})
    else:
        filter_clauses.append({"terms": {"business_line_id": tenant_biz_ids}})

    if status:
        filter_clauses.append({"term": {"status": status}})
    if severity:
        filter_clauses.append({"term": {"severity": severity}})
    if search:
        must_clauses.append({
            "multi_match": {
                "query": search,
                "fields": ["error_signature", "analysis_content"],
                "type": "phrase_prefix",
            }
        })

    # Sort mapping
    sort_field_map = {
        "last_seen": "last_seen",
        "hit_count": "hit_count",
        "created_at": "created_at",
        "severity": "severity",
        "first_seen": "first_seen",
    }
    es_sort_field = sort_field_map.get(sort_by, "last_seen")

    body = {
        "query": {
            "bool": {
                "must": must_clauses or [{"match_all": {}}],
                "filter": filter_clauses,
            }
        },
        "sort": [{es_sort_field: {"order": sort_order}}],
        "from": (page - 1) * page_size,
        "size": page_size,
        "_source": {
            "excludes": ["embedding"],  # Don't return the 1536-dim vector
        },
    }

    try:
        result = await es.search(index=_INDEX_NAME, body=body)
    except Exception as e:
        logger.error("known_issues_list_failed", error=str(e))
        return KnownIssueListResponse(items=[], total=0, page=page, page_size=page_size)

    total = result["hits"]["total"]["value"]
    items = []
    for hit in result["hits"]["hits"]:
        source = hit["_source"]
        items.append(KnownIssueResponse(
            id=hit["_id"],
            business_line_id=source.get("business_line_id", ""),
            error_signature=source.get("error_signature", ""),
            analysis_content=source.get("analysis_content", ""),
            severity=source.get("severity", "info"),
            task_id=source.get("task_id", ""),
            status=source.get("status", "open"),
            hit_count=source.get("hit_count", 1),
            first_seen=source.get("first_seen"),
            last_seen=source.get("last_seen"),
            resolved_at=source.get("resolved_at"),
            feedback_quality=source.get("feedback_quality"),
            created_at=source.get("created_at"),
            ttl_expire_at=source.get("ttl_expire_at"),
        ))

    return KnownIssueListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ── Get Known Issue Detail ───────────────────────────────

@router.get("/{issue_id}", response_model=KnownIssueDetail)
async def get_known_issue(
    issue_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """Get detailed information about a known issue."""
    from logmind.domain.log.service import log_service
    from logmind.domain.tenant.models import BusinessLine

    es = log_service.es

    try:
        result = await es.get(
            index=_INDEX_NAME,
            id=issue_id,
            source_excludes=["embedding"],
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Known issue not found")

    source = result["_source"]

    # Verify tenant access
    biz_id = source.get("business_line_id", "")
    tenant_biz_ids = await _get_tenant_business_line_ids(session, user.tenant_id)
    if biz_id not in tenant_biz_ids:
        raise HTTPException(status_code=404, detail="Known issue not found")

    # Resolve business line name
    biz_name = ""
    if biz_id:
        biz = await session.get(BusinessLine, biz_id)
        if biz:
            biz_name = biz.name

    return KnownIssueDetail(
        id=result["_id"],
        business_line_id=biz_id,
        business_line_name=biz_name,
        error_signature=source.get("error_signature", ""),
        analysis_content=source.get("analysis_content", ""),
        severity=source.get("severity", "info"),
        task_id=source.get("task_id", ""),
        status=source.get("status", "open"),
        hit_count=source.get("hit_count", 1),
        first_seen=source.get("first_seen"),
        last_seen=source.get("last_seen"),
        resolved_at=source.get("resolved_at"),
        feedback_quality=source.get("feedback_quality"),
        created_at=source.get("created_at"),
        ttl_expire_at=source.get("ttl_expire_at"),
    )


# ── Update Known Issue Status ────────────────────────────

@router.put("/{issue_id}/status", response_model=MessageResponse)
async def update_known_issue_status(
    issue_id: str,
    payload: KnownIssueStatusUpdate,
    session: DBSession,
    user: CurrentUser,
):
    """
    Update the status of a known issue.

    Status transitions:
      - open → resolved: Issue is fixed. If the same error re-appears,
        SemanticDedupStage will detect it as a **regression** and auto-upgrade to P0.
      - open → ignored: Noise or acceptable error. Will stop matching in KNN search.
      - resolved → open: Reopen a previously resolved issue.
      - ignored → open: Un-ignore an issue.
    """
    from logmind.domain.log.service import log_service

    # Verify the document exists and tenant has access
    es = log_service.es
    try:
        existing = await es.get(index=_INDEX_NAME, id=issue_id, source=["business_line_id", "status"])
    except Exception:
        raise HTTPException(status_code=404, detail="Known issue not found")

    biz_id = existing["_source"].get("business_line_id", "")
    tenant_biz_ids = await _get_tenant_business_line_ids(session, user.tenant_id)
    if biz_id not in tenant_biz_ids:
        raise HTTPException(status_code=404, detail="Known issue not found")

    old_status = existing["_source"].get("status", "open")
    new_status = payload.status

    # Build update fields
    update_fields = {"status": new_status}

    if new_status == "resolved":
        update_fields["resolved_at"] = datetime.now(timezone.utc).isoformat()
    elif new_status == "open" and old_status == "resolved":
        # Reopening — clear resolved_at
        update_fields["resolved_at"] = None

    if new_status == "ignored":
        # Mark as poor quality so KNN search excludes it
        update_fields["feedback_quality"] = "poor"
    elif new_status == "open" and old_status == "ignored":
        # Un-ignore — clear the poor quality marker
        update_fields["feedback_quality"] = None

    try:
        await es.update(
            index=_INDEX_NAME,
            id=issue_id,
            body={"doc": update_fields},
        )
    except Exception as e:
        logger.error("known_issue_status_update_failed", issue_id=issue_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update status")

    logger.info(
        "known_issue_status_updated",
        issue_id=issue_id,
        old_status=old_status,
        new_status=new_status,
        user=user.username,
    )

    transition_msg = f"状态已更新: {old_status} → {new_status}"
    if new_status == "resolved":
        transition_msg += " (如该错误再次出现将触发回归检测 → P0)"
    elif new_status == "ignored":
        transition_msg += " (该问题将不再参与 KNN 匹配)"

    return MessageResponse(message=transition_msg)


# ── Delete Known Issue ───────────────────────────────────

@router.delete("/{issue_id}", response_model=MessageResponse)
async def delete_known_issue(
    issue_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """
    Permanently delete a known issue from the vector library.

    Use this to remove incorrectly indexed entries or test data.
    For normal operations, prefer changing status to 'resolved' or 'ignored'.
    """
    from logmind.domain.log.service import log_service

    es = log_service.es

    # Verify existence and tenant access
    try:
        existing = await es.get(index=_INDEX_NAME, id=issue_id, source=["business_line_id"])
    except Exception:
        raise HTTPException(status_code=404, detail="Known issue not found")

    biz_id = existing["_source"].get("business_line_id", "")
    tenant_biz_ids = await _get_tenant_business_line_ids(session, user.tenant_id)
    if biz_id not in tenant_biz_ids:
        raise HTTPException(status_code=404, detail="Known issue not found")

    try:
        await es.delete(index=_INDEX_NAME, id=issue_id)
    except Exception as e:
        logger.error("known_issue_delete_failed", issue_id=issue_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to delete issue")

    logger.info("known_issue_deleted", issue_id=issue_id, user=user.username)
    return MessageResponse(message=f"已知问题 {issue_id[:8]}... 已永久删除")


# ── Helpers ──────────────────────────────────────────────

async def _get_tenant_business_line_ids(session, tenant_id: str) -> list[str]:
    """Get all business line IDs for a tenant (for tenant isolation)."""
    from sqlalchemy import select
    from logmind.domain.tenant.models import BusinessLine

    stmt = select(BusinessLine.id).where(BusinessLine.tenant_id == tenant_id)
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]
