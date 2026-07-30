import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from logmind.domain.http_access.models import (
    AccessBaseline,
    AccessIncident,
    AccessMetric,
    AccessSample,
    aggregate_metrics,
    detect_incidents,
    is_allowed_site,
    normalize_request,
)
from logmind.domain.http_access.service import HttpAccessService
from logmind.domain.http_access.state import HttpAccessAlertState
from logmind.domain.http_access.tasks import (
    _parse_ai_summaries,
    _run_http_access_patrol,
    build_http_access_notification,
)


def _utc(hour=14, minute=0):
    return datetime(2026, 7, 30, hour, minute, tzinfo=UTC)


def test_normalize_request_removes_sensitive_query_and_resource_ids():
    method, route = normalize_request(
        "GET /notice/noread/4547978a-cb6b-45d3-b0db-58ec0ee4b614/"
        "?timestamp=1785391556&sign=71783a9d8df6a0ef2209e508fa3d9ede HTTP/1.1"
    )

    assert method == "GET"
    assert route == "/notice/noread/{uuid}/"
    assert "sign" not in route
    assert "1785391556" not in route


def test_site_filter_rejects_scanner_hosts_and_keeps_enterprise_domains():
    suffixes = ("gyyx.cn", "tjlong.cn", "wyx.cn", "costrip.cn")

    assert is_allowed_site("api.qibao.tjlong.cn", suffixes)
    assert is_allowed_site("pigeon.gyyx.cn", suffixes)
    assert not is_allowed_site("_", suffixes)
    assert not is_allowed_site("127.0.0.1:80", suffixes)
    assert not is_allowed_site("www.google.com", suffixes)


def test_single_400_and_normal_200_do_not_trigger_incidents():
    now = _utc()
    metrics = [
        AccessMetric(
            source="nginx",
            site="api.qibao.tjlong.cn",
            minute=now,
            request_count=1,
            status_4xx=1,
            p95_ms=1,
        ),
        AccessMetric(
            source="ingress",
            site="pigeon.gyyx.cn",
            minute=now,
            request_count=1,
            p95_ms=6,
        ),
    ]

    assert detect_incidents(aggregate_metrics(metrics), {}) == []


def test_detects_5xx_latency_and_uses_source_specific_baselines():
    now = _utc()
    metrics = [
        AccessMetric(
            source="nginx",
            site="api.qibao.tjlong.cn",
            minute=now,
            request_count=1000,
            status_5xx=70,
            gateway_5xx=50,
            p95_ms=2800,
        ),
        AccessMetric(
            source="ingress",
            site="api.qibao.tjlong.cn",
            minute=now,
            request_count=1000,
            status_5xx=2,
            p95_ms=100,
        ),
    ]
    baselines = {
        ("nginx", "api.qibao.tjlong.cn"): AccessBaseline(
            source="nginx",
            site="api.qibao.tjlong.cn",
            sample_count=100,
            request_count=1000,
            rate_5xx=0.001,
            p95_ms=200,
        ),
        ("ingress", "api.qibao.tjlong.cn"): AccessBaseline(
            source="ingress",
            site="api.qibao.tjlong.cn",
            sample_count=100,
            request_count=1000,
            rate_5xx=0.001,
            p95_ms=100,
        ),
    }

    incidents = detect_incidents(aggregate_metrics(metrics), baselines)

    assert {(item.source, item.kind) for item in incidents} == {
        ("nginx", "http_5xx"),
        ("nginx", "latency"),
    }
    assert all(item.priority in {"P0", "P1"} for item in incidents)


class _FakeIndices:
    async def exists(self, **_kwargs):
        return False


class _FakeEs:
    def __init__(self, responses):
        self.responses = list(responses)
        self.search_calls = []
        self.indices = _FakeIndices()

    async def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return self.responses.pop(0)


