"""Result Parse Stage — Parse AI output to structured results."""

import json
import re
from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)

_NEGATIVE_CLAUSE_RE = re.compile(
    r"(?:^|(?<=[。！？!?；;，,]))\s*"
    r"[^。！？!?；;，,]*?"
    r"(?:未发现|未检测到|没有发现|未出现|未包含|未见|"
    r"没有明确(?:的)?|无明确(?:的)?|不存在明确(?:的)?)"
    r"[^。！？!?；;]*"
    r"(?:[；;，,]\s*(?:但|不过|然而|只是)|[。！？!?；;]|$)",
    re.IGNORECASE,
)

_ACTIONABLE_SIGNAL_RE = re.compile(
    r"(?:"
    r"\b(?:System|Microsoft)(?:\.[\w`]+)+(?:Exception|Error)\b"
    r"|[\w.]+(?:Exception|Error|Fault|Failure)\b"
    r"|\[(?:ERR|ERROR|FTL|FATAL|CRITICAL)\]"
    r"|^\s*(?:fail|crit):\s"
    r"|\bat\s+[\w.$+`<>]+\s*(?:\(.*?\))?\s+in\s+.*?\.cs:line\s+\d+"
    r"|\b(?:HTTP\s*)?5\d{2}\b"
    r"|\b(?:timeout|timed?\s*out|connection\s+(?:refused|reset)|deadlock|"
    r"out\s+of\s+memory|oom|panic|crash|fatal|failed|failure)\b"
    r"|(?:请求|调用|连接|执行|写入|读取|处理|发送|同步)(?:失败|超时)"
    r"|连接被拒|连接重置|内存溢出|死锁|服务不可用"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

_UNCERTAIN_CAUSE_RE = re.compile(
    r"^(?:待进一步确认|未知|无法确认|无法定位|暂无|不确定|无)$",
    re.IGNORECASE,
)


def _safe_confidence(value, default: float = 0.5) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default


def _sanitize_negative_boilerplate(text: str) -> str:
    """Remove negative exception enumerations while retaining actual positive findings."""
    if not text:
        return text
    cleaned = _NEGATIVE_CLAUSE_RE.sub("", text)
    cleaned = re.sub(r"([。！!？?])\s*([。！!？?])", r"\1", cleaned)
    cleaned = re.sub(r"[，,]\s*([。！!？?])", r"\1", cleaned)
    cleaned = re.sub(r"^\s*(?:但|不过|然而)[，,、\s]*", "", cleaned)
    return cleaned.strip()


def _is_actionable_finding(
    item: dict,
    content: str,
    log_refs: list[str],
) -> bool:
    """Require concrete positive fault evidence before a result may trigger an alert."""
    if not content:
        return False
    if item.get("noise_classification") == "business_noise":
        return False
    if item.get("is_regression"):
        return True
    if _ACTIONABLE_SIGNAL_RE.search(content):
        return True

    root_cause = str(
        item.get("root_cause")
        or item.get("probable_root_cause")
        or item.get("cause")
        or ""
    ).strip()
    if root_cause and not _UNCERTAIN_CAUSE_RE.match(root_cause):
        if _ACTIONABLE_SIGNAL_RE.search(root_cause) or log_refs:
            return True

    correlated = item.get("correlated_errors") or item.get("cross_service_errors")
    if isinstance(correlated, list):
        for entry in correlated:
            if isinstance(entry, dict) and entry.get("error_samples"):
                return True

    error_signals = item.get("error_signals")
    if log_refs and isinstance(error_signals, list) and any(error_signals):
        return True

    change_points = item.get("change_points") or item.get("change_point_evidence")
    return bool(log_refs and isinstance(change_points, list) and change_points)


def _non_actionable_summary(original_content: str) -> str:
    """Return a concise operator-facing result instead of empty or enumerated prose."""
    if not original_content.strip():
        return "AI 未返回有效分析内容，本次任务已记录且不触发告警。"
    if re.search(r"错误率|变点|突增|波动", original_content):
        return "检测到日志量短时波动，但缺少对应失败日志证据，本次仅记录且不触发告警。"
    return "当前时间范围内没有可核验的系统故障证据，本次仅记录且不触发告警。"


class ResultParseStage(PipelineStage):
    """Parse AI response into structured analysis results."""

    name = "result_parse"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.semantic_dedup_hit and ctx.analysis_results:
            ctx.log_metadata["actionable_findings"] = sum(
                1 for result in ctx.analysis_results
                if result.get("alertable", result.get("severity") in {
                    "critical", "error", "warning",
                })
            )
            logger.info(
                "result_parse_reused_semantic_result",
                result_count=len(ctx.analysis_results),
                task_id=ctx.task_id,
            )
            return ctx

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
            elif not isinstance(parsed, list):
                parsed = []

            ctx.analysis_results = []
            all_learned_signals = []
            all_learned_rules = []
            all_noise_classifications = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                # Extract source log references if the AI provided them
                raw_refs = item.get("source_log_refs", item.get("log_refs", []))
                if not isinstance(raw_refs, list):
                    raw_refs = []
                # Normalize: keep only strings, limit to 20
                log_refs = [str(r)[:200] for r in raw_refs if r][:20]

                raw_content = str(item.get("content") or "")
                sanitized_content = _sanitize_negative_boilerplate(raw_content)
                actionable = _is_actionable_finding(item, sanitized_content, log_refs)
                severity = str(item.get("severity", "info")).lower()
                if severity not in {"critical", "error", "warning", "info"}:
                    severity = "info"
                if severity in {"critical", "error", "warning"} and not actionable:
                    logger.info(
                        "result_severity_downgraded_no_evidence",
                        task_id=ctx.task_id,
                        original_severity=severity,
                        content_preview=sanitized_content[:160],
                    )
                    severity = "info"
                    sanitized_content = _non_actionable_summary(raw_content)
                elif not sanitized_content:
                    sanitized_content = _non_actionable_summary(raw_content)

                normalized_item = dict(item)
                normalized_item["content"] = sanitized_content
                normalized_item["severity"] = severity
                normalized_item["alertable"] = actionable

                ctx.analysis_results.append({
                    "result_type": item.get("result_type", "anomaly"),
                    "content": sanitized_content,
                    "severity": severity,
                    "confidence_score": _safe_confidence(
                        item.get("confidence_score", 0.5)
                    ),
                    "structured_data": json.dumps(normalized_item, ensure_ascii=False),
                    "source_log_refs": json.dumps(log_refs, ensure_ascii=False),
                    "alertable": actionable,
                })

                signals = item.get("error_signals", [])
                if actionable and isinstance(signals, list):
                    for sig in signals:
                        if isinstance(sig, str) and 3 <= len(sig) <= 60:
                            all_learned_signals.append(sig)

                rule = item.get("experience_rule", "")
                if actionable and isinstance(rule, str) and 10 <= len(rule) <= 200:
                    all_learned_rules.append(rule)

                # Extract AI noise classification
                noise_class = item.get("noise_classification", "")
                if noise_class == "business_noise":
                    noise_category = item.get("noise_category", "unknown")
                    noise_reason = item.get("noise_reason", "")
                    all_noise_classifications.append({
                        "category": noise_category,
                        "reason": noise_reason,
                    })

            ctx.learned_signals = list(dict.fromkeys(all_learned_signals))
            ctx.learned_rules = list(dict.fromkeys(all_learned_rules))
            ctx.log_metadata["actionable_findings"] = sum(
                1 for result in ctx.analysis_results if result.get("alertable")
            )

            # Propagate AI noise classification to metadata
            if all_noise_classifications:
                # If majority of results are classified as noise, mark the whole task
                noise_ratio = len(all_noise_classifications) / max(len(ctx.analysis_results), 1)
                if noise_ratio >= 0.5:
                    ctx.log_metadata["noise_classification"] = "business_noise"
                    ctx.log_metadata["noise_category"] = all_noise_classifications[0]["category"]
                    ctx.log_metadata["noise_reason"] = all_noise_classifications[0]["reason"]
                    logger.info(
                        "ai_noise_classification",
                        task_id=ctx.task_id,
                        noise_ratio=round(noise_ratio, 2),
                        categories=[n["category"] for n in all_noise_classifications],
                    )

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
                    "source_log_refs": "[]",
                    "alertable": False,
                }]
                ctx.log_metadata["actionable_findings"] = 0

        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as e:
            logger.warning("result_parse_fallback", error=str(e), task_id=ctx.task_id)
            fallback_content = _sanitize_negative_boilerplate(ctx.ai_response)
            fallback_item = {"result_type": "summary"}
            actionable = _is_actionable_finding(fallback_item, fallback_content, [])
            ctx.analysis_results = [{
                "result_type": "summary", "content": fallback_content,
                "severity": "warning" if actionable else "info",
                "confidence_score": 0.5,
                "structured_data": json.dumps(
                    {"alertable": actionable}, ensure_ascii=False
                ),
                "source_log_refs": "[]",
                "alertable": actionable,
            }]
            ctx.log_metadata["actionable_findings"] = int(actionable)

        logger.info("result_parse_completed", result_count=len(ctx.analysis_results),
                     task_id=ctx.task_id)
        return ctx
