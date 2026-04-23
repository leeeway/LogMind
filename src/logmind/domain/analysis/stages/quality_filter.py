"""
Log Quality Filter Stage — False-Positive Elimination

Non-critical: if filtering fails, the original logs pass through unchanged.
"""

import re
from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage

logger = get_logger(__name__)

# Severity weight for comparison
_SEVERITY_RANK = {
    "TRACE": 0, "DEBUG": 1, "INFO": 2,
    "WARN": 3, "WARNING": 3,
    "ERROR": 4, "FATAL": 5, "CRITICAL": 5,
}

_MSG_LEVEL_PATTERNS = [
    re.compile(r"\[(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\]", re.IGNORECASE),
    re.compile(
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*\s+\[[\w\-]+\]\s+"
        r"(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\b", re.IGNORECASE,
    ),
    re.compile(
        r"\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*\]\s+\[[\w\-]+\]\s+"
        r"(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\b", re.IGNORECASE,
    ),
]

_NOISE_INDICATORS = [
    re.compile(r'"status"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"success"\s*:\s*true', re.IGNORECASE),
    re.compile(r'"errorMessage"\s*:\s*"[^"]*成功', re.IGNORECASE),
    re.compile(r'"message"\s*:\s*"[^"]*成功', re.IGNORECASE),
    re.compile(r'获取成功', re.IGNORECASE),
]

_REAL_ERROR_INDICATORS = [
    re.compile(r'[A-Z]\w*(?:Exception|Error|Fault|Failure)\b'),
    re.compile(r'\bat\s+[\w.$]+\([\w.]+:\d+\)'),
    re.compile(r'\bat\s+[\w.]+\s+in\s+\S+:line\s+\d+'),
    re.compile(r'\b[45]\d{2}\b'),
    re.compile(r'(?i)\b(?:fail(?:ed|ure)?|crash|panic|abort|killed|refused|rejected'
               r'|timeout|timed?\s*out|unreachable|connection\s+reset|broken\s+pipe'
               r'|denied|forbidden|unauthorized|overflow|deadlock|OOM|OutOfMemory'
               r'|fatal|null\s*pointer|segfault|core\s+dump)\b'),
    re.compile(r'--- End of (?:inner )?exception'),
    re.compile(r'System\.\w+Exception'),
]

_CHINESE_FAULT_KEYWORDS = [
    "异常", "超时", "连接失败", "连接被拒", "截断",
    "请求失败", "操作失败", "调用失败", "处理失败",
    "通知失败", "发送失败", "同步失败", "执行失败",
    "服务不可用", "服务异常", "系统异常",
]


def _extract_message_level(line: str) -> str | None:
    for pattern in _MSG_LEVEL_PATTERNS:
        match = pattern.search(line)
        if match:
            return match.group(1).upper()
    return None


def _has_real_error_indicator(line: str) -> bool:
    for pattern in _REAL_ERROR_INDICATORS:
        if pattern.search(line):
            return True
    for kw in _CHINESE_FAULT_KEYWORDS:
        if kw in line:
            return True
    return False


class LogQualityFilterStage(PipelineStage):
    """Smart log quality filter — validates message-level severity."""

    name = "log_quality_filter"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.processed_logs or not ctx.raw_logs:
            return ctx

        threshold = (ctx.severity_threshold or "error").upper()
        threshold_rank = _SEVERITY_RANK.get(threshold, 4)

        filtered_lines = []
        total_original = 0
        filtered_out = 0
        shallow_error_count = 0

        for line in ctx.processed_logs.split("\n"):
            total_original += 1

            actual_level = _extract_message_level(line)
            if actual_level:
                actual_rank = _SEVERITY_RANK.get(actual_level.upper(), -1)
                if actual_rank >= 0 and actual_rank < threshold_rank:
                    if not _has_real_error_indicator(line):
                        filtered_out += 1
                        continue

            if self._is_business_noise(line):
                filtered_out += 1
                continue

            if threshold_rank >= 4 and self._is_shallow_error(line):
                filtered_out += 1
                shallow_error_count += 1
                continue

            filtered_lines.append(line)

        if filtered_out > 0:
            logger.info("log_quality_filter_applied", task_id=ctx.task_id,
                        original_lines=total_original, filtered_out=filtered_out,
                        shallow_errors=shallow_error_count, remaining=len(filtered_lines))
            ctx.processed_logs = "\n".join(filtered_lines)
            ctx.log_metadata["quality_filtered"] = filtered_out
            ctx.log_metadata["quality_remaining"] = len(filtered_lines)
            ctx.log_metadata["quality_shallow_errors"] = shallow_error_count

            if not filtered_lines or all(l.strip() == "" for l in filtered_lines):
                ctx.processed_logs = ""
                ctx.log_count = 0
                logger.info("log_quality_filter_all_removed", task_id=ctx.task_id,
                            reason="All logs were INFO/DEBUG, business noise, or shallow errors")

        return ctx

    @staticmethod
    def _is_business_noise(line: str) -> bool:
        noise_score = sum(1 for p in _NOISE_INDICATORS if p.search(line))
        if noise_score >= 2:
            return True
        if noise_score >= 1:
            return not any(kw in line for kw in [
                "Exception", "Error:", "FATAL", "CRITICAL",
                "Caused by:", "Traceback", "panic:",
            ])
        return False

    @staticmethod
    def _is_shallow_error(line: str) -> bool:
        level = _extract_message_level(line)
        if not level or level != "ERROR":
            return False
        return not any(p.search(line) for p in _REAL_ERROR_INDICATORS)
