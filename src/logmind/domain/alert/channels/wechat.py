"""
WeChat Work (企业微信) Alert Channel

Sends alert notifications via WeChat Work webhook.
"""

import httpx

from logmind.core.config import get_settings
from logmind.core.logging import get_logger

logger = get_logger(__name__)


async def send_wechat_alert(
    business_line: str,
    severity: str,
    content: str,
    task_id: str,
    webhook_url: str | None = None,
) -> bool:
    """
    Send an alert notification to WeChat Work.

    Args:
        business_line: Name of the affected business line
        severity: Alert severity (critical/warning/info)
        content: Alert message content
        task_id: Associated analysis task ID
        webhook_url: Override webhook URL (default from settings)

    Returns:
        True if notification was sent successfully
    """
    settings = get_settings()

    if not settings.wechat_enabled:
        logger.info("wechat_disabled", task_id=task_id)
        return False

    url = webhook_url or settings.wechat_webhook_url
    if not url:
        logger.warning("wechat_webhook_not_configured")
        return False

    # Severity emoji mapping
    emoji_map = {
        "critical": "🔴",
        "warning": "🟡",
        "info": "🔵",
    }
    emoji = emoji_map.get(severity, "⚪")

    # Build markdown message
    markdown_content = f"""## {emoji} LogMind 日志告警

**告警级别**: {severity.upper()}
**业务线**: {business_line}
**分析任务**: {task_id[:8]}...

---

**告警内容**:
{content[:2000]}

---
> 请及时处理，如需详情请登录 LogMind 平台查看。"""

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": markdown_content,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()

            if result.get("errcode") == 0:
                logger.info(
                    "wechat_alert_sent",
                    business_line=business_line,
                    severity=severity,
                    task_id=task_id,
                )
                return True
            else:
                logger.error(
                    "wechat_alert_failed",
                    errcode=result.get("errcode"),
                    errmsg=result.get("errmsg"),
                )
                return False

    except Exception as e:
        logger.error("wechat_alert_error", error=str(e), task_id=task_id)
        return False


async def send_wechat_text(text: str, webhook_url: str | None = None) -> bool:
    """Send a plain text message to WeChat Work."""
    settings = get_settings()
    url = webhook_url or settings.wechat_webhook_url
    if not url:
        return False

    payload = {
        "msgtype": "text",
        "text": {"content": text},
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return resp.json().get("errcode") == 0
    except Exception:
        return False