def test_composite_aggregation_counts_more_than_ten_thousand_documents():
    es = _FakeEs(
        [
            {
                "aggregations": {
                    "by_minute_site": {
                        "buckets": [
                            {
                                "key": {
                                    "minute": int(_utc().timestamp() * 1000),
                                    "site": "api.qibao.tjlong.cn",
                                },
                                "doc_count": 15001,
                                "status_4xx": {"doc_count": 0},
                                "status_5xx": {"doc_count": 40},
                                "gateway_5xx": {"doc_count": 20},
                                "upstream_5xx": {"doc_count": 10},
                                "latency": {
                                    "values": {
                                        "50.0": 10.0,
                                        "95.0": 50.0,
                                        "99.0": 100.0,
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        ]
    )
    service = HttpAccessService(es=es)

    metrics = asyncio.run(
        service._collect_source(
            index_name="nginx-log-json",
            source="nginx",
            time_from=_utc(),
            time_to=_utc() + timedelta(minutes=1),
            allowed_suffixes=("tjlong.cn",),
        )
    )

    assert metrics[0].request_count == 15001
    body = es.search_calls[0]["body"]
    assert body["size"] == 0
    assert "lm_status_code" in body["runtime_mappings"]
    assert (
        body["aggs"]["by_minute_site"]["composite"]["size"]
        == 1000
    )


def test_sample_fetch_never_requests_ip_or_sensitive_body(monkeypatch):
    es = _FakeEs(
        [
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-07-30T06:05:56.959Z",
                                "status": "400",
                                "request": (
                                    "GET /notice/noread/4547978a-cb6b-45d3-b0db-"
                                    "58ec0ee4b614/?sign=secret HTTP/1.1"
                                ),
                                "request_time": "0.001",
                                "upstream_status": "400",
                                "upstream_response_time": "0.002",
                                "upstream_addr": "10.70.83.16:80",
                                "remote_addr": "101.82.83.122",
                            }
                        }
                    ]
                }
            }
        ]
    )
    monkeypatch.setattr(
        "logmind.domain.http_access.service.get_settings",
        lambda: SimpleNamespace(http_access_index_list=("nginx-log-json",)),
    )
    service = HttpAccessService(es=es)

    samples = asyncio.run(
        service.fetch_samples(
            source="nginx",
            site="api.qibao.tjlong.cn",
            time_from=_utc(),
            time_to=_utc() + timedelta(minutes=5),
            prefer_latency=False,
        )
    )

    assert samples[0].route == "/notice/noread/{uuid}/"
    assert "secret" not in samples[0].route
    source_fields = es.search_calls[0]["body"]["_source"]
    assert "remote_addr" not in source_fields
    assert "client_ip" not in source_fields
    assert "request_body" not in source_fields
    assert "http_Authorization" not in source_fields


def test_ai_summary_rejects_unsupported_database_claims():
    content = (
        '{"items":['
        '{"key":"nginx|a.gyyx.cn|http_5xx","summary":"数据库写入失败"},'
        '{"key":"nginx|b.gyyx.cn|http_5xx","summary":"upstream返回大量502"}'
        "]}"
    )

    assert _parse_ai_summaries(content) == {
        "nginx|b.gyyx.cn|http_5xx": "upstream返回大量502"
    }


def test_notification_groups_sites_and_omits_internal_metadata_and_secrets():
    sample = AccessSample(
        timestamp="2026-07-30T06:05:56Z",
        method="GET",
        route="/notice/noread/{uuid}/",
        status=502,
        request_time_ms=2800,
        upstream_status=502,
        upstream_time_ms=2700,
        upstream_addr="10.70.83.16:80",
    )
    incidents = [
        AccessIncident(
            source="nginx",
            site="api.qibao.tjlong.cn",
            kind="http_5xx",
            priority="P0",
            request_count=1000,
            current_value=0.062,
            baseline_value=0.0008,
            status_5xx=62,
            upstream_5xx=62,
            p95_ms=2800,
            samples=[sample],
        ),
        AccessIncident(
            source="ingress",
            site="pigeon.gyyx.cn",
            kind="latency",
            priority="P1",
            request_count=500,
            current_value=2100,
            baseline_value=180,
            p95_ms=2100,
            samples=[
                AccessSample(
                    timestamp="2026-07-30T06:05:56Z",
                    method="GET",
                    route="/alarm/current/list",
                    status=200,
                    request_time_ms=2100,
                )
            ],
        ),
    ]

    message = build_http_access_notification(
        incidents,
        [],
        time_from=_utc(),
        time_to=_utc() + timedelta(minutes=5),
    )

    assert message.count("HTTP访问异常汇总") == 1
    assert "api.qibao.tjlong.cn" in message
    assert "pigeon.gyyx.cn" in message
    assert "GET /notice/noread/{uuid}/" in message
    assert "通知原因" not in message
    assert "分析入口" not in message
    assert "任务ID" not in message
    assert "sign=" not in message
    assert "101.82.83.122" not in message


def test_same_site_from_nginx_and_ingress_counts_as_one_site():
    from logmind.domain.http_access.tasks import _count_priority_sites

    incidents = [
        AccessIncident(
            source=source,
            site="api-tong.gyyx.cn",
            kind="http_5xx",
            priority="P1",
            request_count=1000,
            current_value=0.02,
            baseline_value=0.001,
            status_5xx=20,
        )
        for source in ("nginx", "ingress")
    ]

    assert _count_priority_sites(incidents) == (0, 1)


class _FakeRedis:
    def __init__(self):
        self.value = None

    async def get(self, _key):
        return self.value

    async def setex(self, _key, _ttl, value):
        self.value = value


def test_traffic_drop_requires_two_windows_and_recovery_requires_two():
    redis = _FakeRedis()
    state = HttpAccessAlertState(redis=redis)
    incident = AccessIncident(
        source="nginx",
        site="api.qibao.tjlong.cn",
        kind="traffic_drop",
        priority="P1",
        request_count=10,
        current_value=10,
        baseline_value=1000,
    )

    async def scenario():
        first = await state.evaluate([incident], now=_utc())
        assert first.due == []
        await state.save(first, delivered=True)

        second = await state.evaluate(
            [incident],
            now=_utc() + timedelta(minutes=5),
        )
        assert [item.key for item in second.due] == [incident.key]
        await state.save(second, delivered=True)

        first_normal = await state.evaluate(
            [],
            now=_utc() + timedelta(minutes=10),
        )
        assert first_normal.recoveries == []
        await state.save(first_normal, delivered=True)

        second_normal = await state.evaluate(
            [],
            now=_utc() + timedelta(minutes=15),
        )
        assert [item.key for item in second_normal.recoveries] == [incident.key]

    asyncio.run(scenario())


class _ShadowService:
    async def collect_window(self, _time_from, _time_to):
        return []

    async def load_baselines(self, **_kwargs):
        return {}

    async def persist_metrics(self, _metrics):
        return 0


class _ShadowState:
    async def evaluate(self, _incidents, **kwargs):
        from logmind.domain.http_access.state import AccessNotificationBatch

        return AccessNotificationBatch(
            due=[],
            recoveries=[],
            next_state={},
            previous_state={},
            evaluated_at=kwargs["now"],
        )

    async def save(self, _batch, *, delivered):
        assert delivered is True


def test_global_patrol_normal_path_does_not_open_business_line_db(monkeypatch):
    settings = SimpleNamespace(
        http_access_window_minutes=5,
        http_access_baseline_days=7,
        http_access_notification_enabled=False,
    )
    monkeypatch.setattr(
        "logmind.domain.http_access.tasks.get_settings",
        lambda: settings,
    )

    result = asyncio.run(
        _run_http_access_patrol(
            now=_utc(),
            service=_ShadowService(),
            alert_state=_ShadowState(),
        )
    )

    assert result["incident_count"] == 0
    assert result["notification_sent"] is False


class _IncidentService:
    def __init__(self):
        self.metrics = [
            AccessMetric(
                source="nginx",
                site="api.qibao.tjlong.cn",
                minute=_utc(),
                request_count=1000,
                status_5xx=100,
                upstream_5xx=100,
                p95_ms=100,
            ),
            AccessMetric(
                source="ingress",
                site="pigeon.gyyx.cn",
                minute=_utc(),
                request_count=500,
                status_5xx=25,
                gateway_5xx=25,
                p95_ms=100,
            ),
        ]

    async def collect_window(self, _time_from, _time_to):
        return self.metrics

    async def load_baselines(self, **_kwargs):
        return {}

    async def persist_metrics(self, metrics):
        return len(metrics)

    async def fetch_samples(self, **kwargs):
        return [
            AccessSample(
                timestamp="2026-07-30T14:00:00Z",
                method="GET",
                route="/health",
                status=502,
                request_time_ms=100,
                upstream_status=502,
                upstream_addr=(
                    "10.0.0.1:80" if kwargs["source"] == "nginx" else ""
                ),
            )
        ]


def test_patrol_sends_exactly_one_message_for_multiple_sites(monkeypatch):
    settings = SimpleNamespace(
        http_access_window_minutes=5,
        http_access_baseline_days=7,
        http_access_notification_enabled=True,
        http_access_max_notification_sites=10,
        http_access_sample_size=20,
        http_access_ai_enabled=False,
        http_access_dedup_minutes=30,
    )
    monkeypatch.setattr(
        "logmind.domain.http_access.tasks.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "logmind.domain.http_access.state.get_settings",
        lambda: settings,
    )

    sent_messages = []

    async def fake_send(message):
        sent_messages.append(message)
        return True

    async def no_tenant():
        return None

    monkeypatch.setattr(
        "logmind.domain.http_access.tasks._send_notification",
        fake_send,
    )
    monkeypatch.setattr(
        "logmind.domain.http_access.tasks._resolve_tenant_id",
        no_tenant,
    )

    result = asyncio.run(
        _run_http_access_patrol(
            now=_utc(),
            service=_IncidentService(),
            alert_state=HttpAccessAlertState(redis=_FakeRedis()),
        )
    )

    assert result["notification_sent"] is True
    assert len(sent_messages) == 1
    assert "api.qibao.tjlong.cn" in sent_messages[0]
    assert "pigeon.gyyx.cn" in sent_messages[0]
