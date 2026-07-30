from unittest.mock import MagicMock

import pytest

from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis.tasks import _run_learning_hooks


def _context(result: dict) -> PipelineContext:
    ctx = PipelineContext(
        tenant_id="tenant-1",
        task_id="task-1",
        business_line_id="biz-1",
        processed_logs="System.InvalidOperationException: operation failed",
        error_signature=(
            "EXCEPTIONS: System.InvalidOperationException | "
            "STACK: Gyyx.OrderService.Save"
        ),
    )
    ctx.analysis_results = [result]
    return ctx


@pytest.mark.asyncio
async def test_non_actionable_summary_is_not_written_to_semantic_memory(monkeypatch):
    delay = MagicMock()
    monkeypatch.setattr(
        "logmind.domain.analysis.analysis_indexer.index_analysis_result.delay",
        delay,
    )
    ctx = _context({
        "severity": "info",
        "content": "当前时间范围内没有可核验的系统故障证据。",
        "alertable": False,
    })

    await _run_learning_hooks(ctx, ctx.task_id)

    delay.assert_not_called()


@pytest.mark.asyncio
async def test_actionable_finding_is_written_to_semantic_memory(monkeypatch):
    delay = MagicMock()
    monkeypatch.setattr(
        "logmind.domain.analysis.analysis_indexer.index_analysis_result.delay",
        delay,
    )
    ctx = _context({
        "severity": "warning",
        "content": "System.InvalidOperationException 导致订单保存失败。",
        "alertable": True,
    })

    await _run_learning_hooks(ctx, ctx.task_id)

    delay.assert_called_once()
    assert "InvalidOperationException" in delay.call_args.kwargs["analysis_content"]
