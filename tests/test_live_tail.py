from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from logmind.domain.log import live_tail


def test_live_tail_lookback_is_parsed_and_clamped():
    assert live_tail._clamp_lookback_seconds(None) == 300
    assert live_tail._clamp_lookback_seconds("900") == 900
    assert live_tail._clamp_lookback_seconds(1) == 30
    assert live_tail._clamp_lookback_seconds(99999) == 3600


@pytest.mark.asyncio
async def test_initial_live_tail_fetch_returns_newest_logs_in_display_order(monkeypatch):
    es = AsyncMock()
    es.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "new",
                    "_source": {
                        "@timestamp": "2026-07-30T10:00:02Z",
                        "message": (
                            "2026-07-30 18:00:02,001 [17] ERROR "
                            "Gyyx.Payment - payment failed"
                        ),
                        "gy": {"domain": "gpay.tjlong.cn", "filetype": "sys.log.txt"},
                    },
                },
                {
                    "_id": "old",
                    "_source": {
                        "@timestamp": "2026-07-30T10:00:01Z",
                        "message": (
                            "2026-07-30 18:00:01,001 [17] INFO "
                            "Gyyx.Payment - request accepted"
                        ),
                        "gy": {"domain": "gpay.tjlong.cn", "filetype": "sys.log.txt"},
                    },
                },
            ]
        }
    }
    monkeypatch.setattr(live_tail, "get_es_client", lambda: es)

    logs, cursor, error = await live_tail._fetch_latest_logs(
        "logs-gpay-*",
        datetime(2026, 7, 30, 9, 55, tzinfo=timezone.utc),
        newest_first=True,
    )

    assert error is None
    assert [log["id"] for log in logs] == ["old", "new"]
    assert [log["level"] for log in logs] == ["INFO", "ERROR"]
    assert logs[0]["source"] == "gpay.tjlong.cn"
    assert cursor == datetime(2026, 7, 30, 10, 0, 2, tzinfo=timezone.utc)
    body = es.search.await_args.kwargs["body"]
    assert body["sort"][0]["@timestamp"]["order"] == "desc"
    assert body["size"] == live_tail.MAX_LOGS_PER_PUSH


@pytest.mark.asyncio
async def test_live_tail_uses_canonical_warning_filter(monkeypatch):
    es = AsyncMock()
    es.search.return_value = {"hits": {"hits": []}}
    monkeypatch.setattr(live_tail, "get_es_client", lambda: es)

    _, _, error = await live_tail._fetch_latest_logs(
        "logs-*",
        datetime(2026, 7, 30, tzinfo=timezone.utc),
        filters={"level": "warning", "keyword": "Access is denied"},
    )

    assert error is None
    body = es.search.await_args.kwargs["body"]
    clauses = body["query"]["bool"]["must"]
    assert any(
        clause.get("bool", {}).get("should")
        and {"term": {"gy.filetype.keyword": "warn.log"}}
        in clause["bool"]["should"]
        for clause in clauses
    )
    assert any(
        clause.get("bool", {}).get("minimum_should_match") == 1
        and any("match_phrase" in item for item in clause["bool"]["should"])
        for clause in clauses
    )
