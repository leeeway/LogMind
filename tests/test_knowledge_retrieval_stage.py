from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis.stages.knowledge_retrieval import (
    KnowledgeRetrievalStage,
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _context() -> PipelineContext:
    return PipelineContext(
        tenant_id="tenant-1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="支付站点",
        language="csharp",
        processed_logs=(
            "System.UnauthorizedAccessException: Access is denied\n"
            "at Gyyx.ConfigWatcher.WriteFile() in ConfigWatcher.cs:line 88"
        ),
    )


@pytest.mark.asyncio
async def test_retrieves_across_active_tenant_knowledge_bases(monkeypatch):
    session = SimpleNamespace(
        execute=AsyncMock(return_value=_Rows([
            ("kb-1", "故障复盘知识库", "provider-1"),
            ("kb-2", "运维 SOP", None),
        ]))
    )

    @asynccontextmanager
    async def fake_db_context():
        yield session

    embed = AsyncMock(side_effect=[[0.1, 0.2], [0.3, 0.4]])
    search = AsyncMock(side_effect=[
        [{
            "score": 0.94,
            "content": "检查 Windows 服务账号对目标目录的写权限。",
            "metadata": {"filename": "配置同步写入失败复盘.md"},
            "doc_id": "doc-1",
        }],
        [{
            "score": 0.81,
            "content": "使用 icacls 核对目录 ACL，并确认运行身份。",
            "metadata": {"filename": "Windows 权限排查 SOP.md"},
            "doc_id": "doc-2",
        }],
    ])

    monkeypatch.setattr("logmind.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "logmind.domain.analysis.semantic_dedup.cached_embed",
        embed,
    )
    monkeypatch.setattr(
        "logmind.domain.log.service.log_service.knn_search",
        search,
    )

    result = await KnowledgeRetrievalStage().execute(_context())

    assert "配置同步写入失败复盘.md" in result.rag_context
    assert "Windows 权限排查 SOP.md" in result.rag_context
    assert result.rag_sources == [
        "故障复盘知识库 / 配置同步写入失败复盘.md",
        "运维 SOP / Windows 权限排查 SOP.md",
    ]
    assert result.log_metadata["knowledge_retrieval_count"] == 2
    assert embed.await_count == 2  # each KB uses its indexing provider
    assert search.await_count == 2


@pytest.mark.asyncio
async def test_skips_embedding_when_tenant_has_no_active_knowledge_base(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock(return_value=_Rows([])))

    @asynccontextmanager
    async def fake_db_context():
        yield session

    embed = AsyncMock()
    monkeypatch.setattr("logmind.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "logmind.domain.analysis.semantic_dedup.cached_embed",
        embed,
    )

    result = await KnowledgeRetrievalStage().execute(_context())

    assert result.rag_context == ""
    assert result.rag_sources == []
    embed.assert_not_awaited()
