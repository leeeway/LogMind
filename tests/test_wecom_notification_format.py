from types import SimpleNamespace

import pytest


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"errcode": 0}


class _FakeAsyncClient:
    requests: list[tuple[str, dict]] = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict):
        self.requests.append((url, json))
        return _FakeResponse()


@pytest.mark.asyncio
async def test_wecom_omits_notification_reason_and_analysis_entry(monkeypatch):
    from logmind.domain.alert.channels import webhook

    _FakeAsyncClient.requests.clear()
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        webhook,
        "get_settings",
        lambda: SimpleNamespace(wechat_webhook_url=""),
    )

    content = "\n".join([
        "🟡 [P1|43.5分]",
        (
            r"问题: 写入 D:\WebCache\service.config\captcha\captcha.json 失败。"
            "source_log_refs: 2026-07-29T12:54:47.695Z。"
        ),
        "通知原因: P1: warning 级别错误，正常通知",
        "分析入口: https://logmind.example.com/analysis/task-1",
        "请及时处理。",
    ])

    sent = await webhook.send_webhook_notification(
        content,
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
    )

    assert sent is True
    payload_content = _FakeAsyncClient.requests[0][1]["markdown"]["content"]
    assert "D:/WebCache/service.config/captcha/captcha.json" in payload_content
    assert "请及时处理。" in payload_content
    assert "通知原因:" not in payload_content
    assert "分析入口:" not in payload_content
    assert "source_log_refs" not in payload_content


@pytest.mark.asyncio
async def test_non_wecom_keeps_notification_metadata(monkeypatch):
    from logmind.domain.alert.channels import webhook

    _FakeAsyncClient.requests.clear()
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        webhook,
        "get_settings",
        lambda: SimpleNamespace(wechat_webhook_url=""),
    )

    content = "问题: 检测到日志异常\n通知原因: P1\n分析入口: https://example.com"
    sent = await webhook.send_webhook_notification(
        content,
        webhook_url="https://oapi.dingtalk.com/robot/send?access_token=test",
    )

    assert sent is True
    payload_content = _FakeAsyncClient.requests[0][1]["markdown"]["text"]
    assert "通知原因: P1" in payload_content
    assert "分析入口: https://example.com" in payload_content


def test_wecom_byte_truncation_limits_to_4000_bytes():
    from logmind.domain.alert.channels.webhook import _truncate_wecom_content

    # 3000 Chinese characters = 9000 UTF-8 bytes
    huge_text = "这是一段非常长的中文排查日志分析结论，详细说明了数据库死锁的原因和排查步骤。" * 150
    assert len(huge_text.encode("utf-8")) > 6000

    truncated = _truncate_wecom_content(huge_text, max_bytes=4000)
    assert len(truncated.encode("utf-8")) <= 4000
    assert "已截断" in truncated


def test_ai_analysis_alert_includes_time_range_and_app_url():
    from datetime import datetime, timezone
    from logmind.domain.alert.channels.webhook import _build_ai_analysis_alert

    t_from = datetime(2026, 9, 5, 10, 0, 0, tzinfo=timezone.utc)
    t_to = datetime(2026, 9, 5, 10, 5, 0, tzinfo=timezone.utc)

    card = _build_ai_analysis_alert(
        business_line="用户中心",
        domain="user.gydev.cn",
        branch="master",
        host_name="node-1",
        language="java",
        severity="critical",
        content="发现大量 NullPointerException",
        task_id="task-12345678",
        log_count=42,
        time_from=t_from,
        time_to=t_to,
    )

    assert "## 🔴 LogMind AI 分析告警" in card
    assert "**业务线**: 用户中心" in card
    assert "**站点**: user.gydev.cn (正式环境)" in card
    assert "**语言**: Java" in card
    assert "**时间范围**:" in card
    assert "2026-09-05" in card
    assert "(北京时间)" in card

