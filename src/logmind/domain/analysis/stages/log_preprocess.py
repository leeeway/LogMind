"""
Log Preprocess Stage — Clean, deduplicate, merge stack traces

Stage 2 of the analysis pipeline.
Includes diversity-aware sampling and sensitive data masking.

Language-aware stack trace handling:
- Java: at com.example.Class(File.java:123), Caused by:, ... N more
- C#: at Namespace.Class.Method() in File.cs:line 96, --- End of inner exception ---
- Filebeat multiline: skip cross-document merge when log.flags contains "multiline"
"""

import re
from collections import defaultdict

from logmind.core.logging import get_logger
from logmind.domain.analysis.pipeline import PipelineContext, PipelineStage
from logmind.domain.log.service import _FILETYPE_LEVEL_MAP, _normalize_level

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

# ── Constants ────────────────────────────────────────────
MAX_SAMPLED_LOGS = 200
MAX_PROCESSED_CHARS = 32000


class LogPreprocessStage(PipelineStage):
    """
    Clean, deduplicate, merge stack traces, and format logs for AI consumption.
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
        if len(unique_logs) > MAX_SAMPLED_LOGS:
            sampled_logs = self._diversity_sample(unique_logs, MAX_SAMPLED_LOGS)
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
        if len(ctx.processed_logs) > MAX_PROCESSED_CHARS:
            ctx.processed_logs = ctx.processed_logs[:MAX_PROCESSED_CHARS] + "\n... (truncated)"

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
                    if current is not None:
                        merged.append(current)
                    current = dict(log)
                    continue

            is_stack_line = self._is_stack_trace_line(msg)

            if is_stack_line and current is not None:
                current_msg = self._extract_message(current)
                current["message"] = current_msg + "\n" + msg
                current["_stack_merged"] = True
            else:
                if current is not None:
                    merged.append(current)
                current = dict(log)

        if current is not None:
            merged.append(current)

        return merged

    @staticmethod
    def _is_stack_trace_line(msg: str) -> bool:
        """Detect if a message line is part of a stack trace."""
        if not msg:
            return False
        stripped = msg.strip()

        for prefix in _STACK_CONTINUATION_PREFIXES:
            if stripped.startswith(prefix):
                return True

        if re.match(r"^\.\.\.\s*\d+\s+more$", stripped):
            return True

        if _JAVA_STACK_RE.match(msg):
            return True

        if _CSHARP_STACK_RE.match(msg):
            return True

        return False

    @staticmethod
    def _message_has_stack(msg: str) -> bool:
        """Check if a message contains embedded stack trace content."""
        if not msg:
            return False
        if _EXCEPTION_CLASS_RE.search(msg):
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
        """Generate a deduplication key for a log message."""
        if not msg:
            return ""
        exc_match = _EXCEPTION_CLASS_RE.search(msg)
        if exc_match:
            first_line = msg.split("\n")[0][:200]
            return f"{exc_match.group(1)}:{first_line}"
        return msg[:200]

    def _diversity_sample(self, logs: list[dict], max_count: int) -> list[dict]:
        """
        Diversity-aware log sampling — ensures all error types are represented.

        Groups logs by their error pattern, then round-robin samples from each group.
        """
        groups: dict[str, list[dict]] = defaultdict(list)
        for log in logs:
            msg = self._extract_message(log)
            exc_match = _EXCEPTION_CLASS_RE.search(msg)
            if exc_match:
                group_key = exc_match.group(1)
            else:
                first_line = msg.split("\n")[0][:120]
                group_key = re.sub(r'\b[0-9a-f]{8,}[-0-9a-f]*\b', '<ID>', first_line)
                group_key = re.sub(r'\d+', '<N>', group_key)
                group_key = group_key[:80]

            groups[group_key].append(log)

        result = []
        group_list = list(groups.values())

        for group in group_list:
            if len(result) < max_count:
                result.append(group[0])

        idx = [1] * len(group_list)
        while len(result) < max_count:
            added = False
            for i, group in enumerate(group_list):
                if idx[i] < len(group) and len(result) < max_count:
                    result.append(group[idx[i]])
                    idx[i] += 1
                    added = True
            if not added:
                break

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
        """Extract log level — delegates to the canonical implementation in LogService."""
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
