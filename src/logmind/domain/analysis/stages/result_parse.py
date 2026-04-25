"""Result Parse Stage — Parse AI output to structured results."""

import json
from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)


class ResultParseStage(PipelineStage):
    """Parse AI response into structured analysis results."""

    name = "result_parse"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        logger.info("result_parse_input", ai_response_length=len(ctx.ai_response),
                     ai_response_preview=ctx.ai_response[:500], task_id=ctx.task_id)

        try:
            content = ctx.ai_response.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            parsed = json.loads(content)

            if isinstance(parsed, dict):
                if "results" in parsed and isinstance(parsed["results"], list):
                    parsed = parsed["results"]
                else:
                    parsed = [parsed]

            ctx.analysis_results = []
            all_learned_signals = []
            all_learned_rules = []
            for item in parsed:
                # Extract source log references if the AI provided them
                raw_refs = item.get("source_log_refs", item.get("log_refs", []))
                if not isinstance(raw_refs, list):
                    raw_refs = []
                # Normalize: keep only strings, limit to 20
                log_refs = [str(r)[:200] for r in raw_refs if r][:20]

                ctx.analysis_results.append({
                    "result_type": item.get("result_type", "anomaly"),
                    "content": item.get("content", ""),
                    "severity": item.get("severity", "info"),
                    "confidence_score": float(item.get("confidence_score", 0.5)),
                    "structured_data": json.dumps(item, ensure_ascii=False),
                    "source_log_refs": json.dumps(log_refs, ensure_ascii=False),
                })

                signals = item.get("error_signals", [])
                if isinstance(signals, list):
                    for sig in signals:
                        if isinstance(sig, str) and 3 <= len(sig) <= 60:
                            all_learned_signals.append(sig)

                rule = item.get("experience_rule", "")
                if isinstance(rule, str) and 10 <= len(rule) <= 200:
                    all_learned_rules.append(rule)

            ctx.learned_signals = list(dict.fromkeys(all_learned_signals))
            ctx.learned_rules = list(dict.fromkeys(all_learned_rules))

            if not ctx.analysis_results:
                logger.warning("result_parse_empty_fallback", task_id=ctx.task_id)
                summary_text = (
                    f"AI 分析了 {ctx.log_count} 条日志（业务线: {ctx.business_line_name}），"
                    f"未发现需要立即处理的严重问题。\n\n"
                    f"日志来源: {ctx.domain or ctx.host_name or '未知'}\n"
                    f"时间范围: {ctx.time_from} ~ {ctx.time_to}\n"
                    f"建议持续关注日志趋势，如有异常请手动复查。"
                )
                ctx.analysis_results = [{
                    "result_type": "summary", "content": summary_text,
                    "severity": "info", "confidence_score": 0.8,
                    "structured_data": "{}",
                }]

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("result_parse_fallback", error=str(e), task_id=ctx.task_id)
            ctx.analysis_results = [{
                "result_type": "summary", "content": ctx.ai_response,
                "severity": "warning", "confidence_score": 0.8,
                "structured_data": "{}",
            }]

        logger.info("result_parse_completed", result_count=len(ctx.analysis_results),
                     task_id=ctx.task_id)
        return ctx
