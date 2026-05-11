from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from logmind.core.security import TokenPayload
from logmind.domain.log.router import _resolve_index_pattern
from logmind.domain.tenant.models import BusinessLine


class DummySession:
    def __init__(self, business_line=None):
        self.business_line = business_line

    async def get(self, model, business_line_id: str):
        assert model is BusinessLine
        assert business_line_id
        return self.business_line


@pytest.mark.asyncio
async def test_resolve_index_pattern_prefers_direct_index_pattern():
    session = DummySession()
    user = TokenPayload(sub="u1", tenant_id="t1", role="viewer")

    index_pattern = await _resolve_index_pattern(
        session=session,
        user=user,
        index_pattern="logs-*",
        business_line_id=None,
    )

    assert index_pattern == "logs-*"


@pytest.mark.asyncio
async def test_resolve_index_pattern_from_business_line():
    business_line = SimpleNamespace(
        tenant_id="t1",
        is_active=True,
        es_index_pattern="service-*",
    )
    session = DummySession(business_line=business_line)
    user = TokenPayload(sub="u1", tenant_id="t1", role="viewer")

    index_pattern = await _resolve_index_pattern(
        session=session,
        user=user,
        index_pattern=None,
        business_line_id="biz-1",
    )

    assert index_pattern == "service-*"


@pytest.mark.asyncio
async def test_resolve_index_pattern_requires_input():
    session = DummySession()
    user = TokenPayload(sub="u1", tenant_id="t1", role="viewer")

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_index_pattern(
            session=session,
            user=user,
            index_pattern=None,
            business_line_id=None,
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_resolve_index_pattern_rejects_other_tenant():
    business_line = SimpleNamespace(
        tenant_id="t2",
        is_active=True,
        es_index_pattern="service-*",
    )
    session = DummySession(business_line=business_line)
    user = TokenPayload(sub="u1", tenant_id="t1", role="viewer")

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_index_pattern(
            session=session,
            user=user,
            index_pattern=None,
            business_line_id="biz-1",
        )

    assert exc_info.value.status_code == 404
