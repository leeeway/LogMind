"""
AI Analysis Pipeline — 11-Stage Log Analysis

Stages:
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

Language-aware processing:
  - Java: gy.filetype-based level, Java stack traces (at ..., Caused by:)
  - C#: NLog message-based level, .NET stack traces (at ... in ...cs:line N)
"""

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from logmind.core.logging import get_logger
from logmind.domain.provider.base import ChatMessage, ChatRequest, TokenUsage

logger = get_logger(__name__)

# ── Stack Trace Detection Patterns ───────────────────────

# Java stack trace patterns
_JAVA_STACK_RE = re.compile(
    r"^\s+at\s+[\w.$]+\("            # at com.example.Class(File.java:123)
)

# C# .NET stack trace patterns
_CSHARP_STACK_RE = re.compile(
    r"^\s+at\s+[\w.]+\(.*\)"          # at Gyyx.Core.Class.Method(args)
    r"|^\s+at\s+[\w.]+.*\sin\s"       # at Namespace.Class.Method() in D:\path\File.cs:line 96
)

# Common stack trace continuation markers (both Java + C#)
_STACK_CONTINUATION_PREFIXES = (
    "at ",
    "Caused by:",
    "Suppressed:",
    "--- End of",           # C#: --- End of inner exception stack trace ---
    "--- End of stack",     # C#: --- End of stack trace from previous location ---
    "Exception rethrown",   # C# rethrow marker
)

# Pattern to extract exception class name from message
# Supports both Java (java.lang.NullPointerException) and
# C# (System.NullReferenceException, Gyyx.Core.SomeException)
_EXCEPTION_CLASS_RE = re.compile(
    r"([\w.]+(?:Exception|Error|Throwable|Fault))"
)

# C# NLog level regex for pipeline-internal level extraction
_NLOG_LEVEL_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*\s+"
    r"\[[\w\-]+\]\s+"
    r"(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\b",
    re.IGNORECASE,
)

# Bracket level regex
_BRACKET_LEVEL_RE = re.compile(
    r"\[(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\]",
    re.IGNORECASE,
)


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
    priority_decision: dict = field(default_factory=dict)  # PriorityDecision as dict

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
    # Each entry: {"stage": str, "duration_ms": int, "status": "ok"|"skipped"|"error", "error": str|None}
    stage_metrics: list[dict] = field(default_factory=list)
    # Agent tool call records (collected by AgentInferenceStage)
    tool_call_records: list[dict] = field(default_factory=list)

    # Signal self-learning: error signal phrases extracted by AI from analysis
    learned_signals: list[str] = field(default_factory=list)


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
            language=ctx.language,
            extra_filters=ctx.extra_filters,
            size=5000,  # Expand ES window so diversity sampler can see older rare errors
        )
        result = await self.log_service.search_logs(request)
        ctx.raw_logs = [log.raw for log in result.logs]
        ctx.log_count = len(ctx.raw_logs)

        # Extract GYYX business context from first log entry
        if ctx.raw_logs:
            first_log = ctx.raw_logs[0]
            gy = first_log.get("gy", {})
            if isinstance(gy, dict):
                ctx.domain = ctx.domain or gy.get("domain", "")
                ctx.branch = ctx.branch or gy.get("branch", "")
            image = first_log.get("image", {})
            if isinstance(image, dict):
                ctx.image_version = ctx.image_version or image.get("version", "")
            host = first_log.get("host", {})
            if isinstance(host, dict):
                ctx.host_name = ctx.host_name or host.get("name", "")

        logger.info("log_fetch_completed", count=ctx.log_count, task_id=ctx.task_id)
        return ctx


# ── Stage 2: Preprocess ─────────────────────────────────

