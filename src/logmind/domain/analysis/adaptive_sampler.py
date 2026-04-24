"""
Adaptive Intelligent Log Sampler

Replaces the fixed-quota diversity sampler with a multi-strategy engine that
adapts sample budgets per-service based on error diversity, severity distribution,
and historical analysis effectiveness.

Design Principles:
  1. Severity-weighted: critical/fatal get guaranteed slots, info gets remainder
  2. Temporal coverage: recent errors weighted higher, but older rare errors preserved
  3. Diversity-first: every unique error type gets at least 1 representative
  4. Adaptive budget: high-diversity services get larger sample; low-diversity get smaller
  5. Cross-task learning: Redis stores per-service error profiles for budget tuning

Thread Safety:
  All functions are stateless with respect to module globals. Redis operations
  are atomic get/set with TTL. Safe for concurrent Celery workers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────

# Absolute bounds — never exceed these regardless of adaptive logic
MIN_SAMPLE_SIZE = 20
MAX_SAMPLE_SIZE = 300
DEFAULT_SAMPLE_SIZE = 150

# Severity weights determine slot allocation ratio
# More weight → more samples of that severity are kept
SEVERITY_WEIGHTS: dict[str, float] = {
    "CRITICAL": 5.0,
    "FATAL": 5.0,
    "ERROR": 3.0,
    "WARN": 1.5,
    "WARNING": 1.5,
    "INFO": 0.5,
    "DEBUG": 0.2,
    "TRACE": 0.1,
    "UNKNOWN": 1.0,
}

# Minimum guaranteed samples per severity (if available)
SEVERITY_MIN_SLOTS: dict[str, int] = {
    "CRITICAL": 10,
    "FATAL": 10,
    "ERROR": 5,
    "WARN": 2,
    "WARNING": 2,
}

# Exception class name regex (reuse from preprocess)
_EXCEPTION_CLASS_RE = re.compile(
    r"([\w.]+(?:Exception|Error|Throwable|Fault))"
)

# Redis key prefix and TTL for sampling profiles
_REDIS_KEY_PREFIX = "logmind:sampling_profile:"
_REDIS_PROFILE_TTL = 86400  # 24 hours


# ── Data Types ───────────────────────────────────────────

@dataclass(frozen=True)
class SamplingMetrics:
    """Immutable record of a sampling operation's statistics."""

    input_count: int
    output_count: int
    budget: int
    severity_distribution: dict[str, int]  # severity → count in output
    group_count: int                       # number of unique error groups
    temporal_span_seconds: float           # time span covered by output
    strategy: str                          # "adaptive" | "passthrough"

    def to_dict(self) -> dict:
        return {
            "input_count": self.input_count,
            "output_count": self.output_count,
            "budget": self.budget,
            "severity_distribution": self.severity_distribution,
            "group_count": self.group_count,
            "temporal_span_seconds": round(self.temporal_span_seconds, 1),
            "strategy": self.strategy,
        }


@dataclass
class _LogEntry:
    """Internal wrapper around a raw log dict with precomputed fields."""

    raw: dict
    severity: str
    severity_weight: float
    group_key: str
    timestamp_epoch: float  # seconds since epoch, 0 if unparseable
    message_hash: str       # fast dedup hash


@dataclass
class _SeverityBucket:
    """Logs grouped by severity level."""

    severity: str
    weight: float
    entries: list[_LogEntry] = field(default_factory=list)
    allocated_slots: int = 0


# ── Public API ───────────────────────────────────────────

