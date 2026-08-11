"""Administration and signed CI endpoints for read-only Git diagnosis."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from logmind.core.config import get_settings
from logmind.core.dependencies import AdminUser, CurrentUser, DBSession
from logmind.domain.analysis.change_router import verify_ci_webhook_signature
from logmind.domain.analysis.models import ChangeEvent
from logmind.domain.http_access.repository import RepositoryError, git_repository_service
from logmind.domain.http_access.site_config import (
    GitDeploymentRevision,
    GitRepositoryConfig,
)

router = APIRouter(prefix="/http-access/repositories", tags=["HTTP Access Git"])


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    clone_url: str = Field(min_length=8, max_length=1000)
    default_branch: str = Field(default="main", pattern=r"^(main|master)$")
    credential_ref: str = Field(default="", max_length=120)


class RepositoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_branch: str | None = Field(default=None, pattern=r"^(main|master)$")
    credential_ref: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class DeploymentWebhook(BaseModel):
    repository_id: str
    service_name: str = Field(min_length=1, max_length=200)
    branch: str = Field(pattern=r"^(main|master)$")
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    previous_commit_sha: str = Field(default="", pattern=r"^$|^[0-9a-fA-F]{7,64}$")
    image_version: str = Field(default="", max_length=200)
    environment: str = Field(default="production", max_length=32)
    deployed_at: datetime | None = None
    operator: str = Field(default="CI/CD", max_length=100)


def _payload(item: GitRepositoryConfig, *, include_credential_ref: bool = False) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "clone_url": item.clone_url,
        "default_branch": item.default_branch,
        "credential_ref": item.credential_ref if include_credential_ref else "",
        "is_active": item.is_active,
        "last_sync_status": item.last_sync_status,
        "last_sync_error": item.last_sync_error,
        "last_synced_at": item.last_synced_at,
        "last_commit_sha": item.last_commit_sha,
        "cache_size_bytes": item.cache_size_bytes,
    }


@router.get("")
async def list_repositories(session: DBSession, user: CurrentUser) -> dict:
    result = await session.execute(
        select(GitRepositoryConfig)
        .where(GitRepositoryConfig.tenant_id == user.tenant_id)
        .order_by(GitRepositoryConfig.name)
    )
    return {
        "items": [
            _payload(item, include_credential_ref=user.role == "admin")
            for item in result.scalars().all()
        ]
    }


@router.post("")
async def create_repository(req: RepositoryCreate, session: DBSession, user: AdminUser) -> dict:
    existing = await session.execute(
        select(GitRepositoryConfig.id).where(
            GitRepositoryConfig.tenant_id == user.tenant_id,
            GitRepositoryConfig.clone_url == req.clone_url,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "repository already exists")
    item = GitRepositoryConfig(tenant_id=user.tenant_id, **req.model_dump())
    try:
        git_repository_service.validate(item)
    except RepositoryError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.add(item)
    await session.flush()
    return _payload(item, include_credential_ref=True)


@router.patch("/{repository_id}")
async def update_repository(
    repository_id: str,
    req: RepositoryUpdate,
    session: DBSession,
    user: AdminUser,
) -> dict:
    item = await session.get(GitRepositoryConfig, repository_id)
    if not item or item.tenant_id != user.tenant_id:
        raise HTTPException(404, "repository not found")
    for key, value in req.model_dump(exclude_none=True).items():
        setattr(item, key, value)
    try:
        git_repository_service.validate(item)
    except RepositoryError as exc:
        raise HTTPException(422, str(exc)) from exc
    await session.flush()
    return _payload(item, include_credential_ref=True)


@router.post("/{repository_id}/test")
async def test_repository(repository_id: str, session: DBSession, user: AdminUser) -> dict:
    item = await session.get(GitRepositoryConfig, repository_id)
    if not item or item.tenant_id != user.tenant_id:
        raise HTTPException(404, "repository not found")
    try:
        result = await git_repository_service.test_connection(item)
    except RepositoryError as exc:
        item.last_sync_status = "failed"
        item.last_sync_error = str(exc)[:500]
        raise HTTPException(422, str(exc)) from exc
    if result["default_branch"] != item.default_branch:
        item.default_branch = result["default_branch"]
    item.last_sync_status = "connected"
    item.last_sync_error = ""
    return result


@router.post("/{repository_id}/sync")
async def sync_repository(repository_id: str, session: DBSession, user: AdminUser) -> dict:
    item = await session.get(GitRepositoryConfig, repository_id)
    if not item or item.tenant_id != user.tenant_id:
        raise HTTPException(404, "repository not found")
    from logmind.domain.http_access.tasks import sync_http_repository

    sync_http_repository.delay(item.id)
    item.last_sync_status = "queued"
    return {"queued": True, "repository_id": item.id}


@router.post("/deploy-webhook/{tenant_id}")
async def deployment_webhook(
    tenant_id: str,
    request: Request,
    session: DBSession,
    x_logmind_signature: str | None = Header(default=None),
) -> dict:
    settings = get_settings()
    body = await request.body()
    if not settings.ci_webhook_secret or not verify_ci_webhook_signature(
        body, x_logmind_signature, settings.ci_webhook_secret
    ):
        raise HTTPException(401, "invalid CI webhook signature")
    try:
        payload = DeploymentWebhook.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(422, "invalid deployment payload") from exc
    repository = await session.get(GitRepositoryConfig, payload.repository_id)
    if not repository or repository.tenant_id != tenant_id or not repository.is_active:
        raise HTTPException(404, "repository not found")
    if payload.branch != repository.default_branch:
        raise HTTPException(422, "deployment branch does not match allowed default branch")
    deployed_at = payload.deployed_at or datetime.now(UTC)
    deployed_at = (
        deployed_at.replace(tzinfo=UTC)
        if deployed_at.tzinfo is None
        else deployed_at.astimezone(UTC)
    )
    duplicate = await session.execute(
        select(GitDeploymentRevision).where(
            GitDeploymentRevision.tenant_id == tenant_id,
            GitDeploymentRevision.repository_id == repository.id,
            GitDeploymentRevision.service_name == payload.service_name,
            GitDeploymentRevision.environment == payload.environment,
            GitDeploymentRevision.commit_sha == payload.commit_sha.lower(),
            GitDeploymentRevision.deployed_at == deployed_at,
        )
    )
    existing_revision = duplicate.scalar_one_or_none()
    if existing_revision:
        return {
            "ok": True,
            "revision_id": existing_revision.id,
            "sync_queued": False,
            "duplicate": True,
        }
    revision = GitDeploymentRevision(
        tenant_id=tenant_id,
        repository_id=repository.id,
        service_name=payload.service_name,
        branch=payload.branch,
        commit_sha=payload.commit_sha.lower(),
        previous_commit_sha=payload.previous_commit_sha.lower(),
        image_version=payload.image_version,
        environment=payload.environment,
        operator=payload.operator,
        deployed_at=deployed_at,
    )
    session.add(revision)
    session.add(
        ChangeEvent(
            tenant_id=tenant_id,
            service_name=payload.service_name,
            change_type="deploy",
            version=payload.image_version or payload.commit_sha[:12],
            operator=payload.operator,
            description=f"{payload.branch}@{payload.commit_sha[:12]}",
            timestamp=deployed_at,
            correlated_spikes=0,
            repository_id=repository.id,
            commit_sha=payload.commit_sha.lower(),
            image_version=payload.image_version,
            environment=payload.environment,
        )
    )
    await session.flush()
    from logmind.domain.http_access.tasks import sync_http_repository

    sync_http_repository.delay(repository.id)
    return {"ok": True, "revision_id": revision.id, "sync_queued": True}
