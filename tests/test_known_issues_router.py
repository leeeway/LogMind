from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from logmind.core.security import TokenPayload
from logmind.domain.analysis import known_issues_router


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    async def execute(self, _stmt):
        return _Rows([("biz-1",)])


class _FakeIndices:
    def __init__(self, *, exists_result=True, exists_exc=None):
        self._exists_result = exists_result
        self._exists_exc = exists_exc

    async def exists(self, index):
        if self._exists_exc:
            raise self._exists_exc
        return self._exists_result


class _FakeES:
    def __init__(self, *, exists_result=True, exists_exc=None, search_result=None):
        self.indices = _FakeIndices(exists_result=exists_result, exists_exc=exists_exc)
        self._search_result = search_result or {
            "hits": {"total": {"value": 0}, "hits": []},
        }

    async def search(self, index, body):
        return self._search_result


def _user():
    return TokenPayload(sub="user-1", tenant_id="tenant-1", role="viewer")


async def _list_known_issues():
    return await known_issues_router.list_known_issues(
        session=_FakeSession(),
        user=_user(),
        status=None,
        severity=None,
        business_line_id=None,
        search=None,
        sort_by="last_seen",
        sort_order="desc",
        page=1,
        page_size=15,
    )


@pytest.mark.asyncio
async def test_list_known_issues_returns_empty_when_index_exists_check_fails(monkeypatch):
    monkeypatch.setattr(
        "logmind.domain.log.service.log_service",
        SimpleNamespace(es=_FakeES(exists_exc=RuntimeError("es unavailable"))),
    )

    response = await _list_known_issues()

    assert response.items == []
    assert response.total == 0


@pytest.mark.asyncio
async def test_list_known_issues_accepts_legacy_total_integer(monkeypatch):
    monkeypatch.setattr(
        "logmind.domain.log.service.log_service",
        SimpleNamespace(es=_FakeES(search_result={
            "hits": {
                "total": 1,
                "hits": [{
                    "_id": "issue-1",
                    "_source": {
                        "business_line_id": "biz-1",
                        "error_signature": "timeout",
                        "last_seen": "2026-07-01T06:16:54Z",
                    },
                }],
            },
        })),
    )

    response = await _list_known_issues()

    assert response.total == 1
    assert response.items[0].id == "issue-1"


@pytest.mark.asyncio
async def test_list_known_issues_serializes_datetime_source_fields(monkeypatch):
    seen = datetime(2026, 7, 1, 6, 16, 54, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "logmind.domain.log.service.log_service",
        SimpleNamespace(es=_FakeES(search_result={
            "hits": {
                "total": {"value": 1},
                "hits": [{
                    "_id": "issue-1",
                    "_source": {
                        "business_line_id": "biz-1",
                        "error_signature": "timeout",
                        "hit_count": None,
                        "first_seen": seen,
                        "last_seen": seen,
                        "created_at": seen,
                    },
                }],
            },
        })),
    )

    response = await _list_known_issues()

    assert response.items[0].hit_count == 1
    assert response.items[0].first_seen == "2026-07-01T06:16:54+00:00"
    assert response.items[0].last_seen == "2026-07-01T06:16:54+00:00"
