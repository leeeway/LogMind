from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from logmind.core.exceptions import ProviderError
from logmind.core.security import TokenPayload, create_access_token
from logmind.domain.alert.router import AlertRuleCreate
from logmind.domain.analysis.schemas import AnalysisTaskCreate
from logmind.domain.log.schemas import LogQueryRequest
from logmind.domain.provider.models import ProviderConfig
from logmind.domain.tenant.models import BusinessLine, Tenant


class FakeSession:
    def __init__(self, by_model_and_id=None):
        self.by_model_and_id = by_model_and_id or {}
        self.committed = False
        self.flushed = False

    async def get(self, model, id):
        return self.by_model_and_id.get((model, id))

    async def commit(self):
        self.committed = True

    async def flush(self):
        self.flushed = True


def _biz(
    *,
    id: str = "biz-1",
    tenant_id: str = "tenant-1",
    pattern: str = "tenant-logs-*",
    active: bool = True,
) -> BusinessLine:
    now = datetime.now(timezone.utc)
    return BusinessLine(
        id=id,
        tenant_id=tenant_id,
        name="Checkout",
        es_index_pattern=pattern,
        is_active=active,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_log_search_requires_business_line_owned_by_current_tenant(monkeypatch):
    from logmind.domain.log import router as log_router

    captured = {}

    async def fake_search_logs(request):
        captured["index_pattern"] = request.index_pattern
        captured["business_line_id"] = request.business_line_id
        return SimpleNamespace(logs=[], total=0, took_ms=0)

    monkeypatch.setattr(log_router.log_service, "search_logs", fake_search_logs)

    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="analyst")
    session = FakeSession({(BusinessLine, "biz-1"): _biz()})
    req = LogQueryRequest(
        index_pattern="attacker-*",
        business_line_id="biz-1",
        time_from=datetime.now(timezone.utc) - timedelta(hours=1),
        time_to=datetime.now(timezone.utc),
        query="timeout",
    )

    await log_router.search_logs(req, session, user)

    assert captured["index_pattern"] == "tenant-logs-*"
    assert captured["business_line_id"] == "biz-1"


@pytest.mark.asyncio
async def test_log_search_rejects_foreign_business_line():
    from logmind.domain.log import router as log_router

    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="analyst")
    session = FakeSession({(BusinessLine, "biz-2"): _biz(id="biz-2", tenant_id="tenant-2")})
    req = LogQueryRequest(
        business_line_id="biz-2",
        time_from=datetime.now(timezone.utc) - timedelta(hours=1),
        time_to=datetime.now(timezone.utc),
    )

    with pytest.raises(HTTPException) as exc:
        await log_router.search_logs(req, session, user)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_tenant_rejects_cross_tenant_read():
    from logmind.domain.tenant import router as tenant_router

    now = datetime.now(timezone.utc)
    tenant = Tenant(
        id="tenant-2",
        name="Other",
        slug="other",
        created_at=now,
        updated_at=now,
    )
    session = FakeSession({(Tenant, "tenant-2"): tenant})
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="viewer")

    with pytest.raises(HTTPException) as exc:
        await tenant_router.get_tenant("tenant-2", session, user)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_analysis_task_rejects_foreign_business_line(monkeypatch):
    from logmind.domain.analysis import router as analysis_router

    async def fail_create(*args, **kwargs):
        raise AssertionError("task must not be created for a foreign business line")

    monkeypatch.setattr(analysis_router.task_repo, "create", fail_create)

    req = AnalysisTaskCreate(
        business_line_id="biz-2",
        time_from=datetime.now(timezone.utc) - timedelta(hours=1),
        time_to=datetime.now(timezone.utc),
    )
    session = FakeSession({(BusinessLine, "biz-2"): _biz(id="biz-2", tenant_id="tenant-2")})
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="analyst")

    with pytest.raises(HTTPException) as exc:
        await analysis_router.create_analysis_task(req, session, user)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_alert_rule_rejects_foreign_business_line(monkeypatch):
    from logmind.domain.alert import router as alert_router

    async def fail_create(*args, **kwargs):
        raise AssertionError("alert rule must not be created for a foreign business line")

    monkeypatch.setattr(alert_router.rule_repo, "create", fail_create)

    req = AlertRuleCreate(
        business_line_id="biz-2",
        name="Errors",
        rule_type="keyword",
    )
    session = FakeSession({(BusinessLine, "biz-2"): _biz(id="biz-2", tenant_id="tenant-2")})
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="analyst")

    with pytest.raises(HTTPException) as exc:
        await alert_router.create_alert_rule(req, session, user)

    assert exc.value.status_code == 404


def test_rate_limit_identity_prefers_jwt_tenant_over_client_ip():
    from logmind.core.rate_limiter import _get_rate_limit_identity

    token = create_access_token(
        {"sub": "user-1", "tenant_id": "tenant-1", "role": "viewer"}
    )
    scope = {
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("203.0.113.10", 12345),
        "state": {},
    }

    assert _get_rate_limit_identity(scope) == "tenant-1"


def test_provider_config_decrypt_failure_is_explicit(monkeypatch):
    from logmind.domain.provider.manager import ProviderManager

    def fail_if_called(**kwargs):
        raise AssertionError("provider must not be created with encrypted ciphertext")

    monkeypatch.setattr("logmind.domain.provider.manager.create_provider", fail_if_called)

    config = ProviderConfig(
        id="provider-1",
        tenant_id="tenant-1",
        provider_type="openai",
        name="OpenAI",
        api_base_url="https://api.example.test",
        api_key_encrypted="not-a-valid-fernet-token",
        default_model="gpt-test",
    )

    with pytest.raises(ProviderError):
        ProviderManager()._create_or_get_cached(config)
