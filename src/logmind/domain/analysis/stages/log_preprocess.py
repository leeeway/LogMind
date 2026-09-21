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
from logmind.domain.log.service import (
    _DOTNET_CONSOLE_LEVEL_RE,
    _SERILOG_LEVEL_RE,
)

logger = get_logger(__name__)

# ── Stack Trace Detection Patterns ───────────────────────

# Java stack trace patterns
_JAVA_STACK_RE = re.compile(
    r"^\s+at\s+[\w.$]+\("  # at com.example.Class(File.java:123)
)

# C# .NET stack trace patterns
_CSHARP_STACK_RE = re.compile(
    r"^\s+at\s+[\w.$+`<>\[\],]+"  # namespaces, nested/generic/compiler types
    r"(?:\(.*?\))?"  # optional argument list
    r"(?:\s+in\s+.*?:line\s+\d+)?"  # optional Windows/Linux source location
)

# Common stack trace continuation markers (Java, C#, Go, Python)
_STACK_CONTINUATION_PREFIXES = (
    "at ",
    "Caused by:",
    "Suppressed:",
    "--- End of",  # C#: --- End of inner exception stack trace ---
    "--- End of stack",  # C#: --- End of stack trace from previous location ---
    "Exception rethrown",  # C# rethrow marker
    'File "',  # Python: File "app.py", line 12, in ...
    "goroutine ",  # Go: goroutine 1 [running]:
)

# Pattern to extract exception class name from message
_EXCEPTION_CLASS_RE = re.compile(r"([\w.]+(?:Exception|Error|Throwable|Fault))")

# ── Deduplication Key Normalization Patterns ─────────────
_DEDUP_TIMESTAMP_PREFIX_RE = re.compile(
    r"^\[?\d{4}[-/]\d{2}[-/]\d{2}[T\s]\d{2}:\d{2}:\d{2}[,.\d]*\]?\s*"
)
_DEDUP_THREAD_GOROUTINE_PREFIX_RE = re.compile(r"^(?:\[[0-9a-zA-Z_\-.:]+\]\s*)+")
_DEDUP_VOLATILE_CONTEXT_RE = re.compile(
    r"\b(?:request_?id|trace_?id|span_?id|correlation_?id|merchant_?id|user_?id|guild|order_?id|task_?id)\s*[:=]\s*[A-Za-z0-9._:\-]+",
    re.IGNORECASE,
)
_DEDUP_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_DEDUP_HEX_ID_RE = re.compile(
    r"\b[0-9a-f]{16,32}\b",
    re.IGNORECASE,
)
_DEDUP_DYNAMIC_NUM_RE = re.compile(r"\b\d+\b")

# ── Constants ────────────────────────────────────────────
MAX_SAMPLED_LOGS = 200
MAX_PROCESSED_CHARS = 32000


