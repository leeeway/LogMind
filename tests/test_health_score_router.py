from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from logmind.core.security import TokenPayload
from logmind.domain.dashboard.health_score_router import get_health_scores
from logmind.domain.tenant.models import BusinessLine


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows, *, scalar=False):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return _ScalarRows(self._rows)

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("unexpected query")
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_health_scores_treat_null_business_priority_fields_as_defaults():
    now = datetime.now(timezone.utc)
    biz = BusinessLine(
        id="biz-1",
        tenant_id="tenant-1",
        name="Checkout",
        es_index_pattern="checkout-*",
        business_weight=None,
        is_core_path=None,
        created_at=now,
        updated_at=now,
    )
    session = _FakeSession([
        _Result([biz]),
        _Result([]),
        _Result([]),
        _Result([]),
        _Result([]),
    ])
    user = TokenPayload(sub="user-1", tenant_id="tenant-1", role="viewer")

    response = await get_health_scores(session, user, days=7)

    assert response.services[0].service_id == "biz-1"
    assert response.services[0].dimensions[-1].score == 50
    assert response.services[0].dimensions[-1].detail == "权重 5/10"
