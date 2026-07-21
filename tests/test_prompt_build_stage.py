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

