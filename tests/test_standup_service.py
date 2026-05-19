import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from logmind.domain.dashboard import standup_service


def test_default_standup_target_date_uses_previous_local_day():
    now_utc = datetime(2026, 5, 19, 1, 30, tzinfo=timezone.utc)

    target = standup_service.default_standup_target_date(now_utc)

    assert target.strftime("%Y-%m-%d %H:%M") == "2026-05-18 00:00"
    assert target.tzinfo is not None


def test_auto_share_due_uses_default_window_and_honors_disable():
    due_now = datetime(2026, 5, 19, 13, 3, tzinfo=timezone.utc)  # 21:03 Asia/Shanghai
    late_now = datetime(2026, 5, 19, 13, 6, tzinfo=timezone.utc)  # 21:06 Asia/Shanghai

    assert standup_service.is_daily_standup_due("{}", now_utc=due_now) is True
    assert standup_service.is_daily_standup_due("{}", now_utc=late_now) is False
    assert (
        standup_service.is_daily_standup_due(
            '{"daily_standup":{"enabled":false}}',
            now_utc=due_now,
        )
        is False
    )


def test_share_standup_for_tenant_deduplicates_webhooks_and_counts_success(monkeypatch):
    class _ScalarRows:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    class _ExecuteResult:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return _ScalarRows(self._values)

    class _Session:
        async def execute(self, stmt):
            return _ExecuteResult(
                [
                    "https://example.com/a",
                    "https://example.com/a",
                    "https://example.com/b",
                ]
            )

    class _DbContext:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    generate_standup_report = AsyncMock(
        return_value={
            "date": "2026-05-19",
            "ai_summary": "## Summary\n\nEverything looks stable.",
        }
    )
    send_webhook_notification = AsyncMock(side_effect=[True, False])

    monkeypatch.setattr(
        "logmind.core.database.get_db_context",
        lambda: _DbContext(),
    )
    monkeypatch.setattr(
        "logmind.domain.dashboard.standup_generator.generate_standup_report",
        generate_standup_report,
    )
    monkeypatch.setattr(
        "logmind.domain.alert.channels.webhook.send_webhook_notification",
        send_webhook_notification,
    )

    result = asyncio.run(standup_service.share_standup_for_tenant("tenant-1"))

    assert result == {"ok": True, "sent_count": 1, "date": "2026-05-19"}
    generate_standup_report.assert_awaited_once()
    assert send_webhook_notification.await_count == 2
    first_call_content = send_webhook_notification.await_args_list[0].args[0]
    assert "LogMind 每日站会 - 2026-05-19" in first_call_content
