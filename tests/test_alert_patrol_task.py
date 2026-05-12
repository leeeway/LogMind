import pytest

from logmind.domain.alert import tasks as alert_tasks


def test_patrol_single_retries_on_connection_reset(monkeypatch):
    def fake_run_async(coro):
        coro.close()
        raise ConnectionResetError(104, "reset")

    monkeypatch.setattr(alert_tasks, "run_async", fake_run_async)

    retry_called = {}

    def fake_retry(exc):
        retry_called["exc"] = exc
        raise RuntimeError("retry-called")

    fake_self = type(
        "FakeTask",
        (),
        {
            "request": type("Req", (), {"retries": 0})(),
            "retry": staticmethod(fake_retry),
        },
    )()

    with pytest.raises(RuntimeError, match="retry-called"):
        alert_tasks.patrol_single_business_line.run.__func__(fake_self, "biz-1")

    assert isinstance(retry_called["exc"], ConnectionResetError)
