from datetime import timedelta

import pytest

from logmind.domain.alert import tasks as alert_tasks


class _FakeSettings:
    analysis_cooldown_minutes = 10
    analysis_anomaly_window_minutes = 5
    analysis_lookback_minutes = 10
    effective_anomaly_window_minutes = 5
    effective_lookback_minutes = 10


class _FakeBiz:
    id = "biz-1"
    tenant_id = "tenant-1"
    name = "Demo Service"
    is_active = True
    es_index_pattern = "demo-*"
    severity_threshold = "error"


class _FakeSession:
    def __init__(self):
        self.created_task = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, object_id):
        return _FakeBiz()

    def add(self, task):
        self.created_task = task

    async def flush(self):
        return None


class _FakeAnomalyDetector:
    def __init__(self):
        self.window_minutes = None

    async def detect(self, *, index_pattern, window_minutes, severity_threshold):
        from logmind.domain.anomaly.detector import AnomalyResult

        self.window_minutes = window_minutes
        return AnomalyResult(is_anomaly=True, level="warning", current_errors=7)


@pytest.mark.asyncio
async def test_patrol_uses_short_anomaly_window_but_keeps_analysis_lookback(monkeypatch):
    fake_session = _FakeSession()
    fake_detector = _FakeAnomalyDetector()
    delayed_tasks = []

    monkeypatch.setattr("logmind.core.config.get_settings", lambda: _FakeSettings())
    monkeypatch.setattr("logmind.core.database.get_db_context", lambda: fake_session)
    monkeypatch.setattr("logmind.domain.anomaly.detector.anomaly_detector", fake_detector)
    monkeypatch.setattr(
        "logmind.domain.analysis.tasks.run_analysis_task.delay",
        lambda task_id: delayed_tasks.append(task_id),
    )

    await alert_tasks._patrol_single("biz-1")

    assert fake_detector.window_minutes == 5
    assert fake_session.created_task is not None
    task_window = fake_session.created_task.time_to - fake_session.created_task.time_from
    assert task_window == pytest.approx(timedelta(minutes=10))
    assert delayed_tasks == [fake_session.created_task.id]


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
