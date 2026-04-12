"""
Prompt Template — API Router
"""

import json

from fastapi import APIRouter, HTTPException

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.domain.prompt.engine import prompt_engine
from logmind.domain.prompt.models import PromptTemplate
from logmind.domain.prompt.schemas import (
    PromptRenderRequest,
    PromptRenderResponse,
    PromptTemplateCreate,
    PromptTemplateResponse,
    PromptTemplateUpdate,
    PromptValidateRequest,
    PromptValidateResponse,
)
from logmind.shared.base_repository import BaseRepository
from logmind.shared.base_schema import MessageResponse, PaginatedResponse

router = APIRouter(prefix="/prompts", tags=["Prompts"])
repo = BaseRepository(PromptTemplate)


@router.post("", response_model=PromptTemplateResponse, status_code=201)
async def create_template(
    req: PromptTemplateCreate, session: DBSession, user: CurrentUser
):
    """Create a new prompt template."""
    template = PromptTemplate(
        tenant_id=user.tenant_id,
        name=req.name,
        category=req.category,
        version=req.version,
        description=req.description,
        system_prompt=req.system_prompt,
        user_prompt_template=req.user_prompt_template,
        variables_schema=json.dumps(req.variables_schema),
        is_default=req.is_default,
    )
    template = await repo.create(session, template)
    return PromptTemplateResponse.model_validate(template)


@router.get("", response_model=PaginatedResponse)
async def list_templates(
    session: DBSession,
    user: CurrentUser,
    category: str | None = None,
):
    """List prompt templates for current tenant."""
    filters = {"is_active": True}
    if category:
        filters["category"] = category

    items = await repo.get_all(
        session, tenant_id=user.tenant_id, filters=filters
    )
    total = await repo.count(session, tenant_id=user.tenant_id, filters=filters)
    return PaginatedResponse.create(
        items=[PromptTemplateResponse.model_validate(t) for t in items],
        total=total,
        page=1,
        page_size=50,
    )


@router.get("/{template_id}", response_model=PromptTemplateResponse)
async def get_template(template_id: str, session: DBSession, user: CurrentUser):
    """Get a prompt template by ID."""
    template = await repo.get_by_id(session, template_id, tenant_id=user.tenant_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return PromptTemplateResponse.model_validate(template)


@router.put("/{template_id}", response_model=MessageResponse)
async def update_template(
    template_id: str,
    req: PromptTemplateUpdate,
    session: DBSession,
    user: CurrentUser,
):
    """Update a prompt template."""
    values = req.model_dump(exclude_none=True)
    if "variables_schema" in values:
        values["variables_schema"] = json.dumps(values["variables_schema"])

    success = await repo.update_by_id(
        session, template_id, values, tenant_id=user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Template not found")
    return MessageResponse(message="Template updated")


@router.post("/render", response_model=PromptRenderResponse)
async def render_template(
    req: PromptRenderRequest, session: DBSession, user: CurrentUser
):
    """Render a prompt template with variables (preview)."""
    template = await repo.get_by_id(session, req.template_id, tenant_id=user.tenant_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    system_prompt, user_prompt = prompt_engine.render(template, req.variables)
    return PromptRenderResponse(
        system_prompt=system_prompt, user_prompt=user_prompt
    )


@router.post("/validate", response_model=PromptValidateResponse)
async def validate_template_syntax(req: PromptValidateRequest):
    """Validate Jinja2 template syntax."""
    errors = prompt_engine.validate_template_syntax(req.template_str)
    return PromptValidateResponse(is_valid=len(errors) == 0, errors=errors)
