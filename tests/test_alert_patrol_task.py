from datetime import timedelta

import pytest

from logmind.domain.alert import tasks as alert_tasks


class _FakeSettings:
    analysis_cooldown_minutes = 10
    analysis_anomaly_window_minutes = 5
    analysis_lookback_minutes = 10
    effective_anomaly_window_minutes = 5
    effective_lookback_minutes = 10
    analysis_patrol_overlap_minutes = 2
    analysis_patrol_max_catchup_minutes = 60
    analysis_concrete_fault_enabled = True
    analysis_concrete_fault_shadow = True
    effective_patrol_interval_minutes = 5
    patrol_max_queue_depth = 50
    patrol_inflight_ttl_seconds = 600


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

    async def scalar(self, stmt):
        return None

    def add(self, task):
        self.created_task = task

    async def flush(self):
        return None

    async def commit(self):
        return None


class _FakeAnomalyDetector:
    def __init__(self):
        self.window_minutes = None

    async def detect(self, *, index_pattern, window_minutes, severity_threshold, **kwargs):
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


@pytest.mark.asyncio
async def test_dispatch_patrols_skips_when_queue_depth_exceeds_threshold(monkeypatch):
    class FakeBrokerRedis:
        async def llen(self, key):
            return 100  # greater than max_depth 50

    delayed_tasks = []
    monkeypatch.setattr("logmind.core.config.get_settings", lambda: _FakeSettings())
    monkeypatch.setattr("logmind.core.redis.get_celery_broker_redis_client", lambda: FakeBrokerRedis())
    monkeypatch.setattr("logmind.domain.alert.tasks.patrol_single_business_line.delay", lambda biz_id: delayed_tasks.append(biz_id))

    await alert_tasks._dispatch_patrols()
    assert delayed_tasks == []


@pytest.mark.asyncio
async def test_dispatch_patrols_skips_when_biz_is_inflight(monkeypatch):
    class FakeBrokerRedis:
        async def llen(self, key):
            return 0

    class FakeRedisClient:
        async def set(self, key, value, nx=False, ex=None):
            return False  # Already in flight

    class FakeBizListSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def execute(self, stmt):
            class Res:
                def scalars(self):
                    return self
                def all(self):
                    return [_FakeBiz()]
                def scalar_one_or_none(self):
                    return None
            return Res()

    delayed_tasks = []
    monkeypatch.setattr("logmind.core.config.get_settings", lambda: _FakeSettings())
    monkeypatch.setattr("logmind.core.redis.get_celery_broker_redis_client", lambda: FakeBrokerRedis())
    monkeypatch.setattr("logmind.core.redis.get_redis_client", lambda: FakeRedisClient())
    monkeypatch.setattr("logmind.core.database.get_db_context", lambda: FakeBizListSession())
    monkeypatch.setattr("logmind.domain.alert.tasks.patrol_single_business_line.delay", lambda biz_id: delayed_tasks.append(biz_id))

    await alert_tasks._dispatch_patrols()
    assert delayed_tasks == []

