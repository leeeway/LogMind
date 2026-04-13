"""
Tenant Domain — API Router
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from logmind.core.dependencies import AdminUser, CurrentUser, DBSession
from logmind.core.security import create_access_token, hash_password, verify_password
from logmind.domain.tenant.models import BusinessLine, Tenant, User
from logmind.domain.tenant.schemas import (
    BusinessLineCreate,
    BusinessLineResponse,
    BusinessLineUpdate,
    LoginRequest,
    LoginResponse,
    TenantCreate,
    TenantResponse,
    TenantUpdate,
    UserCreate,
    UserResponse,
)
from logmind.shared.base_repository import BaseRepository
from logmind.shared.base_schema import MessageResponse, PaginatedResponse
from logmind.shared.pagination import PaginationParams, get_pagination

router = APIRouter(prefix="/tenants", tags=["Tenants"])
auth_router = APIRouter(prefix="/auth", tags=["Auth"])
biz_router = APIRouter(prefix="/business-lines", tags=["BusinessLines"])

tenant_repo = BaseRepository(Tenant)
user_repo = BaseRepository(User)
biz_repo = BaseRepository(BusinessLine)


# ── Auth ─────────────────────────────────────────────────
@auth_router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, session: DBSession):
    """Authenticate user and return JWT token."""
    from sqlalchemy import select

    stmt = select(User).where(User.username == req.username, User.is_active == True)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    token = create_access_token(
        data={
            "sub": user.id,
            "tenant_id": user.tenant_id,
            "role": user.role,
        }
    )
    return LoginResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


# ── Tenant CRUD ──────────────────────────────────────────
@router.post("", response_model=TenantResponse, status_code=201)
async def create_tenant(req: TenantCreate, session: DBSession, user: AdminUser):
    """Create a new tenant (admin only)."""
    tenant = Tenant(
        name=req.name,
        slug=req.slug,
        description=req.description,
        quota_tokens_daily=req.quota_tokens_daily,
    )
    tenant = await tenant_repo.create(session, tenant)
    return TenantResponse.model_validate(tenant)


@router.get("", response_model=PaginatedResponse)
async def list_tenants(
    session: DBSession,
    user: AdminUser,
    pagination: PaginationParams = Depends(get_pagination),
):
    """List all tenants (admin only)."""
    items = await tenant_repo.get_all(
        session, offset=pagination.offset, limit=pagination.limit
    )
    total = await tenant_repo.count(session)
    return PaginatedResponse.create(
        items=[TenantResponse.model_validate(t) for t in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(tenant_id: str, session: DBSession, user: CurrentUser):
    """Get tenant details."""
    tenant = await tenant_repo.get_by_id(session, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantResponse.model_validate(tenant)


# ── User CRUD ────────────────────────────────────────────
@router.post("/{tenant_id}/users", response_model=UserResponse, status_code=201)
async def create_user(
    tenant_id: str, req: UserCreate, session: DBSession, user: AdminUser
):
    """Create a user within a tenant (admin only)."""
    new_user = User(
        tenant_id=tenant_id,
        username=req.username,
        email=req.email,
        hashed_password=hash_password(req.password),
        role=req.role,
    )
    new_user = await user_repo.create(session, new_user)
    return UserResponse.model_validate(new_user)


# ── BusinessLine CRUD ────────────────────────────────────
@biz_router.post("", response_model=BusinessLineResponse, status_code=201)
async def create_business_line(
    req: BusinessLineCreate, session: DBSession, user: CurrentUser
):
    """Create a business line with ES index mapping."""
    biz = BusinessLine(
        tenant_id=user.tenant_id,
        name=req.name,
        description=req.description,
        es_index_pattern=req.es_index_pattern,
        log_parse_config=json.dumps(req.log_parse_config),
        default_filters=json.dumps(req.default_filters),
        severity_threshold=req.severity_threshold,
        field_mapping=json.dumps(req.field_mapping),
    )
    biz = await biz_repo.create(session, biz)
    return BusinessLineResponse.model_validate(biz)


@biz_router.get("", response_model=PaginatedResponse)
async def list_business_lines(
    session: DBSession,
    user: CurrentUser,
    pagination: PaginationParams = Depends(get_pagination),
):
    """List business lines for current tenant."""
    items = await biz_repo.get_all(
        session,
        tenant_id=user.tenant_id,
        offset=pagination.offset,
        limit=pagination.limit,
        filters={"is_active": True},
    )
    total = await biz_repo.count(
        session, tenant_id=user.tenant_id, filters={"is_active": True}
    )
    return PaginatedResponse.create(
        items=[BusinessLineResponse.model_validate(b) for b in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@biz_router.put("/{biz_id}", response_model=MessageResponse)
async def update_business_line(
    biz_id: str,
    req: BusinessLineUpdate,
    session: DBSession,
    user: CurrentUser,
):
    """Update a business line."""
    values = req.model_dump(exclude_none=True)
    if "log_parse_config" in values:
        values["log_parse_config"] = json.dumps(values["log_parse_config"])
    if "default_filters" in values:
        values["default_filters"] = json.dumps(values["default_filters"])
    if "field_mapping" in values:
        values["field_mapping"] = json.dumps(values["field_mapping"])

    success = await biz_repo.update_by_id(
        session, biz_id, values, tenant_id=user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Business line not found")
    return MessageResponse(message="Updated successfully")
