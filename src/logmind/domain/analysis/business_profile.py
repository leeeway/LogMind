"""
Business Line Intelligence Profile — Accumulated Analysis Experience

Builds a dynamic "knowledge profile" for each business line by querying
historical AI analysis results from ES. This profile is injected into
the system prompt so the AI analyst "remembers" what it learned about
each service from past investigations.

Two components:
  1. Historical Analysis Summary — from logmind-analysis-vectors
  2. Experience Rules — prescriptive "when X, do Y" rules from
     logmind-experience-rules, extracted by AI and refined through feedback

Caching:
  In-memory cache with 10-minute TTL per business line.
"""

import hashlib
import time

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# ── In-memory cache ──────────────────────────────────────
_profile_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 600  # 10 minutes

_VECTORS_INDEX = "logmind-analysis-vectors"
_RULES_INDEX = "logmind-experience-rules"


async def build_profile_context(business_line_id: str) -> str:
    """
    Build a text block summarizing this business line's analysis history
    and accumulated experience rules.

    Returns an empty string if no meaningful history exists.
    """
    global _profile_cache

    now = time.monotonic()
    cached = _profile_cache.get(business_line_id)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        history_section = await _build_history_section(business_line_id)
        rules_section = await _build_rules_section(business_line_id)

        parts = [p for p in [history_section, rules_section] if p]
        profile = "\n\n".join(parts)
    except Exception as e:
        logger.warning(
            "business_profile_build_failed",
            business_line_id=business_line_id,
            error=str(e),
        )
        if cached:
            return cached[0]
        return ""

    _profile_cache[business_line_id] = (profile, now)
    return profile


# ══════════════════════════════════════════════════════════
#  Component 1: Historical Analysis Summary
# ══════════════════════════════════════════════════════════

async def _build_history_section(business_line_id: str) -> str:
    """Build the historical analysis experience section."""
    from logmind.domain.log.service import log_service

    es = log_service.es
    exists = await es.indices.exists(index=_VECTORS_INDEX)
    if not exists:
        return ""

    result = await es.search(
        index=_VECTORS_INDEX,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"business_line_id": business_line_id}},
                    ],
                    "must_not": [
                        {"term": {"feedback_quality": "poor"}},
                        {"term": {"status": "ignored"}},
                    ],
                }
            },
            "sort": [
                {"feedback_quality": {"order": "desc", "missing": "_last"}},
                {"hit_count": {"order": "desc"}},
                {"last_seen": {"order": "desc"}},
            ],
            "size": 8,
            "_source": [
                "error_signature", "analysis_content", "severity",
                "hit_count", "status", "feedback_quality",
            ],
        },
    )

    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        return ""

    entries = []
    for i, hit in enumerate(hits):
        src = hit["_source"]
        severity = src.get("severity", "info").upper()
        hit_count = src.get("hit_count", 1)
        verified = src.get("feedback_quality") == "verified"
        status = src.get("status", "open")
        sig = (src.get("error_signature") or "")[:120]
        content = (src.get("analysis_content") or "")[:300]

        labels = [severity]
        if verified:
            labels.append("已验证")
        if status == "resolved":
            labels.append("已修复")

        entry = f"{i + 1}. [{', '.join(labels)}] {sig}"
        if hit_count > 1:
            entry += f" (累计 {hit_count} 次)"
        conclusion = content.split("\n")[0][:200] if content else ""
        if conclusion:
            entry += f"\n   结论: {conclusion}"
        entries.append(entry)

    if not entries:
        return ""

    logger.info("history_section_built", biz=business_line_id, count=len(entries))

    return (
        "## 该服务的历史分析记录\n"
        "以下是过去分析中的关键发现：\n\n"
        + "\n\n".join(entries)
        + "\n\n请参考但基于当前日志做独立判断。已修复问题再次出现属于回归。"
    )


# ══════════════════════════════════════════════════════════
#  Component 2: Experience Rules (Prompt Dynamic Evolution)
# ══════════════════════════════════════════════════════════

async def _ensure_rules_index():
    """Create the experience-rules ES index if it doesn't exist."""
    from logmind.domain.log.service import log_service

    es = log_service.es
    exists = await es.indices.exists(index=_RULES_INDEX)
    if not exists:
        await es.indices.create(
            index=_RULES_INDEX,
            mappings={
                "properties": {
                    "rule": {"type": "text"},
                    "business_line_id": {"type": "keyword"},
                    "source_task_id": {"type": "keyword"},
                    "confidence": {"type": "float"},
                    "hit_count": {"type": "integer"},
                    "verified": {"type": "boolean"},
                    "first_seen": {"type": "date"},
                    "last_seen": {"type": "date"},
                    "created_at": {"type": "date"},
                }
            },
        )
        logger.info("experience_rules_index_created")


