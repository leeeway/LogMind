from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from logmind.core.security import TokenPayload
from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis.schemas import AnalysisTaskBatchCreate
from logmind.domain.analysis.stages.business_noise_filter import BusinessNoiseFilterStage
from logmind.domain.analysis.stages.log_fetch import LogFetchStage
from logmind.domain.analysis.stages.log_preprocess import LogPreprocessStage
from logmind.domain.analysis.stages.quality_filter import LogQualityFilterStage
from logmind.domain.tenant.models import BusinessLine


class _FakeSession:
    def __init__(self, business_lines):
        self.business_lines = {line.id: line for line in business_lines}
        self.committed = False

    async def get(self, model, object_id):
        if model is BusinessLine:
            return self.business_lines.get(object_id)
        return None

    async def commit(self):
        self.committed = True


def _business_line(object_id: str, tenant_id: str = "tenant-1") -> BusinessLine:
    now = datetime.now(timezone.utc)
    return BusinessLine(
        id=object_id,
        tenant_id=tenant_id,
        name=object_id,
        es_index_pattern=f"{object_id}-*",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_batch_creation_deduplicates_targets_and_dispatches_each_task(monkeypatch):
    from logmind.domain.analysis import router

    now = datetime.now(timezone.utc)
    created = []
    dispatched = []

    async def fake_create(_session, task):
        task.id = f"task-{len(created) + 1}"
        task.created_at = now
        task.updated_at = now
        created.append(task)
        return task

    monkeypatch.setattr(router.task_repo, "create", fake_create)
    monkeypatch.setattr(router.run_analysis_task, "delay", dispatched.append)

    req = AnalysisTaskBatchCreate(
        business_line_ids=["biz-1", "biz-2", "biz-1"],
        time_from=now - timedelta(hours=1),
        time_to=now,
        full_log_analysis=True,
    )
    session = _FakeSession([_business_line("biz-1"), _business_line("biz-2")])
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="analyst")

    response = await router.create_analysis_tasks_batch(req, session, user)

    assert [task.business_line_id for task in created] == ["biz-1", "biz-2"]
    assert all('"full_log_analysis": true' in task.query_params for task in created)
    assert dispatched == ["task-1", "task-2"]
    assert len(response) == 2
    assert session.committed is True


@pytest.mark.asyncio
async def test_full_analysis_fetches_without_severity_and_preserves_total_count():
    captured = {}

    class FakeLogService:
        async def search_logs(self, request):
            captured["request"] = request
            return SimpleNamespace(
                total=12000,
                logs=[SimpleNamespace(raw={"message": "[INFO] healthy"})],
            )

    now = datetime.now(timezone.utc)
    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-1",
        business_line_id="biz-1",
        es_index_pattern="logs-*",
        severity_threshold=None,
        full_log_analysis=True,
        time_from=now - timedelta(hours=1),
        time_to=now,
    )

    result = await LogFetchStage(FakeLogService()).execute(ctx)

    assert captured["request"].severity is None
    assert captured["request"].size == 10000
    assert result.log_count == 12000
    assert result.log_metadata["fetched_count"] == 1


@pytest.mark.asyncio
async def test_full_analysis_bypasses_quality_and_business_noise_filters():
    line = '[INFO] {"status": true, "message": "获取成功"}'
    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-1",
        business_line_id="biz-1",
        full_log_analysis=True,
        raw_logs=[{"message": line}],
        processed_logs=line,
        log_count=1,
    )

    ctx = await LogQualityFilterStage().execute(ctx)
    ctx = await BusinessNoiseFilterStage().execute(ctx)

    assert ctx.processed_logs == line
    assert ctx.log_count == 1
    assert ctx.log_metadata["quality_filter_skipped"] == "full_log_analysis"
    assert ctx.log_metadata["business_noise_filter_skipped"] == "full_log_analysis"


def test_csharp_autodetection_handles_async_serilog_stack():
    logs = [{
        "message": (
            "[ERR] Unhandled exception System.Net.Http.HttpRequestException: request failed\n"
            "   at Gyyx.Api.Client+<SendAsync>d__12.MoveNext() "
            "in D:\\src\\Client.cs:line 88\n"
            "--- End of inner exception stack trace ---"
        ),
        "gy": {"filetype": "sys.log.txt"},
        "host": {"name": "win-api-01"},
    }]

    assert LogPreprocessStage._detect_language(logs) == "csharp"
    assert LogPreprocessStage._message_has_stack(logs[0]["message"]) is True
    assert LogPreprocessStage._extract_level(logs[0]) == "ERROR"
