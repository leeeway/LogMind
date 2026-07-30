from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis.stages.prompt_build import PromptBuildStage


def _make_ctx(**kwargs) -> PipelineContext:
    defaults = {
        "tenant_id": "t1",
        "task_id": "task-001",
        "business_line_id": "biz-001",
        "business_line_name": "demo-service",
        "processed_logs": "ERROR demo failure",
        "log_count": 1,
        "prompt_template_id": "tpl-1",
    }
    defaults.update(kwargs)
    return PipelineContext(**defaults)


@pytest.mark.asyncio
async def test_prompt_build_falls_back_when_template_lookup_connection_resets(monkeypatch):
    stage = PromptBuildStage(prompt_engine=MagicMock(), prompt_repo=MagicMock())
    stage.prompt_repo.get_by_id = AsyncMock(
        side_effect=ConnectionResetError(104, "Connection reset by peer")
    )

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    async def fake_profile_context(_business_line_id: str) -> str:
        return ""

    monkeypatch.setattr("logmind.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "logmind.domain.analysis.business_profile.build_profile_context",
        fake_profile_context,
    )

    ctx = await stage.execute(_make_ctx())

    assert "资深 SRE 工程师" in ctx.system_prompt
    assert "ERROR demo failure" in ctx.user_prompt


@pytest.mark.asyncio
async def test_prompt_build_keeps_rendered_prompt_when_commit_connection_resets(monkeypatch):
    template = SimpleNamespace(id="tpl-1")
    prompt_engine = MagicMock()
    prompt_engine.render.return_value = ("system prompt", "user prompt")

    stage = PromptBuildStage(prompt_engine=prompt_engine, prompt_repo=MagicMock())
    stage.prompt_repo.get_by_id = AsyncMock(return_value=template)

    @asynccontextmanager
    async def fake_db_context():
        yield object()
        raise ConnectionResetError(104, "Connection reset by peer")

    async def fake_profile_context(_business_line_id: str) -> str:
        return ""

    monkeypatch.setattr("logmind.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "logmind.domain.analysis.business_profile.build_profile_context",
        fake_profile_context,
    )

    ctx = await stage.execute(_make_ctx())

    assert ctx.system_prompt == "system prompt"
    assert ctx.user_prompt == "user prompt"
    assert ctx.prompt_template_id == "tpl-1"


@pytest.mark.asyncio
async def test_prompt_build_injects_retrieved_knowledge_and_profile(monkeypatch):
    template = SimpleNamespace(id="tpl-1")
    prompt_engine = MagicMock()
    prompt_engine.render.return_value = ("system prompt", "user prompt")
    stage = PromptBuildStage(prompt_engine=prompt_engine, prompt_repo=MagicMock())
    stage.prompt_repo.get_by_id = AsyncMock(return_value=template)

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    async def fake_profile_context(_business_line_id: str) -> str:
        return "## 历史经验\n配置同步失败通常与目录 ACL 有关。"

    monkeypatch.setattr("logmind.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "logmind.domain.analysis.business_profile.build_profile_context",
        fake_profile_context,
    )

    ctx = _make_ctx()
    ctx.rag_context = "来源: Windows 权限排查 SOP\n检查服务运行账号。"
    ctx.rag_sources = ["运维 SOP / Windows 权限排查.md"]
    ctx = await stage.execute(ctx)

    assert "内部知识库预检索结果" in ctx.system_prompt
    assert "检查服务运行账号" in ctx.system_prompt
    assert "配置同步失败通常与目录 ACL 有关" in ctx.system_prompt
    assert ctx.rag_sources == [
        "运维 SOP / Windows 权限排查.md",
        "自学习业务画像",
    ]
    assert ctx.log_metadata["knowledge_context_injected"] is True
    assert ctx.log_metadata["business_profile_injected"] is True


def test_sanitize_negative_boilerplate():
    from logmind.domain.analysis.stages.result_parse import _sanitize_negative_boilerplate

    sample = (
        "当前提供的日志片段仅包含 DEBUG 级别的数据库 SELECT 执行记录，且均显示“执行成功”，"
        "未发现 ERROR、异常堆栈、DataIntegrityViolationException、SQL 数据截断、核心表写入失败或连接池/数据库故障等严重问题。"
        "因此无法从现有片段确认具体业务故障。"
    )
    res = _sanitize_negative_boilerplate(sample)
    assert "DataIntegrityViolationException" not in res
    assert "SQL 数据截断" not in res
    assert "当前提供的日志片段仅包含 DEBUG 级别的数据库 SELECT 执行记录" in res
    assert "因此无法从现有片段确认具体业务故障" in res


def test_sanitize_negative_boilerplate_with_unshown_wording():
    from logmind.domain.analysis.stages.result_parse import _sanitize_negative_boilerplate

    sample = (
        "当前日志未显示数据库写入失败、DataIntegrityViolationException "
        "或 SQL 截断等结构性数据一致性异常，因此暂不定性为 critical。"
    )

    res = _sanitize_negative_boilerplate(sample)

    assert res == ""
    assert "DataIntegrityViolationException" not in res


def test_sanitize_negative_boilerplate_preserves_positive_but_clause():
    from logmind.domain.analysis.stages.result_parse import _sanitize_negative_boilerplate

    sample = "页面未显示成功提示，但数据库写入失败并抛出 System.Data.SqlClient.SqlException。"

    res = _sanitize_negative_boilerplate(sample)

    assert res == "数据库写入失败并抛出 System.Data.SqlClient.SqlException。"
