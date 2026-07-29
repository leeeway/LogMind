import json

import pytest

from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis.stages.priority_decision import PriorityDecisionStage
from logmind.domain.analysis.stages.result_parse import ResultParseStage


def _context() -> PipelineContext:
    return PipelineContext(
        tenant_id="tenant-1",
        task_id="task-csharp",
        business_line_id="biz-csharp",
        business_line_name="C# Site",
        language="csharp",
        full_log_analysis=True,
        log_count=10000,
        business_weight=8,
        is_core_path=True,
    )


async def _disable_priority_learning(monkeypatch):
    async def adjustment(_business_line_id):
        return 0.0

    async def suppression(_business_line_id, _error_signature):
        return False, ""

    monkeypatch.setattr(
        "logmind.domain.analysis.priority_learning.compute_priority_adjustment",
        adjustment,
    )
    monkeypatch.setattr(
        "logmind.domain.analysis.priority_learning.check_suppression",
        suppression,
    )


@pytest.mark.asyncio
async def test_empty_warning_is_downgraded_and_never_alerts(monkeypatch):
    await _disable_priority_learning(monkeypatch)
    ctx = _context()
    ctx.ai_response = json.dumps([{
        "result_type": "anomaly",
        "content": "",
        "severity": "warning",
        "confidence_score": 0.9,
    }])

    ctx = await ResultParseStage().execute(ctx)
    ctx = await PriorityDecisionStage().execute(ctx)

    assert ctx.analysis_results[0]["severity"] == "info"
    assert ctx.analysis_results[0]["alertable"] is False
    assert ctx.analysis_results[0]["content"] == (
        "AI 未返回有效分析内容，本次任务已记录且不触发告警。"
    )
    assert ctx.priority_decision["priority"] == "P2"
    assert ctx.priority_decision["should_notify"] is False
    assert ctx.alerts_fired == []


@pytest.mark.asyncio
async def test_negative_enumeration_and_unproven_spike_are_suppressed(monkeypatch):
    await _disable_priority_learning(monkeypatch)
    ctx = _context()
    ctx.log_metadata["actionable_level_count"] = 0
    ctx.ai_response = json.dumps([{
        "result_type": "summary",
        "content": (
            "日志中未发现明确的 ERROR、CRITICAL、DataIntegrityViolationException、"
            "SQL 截断或核心表写入失败证据。整体请求和缓存读取均执行成功。"
            "错误率在 17:19 短时突增，但没有对应异常堆栈，无法确认原因。"
        ),
        "severity": "warning",
        "confidence_score": 0.85,
    }], ensure_ascii=False)

    ctx = await ResultParseStage().execute(ctx)
    ctx = await PriorityDecisionStage().execute(ctx)

    finding = ctx.analysis_results[0]
    assert "DataIntegrityViolationException" not in finding["content"]
    assert finding["content"] == (
        "检测到日志量短时波动，但缺少对应失败日志证据，本次仅记录且不触发告警。"
    )
    assert finding["severity"] == "info"
    assert finding["alertable"] is False
    assert ctx.priority_decision["should_notify"] is False
    assert ctx.priority_decision["factors"]["freq_ratio"] == 0.0


@pytest.mark.asyncio
async def test_concrete_dotnet_exception_remains_alertable(monkeypatch):
    await _disable_priority_learning(monkeypatch)
    ctx = _context()
    ctx.log_metadata["actionable_level_count"] = 12
    ctx.ai_response = json.dumps([{
        "result_type": "root_cause",
        "content": (
            "System.NullReferenceException 出现在 "
            "Gyyx.Qibao.OrderService.GetOrder() in OrderService.cs:line 88，"
            "导致订单查询请求失败。"
        ),
        "severity": "warning",
        "confidence_score": 0.91,
        "root_cause": "OrderService.GetOrder 空引用",
        "source_log_refs": ["log-csharp-1"],
    }], ensure_ascii=False)

    ctx = await ResultParseStage().execute(ctx)
    ctx = await PriorityDecisionStage().execute(ctx)

    assert ctx.analysis_results[0]["severity"] == "warning"
    assert ctx.analysis_results[0]["alertable"] is True
    assert ctx.priority_decision["should_notify"] is True
    assert ctx.alerts_fired == ctx.analysis_results


@pytest.mark.asyncio
async def test_full_log_volume_does_not_count_as_error_frequency(monkeypatch):
    await _disable_priority_learning(monkeypatch)
    ctx = _context()
    ctx.log_metadata["actionable_level_count"] = 1
    ctx.analysis_results = [{
        "result_type": "root_cause",
        "content": "System.TimeoutException: upstream timed out",
        "severity": "warning",
        "confidence_score": 0.8,
        "alertable": True,
    }]

    ctx = await PriorityDecisionStage().execute(ctx)

    assert ctx.priority_decision["factors"]["freq_ratio"] == 1.0
    assert ctx.priority_decision["factors"]["bonus"] < 3.0