def adaptive_sample(
    logs: list[dict],
    *,
    max_budget: int = DEFAULT_SAMPLE_SIZE,
    business_line_id: str = "",
    level_extractor: callable = None,
    message_extractor: callable = None,
) -> tuple[list[dict], SamplingMetrics]:
    """
    Adaptively sample logs with severity-weighted, diversity-aware strategy.

    Args:
        logs: Raw log dicts from Elasticsearch.
        max_budget: Upper bound for output sample count.
        business_line_id: Used for Redis-based adaptive budget tuning.
        level_extractor: callable(dict) -> str, extracts severity from a log dict.
        message_extractor: callable(dict) -> str, extracts message from a log dict.

    Returns:
        (sampled_logs, metrics) — sampled list and operation statistics.

    Notes:
        - If len(logs) <= max_budget, all logs pass through (passthrough strategy).
        - Exceptions within sampling never propagate — falls back to head-truncation.
    """
    if not logs:
        return [], SamplingMetrics(
            input_count=0, output_count=0, budget=max_budget,
            severity_distribution={}, group_count=0,
            temporal_span_seconds=0.0, strategy="passthrough",
        )

    # Clamp budget
    budget = max(MIN_SAMPLE_SIZE, min(max_budget, MAX_SAMPLE_SIZE))

    # Passthrough if under budget
    if len(logs) <= budget:
        sev_dist = _count_severities(logs, level_extractor or _default_level)
        return logs, SamplingMetrics(
            input_count=len(logs), output_count=len(logs), budget=budget,
            severity_distribution=sev_dist, group_count=len(sev_dist),
            temporal_span_seconds=0.0, strategy="passthrough",
        )

    try:
        result, metrics = _run_adaptive_sampling(
            logs, budget, level_extractor, message_extractor,
        )
        # Store profile for future budget adaptation
        _store_sampling_profile(business_line_id, metrics)
        return result, metrics
    except Exception as e:
        logger.warning("adaptive_sampling_fallback", error=str(e))
        # Fallback: take head slice (never crash the pipeline)
        fallback = logs[:budget]
        return fallback, SamplingMetrics(
            input_count=len(logs), output_count=len(fallback), budget=budget,
            severity_distribution={}, group_count=0,
            temporal_span_seconds=0.0, strategy="fallback",
        )


def compute_adaptive_budget(
    business_line_id: str,
    input_count: int,
    default_budget: int = DEFAULT_SAMPLE_SIZE,
) -> int:
    """
    Compute an adaptive sample budget based on historical analysis profiles.

    Budget scaling rules:
      - High diversity (many unique groups) → increase budget (up to 1.5x)
      - Low diversity (few groups, repetitive) → decrease budget (down to 0.6x)
      - Recent negative feedback → increase budget (more context for AI)

    Returns:
        Clamped integer budget within [MIN_SAMPLE_SIZE, MAX_SAMPLE_SIZE].
    """
    if input_count <= default_budget:
        return input_count  # No need to sample

    profile = _load_sampling_profile(business_line_id)
    if not profile:
        return default_budget

    # Diversity factor: ratio of unique groups to sample size
    prev_groups = profile.get("group_count", 10)
    prev_output = profile.get("output_count", default_budget)

    if prev_output > 0:
        diversity_ratio = prev_groups / prev_output
    else:
        diversity_ratio = 0.5

    # Scale budget based on diversity
    # High diversity (>0.5 unique groups per sample) → increase budget
    # Low diversity (<0.1) → decrease budget
    if diversity_ratio > 0.5:
        scale = min(1.5, 1.0 + (diversity_ratio - 0.5))
    elif diversity_ratio < 0.1:
        scale = max(0.6, 0.6 + diversity_ratio * 4)
    else:
        scale = 1.0

    adjusted = int(default_budget * scale)
    return max(MIN_SAMPLE_SIZE, min(adjusted, MAX_SAMPLE_SIZE))


# ── Core Sampling Engine ─────────────────────────────────

def _run_adaptive_sampling(
    logs: list[dict],
    budget: int,
    level_extractor: callable | None,
    message_extractor: callable | None,
) -> tuple[list[dict], SamplingMetrics]:
    """
    Execute the multi-phase adaptive sampling algorithm.

    Phase 1: Parse and classify all logs
    Phase 2: Allocate budget across severity levels
    Phase 3: Within each severity bucket, diversity-sample with temporal spread
    Phase 4: Merge and sort by timestamp
    """
    extract_level = level_extractor or _default_level
    extract_msg = message_extractor or _default_message

    # ── Phase 1: Parse all logs into _LogEntry ───────────
    entries: list[_LogEntry] = []
    for log in logs:
        severity = extract_level(log).upper()
        msg = extract_msg(log)
        entries.append(_LogEntry(
            raw=log,
            severity=severity,
            severity_weight=SEVERITY_WEIGHTS.get(severity, 1.0),
            group_key=_compute_group_key(msg),
            timestamp_epoch=_parse_timestamp(log),
            message_hash=hashlib.md5(msg[:500].encode("utf-8", errors="replace")).hexdigest(),
        ))

    # ── Phase 2: Build severity buckets & allocate slots ─
    buckets: dict[str, _SeverityBucket] = {}
    for entry in entries:
        sev = entry.severity
        if sev not in buckets:
            buckets[sev] = _SeverityBucket(severity=sev, weight=entry.severity_weight)
        buckets[sev].entries.append(entry)

    _allocate_budget(buckets, budget)

    # ── Phase 3: Diversity-sample within each bucket ─────
    selected: list[_LogEntry] = []
    for bucket in buckets.values():
        if bucket.allocated_slots <= 0:
            continue
        sampled = _diversity_temporal_sample(bucket.entries, bucket.allocated_slots)
        selected.extend(sampled)

    # ── Phase 4: Sort by timestamp (preserve temporal order) ─
    selected.sort(key=lambda e: e.timestamp_epoch)

    # Compute metrics
    sev_dist: dict[str, int] = defaultdict(int)
    for e in selected:
        sev_dist[e.severity] += 1

    unique_groups = len({e.group_key for e in selected})

    timestamps = [e.timestamp_epoch for e in selected if e.timestamp_epoch > 0]
    temporal_span = (max(timestamps) - min(timestamps)) if len(timestamps) >= 2 else 0.0

    metrics = SamplingMetrics(
        input_count=len(logs),
        output_count=len(selected),
        budget=budget,
        severity_distribution=dict(sev_dist),
        group_count=unique_groups,
        temporal_span_seconds=temporal_span,
        strategy="adaptive",
    )

    logger.info(
        "adaptive_sampling_completed",
        input=metrics.input_count,
        output=metrics.output_count,
        groups=metrics.group_count,
        severity_dist=dict(sev_dist),
    )

    return [e.raw for e in selected], metrics


