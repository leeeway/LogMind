"""
Runbook Executor — Automated Remediation for P0/P1 Incidents

Executes pre-configured remediation actions when high-priority
alerts are triggered. Actions are defined per-BusinessLine in the
`auto_remediation_config` JSON field.

Config format:
{
    "actions": [
        {
            "name": "Restart Service",
            "type": "webhook",
            "url": "https://ci.example.com/api/restart",
            "method": "POST",
            "headers": {"Authorization": "Bearer xxx"},
            "body": {"service": "{{service_name}}"},
            "trigger_on": ["P0"],
            "cooldown_minutes": 30
        },
        {
            "name": "Notify Escalation",
            "type": "webhook",
            "url": "https://hooks.dingtalk.com/xxx",
            "trigger_on": ["P0", "P1"]
        }
    ]
}

Supported action types:
  - webhook: HTTP POST/PUT to a URL with template variables
  - (future: script, k8s_restart, etc.)

Safety:
  - Cooldown per action to prevent runbook storms
  - Results logged to Incident timeline
  - All executions are audited
"""

import json
import time
from dataclasses import dataclass, field

import httpx

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# ── Cooldown tracking (in-memory, per-worker) ────────────
_cooldowns: dict[str, float] = {}  # key → last_execution_timestamp


@dataclass
class RunbookResult:
    """Result of a runbook execution attempt."""
    executed: bool = False
    actions_run: list[dict] = field(default_factory=list)
    actions_skipped: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = []
        if self.actions_run:
            names = [a["name"] for a in self.actions_run]
            parts.append(f"✅ 已执行: {', '.join(names)}")
        if self.actions_skipped:
            names = [a["name"] for a in self.actions_skipped]
            parts.append(f"⏭️ 跳过(冷却中): {', '.join(names)}")
        if self.errors:
            parts.append(f"❌ 失败: {'; '.join(self.errors)}")
        return " | ".join(parts) if parts else "无可用 Runbook"


class RunbookExecutor:
    """
    Executes pre-configured remediation actions for a business line.

    Actions are triggered based on alert priority matching the
    `trigger_on` field in the action config.
    """

    async def execute(
        self,
        config_json: str,
        priority: str,
        alert_message: str,
        service_name: str = "",
        task_id: str = "",
    ) -> RunbookResult:
        """
        Execute matching runbook actions.

        Args:
            config_json: The auto_remediation_config JSON from BusinessLine
            priority: Alert priority ("P0", "P1", "P2")
            alert_message: Alert message for template substitution
            service_name: Service name for template substitution
            task_id: Analysis task ID for auditing
        """
        result = RunbookResult()

        try:
            config = json.loads(config_json or "{}")
        except json.JSONDecodeError:
            return result

        actions = config.get("actions", [])
        if not actions:
            return result

        for action in actions:
            action_name = action.get("name", "unnamed")
            trigger_on = action.get("trigger_on", ["P0"])

            # Check if priority matches
            if priority not in trigger_on:
                continue

            # Check cooldown
            cooldown_minutes = action.get("cooldown_minutes", 10)
            cooldown_key = f"{service_name}:{action_name}"
            now = time.monotonic()
            last_run = _cooldowns.get(cooldown_key, 0)

            if (now - last_run) < cooldown_minutes * 60:
                result.actions_skipped.append({
                    "name": action_name,
                    "reason": f"冷却中（{cooldown_minutes}分钟）",
                })
                continue

            # Execute action
            action_type = action.get("type", "webhook")
            try:
                if action_type == "webhook":
                    await self._execute_webhook(
                        action, alert_message, service_name, task_id,
                    )
                else:
                    result.errors.append(f"不支持的动作类型: {action_type}")
                    continue

                # Record success
                _cooldowns[cooldown_key] = now
                result.executed = True
                result.actions_run.append({
                    "name": action_name,
                    "type": action_type,
                })

                logger.info(
                    "runbook_action_executed",
                    action=action_name,
                    type=action_type,
                    priority=priority,
                    service=service_name,
                    task_id=task_id,
                )

            except Exception as e:
                error_msg = f"{action_name}: {str(e)[:100]}"
                result.errors.append(error_msg)
                logger.error(
                    "runbook_action_failed",
                    action=action_name,
                    error=str(e),
                    task_id=task_id,
                )

        return result

    async def _execute_webhook(
        self,
        action: dict,
        alert_message: str,
        service_name: str,
        task_id: str,
    ):
        """Execute a webhook action with template variable substitution."""
        url = action.get("url", "")
        if not url:
            raise ValueError("Webhook URL is required")

        method = action.get("method", "POST").upper()
        headers = action.get("headers", {})
        body_template = action.get("body", {})

        # Template variable substitution
        variables = {
            "{{service_name}}": service_name,
            "{{alert_message}}": alert_message[:500],
            "{{task_id}}": task_id,
            "{{priority}}": action.get("trigger_on", ["P0"])[0],
        }

        # Substitute in body
        body_str = json.dumps(body_template, ensure_ascii=False)
        for var, val in variables.items():
            body_str = body_str.replace(var, val)
        body = json.loads(body_str)

        # Default body if empty
        if not body:
            body = {
                "msgtype": "text",
                "text": {
                    "content": (
                        f"🔧 [Runbook 自动修复]\n"
                        f"服务: {service_name}\n"
                        f"动作: {action.get('name', 'unknown')}\n"
                        f"告警: {alert_message[:200]}"
                    )
                },
            }

        async with httpx.AsyncClient(timeout=15) as client:
            if method == "POST":
                resp = await client.post(url, json=body, headers=headers)
            elif method == "PUT":
                resp = await client.put(url, json=body, headers=headers)
            else:
                resp = await client.get(url, headers=headers)

            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

            logger.info(
                "runbook_webhook_sent",
                url=url[:60],
                status=resp.status_code,
            )


# Singleton
runbook_executor = RunbookExecutor()
