"""
Global Error Signal Registry — Content-Aware Error Detection

Provides a curated set of high-confidence failure signal phrases that
indicate real errors regardless of the declared log level (gy.filetype,
log.level, etc.).

These signals are used in two places:
  1. ES queries (Channel B):  match_phrase clauses appended to the
     severity filter's bool.should list, so logs containing these
     phrases are fetched even from debug.log / info.log files.
  2. Quality Filter rescue:  _has_real_error_indicator() uses these
     signals to prevent filtering out DEBUG/INFO logs that contain
     genuine fault information.

Self-Learning Loop:
  After each AI analysis, the AI identifies key error signal phrases
  from the logs. These "learned signals" are stored in ES index
  `logmind-learned-signals` and loaded at query time (with in-memory
  caching). This creates a feedback loop:

    AI analysis → extract signals → store in ES → next query uses them

Design principles:
  - Phrases are optimised for ES match_phrase (exact substring match).
  - Only high-confidence signals — avoids false-positives from normal
    business JSON like {"error": ""} or {"errorMessage": "成功"}.
  - Language-agnostic: covers both English infra errors and Chinese
    business failure patterns.
  - Zero per-business-line configuration required.
  - Learned signals require confidence >= 0.7 to be loaded.
"""

import hashlib
import time

from logmind.core.logging import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#  Layer 1: Static Signals (hand-curated, always available)
# ══════════════════════════════════════════════════════════

# ── Infrastructure fault signals ─────────────────────────
# Network, I/O, timeout, resource exhaustion — language-agnostic.
INFRA_SIGNALS: list[str] = [
    # Timeout variants
    "connect timed out",
    "connection timed out",
    "read timed out",
    "socket timeout",
    "SocketTimeoutException",
    "ConnectTimeoutException",
    "TimeoutException",
    # Connection failures
    "connection refused",
    "Connection refused",
    "connection reset",
    "Connection reset",
    "No route to host",
    "broken pipe",
    "Broken pipe",
    # Resource exhaustion
    "OutOfMemoryError",
    "out of memory",
    "Cannot allocate memory",
    "Too many open files",
    "pool exhausted",
    "thread pool rejected",
    # DNS / network
    "UnknownHostException",
    "Name or service not known",
    "Temporary failure in name resolution",
]

# ── Business failure signals (Chinese) ───────────────────
# Common Chinese error phrases used in GYYX Java/C# services.
# These indicate real business-level failures regardless of log level.
BUSINESS_FAILURE_SIGNALS: list[str] = [
    "请求失败",
    "操作失败",
    "调用失败",
    "处理失败",
    "通知失败",
    "发送失败",
    "同步失败",
    "执行失败",
    "连接超时",
    "响应超时",
    "服务不可用",
    "服务异常",
    "系统异常",
    "产生异常",
]

# ── Error code signals ───────────────────────────────────
# Negative error codes and common failure indicators in structured logs.
# ES match_phrase on "errorCode=-" will match errorCode=-1000, -999, etc.
ERROR_CODE_SIGNALS: list[str] = [
    "errorCode=-",
    "error_code=-",
    "resultCode=-",
    "errCode=-",
]

# ── Exception class signals ──────────────────────────────
# High-confidence exception markers that transcend log level.
EXCEPTION_SIGNALS: list[str] = [
    "Caused by:",
    "Traceback (most recent",
    "NullPointerException",
    "NullReferenceException",
    "StackOverflowError",
    "ClassNotFoundException",
    "NoSuchMethodError",
    "IllegalStateException",
    "IllegalArgumentException",
    "ConcurrentModificationException",
    "DataIntegrityViolationException",
    "DeadlockLoserDataAccessException",
    "SQLServerException",
    "SQLException",
    "连接被拒",
]

