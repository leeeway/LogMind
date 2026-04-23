"""
Analysis Quality Scorer — Self-Assessment for AI Analysis Results

Evaluates the quality of AI analysis conclusions and flags low-quality
results for potential re-analysis. This prevents vague, generic, or
incomplete analyses from being served to users.

Quality Dimensions:
  1. Content Length — Too short suggests incomplete analysis
  2. Specificity — Generic phrases indicate low insight value
  3. Actionability — Good analyses contain concrete fix suggestions
  4. Confidence Match — Low AI confidence + strong conclusion = suspect

Scoring:
  - 0-39: LOW quality → flag for re-analysis
  - 40-69: MEDIUM quality → acceptable, log for monitoring
  - 70-100: HIGH quality → good analysis

Integration:
  Called after ResultParseStage. Low-quality results get flagged in
  stage_metrics for optional re-analysis via a different provider.
"""

import re

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# ── Quality Thresholds ───────────────────────────────────

MIN_CONTENT_LENGTH = 50          # Characters
MIN_GOOD_CONTENT_LENGTH = 200
MAX_SCORE = 100

# Generic phrases that indicate low insight value
_GENERIC_PHRASES = [
    "需要进一步分析",
    "建议查看日志",
    "请检查相关配置",
    "可能是网络问题",
    "可能需要重启",
    "no significant issues",
    "looks normal",
    "further investigation needed",
    "check the logs",
    "appears to be a transient error",
]

# Actionable indicators — phrases that suggest concrete recommendations
_ACTIONABLE_PATTERNS = [
    r"(?:修复|fix|patch|upgrade|update).*(?:版本|version|依赖|dependency)",
    r"(?:增加|调整|设置|配置).*(?:超时|timeout|连接池|pool|重试|retry)",
    r"(?:检查|verify|确认).*(?:空值|null|参数|parameter|权限|permission)",
    r"(?:代码|code).*(?:行|line|文件|file)",
    r"(?:根因|root cause|原因)",
    r"(?:影响范围|impact|scope)",
    r"(?:修复建议|recommendation|solution|解决方案)",
]


def score_analysis_quality(
    content: str,
    severity: str = "warning",
    confidence: float = 0.7,
    log_count: int = 0,
) -> dict:
    """
    Score the quality of an AI analysis result.

    Args:
        content: AI analysis conclusion text
        severity: Declared severity level
        confidence: AI confidence score (0.0-1.0)
        log_count: Number of logs analyzed

    Returns:
        {
            "score": int,          # 0-100
            "grade": str,          # "high" / "medium" / "low"
            "reasons": list[str],  # Explanation of score components
            "should_retry": bool,  # Whether re-analysis is recommended
        }
    """
    if not content:
        return {
            "score": 0,
            "grade": "low",
            "reasons": ["Empty analysis content"],
            "should_retry": True,
        }

    score = 0
    reasons = []

    # ── 1. Content Length (0-30 points) ──────────────────
    content_len = len(content.strip())
    if content_len >= MIN_GOOD_CONTENT_LENGTH:
        length_score = 30
    elif content_len >= MIN_CONTENT_LENGTH:
        length_score = int(15 + 15 * (content_len - MIN_CONTENT_LENGTH) / (MIN_GOOD_CONTENT_LENGTH - MIN_CONTENT_LENGTH))
    else:
        length_score = int(15 * content_len / max(MIN_CONTENT_LENGTH, 1))
        reasons.append(f"Content too short ({content_len} chars)")
    score += length_score

    # ── 2. Specificity (0-25 points) ─────────────────────
    generic_hits = sum(1 for p in _GENERIC_PHRASES if p.lower() in content.lower())
    if generic_hits == 0:
        specificity_score = 25
    elif generic_hits <= 1:
        specificity_score = 15
        reasons.append("Contains generic phrases")
    else:
        specificity_score = 5
        reasons.append(f"Contains {generic_hits} generic phrases — low insight value")
    score += specificity_score

    # ── 3. Actionability (0-25 points) ───────────────────
    actionable_hits = sum(
        1 for p in _ACTIONABLE_PATTERNS if re.search(p, content, re.IGNORECASE)
    )
    if actionable_hits >= 3:
        action_score = 25
    elif actionable_hits >= 1:
        action_score = 15
    else:
        action_score = 5
        reasons.append("No actionable recommendations found")
    score += action_score

    # ── 4. Confidence Alignment (0-20 points) ────────────
    if confidence >= 0.8:
        conf_score = 20
    elif confidence >= 0.5:
        conf_score = 12
    else:
        conf_score = 5
        reasons.append(f"Low AI confidence ({confidence:.2f})")
    score += conf_score

    # Determine grade
    if score >= 70:
        grade = "high"
    elif score >= 40:
        grade = "medium"
    else:
        grade = "low"

    # Should retry only for low quality with sufficient input data
    should_retry = grade == "low" and log_count >= 3

    if not reasons:
        reasons.append("Analysis meets quality standards")

    result = {
        "score": min(score, MAX_SCORE),
        "grade": grade,
        "reasons": reasons,
        "should_retry": should_retry,
    }

    if grade == "low":
        logger.warning(
            "analysis_quality_low",
            score=score,
            reasons=reasons,
            content_preview=content[:100],
        )

    return result


def is_low_quality(content: str, confidence: float = 0.7) -> bool:
    """Quick check if analysis content is below quality threshold."""
    result = score_analysis_quality(content, confidence=confidence)
    return result["grade"] == "low"
