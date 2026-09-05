"""
Webhook Notification Channel — Templated Alert Notifications

Supports WeChat Work, DingTalk, Feishu, and generic JSON webhooks.
All notifications use structured markdown templates with essential business context.

Notification types:
  1. AI Analysis Alert — Critical findings from AI analysis
  2. Error Log Alert — Direct error notification (AI disabled)
  3. Pipeline Error — AI model or pipeline failure notification
"""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from logmind.core.config import get_settings
from logmind.core.logging import get_logger

logger = get_logger(__name__)

_DISPLAY_TZ = ZoneInfo("Asia/Shanghai")
_INLINE_TS_RE = re.compile(
    r"(?P<prefix>\[?)"
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"
    r"(?P<suffix>\]?)"
)
_WECOM_OMITTED_METADATA_RE = re.compile(
    r"^\s*(?:[-*>]\s*)?(?:\*\*)?(?:通知原因|分析入口)(?:\*\*)?\s*[:：]"
)
_INLINE_SOURCE_REFS_SUFFIX_RE = re.compile(
    r"\s*(?:[。；;]\s*)?source_log_refs\s*[:：]\s*"
    r"(?:\[[^\]\n]*\]|[^\n。]*)(?:。)?\s*$",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(
    r"\b(?P<drive>[A-Za-z]):\\(?P<rest>[A-Za-z0-9_.$()\-\\]+)"
)


def _is_wecom_webhook(url: str | None) -> bool:
    """Return True when the webhook URL points to WeCom / WeChat Work."""
    if not url:
        return False
    lowered = url.lower()
    return "qyapi.weixin" in lowered or "wecom" in lowered


def _strip_wecom_notification_metadata(content: str) -> str:
    """Remove low-value internal metadata from operator-facing WeCom messages."""
    lines: list[str] = []
    for line in content.splitlines():
        if _WECOM_OMITTED_METADATA_RE.match(line):
            continue
        line = _INLINE_SOURCE_REFS_SUFFIX_RE.sub("", line).rstrip()
        line = _WINDOWS_PATH_RE.sub(
            lambda match: (
                f"{match.group('drive')}:/"
                f"{match.group('rest').replace(chr(92), '/')}"
            ),
            line,
        )
        lines.append(line)
    return "\n".join(lines)


def _truncate_wecom_content(content: str, max_bytes: int = 4000) -> str:
    """
    Ensure markdown content does not exceed WeChat Work's 4096-byte limit.
    Truncates safely on UTF-8 bytes to prevent API error 40058.
    """
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content

    suffix = "\n\n> ⚠️ (内容超过企业微信字数限制已截断，请登录平台查看完整报告)"
    suffix_bytes = len(suffix.encode("utf-8"))
    budget = max_bytes - suffix_bytes

    truncated_text = encoded[:budget].decode("utf-8", errors="ignore")
    last_newline = truncated_text.rfind("\n")
    if last_newline > budget * 0.7:
        truncated_text = truncated_text[:last_newline]

    return truncated_text.rstrip() + suffix


def _to_display_timezone(dt: datetime | None) -> datetime | None:
    """Convert UTC/naive datetimes to the notification display timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_DISPLAY_TZ)


def _format_display_time(dt: datetime | None) -> str:
    """Format a datetime in Asia/Shanghai for operator-facing notifications."""
    local_dt = _to_display_timezone(dt)
    if local_dt is None:
        return "未知"
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_time_range(time_from: datetime | None, time_to: datetime | None) -> str:
    """Format a user-facing time range in Asia/Shanghai."""
    if time_from is None or time_to is None:
        return "未知"
    return f"{_format_display_time(time_from)} ~ {_format_display_time(time_to)} (北京时间)"


def _localize_inline_timestamps(text: str) -> str:
    """Convert inline ISO-8601 UTC timestamps in summaries to Asia/Shanghai."""
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        ts = match.group("ts")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return match.group(0)
        local_ts = _to_display_timezone(dt)
        if local_ts is None:
            return match.group(0)
        rendered = local_ts.strftime("%Y-%m-%d %H:%M:%S")
        return f"{match.group('prefix')}{rendered}{match.group('suffix')}"

    return _INLINE_TS_RE.sub(_replace, text)


# ── Notification Templates ───────────────────────────────

def _build_error_log_alert(
    business_line: str,
    domain: str,
    branch: str,
    host_name: str,
    language: str,
    log_count: int,
    error_summary: str,
    time_from: datetime | None,
    time_to: datetime | None,
) -> str:
    """
    Template: Error Log Alert — direct error notification (AI disabled).
    Sent when ai_enabled=False and error logs are detected.
    """
    env_tag = ""
    if branch == "master":
        env_tag = "🔴 正式环境"
    elif branch == "develop":
        env_tag = "🟡 测试环境"

    lang_names = {"java": "Java", "csharp": "C#", "python": "Python", "go": "Go"}
    lang_display = lang_names.get(language, language)

    source = domain or host_name or "未知"
    time_range = _format_time_range(time_from, time_to)
    localized_summary = _localize_inline_timestamps(error_summary)

    lines = [
        f"## ⚠️ 日志异常告警",
        f"",
        f"**业务线**: {business_line}",
        f"**站点**: {source}",
    ]
    if env_tag:
        lines.append(f"**环境**: {env_tag}")
    lines.extend([
        f"**语言**: {lang_display}",
        f"**时间范围**: {time_range}",
        f"**异常日志数**: {log_count} 条",
        f"",
        f"---",
        f"",
        f"**异常摘要**:",
        f"{localized_summary[:1500]}",
        f"",
        f"---",
    ])
    settings = get_settings()
    app_url = (getattr(settings, "public_app_url", "") or "").strip().rstrip("/")
    if app_url:
        lines.append(f"> 请及时排查处理。[登录 LogMind 平台查看完整日志]({app_url})")
    else:
        lines.append(f"> 请及时排查处理。登录 LogMind 平台查看完整日志。")
    return "\n".join(lines)


def _build_ai_analysis_alert(
    business_line: str,
    domain: str,
    branch: str,
    host_name: str,
    language: str,
    severity: str,
    content: str,
    task_id: str,
    log_count: int,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> str:
    """
    Template: AI Analysis Alert — critical findings from AI analysis.
    Sent when AI analysis finds critical issues.
    """
    emoji_map = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
    emoji = emoji_map.get(severity, "⚪")

    env_tag = ""
    if branch == "master":
        env_tag = " (正式环境)"
    elif branch == "develop":
        env_tag = " (测试环境)"

    lang_names = {"java": "Java", "csharp": "C#", "python": "Python", "go": "Go"}
    lang_display = lang_names.get(language, language) if language else ""

    source = domain or host_name or "未知"

    lines = [
        f"## {emoji} LogMind AI 分析告警",
        f"",
        f"**告警级别**: {severity.upper()}",
        f"**业务线**: {business_line}",
        f"**站点**: {source}{env_tag}",
    ]
    if lang_display:
        lines.append(f"**语言**: {lang_display}")
    if time_from and time_to:
        time_range = _format_time_range(time_from, time_to)
        if time_range != "未知":
            lines.append(f"**时间范围**: {time_range}")
    lines.extend([
        f"**扫描日志数**: {log_count} 条",
        f"**任务ID**: {task_id[:8]}...",
        f"",
        f"---",
        f"",
        f"**摘要**:",
        f"{content[:2000]}",
        f"",
        f"---",
    ])

    settings = get_settings()
    app_url = (getattr(settings, "public_app_url", "") or "").strip().rstrip("/")
    if app_url:
        lines.append(f"> 请及时处理。[登录 LogMind 平台查看完整分析报告]({app_url}/analysis/{task_id})")
    else:
        lines.append(f"> 请及时处理。登录 LogMind 平台查看完整分析报告。")

    return "\n".join(lines)


def _build_pipeline_error_alert(
    business_line: str,
    domain: str,
    error_message: str,
    task_id: str,
) -> str:
    """
    Template: Pipeline Error — AI model or pipeline failure notification.
    Sent when AI inference fails (model error, timeout, quota exceeded, etc.)
    """
    source = domain or "未知"
    lines = [
        f"## 🛑 AI 分析流程异常",
        f"",
        f"**业务线**: {business_line}",
        f"**站点**: {source}",
        f"**任务ID**: {task_id[:8]}...",
        f"",
        f"---",
        f"",
        f"**错误信息**:",
        f"```",
        f"{error_message[:1000]}",
        f"```",
        f"",
        f"---",
        f"> ⚠️ 分析管道执行未能完成",
        f"> 请检查报错查明是基础设施依赖（如 ElasticSearch、网络等）故障，还是 AI 模型调用异常。",
        f"> 当前该业务线的错误日志将仅通过原始日志摘要通知。",
    ]
    return "\n".join(lines)


# ── Webhook Sender ───────────────────────────────────────

async def send_webhook_notification(
    markdown_content: str,
    webhook_url: str | None = None,
    msg_type: str = "markdown",
) -> bool:
    """
    Send a notification to a webhook endpoint.

    Supports:
      - WeChat Work (企业微信): msgtype=markdown
      - DingTalk (钉钉): msgtype=markdown
      - Feishu (飞书): msg_type=interactive
      - Generic: raw JSON POST

    Args:
        markdown_content: Markdown-formatted message content
        webhook_url: Target webhook URL
        msg_type: Message type (markdown/text)

    Returns:
        True if notification was sent successfully
    """
    settings = get_settings()

    url = webhook_url or settings.wechat_webhook_url
    if not url:
        logger.warning("webhook_url_not_configured")
        return False

    # Detect webhook type from URL and build payload accordingly
    if _is_wecom_webhook(url):
        # WeChat Work
        wecom_content = _strip_wecom_notification_metadata(markdown_content)
        wecom_content = _truncate_wecom_content(wecom_content)
        payload = {
            "msgtype": msg_type,
            msg_type: {"content": wecom_content},
        }
    elif "dingtalk" in url or "oapi.dingtalk" in url:
        # DingTalk
        payload = {
            "msgtype": msg_type,
            msg_type: {
                "title": "LogMind 告警通知",
                "text": markdown_content,
            },
        }
    elif "feishu" in url or "lark" in url:
        # Feishu / Lark
        payload = {
            "msg_type": "text",
            "content": {"text": markdown_content},
        }
    else:
        # Generic webhook
        payload = {
            "msgtype": msg_type,
            msg_type: {"content": markdown_content},
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()

            # Check response for known error patterns
            errcode = result.get("errcode", result.get("code", 0))
            if errcode == 0 or result.get("StatusCode") == 0:
                logger.info("webhook_sent", url=url[:50])
                return True
            else:
                logger.error(
                    "webhook_response_error",
                    errcode=errcode,
                    errmsg=result.get("errmsg", result.get("msg", "")),
                )
                return False

    except Exception as e:
        logger.error("webhook_send_failed", error=str(e), url=url[:50])
        return False


# ── High-Level Notification API ──────────────────────────

async def notify_error_logs(
    business_line: str,
    domain: str,
    branch: str,
    host_name: str,
    language: str,
    log_count: int,
    error_summary: str,
    time_from: datetime | None,
    time_to: datetime | None,
    webhook_url: str | None = None,
) -> bool:
    """Send an error log alert notification (AI disabled mode)."""
    content = _build_error_log_alert(
        business_line=business_line,
        domain=domain,
        branch=branch,
        host_name=host_name,
        language=language,
        log_count=log_count,
        error_summary=error_summary,
        time_from=time_from,
        time_to=time_to,
    )
    return await send_webhook_notification(content, webhook_url=webhook_url)


async def notify_ai_alert(
    business_line: str,
    domain: str,
    branch: str,
    host_name: str,
    language: str,
    severity: str,
    content: str,
    task_id: str,
    log_count: int,
    webhook_url: str | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> bool:
    """Send an AI analysis alert notification."""
    markdown = _build_ai_analysis_alert(
        business_line=business_line,
        domain=domain,
        branch=branch,
        host_name=host_name,
        language=language,
        severity=severity,
        content=content,
        task_id=task_id,
        log_count=log_count,
        time_from=time_from,
        time_to=time_to,
    )
    return await send_webhook_notification(markdown, webhook_url=webhook_url)


async def notify_pipeline_error(
    business_line: str,
    domain: str,
    error_message: str,
    task_id: str,
    webhook_url: str | None = None,
) -> bool:
    """Send a pipeline/model error notification."""
    content = _build_pipeline_error_alert(
        business_line=business_line,
        domain=domain,
        error_message=error_message,
        task_id=task_id,
    )
    return await send_webhook_notification(content, webhook_url=webhook_url)


def _build_predictive_alert(
    business_line: str,
    severity: str,
    priority: str,
    predicted_errors_30m: float,
    current_rate: float,
    baseline_mean: float,
    confidence_pct: int,
    detail: str,
) -> str:
    """
    Template: Predictive Alert — Early warning on upcoming threshold breach.
    """
    emoji_map = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
    emoji = emoji_map.get(severity, "🟡")

    lines = [
        f"## {emoji} LogMind AI 预测告警",
        f"",
        f"**告警级别**: {severity.upper()} ({priority})",
        f"**业务线**: {business_line}",
        f"**当前错误率**: {current_rate:.0f} 条/h",
        f"**基线均值**: {baseline_mean:.0f} 条/h",
        f"**预测未来30分钟**: 约 {predicted_errors_30m:.0f} 条错误",
        f"**置信度**: {confidence_pct}%",
        f"",
        f"---",
        f"",
        f"**趋势分析结论**:",
        f"{detail}",
        f"",
        f"---",
        f"> 趋势预测显示近期错误率可能有显著上升，请提前排查潜在隐患。登录 LogMind 平台查看监控大盘。"
    ]
    return "\n".join(lines)


async def notify_predictive_alert(
    business_line: str,
    severity: str,
    priority: str,
    predicted_errors_30m: float,
    current_rate: float,
    baseline_mean: float,
    confidence_pct: int,
    detail: str,
    webhook_url: str | None = None,
) -> bool:
    """Send a predictive early-warning alert notification."""
    if _is_wecom_webhook(webhook_url):
        logger.info("predictive_alert_wecom_webhook_disabled")
        return False

    content = _build_predictive_alert(
        business_line=business_line,
        severity=severity,
        priority=priority,
        predicted_errors_30m=predicted_errors_30m,
        current_rate=current_rate,
        baseline_mean=baseline_mean,
        confidence_pct=confidence_pct,
        detail=detail,
    )
    return await send_webhook_notification(content, webhook_url=webhook_url)