def _allocate_budget(buckets: dict[str, _SeverityBucket], total_budget: int) -> None:
    """
    Allocate sample budget across severity buckets using weighted proportional allocation
    with guaranteed minimums for critical severities.

    Algorithm:
      1. Guarantee minimum slots for critical/fatal/error (if logs exist)
      2. Distribute remaining budget proportionally by (weight × count)
      3. Clamp each bucket to its actual size (no overallocation)
      4. Redistribute unused slots to the largest buckets
    """
    if not buckets:
        return

    remaining = total_budget

    # Step 1: Guaranteed minimums
    for sev, min_slots in SEVERITY_MIN_SLOTS.items():
        if sev in buckets:
            bucket = buckets[sev]
            guaranteed = min(min_slots, len(bucket.entries))
            bucket.allocated_slots = guaranteed
            remaining -= guaranteed

    # Step 2: Proportional allocation of remainder
    if remaining > 0:
        # Compute weighted scores for buckets that still need more
        weighted_scores: dict[str, float] = {}
        for sev, bucket in buckets.items():
            unfilled = len(bucket.entries) - bucket.allocated_slots
            if unfilled > 0:
                weighted_scores[sev] = bucket.weight * math.log2(unfilled + 1)

        total_score = sum(weighted_scores.values())
        if total_score > 0:
            for sev, score in weighted_scores.items():
                bucket = buckets[sev]
                additional = int(remaining * (score / total_score))
                max_additional = len(bucket.entries) - bucket.allocated_slots
                bucket.allocated_slots += min(additional, max_additional)

    # Step 3: Redistribute any remaining (due to rounding or clamping)
    allocated_total = sum(b.allocated_slots for b in buckets.values())
    leftover = total_budget - allocated_total

    if leftover > 0:
        # Give leftovers to largest unfilled buckets
        unfilled = [
            (sev, b)
            for sev, b in buckets.items()
            if b.allocated_slots < len(b.entries)
        ]
        unfilled.sort(key=lambda x: x[1].weight * len(x[1].entries), reverse=True)

        for sev, bucket in unfilled:
            if leftover <= 0:
                break
            can_add = len(bucket.entries) - bucket.allocated_slots
            add = min(can_add, leftover)
            bucket.allocated_slots += add
            leftover -= add


