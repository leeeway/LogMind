"""
Tests for AnalysisPipeline orchestrator.

Covers:
  - Sequential stage execution
  - Non-critical stage error tolerance
  - Critical stage failure propagation
  - Semantic dedup stage skipping
  - Stage metrics collection
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from logmind.domain.analysis.pipeline import (
    AnalysisPipeline,
    PipelineContext,
    PipelineStage,
)


# ── Test Stage Fixtures ──────────────────────────────────

class OkStage(PipelineStage):
    name = "ok_stage"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log_count += 1
        return ctx


class ErrorStage(PipelineStage):
    name = "error_stage"
    is_critical = True

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        raise RuntimeError("Stage exploded")


class NonCriticalErrorStage(PipelineStage):
    name = "non_critical_error"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        raise ValueError("Non-critical failure")


class PromptBuildStub(PipelineStage):
    name = "prompt_build"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        ctx.system_prompt = "test"
        return ctx


class AIInferenceStub(PipelineStage):
    name = "ai_inference"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        ctx.ai_response = "test response"
        return ctx


# ── Helper ───────────────────────────────────────────────

def _make_ctx(**kwargs) -> PipelineContext:
    defaults = {
        "tenant_id": "t1",
        "task_id": "task-001",
        "business_line_id": "biz-001",
    }
    defaults.update(kwargs)
    return PipelineContext(**defaults)


# ── Tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_runs_all_stages_in_order():
    """All stages execute and metrics are recorded."""
    pipeline = AnalysisPipeline(stages=[OkStage(), OkStage(), OkStage()])
    ctx = _make_ctx()

    result = await pipeline.run(ctx)

    assert result.log_count == 3
    assert len(result.stage_metrics) == 3
    assert all(m["status"] == "ok" for m in result.stage_metrics)


@pytest.mark.asyncio
async def test_critical_stage_aborts_pipeline():
    """A critical stage failure stops the pipeline."""
    pipeline = AnalysisPipeline(stages=[OkStage(), ErrorStage(), OkStage()])
    ctx = _make_ctx()

    with pytest.raises(Exception, match="Pipeline stage \\[error_stage\\] failed"):
        await pipeline.run(ctx)

    # Only first two stages should have metrics
    assert len(ctx.stage_metrics) == 2
    assert ctx.stage_metrics[0]["status"] == "ok"
    assert ctx.stage_metrics[1]["status"] == "error"


@pytest.mark.asyncio
async def test_non_critical_stage_continues():
    """A non-critical stage failure is logged but doesn't stop the pipeline."""
    pipeline = AnalysisPipeline(stages=[OkStage(), NonCriticalErrorStage(), OkStage()])
    ctx = _make_ctx()

    result = await pipeline.run(ctx)

    assert result.log_count == 2  # Two OkStages executed
    assert len(result.stage_metrics) == 3
    assert result.stage_metrics[1]["status"] == "error"
    assert result.stage_metrics[2]["status"] == "ok"
    assert len(result.errors) == 1


@pytest.mark.asyncio
async def test_semantic_dedup_hit_skips_ai_stages():
    """When semantic_dedup_hit is True, prompt_build and ai_inference are skipped."""
    pipeline = AnalysisPipeline(stages=[
        OkStage(), PromptBuildStub(), AIInferenceStub(), OkStage()
    ])
    ctx = _make_ctx(semantic_dedup_hit=True)

    result = await pipeline.run(ctx)

    assert result.log_count == 2  # Two OkStages executed
    assert result.system_prompt == ""  # PromptBuild was skipped
    assert result.ai_response == ""    # AIInference was skipped
    assert len(result.stage_metrics) == 4
    assert result.stage_metrics[1]["status"] == "skipped"
    assert result.stage_metrics[2]["status"] == "skipped"


@pytest.mark.asyncio
async def test_stage_metrics_include_duration():
    """Stage metrics record non-negative duration_ms."""
    pipeline = AnalysisPipeline(stages=[OkStage()])
    ctx = _make_ctx()

    result = await pipeline.run(ctx)

    assert len(result.stage_metrics) == 1
    assert result.stage_metrics[0]["duration_ms"] >= 0
    assert result.stage_metrics[0]["error"] is None


@pytest.mark.asyncio
async def test_empty_pipeline():
    """Pipeline with no stages returns context unchanged."""
    pipeline = AnalysisPipeline(stages=[])
    ctx = _make_ctx()

    result = await pipeline.run(ctx)

    assert result.log_count == 0
    assert result.stage_metrics == []
