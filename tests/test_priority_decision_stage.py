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