def _diversity_temporal_sample(
    entries: list[_LogEntry],
    target_count: int,
) -> list[_LogEntry]:
    """
    Sample from a severity bucket with diversity AND temporal spread.

    Algorithm:
      1. Group by error pattern (group_key)
      2. From each group, always pick the MOST RECENT entry (highest diagnostic value)
      3. Round-robin through groups for remaining budget
      4. Within a group, prefer entries that spread the temporal range
    """
    if len(entries) <= target_count:
        return list(entries)

    # Group by error pattern
    groups: dict[str, list[_LogEntry]] = defaultdict(list)
    for entry in entries:
        groups[entry.group_key].append(entry)

    # Sort entries within each group by timestamp descending (newest first)
    for group_entries in groups.values():
        group_entries.sort(key=lambda e: e.timestamp_epoch, reverse=True)

    selected: list[_LogEntry] = []
    seen_hashes: set[str] = set()
    group_list = list(groups.values())

    # Phase A: Pick the most recent from each group (guaranteed diversity)
    for group_entries in group_list:
        if len(selected) >= target_count:
            break
        entry = group_entries[0]
        if entry.message_hash not in seen_hashes:
            selected.append(entry)
            seen_hashes.add(entry.message_hash)

    # Phase B: Round-robin for remaining budget (temporal spread)
    idx = [1] * len(group_list)  # start from index 1 (0 already picked)
    rounds = 0
    max_rounds = max(len(g) for g in group_list) if group_list else 0

    while len(selected) < target_count and rounds < max_rounds:
        added_this_round = False
        for i, group_entries in enumerate(group_list):
            if len(selected) >= target_count:
                break
            if idx[i] < len(group_entries):
                entry = group_entries[idx[i]]
                if entry.message_hash not in seen_hashes:
                    selected.append(entry)
                    seen_hashes.add(entry.message_hash)
                    added_this_round = True
                idx[i] += 1
        if not added_this_round:
            break
        rounds += 1

    return selected


# ── Helpers ──────────────────────────────────────────────

def _compute_group_key(msg: str) -> str:
    """
    Generate a stable grouping key for a log message.

    Groups logs by their error pattern (exception class or normalized first line),
    stripping variable parts like IDs, timestamps, and numbers.
    """
    if not msg:
        return "__empty__"

    # Priority 1: Exception class name
    exc_match = _EXCEPTION_CLASS_RE.search(msg)
    if exc_match:
        return exc_match.group(1)

    # Priority 2: Normalized first line
    first_line = msg.split("\n")[0][:120]
    # Strip UUIDs, hex strings, numbers
    normalized = re.sub(r"\b[0-9a-f]{8,}[-0-9a-f]*\b", "<ID>", first_line)
    normalized = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<IP>", normalized)
    normalized = re.sub(r"\d+", "<N>", normalized)
    return normalized[:80]


def _parse_timestamp(log: dict) -> float:
    """
    Extract timestamp as epoch seconds from a log dict.

    Handles common Elasticsearch timestamp formats.
    Returns 0.0 on failure (never raises).
    """
    ts = log.get("@timestamp", "")
    if not ts:
        return 0.0

    if isinstance(ts, (int, float)):
        return float(ts)

    if isinstance(ts, str):
        try:
            from datetime import datetime, timezone
            # ISO 8601 formats
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    dt = datetime.strptime(ts, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.timestamp()
                except ValueError:
                    continue
        except Exception:
            pass

    return 0.0


def _default_level(log: dict) -> str:
    """Default severity extractor for raw log dicts."""
    for field_name in ("level", "severity", "loglevel"):
        val = log.get(field_name)
        if val:
            return str(val).upper()
    log_meta = log.get("log")
    if isinstance(log_meta, dict):
        val = log_meta.get("level", "")
        if val:
            return str(val).upper()
    return "UNKNOWN"


def _default_message(log: dict) -> str:
    """Default message extractor for raw log dicts."""
    for field_name in ("message", "msg", "log", "content"):
        val = log.get(field_name)
        if isinstance(val, str):
            return val
    return str(log)[:500]


def _count_severities(
    logs: list[dict], level_extractor: callable
) -> dict[str, int]:
    """Count logs per severity level."""
    counts: dict[str, int] = defaultdict(int)
    for log in logs:
        counts[level_extractor(log).upper()] += 1
    return dict(counts)


# ── Redis Profile Storage (optional, graceful degradation) ─

def _store_sampling_profile(business_line_id: str, metrics: SamplingMetrics) -> None:
    """Store sampling metrics in Redis for adaptive budget computation."""
    if not business_line_id:
        return
    try:
        from logmind.core.redis import get_redis_sync
        r = get_redis_sync()
        if r is None:
            return
        key = f"{_REDIS_KEY_PREFIX}{business_line_id}"
        r.setex(key, _REDIS_PROFILE_TTL, json.dumps(metrics.to_dict()))
    except Exception:
        pass  # Non-critical — never crash for profile storage


def _load_sampling_profile(business_line_id: str) -> dict | None:
    """Load previous sampling profile from Redis."""
    if not business_line_id:
        return None
    try:
        from logmind.core.redis import get_redis_sync
        r = get_redis_sync()
        if r is None:
            return None
        key = f"{_REDIS_KEY_PREFIX}{business_line_id}"
        data = r.get(key)
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None