# ── Aggregate: all static signals ────────────────────────
ALL_STATIC_SIGNALS: list[str] = (
    INFRA_SIGNALS
    + BUSINESS_FAILURE_SIGNALS
    + ERROR_CODE_SIGNALS
    + EXCEPTION_SIGNALS
)

# Backward compatibility alias
ALL_ERROR_SIGNALS = ALL_STATIC_SIGNALS


# ══════════════════════════════════════════════════════════
#  Layer 2: Learned Signals (AI-discovered, stored in ES)
# ══════════════════════════════════════════════════════════

# ES index for storing learned error signal phrases
_LEARNED_SIGNALS_INDEX = "logmind-learned-signals"

# In-memory cache — avoids hitting ES on every search_logs call.
# Celery workers and FastAPI processes both persist module-level state.
_learned_cache: list[str] = []
_cache_ts: float = 0.0
_CACHE_TTL = 300  # 5 minutes


async def _ensure_learned_index():
    """Create the learned-signals ES index if it doesn't exist."""
    from logmind.domain.log.service import log_service

    es = log_service.es
    exists = await es.indices.exists(index=_LEARNED_SIGNALS_INDEX)
    if not exists:
        mapping = {
            "properties": {
                "signal": {"type": "keyword"},
                "source_task_id": {"type": "keyword"},
                "business_line_id": {"type": "keyword"},
                "confidence": {"type": "float"},
                "hit_count": {"type": "integer"},
                "first_seen": {"type": "date"},
                "last_seen": {"type": "date"},
                "created_at": {"type": "date"},
            }
        }
        await es.indices.create(index=_LEARNED_SIGNALS_INDEX, mappings=mapping)
        logger.info("learned_signals_index_created")


async def store_learned_signal(
    signal: str,
    source_task_id: str,
    business_line_id: str,
    confidence: float = 0.8,
):
    """
    Upsert a learned error signal into ES.

    - First occurrence: creates a new document (hit_count=1).
    - Subsequent: increments hit_count and updates last_seen.
    - Uses MD5(signal) as doc ID for idempotent upsert.

    Signals are only loaded into ES queries when they have sufficient
    confidence (>= 0.7), providing a natural quality gate.
    """
    from datetime import datetime, timezone
    from logmind.domain.log.service import log_service

    # Skip signals that are too short or already in static list
    if not signal or len(signal) < 3:
        return
    if signal in _static_set:
        return

    try:
        await _ensure_learned_index()
        es = log_service.es

        now_iso = datetime.now(timezone.utc).isoformat()
        doc_id = hashlib.md5(signal.encode("utf-8")).hexdigest()

        await es.update(
            index=_LEARNED_SIGNALS_INDEX,
            id=doc_id,
            body={
                "script": {
                    "source": """
                        ctx._source.hit_count += 1;
                        ctx._source.last_seen = params.now;
                        if (ctx._source.confidence < params.confidence) {
                            ctx._source.confidence = params.confidence;
                        }
                    """,
                    "params": {
                        "now": now_iso,
                        "confidence": confidence,
                    },
                },
                "upsert": {
                    "signal": signal,
                    "source_task_id": source_task_id,
                    "business_line_id": business_line_id,
                    "confidence": confidence,
                    "hit_count": 1,
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                    "created_at": now_iso,
                },
            },
        )
        logger.info("learned_signal_stored", signal=signal[:50], doc_id=doc_id[:8])

    except Exception as e:
        logger.warning("learned_signal_store_failed", signal=signal[:50], error=str(e))


