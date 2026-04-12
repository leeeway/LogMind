"""
Provider Domain — API Router
"""

import json

from fastapi import APIRouter, HTTPException

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.domain.provider.factory import get_registered_providers
from logmind.domain.provider.manager import provider_manager
from logmind.domain.provider.models import ProviderConfig
from logmind.domain.provider.schemas import (
    ProviderConfigCreate,
    ProviderConfigResponse,
    ProviderConfigUpdate,
    ProviderHealthResponse,
    RegisteredProvidersResponse,
)
from logmind.shared.base_repository import BaseRepository
from logmind.shared.base_schema import MessageResponse, PaginatedResponse
from logmind.shared.encryption import encrypt_value

router = APIRouter(prefix="/providers", tags=["Providers"])
repo = BaseRepository(ProviderConfig)


@router.get("/registered", response_model=RegisteredProvidersResponse)
async def list_registered_providers():
    """List all registered (available) provider types."""
    return RegisteredProvidersResponse(providers=get_registered_providers())


@router.post("", response_model=ProviderConfigResponse, status_code=201)
async def create_provider_config(
    req: ProviderConfigCreate, session: DBSession, user: CurrentUser
):
    """Create a new provider configuration."""
    config = ProviderConfig(
        tenant_id=user.tenant_id,
        provider_type=req.provider_type,
        name=req.name,
        api_base_url=req.api_base_url,
        api_key_encrypted=encrypt_value(req.api_key) if req.api_key else "",
        default_model=req.default_model,
        model_params=json.dumps(req.model_params),
        priority=req.priority,
        rate_limit_rpm=req.rate_limit_rpm,
    )
    config = await repo.create(session, config)
    return ProviderConfigResponse.model_validate(config)


@router.get("", response_model=PaginatedResponse)
async def list_providers(session: DBSession, user: CurrentUser):
    """List all provider configurations for current tenant."""
    items = await repo.get_all(session, tenant_id=user.tenant_id)
    total = await repo.count(session, tenant_id=user.tenant_id)
    return PaginatedResponse.create(
        items=[ProviderConfigResponse.model_validate(p) for p in items],
        total=total,
        page=1,
        page_size=50,
    )


@router.put("/{provider_id}", response_model=MessageResponse)
async def update_provider(
    provider_id: str,
    req: ProviderConfigUpdate,
    session: DBSession,
    user: CurrentUser,
):
    """Update a provider configuration."""
    values = req.model_dump(exclude_none=True)
    if "api_key" in values:
        values["api_key_encrypted"] = encrypt_value(values.pop("api_key"))
    if "model_params" in values:
        values["model_params"] = json.dumps(values["model_params"])

    success = await repo.update_by_id(
        session, provider_id, values, tenant_id=user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Provider not found")
    return MessageResponse(message="Provider updated")


@router.delete("/{provider_id}", response_model=MessageResponse)
async def delete_provider(
    provider_id: str, session: DBSession, user: CurrentUser
):
    """Delete a provider configuration."""
    success = await repo.delete_by_id(
        session, provider_id, tenant_id=user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Provider not found")
    return MessageResponse(message="Provider deleted")


@router.get("/health", response_model=list[ProviderHealthResponse])
async def check_all_provider_health(session: DBSession, user: CurrentUser):
    """Health check all active providers for current tenant."""
    results = await provider_manager.health_check_all(session, user.tenant_id)
    return [ProviderHealthResponse(**r) for r in results]
