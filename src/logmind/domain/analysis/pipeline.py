"""
AI Analysis Pipeline — Orchestrator & Core Types

This module defines:
  - PipelineContext: shared data flowing through all stages
  - PipelineStage: abstract base class for pipeline stages
  - AnalysisPipeline: orchestrator that runs stages in sequence

Stage implementations are in the `stages/` subpackage.

Stages (assembled in tasks.py):
 1. LogFetchStage         — Fetch logs from ES
 2. LogPreprocessStage    — Clean, deduplicate, truncate, merge stack traces
 3. LogQualityFilterStage — Filter false-positive INFO/noise logs
 4. ErrorBaselineStage    — Query historical error frequency baseline
 5. ErrorFingerprintStage — Fast MD5 fingerprint dedup (Redis)
 6. SemanticDedupStage    — Vector-level semantic dedup (ES KNN)
 7. PromptBuildStage      — Assemble prompt from template
 8. AgentInferenceStage   — Multi-step AI Agent with tool calling
 9. ResultParseStage      — Parse AI output to structured results
10. PriorityDecisionStage — P0/P1/P2 priority scoring + night policy
11. PersistStage          — Save results to DB
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from logmind.core.logging import get_logger
from logmind.domain.provider.base import TokenUsage

logger = get_logger(__name__)


# ── Pipeline Context ─────────────────────────────────────

@dataclass
class PipelineContext:
    """Shared context flowing through all pipeline stages."""

    # Input
    tenant_id: str
    task_id: str
    business_line_id: str
    business_line_name: str = ""
    es_index_pattern: str = ""
    severity_threshold: str = "error"
    time_from: datetime | None = None
    time_to: datetime | None = None
    query: str = ""
    extra_filters: dict = field(default_factory=dict)

    # Business line language — determines parsing strategy
    language: str = "java"  # java / csharp / python / go / other

    # GYYX business context
    domain: str = ""
    branch: str = ""
    image_version: str = ""
    host_name: str = ""

    # Stage 1: Log Fetch
    raw_logs: list[dict] = field(default_factory=list)
    log_count: int = 0

    # Stage 2: Preprocess
    processed_logs: str = ""
    log_metadata: dict = field(default_factory=dict)
    has_stack_traces: bool = False

    # Stage 3: RAG
    rag_context: str = ""
    rag_sources: list[str] = field(default_factory=list)

    # Stage 4: Prompt Build
    system_prompt: str = ""
    user_prompt: str = ""
    prompt_template_id: str = ""

    # Stage 5: AI Inference
    ai_response: str = ""
    token_usage: TokenUsage | None = None
    provider_config_id: str = ""

    # Stage 6: Result Parse
    analysis_results: list[dict] = field(default_factory=list)

    # Stage 7: Alert / Priority Decision
    alerts_fired: list[dict] = field(default_factory=list)
    priority_decision: dict = field(default_factory=dict)

    # Business line priority config (loaded from DB)
    business_weight: int = 5
    is_core_path: bool = False
    estimated_dau: int = 0
    night_policy: str = "p0_only"
    night_hours: str = "22:00-08:00"

    # Error tracking
    errors: list[str] = field(default_factory=list)

    # Semantic dedup (Phase 3)
    semantic_dedup_hit: bool = False
    error_signature: str = ""

    # Observability: per-stage execution metrics
    stage_metrics: list[dict] = field(default_factory=list)
    # Agent tool call records
    tool_call_records: list[dict] = field(default_factory=list)

    # Signal self-learning
    learned_signals: list[str] = field(default_factory=list)
    learned_rules: list[str] = field(default_factory=list)


# ── Stage Base ───────────────────────────────────────────

class PipelineStage(ABC):
    """Abstract pipeline stage."""

    is_critical: bool = True  # If True, pipeline aborts on failure

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        ...


# ── Pipeline Orchestrator ────────────────────────────────

class AnalysisPipeline:
    """
    Orchestrates the multi-stage log analysis pipeline.

    Each stage receives and returns a PipelineContext.
    Critical stages abort the pipeline on failure;
    non-critical stages log errors and continue.

    Stage metrics (name, duration_ms, status) are collected in
    ctx.stage_metrics for persistence and observability.
    """

    def __init__(self, stages: list[PipelineStage]):
        self.stages = stages

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Execute all pipeline stages in order, recording per-stage metrics."""
        for stage in self.stages:
            # Semantic dedup hit → skip AI inference stages
            if ctx.semantic_dedup_hit and stage.name in (
                'prompt_build', 'ai_inference'
            ):
                logger.info("stage_skipped_semantic_dedup", stage=stage.name, task_id=ctx.task_id)
                ctx.stage_metrics.append({
                    "stage": stage.name,
                    "duration_ms": 0,
                    "status": "skipped",
                    "error": None,
                })
                from logmind.core.metrics import record_stage_duration
                record_stage_duration(stage.name, 0, "skipped")
                continue

            t0 = time.monotonic()
            try:
                logger.info("pipeline_stage_start", stage=stage.name, task_id=ctx.task_id)
                ctx = await stage.execute(ctx)
                duration_ms = int((time.monotonic() - t0) * 1000)

                logger.info(
                    "pipeline_stage_done",
                    stage=stage.name,
                    duration_ms=duration_ms,
                    task_id=ctx.task_id,
                )
                ctx.stage_metrics.append({
                    "stage": stage.name,
                    "duration_ms": duration_ms,
                    "status": "ok",
                    "error": None,
                })
                # Prometheus metrics
                from logmind.core.metrics import record_stage_duration
                record_stage_duration(stage.name, duration_ms / 1000, "ok")
            except Exception as e:
                duration_ms = int((time.monotonic() - t0) * 1000)
                error_msg = f"Stage [{stage.name}] failed: {e}"
                logger.error(
                    "pipeline_stage_failed",
                    stage=stage.name,
                    duration_ms=duration_ms,
                    error=str(e),
                )
                ctx.errors.append(error_msg)
                ctx.stage_metrics.append({
                    "stage": stage.name,
                    "duration_ms": duration_ms,
                    "status": "error",
                    "error": str(e)[:500],
                })
                from logmind.core.metrics import record_stage_duration
                record_stage_duration(stage.name, duration_ms / 1000, "error")

                if stage.is_critical:
                    from logmind.core.exceptions import PipelineError
                    raise PipelineError(stage.name, e)
                # Non-critical → continue

        return ctx
