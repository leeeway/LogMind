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
