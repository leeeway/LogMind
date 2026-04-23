"""Persist Stage — Save analysis results to the database."""

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)


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
