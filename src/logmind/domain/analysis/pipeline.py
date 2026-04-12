"""
AI Analysis Pipeline — 8-Stage Log Analysis

Stages:
1. LogFetchStage     — Fetch logs from ES
2. LogPreprocessStage — Clean, deduplicate, truncate
3. RAGRetrieveStage   — Retrieve relevant knowledge
4. PromptBuildStage   — Assemble prompt from template
5. AIInferenceStage   — Call AI provider
6. ResultParseStage   — Parse AI output to structured results
7. AlertEvalStage     — Evaluate alert rules
8. PersistStage       — Save results to DB
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from logmind.core.logging import get_logger
from logmind.domain.provider.base import ChatMessage, ChatRequest, TokenUsage

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

    # Stage 1: Log Fetch
    raw_logs: list[dict] = field(default_factory=list)
    log_count: int = 0

    # Stage 2: Preprocess
    processed_logs: str = ""
    log_metadata: dict = field(default_factory=dict)

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

    # Stage 7: Alert
    alerts_fired: list[dict] = field(default_factory=list)

    # Error tracking
    errors: list[str] = field(default_factory=list)


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


# ── Stage 1: Log Fetch ───────────────────────────────────

class LogFetchStage(PipelineStage):
    """Fetch logs from Elasticsearch."""

    name = "log_fetch"

    def __init__(self, log_service):
        self.log_service = log_service

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        from logmind.domain.log.schemas import LogQueryRequest

        request = LogQueryRequest(
            index_pattern=ctx.es_index_pattern,
            time_from=ctx.time_from,
            time_to=ctx.time_to,
            query=ctx.query,
            severity=ctx.severity_threshold,
            extra_filters=ctx.extra_filters,
            size=500,  # Cost control: max logs per task
        )
        result = await self.log_service.search_logs(request)
        ctx.raw_logs = [log.raw for log in result.logs]
        ctx.log_count = len(ctx.raw_logs)

        logger.info("log_fetch_completed", count=ctx.log_count, task_id=ctx.task_id)
        return ctx


# ── Stage 2: Preprocess ─────────────────────────────────

class LogPreprocessStage(PipelineStage):
    """Clean, deduplicate, and format logs for AI consumption."""

    name = "log_preprocess"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.raw_logs:
            ctx.processed_logs = "(No logs found matching the query)"
            return ctx

        # Deduplicate by message
        seen = set()
        unique_logs = []
        for log in ctx.raw_logs:
            msg = self._extract_message(log)
            msg_key = msg[:200]  # Dedup key
            if msg_key not in seen:
                seen.add(msg_key)
                unique_logs.append(log)

        # Format logs
        lines = []
        for log in unique_logs[:200]:  # Limit to 200 unique entries
            ts = log.get("@timestamp", "")
            level = self._extract_level(log)
            msg = self._extract_message(log)
            ns = log.get("kubernetes", {}).get("namespace", "")
            pod = log.get("kubernetes", {}).get("pod", {}).get("name", "")

            if ns and pod:
                lines.append(f"[{ts}] [{level}] [{ns}/{pod}] {msg}")
            else:
                lines.append(f"[{ts}] [{level}] {msg}")

        ctx.processed_logs = "\n".join(lines)

        # Truncate to ~8000 tokens (~32000 chars)
        if len(ctx.processed_logs) > 32000:
            ctx.processed_logs = ctx.processed_logs[:32000] + "\n... (truncated)"

        ctx.log_metadata = {
            "original_count": ctx.log_count,
            "deduped_count": len(unique_logs),
            "formatted_count": len(lines),
        }

        logger.info("log_preprocess_completed", **ctx.log_metadata, task_id=ctx.task_id)
        return ctx

    @staticmethod
    def _extract_level(source: dict) -> str:
        for field_name in ["level", "severity", "loglevel"]:
            if field_name in source:
                return str(source[field_name]).upper()
        if isinstance(source.get("log"), dict):
            return str(source["log"].get("level", "")).upper()
        return "UNKNOWN"

    @staticmethod
    def _extract_message(source: dict) -> str:
        for field_name in ["message", "msg", "log", "content"]:
            val = source.get(field_name)
            if isinstance(val, str):
                return val
        return str(source)[:500]


# ── Stage 3: RAG Retrieve ────────────────────────────────

class RAGRetrieveStage(PipelineStage):
    """Retrieve relevant context from RAG knowledge base."""

    name = "rag_retrieve"
    is_critical = False  # Non-critical — analysis proceeds without RAG

    def __init__(self, rag_service=None):
        self.rag_service = rag_service

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not self.rag_service:
            return ctx  # RAG not configured

        # TODO: Implement RAG retrieval in Phase 4
        # Extract key error messages and search knowledge base
        logger.info("rag_stage_skipped", reason="not_implemented", task_id=ctx.task_id)
        return ctx


# ── Stage 4: Prompt Build ────────────────────────────────

class PromptBuildStage(PipelineStage):
    """Assemble the final prompt from template + variables."""

    name = "prompt_build"

    def __init__(self, prompt_engine, prompt_repo):
        self.prompt_engine = prompt_engine
        self.prompt_repo = prompt_repo

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        from sqlalchemy import select

        from logmind.core.database import get_db_context
        from logmind.domain.prompt.models import PromptTemplate

        async with get_db_context() as session:
            # Get template — use specified or find default
            if ctx.prompt_template_id:
                template = await self.prompt_repo.get_by_id(
                    session, ctx.prompt_template_id, tenant_id=ctx.tenant_id
                )
            else:
                stmt = select(PromptTemplate).where(
                    PromptTemplate.tenant_id == ctx.tenant_id,
                    PromptTemplate.category == "log_analysis",
                    PromptTemplate.is_default == True,
                    PromptTemplate.is_active == True,
                ).limit(1)
                result = await session.execute(stmt)
                template = result.scalar_one_or_none()

            if not template:
                # Use built-in fallback prompt
                ctx.system_prompt = self._fallback_system_prompt()
                ctx.user_prompt = self._fallback_user_prompt(ctx)
                return ctx

            variables = {
                "business_line": ctx.business_line_name,
                "service_name": ctx.business_line_name,
                "time_range": f"{ctx.time_from} ~ {ctx.time_to}",
                "namespace": "",
                "logs": ctx.processed_logs,
                "log_count": ctx.log_count,
                "rag_context": ctx.rag_context,
            }

            ctx.system_prompt, ctx.user_prompt = self.prompt_engine.render(
                template, variables
            )
            ctx.prompt_template_id = template.id

        logger.info("prompt_built", template_id=ctx.prompt_template_id, task_id=ctx.task_id)
        return ctx

    @staticmethod
    def _fallback_system_prompt() -> str:
        return """你是一名资深 SRE 工程师和日志分析专家。