class LogPreprocessStage(PipelineStage):
    """
    Clean, deduplicate, merge stack traces, and format logs for AI consumption.

    Language-aware stack trace handling:
    - Java: at com.example.Class(File.java:123), Caused by:, ... N more
    - C#: at Namespace.Class.Method() in File.cs:line 96, --- End of inner exception ---
    - Filebeat multiline: skip cross-document merge when log.flags contains "multiline"
    """

    name = "log_preprocess"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.raw_logs:
            ctx.processed_logs = "(No logs found matching the query)"
            return ctx

        # Phase 1: Merge stack traces (skip for Filebeat multiline-merged docs)
        merged_logs = self._merge_stack_traces(ctx.raw_logs)

        # Phase 2: Deduplicate
        seen = set()
        unique_logs = []
        for log in merged_logs:
            msg = self._extract_message(log)
            dedup_key = self._make_dedup_key(msg)
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique_logs.append(log)

        # Phase 3: Diversity-aware sampling
        # Group logs by error pattern to ensure ALL distinct error types
        # get represented in the sample, not just the most frequent ones.
        # This prevents high-frequency errors from drowning out critical
        # low-frequency exceptions (e.g. SQL truncation in warn.log).
        max_logs = 200
        if len(unique_logs) > max_logs:
            sampled_logs = self._diversity_sample(unique_logs, max_logs)
        else:
            sampled_logs = unique_logs

        # Phase 4: Format logs with business context
        # Apply sensitive data masking before sending to LLM
        from logmind.domain.analysis.sensitive_masker import mask_sensitive

        lines = []
        for log in sampled_logs:
            ts = log.get("@timestamp", "")
            level = self._extract_level(log)
            msg = mask_sensitive(self._extract_message(log))

            # GYYX gy.* context
            gy = log.get("gy", {}) if isinstance(log.get("gy"), dict) else {}
            domain = gy.get("domain", "")
            branch = gy.get("branch", "")

            # Host context (for C# VM-deployed services)
            host = log.get("host", {}) if isinstance(log.get("host"), dict) else {}
            host_name = host.get("name", "")

            # Kubernetes context (backward compatible)
            k8s = log.get("kubernetes", {})
            ns = k8s.get("namespace", "") if isinstance(k8s, dict) else ""
            pod = k8s.get("pod", {}).get("name", "") if isinstance(k8s, dict) else ""

            # Build formatted line with available context
            context_parts = []
            if domain:
                context_parts.append(f"domain:{domain}")
            if branch:
                context_parts.append(f"branch:{branch}")
            if ns and pod:
                context_parts.append(f"{ns}/{pod}")
            elif host_name:
                context_parts.append(f"host:{host_name}")

            context_str = f" [{', '.join(context_parts)}]" if context_parts else ""
            lines.append(f"[{ts}] [{level}]{context_str} {msg}")

        ctx.processed_logs = "\n".join(lines)

        # Truncate to ~8000 tokens (~32000 chars)
        if len(ctx.processed_logs) > 32000:
            ctx.processed_logs = ctx.processed_logs[:32000] + "\n... (truncated)"

        # Detect stack traces in processed output
        has_stacks = any(
            log.get("_stack_merged") or self._message_has_stack(self._extract_message(log))
            for log in merged_logs
        )
        ctx.has_stack_traces = has_stacks

        ctx.log_metadata = {
            "original_count": ctx.log_count,
            "merged_count": len(merged_logs),
            "deduped_count": len(unique_logs),
            "formatted_count": len(lines),
            "has_stack_traces": ctx.has_stack_traces,
            "language": ctx.language,
        }

        logger.info("log_preprocess_completed", **ctx.log_metadata, task_id=ctx.task_id)
        return ctx

    def _merge_stack_traces(self, logs: list[dict]) -> list[dict]:
        """
        Merge multi-line stack trace entries into their parent exception log.

        Skips cross-document merging for logs with log.flags: multiline
        (Filebeat has already merged them at the source).
        """
        if not logs:
            return logs

        merged = []
        current = None

        for log in logs:
            msg = self._extract_message(log)

            # Check if Filebeat already merged multiline for this doc
            log_meta = log.get("log", {})
            if isinstance(log_meta, dict):
                flags = log_meta.get("flags", "")
                if isinstance(flags, list):
                    flags = ",".join(flags)
                if "multiline" in str(flags):
                    # Already merged by Filebeat — treat as a single entry
                    if current is not None:
                        merged.append(current)
                    current = dict(log)
                    continue

            is_stack_line = self._is_stack_trace_line(msg)

            if is_stack_line and current is not None:
                # Continuation of a stack trace — append to current
                current_msg = self._extract_message(current)
                current["message"] = current_msg + "\n" + msg
                current["_stack_merged"] = True
            else:
                # New log entry
                if current is not None:
                    merged.append(current)
                current = dict(log)  # shallow copy

        # Don't forget the last entry
        if current is not None:
            merged.append(current)

        return merged

    @staticmethod
    def _is_stack_trace_line(msg: str) -> bool:
        """
        Detect if a message line is part of a stack trace.
        Supports both Java and C# .NET stack trace formats.
        """
        if not msg:
            return False
        stripped = msg.strip()

        # Check common prefixes (works for both Java and C#)
        for prefix in _STACK_CONTINUATION_PREFIXES:
            if stripped.startswith(prefix):
                return True

        # Java: "... 12 more"
        if re.match(r"^\.\.\.\s*\d+\s+more$", stripped):
            return True

        # Java stack frame
        if _JAVA_STACK_RE.match(msg):
            return True

        # C# stack frame
        if _CSHARP_STACK_RE.match(msg):
            return True

        return False

    @staticmethod
    def _message_has_stack(msg: str) -> bool:
        """Check if a message contains embedded stack trace content."""
        if not msg:
            return False
        # Check for exception class names
        if _EXCEPTION_CLASS_RE.search(msg):
            # Also verify there's a stack-trace-like pattern
            if "\n" in msg:
                for line in msg.split("\n")[1:]:
                    stripped = line.strip()
                    for prefix in _STACK_CONTINUATION_PREFIXES:
                        if stripped.startswith(prefix):
                            return True
        return False

    @staticmethod
    def _contains_exception(msg: str) -> bool:
        """Check if a message contains an exception class reference."""
        return bool(_EXCEPTION_CLASS_RE.search(msg))

    @staticmethod
    def _make_dedup_key(msg: str) -> str:
        """
        Generate a deduplication key for a log message.

        For stack traces: use exception class + first line
        For normal logs: use first 200 characters
        """
        if not msg:
            return ""

        # For stack traces, use exception class name as key
        exc_match = _EXCEPTION_CLASS_RE.search(msg)
        if exc_match:
            first_line = msg.split("\n")[0][:200]
            return f"{exc_match.group(1)}:{first_line}"

        # For normal messages, use first 200 chars
        return msg[:200]

    def _diversity_sample(self, logs: list[dict], max_count: int) -> list[dict]:
        """
        Diversity-aware log sampling — ensures all error types are represented.

        Groups logs by their error pattern (exception class name or error signature),
        then round-robin samples from each group. Low-frequency but critical errors
        (e.g. SQL truncation appearing once) will always appear alongside high-frequency
        errors (e.g. cookie failures appearing 100 times).

        Example: 300 logs with 295 "cookie failure" + 5 "SQL truncation"
        → Old: first 200 = all cookie failures (SQL truncation lost)
        → New: 195 cookie failures + 5 SQL truncation (all types represented)
        """
        from collections import defaultdict

        # Group by error pattern
        groups: dict[str, list[dict]] = defaultdict(list)
        for log in logs:
            msg = self._extract_message(log)
            # Use exception class as group key, fallback to normalized first line
            exc_match = _EXCEPTION_CLASS_RE.search(msg)
            if exc_match:
                group_key = exc_match.group(1)
            else:
                # Normalize: strip numbers, IPs, UUIDs, thread IDs for coarse grouping
                first_line = msg.split("\n")[0][:120]
                group_key = re.sub(r'\b[0-9a-f]{8,}[-0-9a-f]*\b', '<ID>', first_line)
                group_key = re.sub(r'\d+', '<N>', group_key)
                group_key = group_key[:80]

            groups[group_key].append(log)

        # Round-robin sample: ensure every group gets at least 1 representative
        result = []
        group_list = list(groups.values())

        # First pass: take 1 from each group (guarantee diversity)
        for group in group_list:
            if len(result) < max_count:
                result.append(group[0])

        # Second pass: fill remaining slots round-robin
        idx = [1] * len(group_list)  # Start from index 1 (already took 0)
        while len(result) < max_count:
            added = False
            for i, group in enumerate(group_list):
                if idx[i] < len(group) and len(result) < max_count:
                    result.append(group[idx[i]])
                    idx[i] += 1
                    added = True
            if not added:
                break  # All groups exhausted

        logger.info(
            "diversity_sample_applied",
            total_unique=len(logs),
            sampled=len(result),
            groups=len(group_list),
            group_sizes={k: len(v) for k, v in list(groups.items())[:10]},
        )

        return result

    @staticmethod
    def _extract_level(source: dict) -> str:
        """
        Extract log level from varied field names.
        Supports: level, severity, loglevel, log.level, gy.filetype, message content.
        """
        from logmind.domain.log.service import _FILETYPE_LEVEL_MAP, _normalize_level

        # 1. Dedicated level fields
        for field_name in ["level", "severity", "loglevel"]:
            if field_name in source:
                return _normalize_level(str(source[field_name])).upper()
        if isinstance(source.get("log"), dict):
            val = source["log"].get("level", "")
            if val:
                return _normalize_level(str(val)).upper()

        # 2. Java gy.filetype mapping
        gy = source.get("gy", {})
        if isinstance(gy, dict):
            filetype = gy.get("filetype", "")
            if filetype in _FILETYPE_LEVEL_MAP:
                return _FILETYPE_LEVEL_MAP[filetype].upper()

        # 3. C# NLog/log4net message parsing
        message = source.get("message", "")
        if isinstance(message, str):
            match = _NLOG_LEVEL_RE.search(message)
            if match:
                return _normalize_level(match.group(1)).upper()
            match = _BRACKET_LEVEL_RE.search(message)
            if match:
                return _normalize_level(match.group(1)).upper()

        return "UNKNOWN"

    @staticmethod
    def _extract_message(source: dict) -> str:
        for field_name in ["message", "msg", "log", "content"]:
            val = source.get(field_name)
            if isinstance(val, str):
                return val
        return str(source)[:500]


