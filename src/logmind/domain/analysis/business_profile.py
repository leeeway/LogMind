"""
Business Line Intelligence Profile — Accumulated Analysis Experience

Builds a dynamic "knowledge profile" for each business line by querying
historical AI analysis results from ES. This profile is injected into
the system prompt so the AI analyst "remembers" what it learned about
each service from past investigations.

Data source:
  - ES index `logmind-analysis-vectors` (populated by analysis_indexer.py)
  - Fields used: error_signature, analysis_content, severity, hit_count,
    status, feedback_quality

Profile construction:
  1. Query top-N historical analyses for this business line
  2. Prioritize: verified (+1 feedback) > high hit_count > recent
  3. Exclude: poor feedback, ignored status
  4. Synthesize into a concise text block for prompt injection

Caching:
  In-memory cache with 10-minute TTL per business line.
  Profile rarely changes (only after new analyses), so cache is effective.
"""

import time

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# ── In-memory cache ──────────────────────────────────────
# key: business_line_id → (profile_text, timestamp)
_profile_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 600  # 10 minutes

_VECTORS_INDEX = "logmind-analysis-vectors"


async def build_profile_context(business_line_id: str) -> str:
    """
    Build a text block summarizing this business line's analysis history.

    Returns an empty string if no meaningful history exists.
    The returned text is ready for direct injection into the AI system prompt.
    """
    global _profile_cache

    now = time.monotonic()

    # Check cache
    cached = _profile_cache.get(business_line_id)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        profile = await _query_profile_from_es(business_line_id)
    except Exception as e:
        logger.warning(
            "business_profile_build_failed",
            business_line_id=business_line_id,
            error=str(e),
        )
        # Return stale cache on error
        if cached:
            return cached[0]
        return ""

    _profile_cache[business_line_id] = (profile, now)
    return profile


async def _query_profile_from_es(business_line_id: str) -> str:
    """Query ES for historical analyses and synthesize a profile."""
    from logmind.domain.log.service import log_service

    es = log_service.es

    exists = await es.indices.exists(index=_VECTORS_INDEX)
    if not exists:
        return ""

    # Query: top analyses for this business line, sorted by relevance
    # Priority: verified first, then by hit_count (most recurring issues)
    result = await es.search(
        index=_VECTORS_INDEX,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"business_line_id": business_line_id}},
                    ],
                    "must_not": [
                        # Exclude poor-quality or ignored entries
                        {"term": {"feedback_quality": "poor"}},
                        {"term": {"status": "ignored"}},
                    ],
                }
            },
            "sort": [
                # Verified entries first, then high hit_count, then recent
                {"feedback_quality": {"order": "desc", "missing": "_last"}},
                {"hit_count": {"order": "desc"}},
                {"last_seen": {"order": "desc"}},
            ],
            "size": 10,
            "_source": [
                "error_signature",
                "analysis_content",
                "severity",
                "hit_count",
                "status",
                "feedback_quality",
                "first_seen",
                "last_seen",
            ],
        },
    )

    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        return ""

    # Synthesize profile text
    entries = []
    for i, hit in enumerate(hits):
        src = hit["_source"]
        severity = src.get("severity", "info").upper()
        hit_count = src.get("hit_count", 1)
        verified = src.get("feedback_quality") == "verified"
        status = src.get("status", "open")
        sig = (src.get("error_signature") or "")[:120]
        content = (src.get("analysis_content") or "")[:300]

        # Build label
        labels = [severity]
        if verified:
            labels.append("已验证")
        if status == "resolved":
            labels.append("已修复")
        label_str = ", ".join(labels)

        entry = f"{i + 1}. [{label_str}] {sig}"
        if hit_count > 1:
            entry += f" (累计出现 {hit_count} 次)"
        # Add condensed conclusion (first 200 chars)
        conclusion = content.split("\n")[0][:200] if content else ""
        if conclusion:
            entry += f"\n   结论摘要: {conclusion}"

        entries.append(entry)

    if not entries:
        return ""

    profile_text = (
        "## 该服务的历史分析经验\n"
        "以下是 LogMind 对该服务过去分析中积累的经验，供你参考：\n\n"
        + "\n\n".join(entries)
        + "\n\n请在分析时参考这些经验，但务必基于当前日志的实际内容做独立判断。"
        "已修复的问题如果再次出现，属于回归。\n"
    )

    logger.info(
        "business_profile_built",
        business_line_id=business_line_id,
        entry_count=len(entries),
    )

    return profile_text


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