分析 Kubernetes 集群中的应用日志，识别错误模式并给出根因分析。

## 输出要求
请以 JSON 数组格式输出，每个元素包含：
- result_type: "anomaly" | "root_cause" | "suggestion"
- severity: "critical" | "warning" | "info"
- content: 详细分析说明
- confidence_score: 置信度 0.0~1.0

只输出 JSON 数组，不要输出其他内容。只关注严重问题 (ERROR/CRITICAL)。"""

    @staticmethod
    def _fallback_user_prompt(ctx: PipelineContext) -> str:
        return f"""## 分析上下文
- 业务线: {ctx.business_line_name}
- 时间范围: {ctx.time_from} ~ {ctx.time_to}
- 日志数量: {ctx.log_count}

## 日志内容
```
{ctx.processed_logs}
```

请分析以上日志，只聚焦严重错误，输出 JSON 数组格式的分析结果。"""


# ── Stage 5: AI Inference ────────────────────────────────

class AIInferenceStage(PipelineStage):
    """Call AI provider for analysis."""

    name = "ai_inference"

    def __init__(self, provider_manager):
        self.provider_manager = provider_manager

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        from logmind.core.database import get_db_context

        request = ChatRequest(
            messages=[
                ChatMessage(role="system", content=ctx.system_prompt),
                ChatMessage(role="user", content=ctx.user_prompt),
            ],
            temperature=0.3,
            max_tokens=4096,
        )

        async with get_db_context() as session:
            response, provider_id = await self.provider_manager.chat_with_fallback(
                session=session,
                tenant_id=ctx.tenant_id,
                request=request,
                preferred_provider_id=ctx.provider_config_id or None,
            )

        ctx.ai_response = response.content
        ctx.token_usage = response.usage
        ctx.provider_config_id = provider_id

        logger.info(
            "ai_inference_completed",
            tokens=response.usage.total_tokens,
            model=response.model,
            task_id=ctx.task_id,
        )
        return ctx


# ── Stage 6: Result Parse ────────────────────────────────

class ResultParseStage(PipelineStage):
    """Parse AI response into structured analysis results."""

    name = "result_parse"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        try:
            # Try to extract JSON from the response
            content = ctx.ai_response.strip()

            # Handle markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            parsed = json.loads(content)

            if isinstance(parsed, dict):
                parsed = [parsed]

            ctx.analysis_results = []
            for item in parsed:
                ctx.analysis_results.append({
                    "result_type": item.get("result_type", "anomaly"),
                    "content": item.get("content", ""),
                    "severity": item.get("severity", "info"),
                    "confidence_score": float(item.get("confidence_score", 0.5)),
                    "structured_data": json.dumps(item, ensure_ascii=False),
                })

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            # Fallback: treat entire response as a single result
            logger.warning("result_parse_fallback", error=str(e), task_id=ctx.task_id)
            ctx.analysis_results = [{
                "result_type": "summary",
                "content": ctx.ai_response,
                "severity": "info",
                "confidence_score": 0.5,
                "structured_data": "{}",
            }]

        logger.info(
            "result_parse_completed",
            result_count=len(ctx.analysis_results),
            task_id=ctx.task_id,
        )
        return ctx


# ── Stage 7: Alert Eval ──────────────────────────────────

class AlertEvalStage(PipelineStage):
    """Evaluate analysis results against alert rules."""

    name = "alert_eval"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        # Check for critical severity results → fire alerts
        critical_results = [
            r for r in ctx.analysis_results
            if r.get("severity") == "critical" and r.get("confidence_score", 0) >= 0.7
        ]

        if critical_results:
            ctx.alerts_fired = critical_results
            logger.warning(
                "critical_alerts_detected",
                count=len(critical_results),
                task_id=ctx.task_id,
            )
            # Alert notification will be handled by the alert domain

        return ctx


# ── Stage 8: Persist ─────────────────────────────────────

class PersistStage(PipelineStage):
    """Persist analysis results to the database."""

    name = "persist"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        from logmind.core.database import get_db_context
        from logmind.domain.analysis.models import AnalysisResult

        async with get_db_context() as session:
            for result in ctx.analysis_results:
                ar = AnalysisResult(
                    task_id=ctx.task_id,
                    result_type=result["result_type"],
                    content=result["content"],
                    severity=result["severity"],
                    confidence_score=result["confidence_score"],
                    structured_data=result.get("structured_data", "{}"),
                )
                session.add(ar)
            await session.flush()

        logger.info("results_persisted", count=len(ctx.analysis_results), task_id=ctx.task_id)
        return ctx


# ── Pipeline Orchestrator ────────────────────────────────

class AnalysisPipeline:
    """
    Orchestrates the 8-stage log analysis pipeline.

    Each stage receives and returns a PipelineContext.
    Critical stages abort the pipeline on failure;
    non-critical stages log errors and continue.
    """

    def __init__(self, stages: list[PipelineStage]):
        self.stages = stages

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Execute all pipeline stages in order."""
        for stage in self.stages:
            try:
                logger.info("pipeline_stage_start", stage=stage.name, task_id=ctx.task_id)
                ctx = await stage.execute(ctx)
                logger.info("pipeline_stage_done", stage=stage.name, task_id=ctx.task_id)
            except Exception as e:
                error_msg = f"Stage [{stage.name}] failed: {e}"
                logger.error("pipeline_stage_failed", stage=stage.name, error=str(e))
                ctx.errors.append(error_msg)

                if stage.is_critical:
                    from logmind.core.exceptions import PipelineError
                    raise PipelineError(stage.name, e)
                # Non-critical → continue

        return ctx