async def store_experience_rule(
    rule: str,
    business_line_id: str,
    source_task_id: str,
    confidence: float = 0.8,
):
    """
    Upsert an experience rule into ES.

    Uses MD5(business_line_id + rule) as doc ID for idempotent upsert.
    Subsequent stores increment hit_count and update confidence upward.
    """
    from datetime import datetime, timezone
    from logmind.domain.log.service import log_service

    if not rule or len(rule) < 10:
        return

    try:
        await _ensure_rules_index()
        es = log_service.es

        now_iso = datetime.now(timezone.utc).isoformat()
        doc_id = hashlib.md5(
            f"{business_line_id}:{rule}".encode("utf-8")
        ).hexdigest()

        await es.update(
            index=_RULES_INDEX,
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
                    "params": {"now": now_iso, "confidence": confidence},
                },
                "upsert": {
                    "rule": rule,
                    "business_line_id": business_line_id,
                    "source_task_id": source_task_id,
                    "confidence": confidence,
                    "hit_count": 1,
                    "verified": False,
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                    "created_at": now_iso,
                },
            },
        )
        logger.info("experience_rule_stored", rule=rule[:60], doc_id=doc_id[:8])

    except Exception as e:
        logger.warning("experience_rule_store_failed", rule=rule[:60], error=str(e))


async def _build_rules_section(business_line_id: str) -> str:
    """Build the experience rules section for prompt injection."""
    from logmind.domain.log.service import log_service

    es = log_service.es
    exists = await es.indices.exists(index=_RULES_INDEX)
    if not exists:
        return ""

    result = await es.search(
        index=_RULES_INDEX,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"business_line_id": business_line_id}},
                        {"range": {"confidence": {"gte": 0.6}}},
                    ]
                }
            },
            "sort": [
                {"verified": {"order": "desc"}},
                {"hit_count": {"order": "desc"}},
                {"confidence": {"order": "desc"}},
            ],
            "size": 10,
            "_source": ["rule", "hit_count", "verified", "confidence"],
        },
    )

    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        return ""

    rules = []
    for i, hit in enumerate(hits):
        src = hit["_source"]
        rule_text = src.get("rule", "")
        verified = src.get("verified", False)
        hit_count = src.get("hit_count", 1)

        prefix = "✅" if verified else "💡"
        suffix = f" (验证{hit_count}次)" if hit_count > 1 else ""
        rules.append(f"{i + 1}. {prefix} {rule_text}{suffix}")

    if not rules:
        return ""

    logger.info("rules_section_built", biz=business_line_id, count=len(rules))

    return (
        "## 分析规则（从历史经验中提炼）\n"
        "以下规则由 AI 从过去的分析中提炼，请在相关场景中遵循：\n\n"
        + "\n".join(rules)
    )


async def downgrade_rules_for_task(source_task_id: str):
    """
    Downgrade experience rules from a negatively-reviewed analysis.

    Called when user gives feedback score=-1. Halves confidence;
    deletes rules that drop below 0.3.
    """
    from logmind.domain.log.service import log_service

    try:
        es = log_service.es
        exists = await es.indices.exists(index=_RULES_INDEX)
        if not exists:
            return

        result = await es.search(
            index=_RULES_INDEX,
            body={
                "query": {"term": {"source_task_id": source_task_id}},
                "size": 50,
                "_source": ["rule", "confidence"],
            },
        )

        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            return

        for hit in hits:
            doc_id = hit["_id"]
            conf = hit["_source"].get("confidence", 0.8)
            new_conf = conf * 0.5

            if new_conf < 0.3:
                await es.delete(index=_RULES_INDEX, id=doc_id, ignore=[404])
            else:
                await es.update(
                    index=_RULES_INDEX,
                    id=doc_id,
                    body={"doc": {"confidence": new_conf}},
                )

        logger.info("rules_downgraded", task_id=source_task_id, count=len(hits))

    except Exception as e:
        logger.warning("rules_downgrade_failed", error=str(e))


# ══════════════════════════════════════════════════════════
#  Cache Management
# ══════════════════════════════════════════════════════════

def invalidate_profile_cache(business_line_id: str | None = None):
    """
    Invalidate cached profile(s).

    Called after new analysis completes or feedback changes,
    so the next prompt build picks up fresh data.
    """
    global _profile_cache

    if business_line_id:
        _profile_cache.pop(business_line_id, None)
    else:
        _profile_cache.clear()