# ── Stage 2.5: Log Quality Filter ────────────────────────

class LogQualityFilterStage(PipelineStage):
    """
    Smart log quality filter — second layer of severity validation.

    Catches false-positive logs that passed ES query but are actually INFO/DEBUG:
    - Validates message-level severity against file-level severity
    - Detects business JSON response noise
    - Detects "shallow errors": log.error() used for non-error content
    - Updates processed_logs and log_count after filtering

    Non-critical: if filtering fails, the original logs pass through unchanged.
    """

    name = "log_quality_filter"
    is_critical = False

    # Regex to extract actual log level from message content
    _MSG_LEVEL_PATTERNS = [
        # [ERROR], [INFO], [WARN], etc.
        re.compile(r"\[(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\]", re.IGNORECASE),
        # NLog/log4net: timestamp [thread] LEVEL class
        re.compile(
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*\s+"
            r"\[[\w\-]+\]\s+"
            r"(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\b",
            re.IGNORECASE,
        ),
        # Java Logback: [timestamp] [thread] LEVEL class
        re.compile(
            r"\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*\]\s+"
            r"\[[\w\-]+\]\s+"
            r"(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL|TRACE)\b",
            re.IGNORECASE,
        ),
    ]

    # Business noise patterns — logs that are clearly routine operations
    _NOISE_INDICATORS = [
        re.compile(r'"status"\s*:\s*true', re.IGNORECASE),
        re.compile(r'"success"\s*:\s*true', re.IGNORECASE),
        re.compile(r'"errorMessage"\s*:\s*"[^"]*成功', re.IGNORECASE),
        re.compile(r'"message"\s*:\s*"[^"]*成功', re.IGNORECASE),
        re.compile(r'获取成功', re.IGNORECASE),
    ]

    # Severity weight for comparison  
    _SEVERITY_RANK = {
        "TRACE": 0, "DEBUG": 1, "INFO": 2,
        "WARN": 3, "WARNING": 3,
        "ERROR": 4, "FATAL": 5, "CRITICAL": 5,
    }

    # ── Real error indicators ────────────────────────────────
    # If an ERROR-level log contains NONE of these, it's likely a misused
    # log.error() call (e.g. "限制缓存 key:xxx,获取结果:xxx") and should
    # be de-prioritized.
    _REAL_ERROR_INDICATORS = [
        # Exception class names (Java/C#)
        re.compile(r'[A-Z]\w*(?:Exception|Error|Fault|Failure)\b'),
        # Stack trace markers
        re.compile(r'\bat\s+[\w.$]+\([\w.]+:\d+\)'),       # Java: at com.xxx.Class(File.java:123)
        re.compile(r'\bat\s+[\w.]+\s+in\s+\S+:line\s+\d+'),  # C#: at Xxx in File.cs:line 96
        # HTTP error status codes
        re.compile(r'\b[45]\d{2}\b'),                        # 400, 404, 500, 503, etc.
        # Failure/crash keywords
        re.compile(
            r'(?i)\b(?:fail(?:ed|ure)?|crash|panic|abort|killed|refused|rejected'
            r'|timeout|timed?\s*out|unreachable|connection\s+reset|broken\s+pipe'
            r'|denied|forbidden|unauthorized|overflow|deadlock|OOM|OutOfMemory'
            r'|fatal|null\s*pointer|segfault|core\s+dump)\b'
        ),
        # C# specific
        re.compile(r'--- End of (?:inner )?exception'),
        re.compile(r'System\.\w+Exception'),
    ]

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.processed_logs or not ctx.raw_logs:
            return ctx

        threshold = (ctx.severity_threshold or "error").upper()
        threshold_rank = self._SEVERITY_RANK.get(threshold, 4)

        filtered_lines = []
        total_original = 0
        filtered_out = 0
        shallow_error_count = 0

        for line in ctx.processed_logs.split("\n"):
            total_original += 1

            # 1. Extract the actual severity from the message content
            actual_level = self._extract_message_level(line)

            if actual_level:
                actual_rank = self._SEVERITY_RANK.get(actual_level.upper(), -1)
                if actual_rank >= 0 and actual_rank < threshold_rank:
                    # Message level is below threshold (e.g. INFO when looking for ERROR)
                    # BUT: allow through if the message contains real fault signals.
                    # This rescues logs where devs logged genuine failures at the
                    # wrong level (e.g. "请求失败：connect timed out" in debug.log).
                    if self._has_real_error_indicator(line):
                        pass  # Contains fault signal → keep regardless of declared level
                    else:
                        filtered_out += 1
                        continue

            # 2. Check for business noise (JSON success responses)
            if self._is_business_noise(line):
                filtered_out += 1
                continue

            # 3. Check for "shallow error" — log.error() with no real error content
            #    e.g. log.error("限制缓存 key:{},获取结果:{}", key, limit)
            if threshold_rank >= 4 and self._is_shallow_error(line):
                filtered_out += 1
                shallow_error_count += 1
                continue

            filtered_lines.append(line)

        if filtered_out > 0:
            logger.info(
                "log_quality_filter_applied",
                task_id=ctx.task_id,
                original_lines=total_original,
                filtered_out=filtered_out,
                shallow_errors=shallow_error_count,
                remaining=len(filtered_lines),
            )

            ctx.processed_logs = "\n".join(filtered_lines)
            ctx.log_metadata["quality_filtered"] = filtered_out
            ctx.log_metadata["quality_remaining"] = len(filtered_lines)
            ctx.log_metadata["quality_shallow_errors"] = shallow_error_count

            # If ALL logs were filtered out, no real errors remain
            if not filtered_lines or all(l.strip() == "" for l in filtered_lines):
                ctx.processed_logs = ""
                ctx.log_count = 0
                logger.info(
                    "log_quality_filter_all_removed",
                    task_id=ctx.task_id,
                    reason="All logs were INFO/DEBUG, business noise, or shallow errors",
                )

        return ctx

    def _extract_message_level(self, line: str) -> str | None:
        """Extract log level from a formatted log line's message content."""
        for pattern in self._MSG_LEVEL_PATTERNS:
            match = pattern.search(line)
            if match:
                return match.group(1).upper()
        return None

    def _is_business_noise(self, line: str) -> bool:
        """Detect routine business operation logs (not actual errors)."""
        # Must match multiple noise indicators to be confident
        noise_score = 0
        for pattern in self._NOISE_INDICATORS:
            if pattern.search(line):
                noise_score += 1

        # Also check: if the line is clearly [INFO] level content
        if noise_score >= 2:
            return True

        # Single noise indicator + no error indicators = likely noise
        if noise_score >= 1:
            has_error_indicator = any(kw in line for kw in [
                "Exception", "Error:", "FATAL", "CRITICAL",
                "Caused by:", "Traceback", "panic:",
            ])
            if not has_error_indicator:
                return True

        return False

    def _is_shallow_error(self, line: str) -> bool:
        """
        Detect "shallow errors" — log.error() calls that log routine content.

        Strategy: if the line is at ERROR level but contains ZERO real error
        indicators (no exceptions, no stack traces, no failure keywords, no
        HTTP error codes), it's likely a misused log.error().

        Examples that should be filtered:
          - log.error("限制缓存 key:{},获取结果:{}", key, limit)
          - log.error("查询结果,账号：{},结果：{}", account, result)

        Examples that should NOT be filtered:
          - log.error("请求超时", e)  → contains "timeout" keyword
          - log.error("NullPointerException: null")  → contains Exception class
        """
        # Only apply to lines that appear to be ERROR-level
        level = self._extract_message_level(line)
        if not level or level != "ERROR":
            return False

        # Check if ANY real error indicator is present
        for pattern in self._REAL_ERROR_INDICATORS:
            if pattern.search(line):
                return False  # Has real error content → keep it

        # ERROR level + no error indicators = shallow error → filter out
        return True

    def _has_real_error_indicator(self, line: str) -> bool:
        """
        Check if a log line contains real error/exception indicators.

        Used to rescue WARN-level logs that contain genuine exceptions
        (e.g. Spring DataIntegrityViolationException, SQLServerException)
        from being filtered by the level check.
        """
        # Reuse the compiled patterns from _REAL_ERROR_INDICATORS
        for pattern in self._REAL_ERROR_INDICATORS:
            if pattern.search(line):
                return True

        # Additional Chinese fault keywords common in Java/C# apps
        chinese_indicators = [
            "异常", "超时", "连接失败", "连接被拒", "截断",
            "请求失败", "操作失败", "调用失败", "处理失败",
            "通知失败", "发送失败", "同步失败", "执行失败",
            "服务不可用", "服务异常", "系统异常",
        ]
        for kw in chinese_indicators:
            if kw in line:
                return True

        return False


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
            # For error logs with stack traces, prefer stack_trace_analysis template
            target_category = "log_analysis"
            if ctx.has_stack_traces:
                target_category = "stack_trace_analysis"

            if ctx.prompt_template_id:
                template = await self.prompt_repo.get_by_id(
                    session, ctx.prompt_template_id, tenant_id=ctx.tenant_id
                )
            else:
                # Try to find category-specific template first
                stmt = select(PromptTemplate).where(
                    PromptTemplate.tenant_id == ctx.tenant_id,
                    PromptTemplate.category == target_category,
                    PromptTemplate.is_default == True,
                    PromptTemplate.is_active == True,
                ).limit(1)
                result = await session.execute(stmt)
                template = result.scalar_one_or_none()

                # Fallback to log_analysis if no stack_trace template
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
                # Use built-in fallback prompt
                ctx.system_prompt = self._fallback_system_prompt(ctx)
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
                # GYYX context
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

        # ── Inject business line intelligence profile ────────
        # Appends accumulated analysis experience for this service
        # so the AI "remembers" past incidents and root causes.
        try:
            from logmind.domain.analysis.business_profile import build_profile_context
            profile = await build_profile_context(ctx.business_line_id)
            if profile:
                ctx.system_prompt = ctx.system_prompt + "\n\n" + profile
                logger.info(
                    "business_profile_injected",
                    business_line_id=ctx.business_line_id,
                    profile_length=len(profile),
                    task_id=ctx.task_id,
                )
        except Exception as e:
            logger.warning("business_profile_inject_failed", error=str(e))

        logger.info("prompt_built", template_id=ctx.prompt_template_id, task_id=ctx.task_id)
        return ctx

    @staticmethod
    def _fallback_system_prompt(ctx: PipelineContext) -> str:
        lang_desc = {
            "java": "Java/Spring Boot",
            "csharp": "C#/.NET",
            "python": "Python",
            "go": "Go",
            "other": "",
        }
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
  这些短语应能在未来的日志中匹配同类故障，让系统自动学习新的错误模式。
  示例: ["connect timed out", "请求失败", "队列满"]