async def load_learned_signals() -> list[str]:
    """
    Load learned signals from ES with in-memory cache (5-min TTL).

    Quality gate: only signals with confidence >= 0.7 are loaded.
    This prevents one-off false positives from polluting the registry.
    """
    global _learned_cache, _cache_ts

    now = time.monotonic()
    if _learned_cache and (now - _cache_ts) < _CACHE_TTL:
        return _learned_cache

    try:
        from logmind.domain.log.service import log_service

        es = log_service.es
        exists = await es.indices.exists(index=_LEARNED_SIGNALS_INDEX)
        if not exists:
            _learned_cache = []
            _cache_ts = now
            return []

        result = await es.search(
            index=_LEARNED_SIGNALS_INDEX,
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"range": {"confidence": {"gte": 0.7}}},
                        ]
                    }
                },
                "size": 200,
                "_source": ["signal"],
                "sort": [{"hit_count": {"order": "desc"}}],
            },
        )

        signals = [
            hit["_source"]["signal"]
            for hit in result["hits"]["hits"]
            if hit["_source"].get("signal")
        ]

        _learned_cache = signals
        _cache_ts = now

        if signals:
            logger.info("learned_signals_loaded", count=len(signals))

        return signals

    except Exception as e:
        logger.warning("learned_signals_load_failed", error=str(e))
        return _learned_cache  # Return stale cache on error


# ══════════════════════════════════════════════════════════
#  Layer 3: Combined — Static + Learned
# ══════════════════════════════════════════════════════════

# Pre-compute static set for fast dedup lookups
_static_set: set[str] = set(ALL_STATIC_SIGNALS)


async def get_all_error_signals() -> list[str]:
    """
    Get combined static + learned error signals.

    Returns the full list of signals for ES query Channel B.
    Learned signals that duplicate static ones are excluded.
    """
    learned = await load_learned_signals()

    if not learned:
        return ALL_STATIC_SIGNALS

    # Deduplicate: only add learned signals not already in static list
    new_signals = [s for s in learned if s not in _static_set]
    return ALL_STATIC_SIGNALS + new_signals


# ══════════════════════════════════════════════════════════
#  Negative Learning — Feedback-Driven Signal Downgrade
# ══════════════════════════════════════════════════════════

async def downgrade_learned_signals(source_task_id: str):
    """
    Downgrade confidence of all learned signals from a specific analysis task.

    Called when a user gives negative feedback (score=-1) on an analysis result.
    This prevents bad AI judgments from continuing to influence future queries.

    Strategy:
      - Halve the confidence of matching signals
      - If confidence drops below 0.3, delete the signal entirely
      - Invalidate the in-memory cache so changes take effect immediately
    """
    try:
        from logmind.domain.log.service import log_service

        es = log_service.es
        exists = await es.indices.exists(index=_LEARNED_SIGNALS_INDEX)
        if not exists:
            return

        # Find all signals that were created by this task
        result = await es.search(
            index=_LEARNED_SIGNALS_INDEX,
            body={
                "query": {"term": {"source_task_id": source_task_id}},
                "size": 50,
                "_source": ["signal", "confidence"],
            },
        )

        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            logger.info("no_signals_to_downgrade", task_id=source_task_id)
            return

        downgraded = 0
        deleted = 0

        for hit in hits:
            doc_id = hit["_id"]
            current_confidence = hit["_source"].get("confidence", 0.8)
            new_confidence = current_confidence * 0.5  # Halve confidence

            if new_confidence < 0.3:
                # Too low — delete entirely
                await es.delete(index=_LEARNED_SIGNALS_INDEX, id=doc_id, ignore=[404])
                deleted += 1
            else:
                # Downgrade confidence
                await es.update(
                    index=_LEARNED_SIGNALS_INDEX,
                    id=doc_id,
                    body={"doc": {"confidence": new_confidence}},
                )
                downgraded += 1

        # Force cache refresh
        invalidate_signal_cache()

        logger.info(
            "learned_signals_downgraded",
            task_id=source_task_id,
            downgraded=downgraded,
            deleted=deleted,
        )

    except Exception as e:
        logger.warning(
            "learned_signals_downgrade_failed",
            task_id=source_task_id,
            error=str(e),
        )


def invalidate_signal_cache():
    """Force refresh of the learned signals cache on next query."""
    global _cache_ts
    _cache_ts = 0.0

