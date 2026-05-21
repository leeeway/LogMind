from unittest.mock import AsyncMock

import pytest

from logmind.domain.alert.channels import webhook


@pytest.mark.asyncio
async def test_predictive_alert_skips_wecom_webhook(monkeypatch):
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(webhook, "send_webhook_notification", send_mock)

    ok = await webhook.notify_predictive_alert(
        business_line="checkout",
        severity="warning",
        priority="P2",
        predicted_errors_30m=12,
        current_rate=20,
        baseline_mean=8,
        confidence_pct=83,
        detail="trend rising",
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
    )

    assert ok is False
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_predictive_alert_still_sends_non_wecom_webhook(monkeypatch):
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(webhook, "send_webhook_notification", send_mock)

    ok = await webhook.notify_predictive_alert(
        business_line="checkout",
        severity="critical",
        priority="P1",
        predicted_errors_30m=30,
        current_rate=45,
        baseline_mean=10,
        confidence_pct=96,
        detail="sharp anomaly",
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
    )

    assert ok is True
    send_mock.assert_awaited_once()
