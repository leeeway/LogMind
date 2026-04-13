"""
Webhook Notification Channel — Templated Alert Notifications

Supports WeChat Work, DingTalk, Feishu, and generic JSON webhooks.
All notifications use structured markdown templates with essential business context.

Notification types:
  1. AI Analysis Alert — Critical findings from AI analysis
  2. Error Log Alert — Direct error notification (AI disabled)
  3. Pipeline Error — AI model or pipeline failure notification
"""

from datetime import datetime, timezone

import httpx

from logmind.core.config import get_settings
from logmind.core.logging import get_logger

logger = get_logger(__name__)


# ── Notification Templates ───────────────────────────────

def _build_error_log_alert(
    business_line: str,
    domain: str,
    branch: str,
    host_name: str,
    language: str,
    log_count: int,
    error_summary: str,
    time_range: str,
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
        f"{error_summary[:1500]}",
        f"",
        f"---",
        f"> 请及时排查处理。登录 LogMind 平台查看完整日志。",
    ])
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

    source = domain or host_name or "未知"

    lines = [
        f"## {emoji} LogMind AI 分析告警",
        f"",
        f"**告警级别**: {severity.upper()}",
        f"**业务线**: {business_line}",
        f"**站点**: {source}{env_tag}",
        f"**分析日志数**: {log_count} 条",
        f"**任务ID**: {task_id[:8]}...",
        f"",
        f"---",
        f"",
        f"**AI 分析结论**:",
        f"{content[:2000]}",
        f"",
        f"---",
        f"> 请及时处理。登录 LogMind 平台查看完整分析报告。",
    ]
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
        f"> AI 模型调用异常，请检查模型配置和 API Key。",
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
    if "qyapi.weixin" in url or "wecom" in url:
        # WeChat Work
        payload = {
            "msgtype": msg_type,
            msg_type: {"content": markdown_content},
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
    time_range: str,
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
        time_range=time_range,
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
