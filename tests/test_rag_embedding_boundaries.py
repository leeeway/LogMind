import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from logmind.domain.provider.models import ProviderConfig
from logmind.domain.rag.models import KBDocument, KnowledgeBase


class _FakeScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def first(self):
        return self.value


class _NoRedis:
    async def get(self, key):
        return None

    async def setex(self, key, ttl, value):
        return None


@pytest.mark.asyncio
async def test_cached_embed_requires_tenant_scoped_provider(monkeypatch):
    from logmind.domain.analysis import semantic_dedup

    config = ProviderConfig(
        id="provider-1",
        tenant_id="tenant-1",
        provider_type="openai",
        name="OpenAI",
        api_base_url="https://api.example.test",
        api_key_encrypted="",
        default_model="embedding-test",
        priority=10,
    )
    executed = {}

    class FakeSession:
        async def execute(self, stmt):
            executed["sql"] = str(stmt)
            return _FakeScalarResult(config)

    @asynccontextmanager
    async def fake_db_context():
        yield FakeSession()

    class FakeProvider:
        async def embed(self, req):
            return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]])

    monkeypatch.setattr("logmind.core.redis.get_redis_client", lambda: _NoRedis())
    monkeypatch.setattr("logmind.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "logmind.domain.provider.manager.provider_manager._create_or_get_cached",
        lambda cfg: FakeProvider(),
    )

    vector = await semantic_dedup.cached_embed(
        text="java.lang.NullPointerException",
        cache_ttl=60,
        tenant_id="tenant-1",
    )

    assert vector == [0.1, 0.2, 0.3]
    assert "provider_config.tenant_id" in executed["sql"]


@pytest.mark.asyncio
async def test_rag_document_indexing_uses_kb_tenant_provider(monkeypatch):
    from logmind.domain.rag import tasks

    now = datetime.now(timezone.utc)
    kb = KnowledgeBase(
        id="kb-1",
        tenant_id="tenant-1",
        name="Ops KB",
        description="",
        embedding_provider_id="provider-1",
        vector_index_name="",
        created_at=now,
        updated_at=now,
    )
    doc = KBDocument(
        id="doc-1",
        kb_id="kb-1",
        filename="runbook.md",
        metadata_json=json.dumps({"raw_text": "restart service when timeout happens"}),
        status="pending",
        created_at=now,
        updated_at=now,
    )
    provider_calls = {}

    class FakeSession:
        async def get(self, model, object_id):
            if model is KBDocument and object_id == "doc-1":
                return doc
            if model is KnowledgeBase and object_id == "kb-1":
                return kb
            return None

        async def flush(self):
            return None

    @asynccontextmanager
    async def fake_db_context():
        yield FakeSession()

    class FakeProvider:
        async def embed(self, req):
            provider_calls["texts"] = req.texts
            return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3] for _ in req.texts])

    async def fake_get_provider(session, tenant_id, provider_config_id=None):
        provider_calls["tenant_id"] = tenant_id
        provider_calls["provider_config_id"] = provider_config_id
        return FakeProvider()

    async def fake_create_index(kb_id, vector_dim=1536):
        return f"logmind-kb-{kb_id}"

    async def fake_insert_chunks(index_name, chunks):
        provider_calls["index_name"] = index_name
        provider_calls["chunks"] = chunks
        return len(chunks)

    monkeypatch.setattr("logmind.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "logmind.domain.provider.manager.provider_manager.get_provider",
        fake_get_provider,
    )
    monkeypatch.setattr(
        "logmind.domain.log.service.log_service.create_kb_index_if_not_exists",
        fake_create_index,
    )
    monkeypatch.setattr(
        "logmind.domain.log.service.log_service.insert_chunks",
        fake_insert_chunks,
    )

    await tasks._async_index_document("doc-1")

    assert doc.status == "indexed"
    assert doc.chunk_count == 1
    assert provider_calls["tenant_id"] == "tenant-1"
    assert provider_calls["provider_config_id"] == "provider-1"
    assert provider_calls["index_name"] == "logmind-kb-kb-1"


@pytest.mark.asyncio
async def test_upload_document_commits_before_dispatch(monkeypatch):
    from logmind.domain.rag import router

    events = []
    now = datetime.now(timezone.utc)
    kb = KnowledgeBase(
        id="kb-1",
        tenant_id="tenant-1",
        name="Ops KB",
        description="",
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    class FakeSession:
        async def get(self, model, object_id):
            return kb if model is KnowledgeBase and object_id == "kb-1" else None

        async def execute(self, stmt):
            return _FakeScalarResult(None)

        def add(self, obj):
            events.append("add")
            self.doc = obj

        async def flush(self):
            events.append("flush")
            if not getattr(self.doc, "id", None):
                self.doc.id = "doc-1"
            if getattr(self.doc, "chunk_count", None) is None:
                self.doc.chunk_count = 0
            if getattr(self.doc, "created_at", None) is None:
                self.doc.created_at = now
            if getattr(self.doc, "updated_at", None) is None:
                self.doc.updated_at = now

        async def refresh(self, obj):
            events.append("refresh")

        async def commit(self):
            events.append("commit")

    def fake_delay(doc_id):
        events.append(f"delay:{doc_id}")

    monkeypatch.setattr(
        "logmind.domain.rag.tasks.index_document.delay",
        fake_delay,
    )

    payload = router.KBDocumentUpload(
        filename="runbook.md",
        content="restart service",
        metadata={},
    )
    user = SimpleNamespace(tenant_id="tenant-1")

    await router.upload_document("kb-1", payload, FakeSession(), user)

    assert events.index("commit") < events.index("delay:doc-1")


def test_ci_webhook_signature_verification():
    from logmind.domain.analysis.change_router import verify_ci_webhook_signature

    body = b'{"status":"success"}'
    secret = "secret-1"
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    assert verify_ci_webhook_signature(body, signature, secret) is True
    assert verify_ci_webhook_signature(body, "sha256=bad", secret) is False
    assert verify_ci_webhook_signature(body, "", secret) is False


def test_postmortem_index_task_is_available():
    from logmind.domain.rag import tasks

    assert hasattr(tasks, "index_document_chunks")