## 重要规则
1. 只输出 JSON 数组，不要输出其他内容（不要包裹在 markdown 代码块中）。
2. 数组中必须至少包含一个元素。分析所有错误模式，包括高频重复错误、异常堆栈、连接超时等。
3. 即使日志中没有严重问题，也请输出至少一条 info 级别的总结说明当前系统健康状况。
4. 对相同类型的错误请合并分析，说明出现频率和影响范围。
5. 对于 severity 为 critical 或 warning 的结果，务必提供 error_signals 字段，
   提取日志中可复用的故障信号短语（3-30字符，能精确匹配同类故障即可）。"""

        if ctx.has_stack_traces:
            if ctx.language == "csharp":
                base += """

## .NET 堆栈异常分析指引
- 追踪 InnerException 链，找到最内层根因异常
- 重点关注 Gyyx.* 命名空间下的业务代码异常
- 区分业务代码异常 vs 框架异常（System.*, Microsoft.*)
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

    @staticmethod
    def _fallback_user_prompt(ctx: PipelineContext) -> str:
        lang_names = {
            "java": "Java",
            "csharp": "C#/.NET",
            "python": "Python",
            "go": "Go",
        }
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

请全面分析以上日志中的所有错误模式和异常趋势，输出 JSON 数组格式的分析结果。数组中至少包含一个元素。"""


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
        logger.info(
            "result_parse_input",
            ai_response_length=len(ctx.ai_response),
            ai_response_preview=ctx.ai_response[:500],
            task_id=ctx.task_id,
        )

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
                # Support {"results": [...]} wrapper format
                if "results" in parsed and isinstance(parsed["results"], list):
                    parsed = parsed["results"]
                else:
                    parsed = [parsed]

            ctx.analysis_results = []
            all_learned_signals = []
            for item in parsed:
                ctx.analysis_results.append({
                    "result_type": item.get("result_type", "anomaly"),
                    "content": item.get("content", ""),
                    "severity": item.get("severity", "info"),
                    "confidence_score": float(item.get("confidence_score", 0.5)),
                    "structured_data": json.dumps(item, ensure_ascii=False),
                })

                # Extract AI-discovered error signals for self-learning
                signals = item.get("error_signals", [])
                if isinstance(signals, list):
                    for sig in signals:
                        if isinstance(sig, str) and 3 <= len(sig) <= 60:
                            all_learned_signals.append(sig)

            # Deduplicate and store in context for post-analysis persistence
            ctx.learned_signals = list(dict.fromkeys(all_learned_signals))

            # If AI returned content but parsed to zero results, fallback to summary
            if not ctx.analysis_results:
                logger.warning("result_parse_empty_fallback", task_id=ctx.task_id)
                # Generate meaningful summary instead of forwarding raw AI response
                summary_text = (
                    f"AI 分析了 {ctx.log_count} 条日志（业务线: {ctx.business_line_name}），"
                    f"未发现需要立即处理的严重问题。\n\n"
                    f"日志来源: {ctx.domain or ctx.host_name or '未知'}\n"
                    f"时间范围: {ctx.time_from} ~ {ctx.time_to}\n"
                    f"建议持续关注日志趋势，如有异常请手动复查。"
                )
                ctx.analysis_results = [{
                    "result_type": "summary",
                    "content": summary_text,
                    "severity": "info",
                    "confidence_score": 0.8,
                    "structured_data": "{}",
                }]

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            # Fallback: treat entire response as a single result
            logger.warning("result_parse_fallback", error=str(e), task_id=ctx.task_id)
            ctx.analysis_results = [{
                "result_type": "summary",
                "content": ctx.ai_response,
                "severity": "warning",
                "confidence_score": 0.8,
                "structured_data": "{}",
            }]

        logger.info(
            "result_parse_completed",
            result_count=len(ctx.analysis_results),
            task_id=ctx.task_id,
        )
        return ctx


# ── Stage 7: Priority Decision ───────────────────────────

class PriorityDecisionStage(PipelineStage):
    """
    AI-driven alert priority decision engine.

    Replaces the simple AlertEvalStage with multi-dimensional scoring:
      - AI severity (30%)
      - Error frequency anomaly (25%)
      - Business weight (25%)
      - Core path bonus (10%)
      - AI confidence (10%)

    Outputs: P0/P1/P2 priority + notification action decisions.
    Non-critical: fallback to "P1, always notify" if decision fails.
    """

    name = "priority_decision"
    is_critical = False

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        from logmind.domain.analysis.priority_engine import (
            PriorityDecisionEngine,
            PriorityFactors,
        )

        engine = PriorityDecisionEngine()

        # Determine top severity and confidence from analysis results
        top_severity = "info"
        top_confidence = 0.5
        unique_errors = set()

        for r in ctx.analysis_results:
            sev = r.get("severity", "info")
            if self._severity_rank(sev) > self._severity_rank(top_severity):
                top_severity = sev
            conf = r.get("confidence_score", 0.5)
            if conf > top_confidence:
                top_confidence = conf
            # Count unique error types from result_type
            if r.get("result_type") in ("anomaly", "root_cause"):
                unique_errors.add(r.get("content", "")[:80])

        # Get error frequency from log metadata (if available)
        current_errors = ctx.log_count
        baseline_errors = ctx.log_metadata.get("baseline_error_count", 0)
        # If no baseline, assume current is 1x (neutral)
        if baseline_errors == 0:
            baseline_errors = max(current_errors, 1)

        factors = PriorityFactors(
            ai_severity=top_severity,
            confidence=top_confidence,
            current_error_count=current_errors,
            baseline_error_count=baseline_errors,
            business_weight=ctx.business_weight,
            is_core_path=ctx.is_core_path,
            estimated_dau=ctx.estimated_dau,
            log_count=ctx.log_count,
            has_stack_traces=ctx.has_stack_traces,
            unique_error_types=max(len(unique_errors), 1),
        )

        decision = engine.decide(
            factors=factors,
            night_policy=ctx.night_policy,
            night_hours=ctx.night_hours,
        )

        # Store decision in context
        ctx.priority_decision = {
            "priority": decision.priority,
            "score": decision.score,
            "should_notify": decision.actions.should_notify,
            "should_wake": decision.actions.should_wake,
            "delay_until_morning": decision.actions.delay_until_morning,
            "include_in_digest": decision.actions.include_in_digest,
            "reason": decision.actions.reason,
            "factors": decision.factors_summary,
        }

        # ── Regression Override ─────────────────────────
        # If SemanticDedupStage detected a regression (resolved issue reappeared),
        # force-upgrade to P0 regardless of scoring result.
        if ctx.log_metadata.get("is_regression"):
            ctx.priority_decision["priority"] = "P0"
            ctx.priority_decision["should_notify"] = True
            ctx.priority_decision["should_wake"] = True
            ctx.priority_decision["reason"] = (
                f"🔄 [回归] 已修复的问题再次出现 — 自动升级为 P0 "
                f"(原始评分: {decision.score})"
            )
            logger.warning(
                "regression_priority_upgrade",
                original_priority=decision.priority,
                task_id=ctx.task_id,
            )

        # Populate alerts_fired for backward compatibility
        # Only fire alerts if the decision says we should notify
        if decision.actions.should_notify:
            alertable_results = [
                r for r in ctx.analysis_results
                if r.get("severity") in ("critical", "warning", "error")
                and r.get("confidence_score", 0) >= 0.4
            ]
            if not alertable_results:
                alertable_results = ctx.analysis_results[:1]  # At least send summary

            ctx.alerts_fired = alertable_results

        logger.info(
            "priority_decision_result",
            priority=decision.priority,
            score=decision.score,
            should_notify=decision.actions.should_notify,
            reason=decision.actions.reason,
            task_id=ctx.task_id,
        )

        return ctx

    @staticmethod
    def _severity_rank(severity: str) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}.get(severity, 0)


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
    Orchestrates the 11-stage log analysis pipeline.

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

                if stage.is_critical:
                    from logmind.core.exceptions import PipelineError
                    raise PipelineError(stage.name, e)
                # Non-critical → continue

        return ctx
