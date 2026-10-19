import pytest

from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis.stages.priority_decision import PriorityDecisionStage


@pytest.mark.asyncio
async def test_regression_override_populates_alerts_even_when_base_decision_suppresses(monkeypatch):
    async def fake_adjustment(_business_line_id):
        return 0.0

    async def fake_suppression(_business_line_id, _error_signature):
        return False, ""

    monkeypatch.setattr(
        "logmind.domain.analysis.priority_learning.compute_priority_adjustment",
        fake_adjustment,
    )
    monkeypatch.setattr(
        "logmind.domain.analysis.priority_learning.check_suppression",
        fake_suppression,
    )

    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-1",
        business_line_id="biz-1",
        business_line_name="Core Service",
        log_count=1,
        business_weight=1,
    )
    ctx.log_metadata["is_regression"] = True
    ctx.analysis_results = [
        {
            "result_type": "summary",
            "content": "历史已修复问题再次出现",
            "severity": "info",
            "confidence_score": 0.9,
        }
    ]

    result = await PriorityDecisionStage().execute(ctx)

    assert result.priority_decision["priority"] == "P0"
    assert result.priority_decision["should_notify"] is True
    assert result.priority_decision["should_wake"] is True
    assert result.alerts_fired == ctx.analysis_results[:1]


@pytest.mark.asyncio
async def test_zero_baseline_spike_and_csharp_concrete_fault_notify_with_default_weight(monkeypatch):
    async def fake_adjustment(_business_line_id):
        return 0.0

    async def fake_suppression(_business_line_id, _error_signature):
        return False, ""

    monkeypatch.setattr(
        "logmind.domain.analysis.priority_learning.compute_priority_adjustment",
        fake_adjustment,
    )
    monkeypatch.setattr(
        "logmind.domain.analysis.priority_learning.check_suppression",
        fake_suppression,
    )

    # 1. Zero-baseline error burst on a default-weight C# service -> scores P0
    spike_ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-spike",
        business_line_id="biz-qibao",
        business_line_name="游戏社区-奇宝斋官网",
        language="csharp",
        log_count=50,
        business_weight=5,
        is_core_path=False,
        has_stack_traces=True,
        night_policy="always",
    )
    spike_ctx.log_metadata["baseline_error_count"] = 0
    spike_ctx.log_metadata["baseline_computed"] = True
    spike_ctx.analysis_results = [
        {
            "result_type": "root_cause",
            "content": "GetItemForBid 数据库查询超时",
            "severity": "critical",
            "confidence_score": 0.85,
            "alertable": True,
        }
    ]
    spike_res = await PriorityDecisionStage().execute(spike_ctx)
    assert spike_res.priority_decision["priority"] == "P0"
    assert spike_res.priority_decision["should_notify"] is True
    assert spike_res.priority_decision["factors"]["frequency"] == 25.0

    # 2. Low-frequency C# concrete exception on a default-weight service -> notifies at P1
    low_freq_ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-low-freq",
        business_line_id="biz-pay-oa",
        business_line_name="办公-pay.oa.gyyx.cn",
        language="csharp",
        log_count=1,
        business_weight=5,
        is_core_path=False,
        has_stack_traces=True,
        night_policy="always",
    )
    low_freq_ctx.log_metadata["patrol"] = {"trigger": "concrete_exception"}
    low_freq_ctx.analysis_results = [
        {
            "result_type": "root_cause",
            "content": "PayOrderService.Get 抛出 System.NullReferenceException",
            "severity": "error",
            "confidence_score": 0.85,
            "alertable": True,
        }
    ]
    low_freq_res = await PriorityDecisionStage().execute(low_freq_ctx)
    assert low_freq_res.priority_decision["priority"] in ("P0", "P1")
    assert low_freq_res.priority_decision["should_notify"] is True
    assert len(low_freq_res.alerts_fired) == 1

