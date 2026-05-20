import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

from logmind.core.security import TokenPayload
from logmind.domain.alert.router import (
    BatchAlertRequest,
    batch_acknowledge_alerts,
    batch_resolve_alerts,
)
from logmind.domain.alert.models import AlertHistory


class MockResult:
    def __init__(self, items):
        self.items = items

    def scalars(self):
        return self

    def all(self):
        return self.items


class MockSession:
    def __init__(self, items):
        self.items = items
        self.flushed = False

    async def execute(self, stmt):
        return MockResult(self.items)

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_batch_acknowledge_alerts_success():
    # Arrange
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="admin")
    req = BatchAlertRequest(alert_ids=["a1", "a2"])
    
    alert1 = AlertHistory(
        id="a1",
        tenant_id="tenant-1",
        status="fired",
        message="Alert 1",
        fired_at=datetime.now(timezone.utc),
    )
    alert2 = AlertHistory(
        id="a2",
        tenant_id="tenant-1",
        status="fired",
        message="Alert 2",
        fired_at=datetime.now(timezone.utc),
    )
    
    session = MockSession([alert1, alert2])

    # Act
    res = await batch_acknowledge_alerts(req, session, user)

    # Assert
    assert res.message == "成功确认 2 条告警记录"
    assert alert1.status == "acknowledged"
    assert alert1.acked_by == "user-1"
    assert alert1.acked_at is not None
    assert alert2.status == "acknowledged"
    assert alert2.acked_by == "user-1"
    assert alert2.acked_at is not None
    assert session.flushed is True


@pytest.mark.asyncio
async def test_batch_acknowledge_alerts_empty():
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="admin")
    req = BatchAlertRequest(alert_ids=[])
    session = MockSession([])

    res = await batch_acknowledge_alerts(req, session, user)

    assert res.message == "未提供告警 ID 列表"
    assert session.flushed is False


@pytest.mark.asyncio
async def test_batch_resolve_alerts_success():
    # Arrange
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="admin")
    req = BatchAlertRequest(alert_ids=["a1", "a2"])
    
    alert1 = AlertHistory(
        id="a1",
        tenant_id="tenant-1",
        status="fired",
        message="Alert 1",
        fired_at=datetime.now(timezone.utc),
    )
    alert2 = AlertHistory(
        id="a2",
        tenant_id="tenant-1",
        status="acknowledged",
        message="Alert 2",
        fired_at=datetime.now(timezone.utc),
        acked_by="someone-else",
        acked_at=datetime.now(timezone.utc),
    )
    
    session = MockSession([alert1, alert2])

    # Act
    res = await batch_resolve_alerts(req, session, user)

    # Assert
    assert res.message == "成功解决 2 条告警记录"
    assert alert1.status == "resolved"
    assert alert1.resolved_by == "user-1"
    assert alert1.resolved_at is not None
    assert alert1.acked_by == "user-1"
    assert alert1.acked_at is not None

    assert alert2.status == "resolved"
    assert alert2.resolved_by == "user-1"
    assert alert2.resolved_at is not None
    assert alert2.acked_by == "someone-else"  # Should NOT overwrite existing ack
    assert session.flushed is True


@pytest.mark.asyncio
async def test_batch_resolve_alerts_empty():
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="admin")
    req = BatchAlertRequest(alert_ids=[])
    session = MockSession([])

    res = await batch_resolve_alerts(req, session, user)

    assert res.message == "未提供告警 ID 列表"
    assert session.flushed is False
