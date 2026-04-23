"""AI Inference Stage — One-shot AI call (non-Agent mode)."""

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage
from logmind.domain.provider.base import ChatMessage, ChatRequest

logger = get_logger(__name__)


class AIInferenceStage(PipelineStage):
    """Call AI provider for analysis (single-shot, no tool calling)."""

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

        logger.info("ai_inference_completed", tokens=response.usage.total_tokens,
                     model=response.model, task_id=ctx.task_id)
        return ctx
