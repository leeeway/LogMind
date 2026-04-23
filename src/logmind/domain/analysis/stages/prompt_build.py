"""
Prompt Build Stage — Assemble AI prompt from template + variables

Stage 4 of the analysis pipeline.
"""

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)


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
            target_category = "log_analysis"
            if ctx.has_stack_traces:
                target_category = "stack_trace_analysis"

            if ctx.prompt_template_id:
                template = await self.prompt_repo.get_by_id(
                    session, ctx.prompt_template_id, tenant_id=ctx.tenant_id
                )
            else:
                stmt = select(PromptTemplate).where(
                    PromptTemplate.tenant_id == ctx.tenant_id,
                    PromptTemplate.category == target_category,
                    PromptTemplate.is_default == True,
                    PromptTemplate.is_active == True,
                ).limit(1)
                result = await session.execute(stmt)
                template = result.scalar_one_or_none()

                if not template and target_category != "log_analysis":
                    stmt = select(PromptTemplate).where(
                        PromptTemplate.tenant_id == ctx.tenant_id,
                        PromptTemplate.category == "log_analysis",
                        PromptTemplate.is_default == True,
                        PromptTemplate.is_active == True,
                    ).limit(1)
                    result = await session.execute(stmt)
                    template = result.scalar_one_or_none()

            if not template:
                ctx.system_prompt = _fallback_system_prompt(ctx)
                ctx.user_prompt = _fallback_user_prompt(ctx)
                return ctx

            variables = {
                "business_line": ctx.business_line_name,
                "service_name": ctx.business_line_name,
                "time_range": f"{ctx.time_from} ~ {ctx.time_to}",
                "namespace": "",
                "logs": ctx.processed_logs,
                "log_count": ctx.log_count,
                "rag_context": ctx.rag_context,
                "domain": ctx.domain,
                "branch": ctx.branch,
                "image_version": ctx.image_version,
                "host_name": ctx.host_name,
                "language": ctx.language,
                "has_stack_traces": ctx.has_stack_traces,
            }

            ctx.system_prompt, ctx.user_prompt = self.prompt_engine.render(
                template, variables
            )
            ctx.prompt_template_id = template.id

        # Inject business line intelligence profile
        try:
            from logmind.domain.analysis.business_profile import build_profile_context
            profile = await build_profile_context(ctx.business_line_id)
            if profile:
                ctx.system_prompt = ctx.system_prompt + "\n\n" + profile
                logger.info("business_profile_injected",
                            business_line_id=ctx.business_line_id,
                            profile_length=len(profile), task_id=ctx.task_id)
        except Exception as e:
            logger.warning("business_profile_inject_failed", error=str(e))

        logger.info("prompt_built", template_id=ctx.prompt_template_id, task_id=ctx.task_id)
        return ctx


def _fallback_system_prompt(ctx: PipelineContext) -> str:
    lang_desc = {"java": "Java/Spring Boot", "csharp": "C#/.NET",
                 "python": "Python", "go": "Go", "other": ""}
    tech_stack = lang_desc.get(ctx.language, "")
    tech_hint = f"（技术栈: {tech_stack}）" if tech_stack else ""

    base = f"""你是一名资深 SRE 工程师和日志分析专家。
分析应用服务日志{tech_hint}，识别错误模式、异常趋势并给出根因分析。

## 输出要求
请以 JSON 数组格式输出，每个元素包含：
- result_type: "anomaly" | "root_cause" | "suggestion"
- severity: "critical" | "warning" | "info"
- content: 详细分析说明
- confidence_score: 置信度 0.0~1.0
- error_signals: (可选) 从日志中识别出的关键错误信号短语列表。
- experience_rule: (可选) 一条可复用的分析经验规则。

## 重要规则
1. 只输出 JSON 数组，不要输出其他内容。
2. 数组中必须至少包含一个元素。
3. 即使日志中没有严重问题，也请输出至少一条 info 级别的总结。
4. 对相同类型的错误请合并分析，说明出现频率和影响范围。
5. 对于 severity 为 critical 或 warning 的结果，务必提供 error_signals 和 experience_rule 字段。"""

    if ctx.has_stack_traces:
        if ctx.language == "csharp":
            base += """

## .NET 堆栈异常分析指引
- 追踪 InnerException 链，找到最内层根因异常
- 重点关注 Gyyx.* 命名空间下的业务代码异常
- 区分业务代码异常 vs 框架异常（System.*, Microsoft.*）
- 对 NullReferenceException 分析可能的空引用来源
- 关注数据库操作异常（SqlException、连接池耗尽等）
- 合并相同异常类的多次出现，统计频率
- 给出具体的代码修复建议（涉及的类名和方法）"""
        else:
            base += """

## Java 堆栈异常分析指引
- 重点关注 Caused by 链，找到根因异常
- 区分业务代码异常（cn.gyyx.* 包）和框架异常（Spring、MyBatis 等）
- 对 NullPointerException 类分析可能的空值来源
- 合并相同异常类的多次出现，统计频率
- 给出具体的代码修复建议（涉及的类名和方法）"""

    return base


def _fallback_user_prompt(ctx: PipelineContext) -> str:
    lang_names = {"java": "Java", "csharp": "C#/.NET", "python": "Python", "go": "Go"}
    context_lines = [
        f"- 业务线: {ctx.business_line_name}",
        f"- 时间范围: {ctx.time_from} ~ {ctx.time_to}",
        f"- 日志数量: {ctx.log_count}",
    ]
    if ctx.language in lang_names:
        context_lines.append(f"- 开发语言: {lang_names[ctx.language]}")
    if ctx.domain:
        context_lines.append(f"- 站点域名: {ctx.domain}")
    if ctx.branch:
        context_lines.append(f"- 代码分支: {ctx.branch}")
    if ctx.image_version:
        context_lines.append(f"- 镜像版本: {ctx.image_version}")
    if ctx.host_name:
        context_lines.append(f"- 主机名: {ctx.host_name}")

    context_str = "\n".join(context_lines)
    return f"""## 分析上下文
{context_str}

## 日志内容
```
{ctx.processed_logs}
```

请全面分析以上日志中的所有错误模式和异常趋势，输出 JSON 数组格式的分析结果。"""
