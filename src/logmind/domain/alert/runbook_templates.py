"""
Runbook Template Marketplace — Pre-built Remediation Strategies

Provides a curated set of ready-to-use runbook configurations
that can be imported into a BusinessLine's auto_remediation_config.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from logmind.core.dependencies import CurrentUser
from logmind.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/runbook-templates", tags=["Runbook"])


class RunbookTemplate(BaseModel):
    id: str
    name: str
    description: str
    category: str  # notification / restart / escalation / custom
    icon: str
    config: dict  # Ready-to-use auto_remediation_config JSON


# ── Pre-built Templates ──────────────────────────────────
TEMPLATES: list[RunbookTemplate] = [
    RunbookTemplate(
        id="dingtalk-p0",
        name="钉钉 P0 升级通知",
        description="P0 告警触发时立即通过钉钉机器人发送升级通知",
        category="notification",
        icon="🔔",
        config={
            "actions": [{
                "name": "钉钉 P0 升级通知",
                "type": "webhook",
                "url": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN",
                "trigger_on": ["P0"],
                "cooldown_minutes": 30,
                "body": {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": "🔴 P0 Runbook 告警",
                        "text": (
                            "## 🔴 P0 自动修复触发\n\n"
                            "**服务**: {{service_name}}\n\n"
                            "**告警**: {{alert_message}}\n\n"
                            "**任务ID**: {{task_id}}\n\n"
                            "> 请立即处理"
                        ),
                    },
                },
            }],
        },
    ),
    RunbookTemplate(
        id="feishu-p0p1",
        name="飞书 P0+P1 告警通知",
        description="P0/P1 告警触发时通过飞书机器人通知",
        category="notification",
        icon="📮",
        config={
            "actions": [{
                "name": "飞书告警通知",
                "type": "webhook",
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_HOOK_ID",
                "trigger_on": ["P0", "P1"],
                "cooldown_minutes": 15,
                "body": {
                    "msg_type": "interactive",
                    "card": {
                        "header": {
                            "title": {"content": "🔧 Runbook 告警: {{service_name}}", "tag": "plain_text"},
                        },
                        "elements": [{
                            "tag": "div",
                            "text": {"content": "{{alert_message}}", "tag": "plain_text"},
                        }],
                    },
                },
            }],
        },
    ),
    RunbookTemplate(
        id="wechat-work",
        name="企业微信告警",
        description="通过企业微信机器人发送 P0 告警通知",
        category="notification",
        icon="💬",
        config={
            "actions": [{
                "name": "企微告警",
                "type": "webhook",
                "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY",
                "trigger_on": ["P0"],
                "cooldown_minutes": 30,
                "body": {
                    "msgtype": "markdown",
                    "markdown": {
                        "content": (
                            "## 🔴 Runbook 自动告警\n"
                            "> **服务**: {{service_name}}\n"
                            "> **告警**: {{alert_message}}\n"
                            "> **任务**: {{task_id}}"
                        ),
                    },
                },
            }],
        },
    ),
    RunbookTemplate(
        id="ci-restart",
        name="CI/CD 服务重启",
        description="P0 告警时自动调用 CI/CD 系统重启服务（需配置认证）",
        category="restart",
        icon="🔄",
        config={
            "actions": [{
                "name": "CI/CD 自动重启",
                "type": "webhook",
                "method": "POST",
                "url": "https://ci.example.com/api/v1/restart",
                "headers": {"Authorization": "Bearer YOUR_CI_TOKEN"},
                "trigger_on": ["P0"],
                "cooldown_minutes": 60,
                "body": {
                    "service": "{{service_name}}",
                    "reason": "Runbook auto-restart: {{alert_message}}",
                    "triggered_by": "logmind",
                },
            }],
        },
    ),
    RunbookTemplate(
        id="multi-escalation",
        name="多级升级链",
        description="P0 → 钉钉+飞书同时通知，P1 → 仅钉钉通知",
        category="escalation",
        icon="📢",
        config={
            "actions": [
                {
                    "name": "钉钉 P0 通知",
                    "type": "webhook",
                    "url": "https://oapi.dingtalk.com/robot/send?access_token=TOKEN_1",
                    "trigger_on": ["P0"],
                    "cooldown_minutes": 30,
                    "body": {
                        "msgtype": "text",
                        "text": {"content": "🔴 [P0 Runbook] {{service_name}}: {{alert_message}}"},
                    },
                },
                {
                    "name": "飞书 P0 通知",
                    "type": "webhook",
                    "url": "https://open.feishu.cn/open-apis/bot/v2/hook/HOOK_ID",
                    "trigger_on": ["P0"],
                    "cooldown_minutes": 30,
                    "body": {
                        "msg_type": "text",
                        "content": {"text": "🔴 [P0 Runbook] {{service_name}}: {{alert_message}}"},
                    },
                },
                {
                    "name": "钉钉 P1 通知",
                    "type": "webhook",
                    "url": "https://oapi.dingtalk.com/robot/send?access_token=TOKEN_1",
                    "trigger_on": ["P1"],
                    "cooldown_minutes": 60,
                    "body": {
                        "msgtype": "text",
                        "text": {"content": "🟡 [P1 Runbook] {{service_name}}: {{alert_message}}"},
                    },
                },
            ],
        },
    ),
    RunbookTemplate(
        id="generic-webhook",
        name="通用 Webhook 调用",
        description="通用模板，可自定义 URL、请求方法和请求体",
        category="custom",
        icon="🔗",
        config={
            "actions": [{
                "name": "自定义 Webhook",
                "type": "webhook",
                "method": "POST",
                "url": "https://your-service.example.com/api/webhook",
                "headers": {},
                "trigger_on": ["P0", "P1"],
                "cooldown_minutes": 15,
                "body": {
                    "event": "logmind_alert",
                    "service": "{{service_name}}",
                    "message": "{{alert_message}}",
                    "task_id": "{{task_id}}",
                },
            }],
        },
    ),
]


@router.get("", response_model=list[RunbookTemplate])
async def list_runbook_templates(user: CurrentUser):
    """List all available runbook templates."""
    return TEMPLATES
