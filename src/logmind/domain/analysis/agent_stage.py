"""
Agent Inference Stage — Multi-step AI Reasoning with Tool Calling

Replaces the one-shot AIInferenceStage with an iterative agent loop.
The AI can autonomously call ES tools to gather more context before
producing its final analysis.

Loop:
  1. Send messages + tools to AI
  2. If AI returns tool_calls → execute tools → append results → goto 1
  3. If AI returns content (finish_reason=stop) → done

Bounded by max_steps to control token consumption.
"""

import json

from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.analysis.agent_tools import AGENT_TOOLS, execute_tool
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage
from logmind.domain.provider.base import ChatMessage, ChatRequest, TokenUsage

logger = get_logger(__name__)


class AgentInferenceStage(PipelineStage):
    """
    AI Agent with tool-calling capability.

    Iteratively calls the AI model, executing tool calls as requested,
    until the AI produces a final answer or max_steps is reached.
    """

    name = "ai_inference"  # Keep same name for log compatibility

    def __init__(self, provider_manager):
        self.provider_manager = provider_manager

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        from logmind.core.database import get_db_context

        settings = get_settings()
        max_steps = settings.analysis_agent_max_steps
        agent_enabled = settings.analysis_agent_enabled

        # Build initial messages
        messages = [
            {"role": "system", "content": self._build_agent_system_prompt(ctx)},
            {"role": "user", "content": ctx.user_prompt},
        ]

        # If agent is disabled, fall back to one-shot (no tools)
        tools = AGENT_TOOLS if agent_enabled else None

        total_usage = TokenUsage()
        step = 0
        response = None

        # Use a single DB session for the entire agent loop to avoid
        # 'Event loop is closed' errors in Celery's asyncio.run() context
        async with get_db_context() as session:
            while step < max_steps:
                step += 1

                request = ChatRequest(
                    messages=[
                        ChatMessage(role=m["role"], content=m.get("content", ""))
                        for m in messages
                        if m.get("content")
                    ],
                    tools=tools,
                    temperature=0.3,
                    max_tokens=4096,
                    extra_params={"_raw_messages": messages} if tools else {},
                )

                response, provider_id = await self.provider_manager.chat_with_fallback(
                    session=session,
                    tenant_id=ctx.tenant_id,
                    request=request,
                    preferred_provider_id=ctx.provider_config_id or None,
                )

                # Accumulate token usage
                total_usage.prompt_tokens += response.usage.prompt_tokens
                total_usage.completion_tokens += response.usage.completion_tokens
                total_usage.total_tokens += response.usage.total_tokens
                ctx.provider_config_id = provider_id

                # Check if AI wants to call tools
                if response.tool_calls and tools:
                    logger.info(
                        "agent_tool_calls",
                        step=step,
                        tools=[tc["function"]["name"] for tc in response.tool_calls],
                        task_id=ctx.task_id,
                    )

                    messages.append({
                        "role": "assistant",
                        "content": response.content or None,
                        "tool_calls": response.tool_calls,
                    })

                    for tc in response.tool_calls:
                        func_name = tc["function"]["name"]
                        try:
                            func_args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            func_args = {}

                        result = await execute_tool(
                            tool_name=func_name,
                            arguments=func_args,
                            es_index_pattern=ctx.es_index_pattern,
                            time_from=ctx.time_from,
                            time_to=ctx.time_to,
                        )

                        if len(result) > 8000:
                            result = result[:8000] + "\n... (truncated)"

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })

                        logger.info(
                            "agent_tool_result",
                            tool=func_name,
                            result_length=len(result),
                            task_id=ctx.task_id,
                        )
                    continue

                # AI produced final content — exit loop
                ctx.ai_response = response.content
                break

            else:
                logger.warning(
                    "agent_max_steps_reached",
                    max_steps=max_steps,
                    task_id=ctx.task_id,
                )
                if not ctx.ai_response and response:
                    ctx.ai_response = response.content or ""

        ctx.token_usage = total_usage

        logger.info(
            "ai_inference_completed",
            tokens=total_usage.total_tokens,
            model=response.model if response else "unknown",
            agent_steps=step,
            task_id=ctx.task_id,
        )
        return ctx

    def _build_agent_system_prompt(self, ctx: PipelineContext) -> str:
        """
        Build system prompt that includes agent instructions.

        Prepends tool-usage guidance to the existing system prompt.
        """
        agent_instructions = """## 你的工作方式
你是一名拥有 Elasticsearch 查询能力的 SRE 分析师。
你可以调用工具来主动搜索更多日志、查看上下文、统计错误频率，
并参考历史分析记录来加速诊断。

### 智能分析策略
1. **先查历史**: 首先调用 search_similar_incidents 工具，看看历史上是否分析过类似错误模式
2. **如有历史**: 参考历史结论，验证其是否仍然适用于当前情况，避免重复无效分析
3. **如无历史**: 按正常流程使用 search_logs、get_log_context 等工具调查
4. **趋势感知**: 调用 count_error_patterns 对比当前错误频率，判断偶发还是频发

### 工具使用原则
- 只在需要更多信息时调用工具，不要为了调用而调用
- 优先使用 search_similar_incidents 查看历史经验
- 使用 count_error_patterns 了解全局情况
- 使用 search_logs 深入调查具体错误
- 使用 get_log_context 理解错误发生的完整场景
- 使用 search_knowledge_base 查阅内部 SOP 文档

### 最终输出
当你完成分析后，直接输出 JSON 数组格式的分析结果（不要再调用工具）。

"""
        return agent_instructions + ctx.system_prompt