def _fit_complete_events(events: list[str], max_chars: int) -> str:
    """Fit complete log events while retaining both early and recent evidence."""
    rendered = "\n".join(events)
    if len(rendered) <= max_chars:
        return rendered

    marker = "\n... (middle events omitted) ...\n"
    available = max(max_chars - len(marker), 1)

    newest = events[-1]
    if len(newest) > available:
        side_budget = max(available // 2, 1)
        return newest[:side_budget] + marker + newest[-side_budget:]

    # Always retain the latest complete event; root exceptions and failure
    # responses commonly appear at the end of the sampled timeline.
    tail = [newest]
    remaining = available - len(newest)
    head: list[str] = []
    head_size = 0
    for event in events[:-1]:
        cost = len(event) + (1 if head else 0)
        if head_size + cost > remaining:
            break
        head.append(event)
        head_size += cost

    return "\n".join(head) + marker + "\n".join(tail)


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
        configured_language = ctx.language
        detected_language = self._detect_language(merged_logs)
        if detected_language and ctx.language in {"auto", "other", ""}:
            ctx.language = detected_language
            logger.info(
                "language_auto_detected",
                task_id=ctx.task_id,
                configured_language=configured_language,
                detected_language=detected_language,
            )
        from logmind.domain.log.events import NORMALIZATION_VERSION, evidence, metadata

        ctx.log_metadata["normalization_version"] = NORMALIZATION_VERSION
        ctx.log_metadata["language_conflict"] = bool(
            detected_language
            and configured_language not in {"auto", "other", "", detected_language}
        )
        ctx.log_metadata["detected_language"] = detected_language

        # Phase 2: Deduplicate
        seen = set()
        unique_logs = []
        occurrence_counts: dict[str, int] = defaultdict(int)
        groups: dict[str, dict] = {}
        for log in merged_logs:
            msg = self._extract_message(log)
            dedup_key = self._make_dedup_key(msg)
            occurrence_counts[dedup_key] += 1
            meta = metadata(log)
            group = groups.setdefault(
                dedup_key,
                {
                    "key": dedup_key,
                    "count": 0,
                    "first_seen": "",
                    "last_seen": "",
                    "instances": [],
                    "versions": [],
                },
            )
            group["count"] += 1
            ts = log.get("@timestamp", "")
            if ts:
                group["first_seen"] = min(group["first_seen"] or ts, ts)
                group["last_seen"] = max(group["last_seen"], ts)
            for field, value in (
                ("instances", meta["pod_name"] or meta["host_name"]),
                ("versions", meta["image_version"]),
            ):
                if value and value not in group[field] and len(group[field]) < 20:
                    group[field].append(value)
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique_logs.append(log)

        # Phase 3: Adaptive intelligent sampling
        from logmind.domain.analysis.adaptive_sampler import (
            adaptive_sample,
            compute_adaptive_budget,
        )

        # Compute per-service adaptive budget
        budget = compute_adaptive_budget(
            business_line_id=ctx.business_line_id,
            input_count=len(unique_logs),
            default_budget=MAX_SAMPLED_LOGS,
        )

        sampled_logs, sampling_metrics = adaptive_sample(
            unique_logs,
            max_budget=budget,
            business_line_id=ctx.business_line_id,
            level_extractor=self._extract_level,
            message_extractor=self._extract_message,
        )
        # A rare trigger must survive ordinary sampling; append to preserve the
        # latest-evidence side of the character budget as well.
        pinned = {
            self._make_dedup_key(self._extract_message(log)): log
            for log in merged_logs
            if log.get("_trigger_evidence")
        }
        sampled_logs = [
            log
            for log in sampled_logs
            if self._make_dedup_key(self._extract_message(log)) not in pinned
        ] + list(pinned.values())
        actionable_level_count = sum(
            1 for log in ctx.raw_logs if self._extract_level(log) in {"ERROR", "FATAL", "CRITICAL"}
        )

        # Phase 4: Format logs with business context
        # Apply sensitive data masking before sending to LLM
        from logmind.domain.analysis.sensitive_masker import mask_sensitive

        lines = []
        selected_evidence = []
        for log in sampled_logs:
            ts = log.get("@timestamp", "")
            level = self._extract_level(log)
            msg = mask_sensitive(self._extract_message(log))
            item = evidence(log, self._detect_language([log]))
            item.update(groups[self._make_dedup_key(self._extract_message(log))])
            selected_evidence.append(item)

            # GYYX gy.* context
            gy = log.get("gy", {}) if isinstance(log.get("gy"), dict) else {}
            domain = gy.get("domain", "")
            branch = gy.get("branch", "")

            # Host context (for C# VM / container deployed services)
            host = log.get("host", {}) if isinstance(log.get("host"), dict) else {}
            agent = log.get("agent", {}) if isinstance(log.get("agent"), dict) else {}
            host_name = host.get("name", "") or agent.get("name", "")

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
            occurrences = occurrence_counts[self._make_dedup_key(self._extract_message(log))]
            frequency_str = f" [occurrences:{occurrences}]" if occurrences > 1 else ""
            lines.append(f"[{ts}] [{level}]{context_str}{frequency_str} {msg}")

        # Fit complete events to the model budget. Keep both ends of the sampled
        # timeline so a recent trigger or a trailing C# InnerException is not lost.
        ctx.processed_logs = _fit_complete_events(lines, MAX_PROCESSED_CHARS)
        # Metadata carries bounded, sanitized structure rather than raw logs.
        ctx.log_metadata["event_evidence"] = selected_evidence

        # Detect stack traces in processed output
        has_stacks = any(
            log.get("_stack_merged") or self._message_has_stack(self._extract_message(log))
            for log in merged_logs
        )
        ctx.has_stack_traces = has_stacks

        fetch_metadata = dict(ctx.log_metadata)
        ctx.log_metadata = {
            **fetch_metadata,
            "original_count": ctx.log_count,
            "merged_count": len(merged_logs),
            "deduped_count": len(unique_logs),
            "duplicate_occurrences": sum(
                count - 1 for count in occurrence_counts.values() if count > 1
            ),
            "formatted_count": len(lines),
            "has_stack_traces": ctx.has_stack_traces,
            "language": ctx.language,
            "configured_language": configured_language,
            "actionable_level_count": actionable_level_count,
            "sampling": sampling_metrics.to_dict(),
        }

        logger.info("log_preprocess_completed", **ctx.log_metadata, task_id=ctx.task_id)
        return ctx

    def _merge_stack_traces(self, logs: list[dict]) -> list[dict]:
        """Assemble only same-stream, non-conflicting events in capture order."""
        from datetime import datetime

        from logmind.domain.log.events import metadata
        from logmind.domain.log.service import LogService

        streams = defaultdict(list)
        for i, log in enumerate(logs):
            meta = metadata(log)
            file = (
                log.get("log", {}).get("file", {}).get("path", "")
                if isinstance(log.get("log"), dict)
                else ""
            )
            instance = meta["pod_name"] or meta["host_name"]
            origin = log.get("_es_index") or meta["domain"]
            # Missing source identity is not permission to attach a foreign stack.
            key = (origin, instance, file) if origin and instance and file else ("isolated", i)
            streams[key].append(log)

        def order(log):
            try:
                stamp = datetime.fromisoformat(
                    log.get("@timestamp", "").replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                stamp = 0
            info = log.get("log", {})
            offset = info.get("offset", 0) if isinstance(info, dict) else 0
            return (stamp, offset if isinstance(offset, (int, float)) else 0)

        def request_id(log):
            trace = log.get("trace", {})
            value = log.get("request_id") or (trace.get("id") if isinstance(trace, dict) else "")
            if value:
                return str(value)
            match = re.search(
                r"(?:request_?id|trace_?id)\s*[:=]\s*([\w-]+)",
                LogService._extract_message(log),
                re.I,
            )
            return match.group(1) if match else ""

        merged = []
        for stream in streams.values():
            current = None
            sealed = False
            owner = ""
            for log in sorted(stream, key=order):
                msg = self._extract_message(log)
                info = log.get("log", {})
                flags = info.get("flags", "") if isinstance(info, dict) else ""
                multiline = "multiline" in str(flags)
                req = request_id(log)
                prior = self._extract_message(current) if current else ""
                active = bool(
                    re.search(
                        r"Exception|Error|Traceback|panic:|goroutine|\bERROR\b|\bFATAL\b", prior
                    )
                )
                python_tail = "Traceback (" in prior and bool(
                    re.match(r"^\s+\S", msg) or re.match(r"^[\w.]+(?:Error|Exception):", msg)
                )
                gap = order(log)[0] - order(current)[0] if current else 0
                can_merge = (
                    current is not None
                    and not sealed
                    and not multiline
                    and active
                    and not (owner and req and owner != req)
                    and 0 <= gap <= 2
                    and not _DEDUP_TIMESTAMP_PREFIX_RE.match(msg)
                    and (self._is_stack_trace_line(msg) or python_tail)
                )
                if can_merge:
                    current["message"] = prior + "\n" + msg
                    current["_stack_merged"] = True
                    current["_trigger_evidence"] = bool(
                        current.get("_trigger_evidence") or log.get("_trigger_evidence")
                    )
                    owner = owner or req
                else:
                    if current is not None:
                        merged.append(current)
                    current = dict(log)
                    sealed, owner = multiline, req
            if current is not None:
                merged.append(current)
        return sorted(merged, key=order)

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

        # Go source line continuation (tab-indented path:line, e.g. \t/app/main.go:42)
        if re.match(r"^\t+/.+?\.go:\d+", msg):
            return True
        if re.match(r"^(?:(?:[\w.-]+/)+[\w./*()~-]+|[\w]+\.[\w]+)\([^\n]*\)$", stripped):
            return True

        # Python traceback line
        return bool(re.match(r'^\s*File\s+".+?\.py",\s+line\s+\d+', msg))

    @staticmethod
    def _message_has_stack(msg: str) -> bool:
        """Check if a message contains embedded stack trace content."""
        if not msg:
            return False
        if re.search(r"\bat\s+[\w.$+`<>]+\([^\n]*?\)", msg):
            return True
        if "Traceback (most recent call last):" in msg or re.search(r"\.go:\d+", msg):
            return True
        if _EXCEPTION_CLASS_RE.search(msg) and "\n" in msg:
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
    def _detect_language(logs: list[dict]) -> str | None:
        """Detect language evidence so default-configured services get language-aware analysis."""
        from logmind.domain.log.service import LogService

        csharp_score = 0
        java_score = 0
        go_score = 0
        python_score = 0
        for log in logs[:200]:
            # Language signatures frequently live in the logging prefix
            # (Go package/receiver, Java logger). The C# renderer intentionally
            # strips that prefix for prompts, so detection must inspect raw text.
            msg = LogService._extract_message(log)
            gy = log.get("gy", {}) if isinstance(log.get("gy"), dict) else {}
            filetype = str(gy.get("filetype", "")).lower()

            if filetype in {"sys.log.txt", "sys.log", "app.log.txt"}:
                csharp_score += 2
            if re.search(r"\bSystem(?:\.[\w`]+)+(?:Exception|Error)\b", msg):
                csharp_score += 4
            if re.search(r"\bat\s+[\w.$+`<>]+\s*(?:\(.*?\))?\s+in\s+.*?\.cs:line\s+\d+", msg):
                csharp_score += 3
            if "--- End of inner exception" in msg or "InnerException" in msg:
                csharp_score += 3
            if _SERILOG_LEVEL_RE.search(msg) or _DOTNET_CONSOLE_LEVEL_RE.search(msg):
                csharp_score += 1

            if re.search(r"\bjava\.[\w.]+(?:Exception|Error)\b", msg):
                java_score += 2
            if re.search(r"\bat\s+[\w.$]+\(.*?\.java:\d+\)", msg):
                java_score += 2
            if "Caused by:" in msg:
                java_score += 1

            # Go detection
            if re.search(r"(?:^|\s)(?:[\w./-]+\.)\(\*?[A-Za-z_][A-Za-z0-9_]*\)\s*\[", msg):
                go_score += 3
            if re.search(r"\bgoroutine\s+\d+\s+\[", msg):
                go_score += 3
            if re.search(r"\b(?:github\.com|golang\.org|google\.golang\.org)/", msg):
                go_score += 2
            if re.search(r"\.go:\d+\b", msg):
                go_score += 2

            # Python detection
            if "Traceback (most recent call last):" in msg:
                python_score += 3
            if re.search(r'File\s+".*?\.py",\s+line\s+\d+', msg):
                python_score += 3
            if re.search(
                r"\b(?:TypeError|ValueError|KeyError|AttributeError|ImportError|RuntimeError|ModuleNotFoundError):",
                msg,
            ):
                python_score += 2

        if csharp_score >= 4 and java_score == 0:
            return "csharp"
        if go_score >= 3 and java_score == 0 and csharp_score == 0:
            return "go"
        if python_score >= 4 and java_score == 0 and csharp_score == 0:
            return "python"
        if java_score >= 2 and csharp_score == 0 and go_score == 0 and python_score == 0:
            return "java"

        return None

    @staticmethod
    def _make_dedup_key(msg: str) -> str:
        from logmind.domain.log.events import event_key

        return event_key(msg)

    @staticmethod
    def _legacy_dedup_key(msg: str) -> str:
        """Read-only comparator for rollout metrics; never drops evidence."""
        if not msg:
            return ""
        first_line = msg.split("\n")[0].strip()
        cleaned = _DEDUP_TIMESTAMP_PREFIX_RE.sub("", first_line)
        cleaned = _DEDUP_THREAD_GOROUTINE_PREFIX_RE.sub("", cleaned)
        cleaned = _DEDUP_VOLATILE_CONTEXT_RE.sub("", cleaned)
        cleaned = _DEDUP_UUID_RE.sub("<UUID>", cleaned)
        cleaned = _DEDUP_HEX_ID_RE.sub("<HEX>", cleaned)
        cleaned = _DEDUP_DYNAMIC_NUM_RE.sub("<N>", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,-;:[]")

        exc_match = _EXCEPTION_CLASS_RE.search(msg)
        if exc_match:
            return f"{exc_match.group(1)}:{cleaned[:160]}"
        return cleaned[:200] or msg[:200]

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
                group_key = re.sub(r"\b[0-9a-f]{8,}[-0-9a-f]*\b", "<ID>", first_line)
                group_key = re.sub(r"\d+", "<N>", group_key)
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
        """Single source of truth shared with queries and analysis."""
        from logmind.domain.log.service import LogService

        return LogService._extract_level(source).upper()

    @staticmethod
    def _extract_message(source: dict) -> str:
        from logmind.domain.log.csharp import parse_dotnet

        for field_name in ["message", "msg", "log", "content"]:
            val = source.get(field_name)
            if isinstance(val, str):
                return parse_dotnet(val).message
        return str(source)[:500]
