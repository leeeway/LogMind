import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from logmind.domain.http_access.models import (
    AccessBaseline,
    AccessIncident,
    AccessMetric,
    AccessRecovery,
    AccessRouteMetric,
    AccessSample,
    aggregate_metrics,
    detect_incidents,
    detect_route_incidents,
    is_allowed_site,
    normalize_request,
)
from logmind.domain.http_access.router import get_http_access_patrol_status
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


def test_latency_requires_a_nonzero_ready_baseline():
    window = aggregate_metrics(
        [
            AccessMetric(
                source="nginx",
                site="activity.gyyx.cn",
                minute=_utc(),
                request_count=500,
                p95_ms=3200,
            )
        ]
    )

    assert detect_incidents(window, {}) == []
    assert detect_incidents(
        window,
        {
            ("nginx", "activity.gyyx.cn"): AccessBaseline(
                source="nginx",
                site="activity.gyyx.cn",
                sample_count=100,
                request_count=500,
                p95_ms=0,
            )
        },
    ) == []


def test_latency_ignores_windows_dominated_by_4xx():
    window = aggregate_metrics(
        [
            AccessMetric(
                source="nginx",
                site="zeus-mobile-ops.gyyx.cn",
                minute=_utc(),
                request_count=1000,
                status_4xx=900,
                p95_ms=56045,
            )
        ]
    )
    baseline = AccessBaseline(
        source="nginx",
        site="zeus-mobile-ops.gyyx.cn",
        sample_count=100,
        request_count=1000,
        p95_ms=500,
    )

    assert detect_incidents(
        window,
        {("nginx", "zeus-mobile-ops.gyyx.cn"): baseline},
    ) == []


def test_route_4xx_detects_concentration_hidden_by_site_traffic():
    incidents = detect_route_incidents(
        [
            AccessRouteMetric(
                source="nginx",
                site="api.qibao.tjlong.cn",
                route_key="GET /notice/noread/{uuid}/",
                request_count=116,
                status_4xx=19,
                p95_ms=2,
            ),
            AccessRouteMetric(
                source="nginx",
                site="api.qibao.tjlong.cn",
                route_key="GET /healthy",
                request_count=5000,
                status_4xx=0,
                p95_ms=8,
            ),
        ]
    )

    assert len(incidents) == 1
    assert incidents[0].route_key == "GET /notice/noread/{uuid}/"
    assert incidents[0].current_value == 19 / 116
    assert "GET /notice/noread/{uuid}/" in incidents[0].key


def test_route_4xx_ignores_single_400():
    assert detect_route_incidents(
        [
            AccessRouteMetric(
                source="nginx",
                site="api.qibao.tjlong.cn",
                route_key="GET /notice/noread/{uuid}/",
                request_count=1,
                status_4xx=1,
            )
        ]
    ) == []


def test_route_4xx_ignores_known_security_probe_paths():
    assert detect_route_incidents(
        [
            AccessRouteMetric(
                source="nginx",
                site="zeus-mobile-ops.gyyx.cn",
                route_key="GET /nuclei.svg",
                request_count=100,
                status_4xx=100,
            )
        ]
    ) == []


def test_route_4xx_ignores_site_dominated_by_rejected_scan_traffic():
    metric = AccessRouteMetric(
        source="nginx",
        site="zeus-mobile-ops.gyyx.cn",
        route_key="GET /index.php",
        request_count=72,
        status_4xx=72,
    )
    windows = {
        ("nginx", "zeus-mobile-ops.gyyx.cn"): aggregate_metrics(
            [
                AccessMetric(
                    source="nginx",
                    site="zeus-mobile-ops.gyyx.cn",
                    minute=_utc(),
                    request_count=13938,
                    status_4xx=13684,
                )
            ]
        )[("nginx", "zeus-mobile-ops.gyyx.cn")]
    }

    assert detect_route_incidents([metric], windows) == []


class _FakeIndices:
    async def exists(self, **_kwargs):
        return False


class _ExistingIndices:
    async def exists(self, **_kwargs):
        return True


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
                                "successful": {
                                    "latency": {
                                        "values": {
                                            "50.0": 10.0,
                                            "95.0": 50.0,
                                            "99.0": 100.0,
                                        }
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
    assert body["aggs"]["by_minute_site"]["aggs"]["successful"]["filter"] == {
        "range": {"lm_status_code": {"gte": 200, "lt": 400}}
    }


def test_route_aggregation_handles_many_server_names_in_one_query():
    es = _FakeEs(
        [
            {
                "aggregations": {
                    "by_site_route": {
                        "buckets": [
                            {
                                "key": {
                                    "site": "api.qibao.tjlong.cn",
                                    "route": "GET /notice/noread/{uuid}/",
                                },
                                "doc_count": 49,
                                "status_4xx": {"doc_count": 49},
                                "status_5xx": {"doc_count": 0},
                                "latency": {"values": {"95.0": 2.0}},
                            },
                            {
                                "key": {
                                    "site": "pigeon.gyyx.cn",
                                    "route": "GET /alarm/current/list",
                                },
                                "doc_count": 1000,
                                "status_4xx": {"doc_count": 0},
                                "status_5xx": {"doc_count": 0},
                                "latency": {"values": {"95.0": 6.0}},
                            },
                        ]
                    }
                }
            }
        ]
    )
    service = HttpAccessService(es=es)

    metrics = asyncio.run(
        service._collect_route_source(
            index_name="nginx-log-json",
            source="nginx",
            sites=["api.qibao.tjlong.cn", "pigeon.gyyx.cn"],
            time_from=_utc(),
            time_to=_utc() + timedelta(minutes=5),
        )
    )

    assert len(metrics) == 2
    assert len(es.search_calls) == 1
    body = es.search_calls[0]["body"]
    assert body["query"]["bool"]["filter"][1]["terms"][
        "server_name.keyword"
    ] == ["api.qibao.tjlong.cn", "pigeon.gyyx.cn"]
    assert "lm_route_key" in body["runtime_mappings"]


def test_baseline_uses_same_time_slots_and_medians(monkeypatch):
    es = _FakeEs(
        [
            {
                "aggregations": {
                    "by_source_site": {
                        "buckets": [
                            {
                                "key": {
                                    "source": "nginx",
                                    "site": "creator-ops.gyyx.cn",
                                },
                                "samples": {"value": 240},
                                "request_median": {
                                    "values": {"50.0": 20.0}
                                },
                                "request_sum": {"value": 2400},
                                "status_4xx_sum": {"value": 24},
                                "status_5xx_sum": {"value": 12},
                                "p95_median": {
                                    "values": {"50.0": 180.0}
                                },
                            }
                        ]
                    }
                }
            }
        ]
    )
    es.indices = _ExistingIndices()
    monkeypatch.setattr(
        "logmind.domain.http_access.service.get_settings",
        lambda: SimpleNamespace(
            http_access_metrics_index="logmind-http-access-metrics-v1",
            http_access_baseline_slot_minutes=60,
        ),
    )
    service = HttpAccessService(es=es)

    baselines = asyncio.run(
        service.load_baselines(
            before=_utc(hour=7, minute=45),
            window_minutes=5,
            days=7,
        )
    )

    baseline = baselines[("nginx", "creator-ops.gyyx.cn")]
    assert baseline.request_count == 100
    assert baseline.p95_ms == 180
    assert baseline.rate_5xx == 0.005
    assert baseline.sample_count == 4
    assert baseline.is_ready
    query = es.search_calls[0]["body"]["query"]["bool"]
    assert len(query["should"]) == 7
    first_range = query["should"][0]["range"]["minute"]
    assert first_range["gte"] == "2026-07-29T07:15:00+00:00"
    assert first_range["lt"] == "2026-07-29T08:15:00+00:00"


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
            route_keys=["GET /notice/noread/{uuid}/"],
        )
    )

    assert samples[0].route == "/notice/noread/{uuid}/"
    assert "secret" not in samples[0].route
    source_fields = es.search_calls[0]["body"]["_source"]
    assert "remote_addr" not in source_fields
    assert "client_ip" not in source_fields
    assert "request_body" not in source_fields
    assert "http_Authorization" not in source_fields
    body = es.search_calls[0]["body"]
    assert body["query"]["bool"]["filter"][-1] == {
        "terms": {
            "lm_route_key": ["GET /notice/noread/{uuid}/"]
        }
    }


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

    assert message.count("HTTP访问告警") == 1
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


def test_route_notification_names_each_interface_without_redundant_main_route():
    incidents = [
        AccessIncident(
            source="nginx",
            site="api.qibao.tjlong.cn",
            kind="route_4xx",
            priority="P1",
            request_count=219,
            current_value=22 / 219,
            baseline_value=0,
            status_4xx=22,
            route_key="POST /statistics/operatorrecord",
        ),
        AccessIncident(
            source="nginx",
            site="api.qibao.tjlong.cn",
            kind="route_4xx",
            priority="P1",
            request_count=116,
            current_value=19 / 116,
            baseline_value=0,
            status_4xx=19,
            route_key="GET /notice/noread/{uuid}/",
        ),
    ]

    message = build_http_access_notification(
        incidents,
        [],
        time_from=_utc(),
        time_to=_utc() + timedelta(minutes=5),
    )

    assert "接口异常: POST /statistics/operatorrecord，4xx 22/219" in message
    assert "接口异常: GET /notice/noread/{uuid}/，4xx 19/116" in message
    assert "- 主要接口:" not in message


class _FakeRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, _ttl, value):
        self.values[key] = value

    async def set(self, key, value, *, ex=None, nx=False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, _script, _numkeys, key, token):
        if self.values.get(key) != token:
            return 0
        del self.values[key]
        return 1


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


def test_latency_requires_two_consecutive_windows():
    redis = _FakeRedis()
    state = HttpAccessAlertState(redis=redis)
    incident = AccessIncident(
        source="nginx",
        site="activity.gyyx.cn",
        kind="latency",
        priority="P1",
        request_count=500,
        current_value=3200,
        baseline_value=300,
        p95_ms=3200,
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

    asyncio.run(scenario())


def test_summary_reservation_is_atomic_and_p0_bypasses_p1_cooldown(
    monkeypatch,
):
    settings = SimpleNamespace(
        http_access_dedup_minutes=30,
        http_access_notification_cooldown_minutes=30,
    )
    monkeypatch.setattr(
        "logmind.domain.http_access.state.get_settings",
        lambda: settings,
    )
    state = HttpAccessAlertState(redis=_FakeRedis())
    p1 = AccessIncident(
        source="nginx",
        site="creator-ops.gyyx.cn",
        kind="latency",
        priority="P1",
        request_count=500,
        current_value=2200,
        baseline_value=300,
    )
    p0 = AccessIncident(
        source="nginx",
        site="api.gyyx.cn",
        kind="http_5xx",
        priority="P0",
        request_count=1000,
        current_value=0.1,
        baseline_value=0.001,
        status_5xx=100,
    )

    async def scenario():
        p1_lease = await state.reserve_summary([p1])
        assert p1_lease is not None
        assert await state.reserve_summary([p1]) is None

        await state.finish_summary(p1_lease, delivered=False)
        retry_lease = await state.reserve_summary([p1])
        assert retry_lease is not None
        await state.finish_summary(retry_lease, delivered=True)
        assert await state.reserve_summary([p1]) is None

        p0_lease = await state.reserve_summary([p0])
        assert p0_lease is not None
        assert await state.reserve_summary([p0]) is None

    asyncio.run(scenario())


def test_patrol_lease_and_run_snapshot_are_shared_in_redis():
    state = HttpAccessAlertState(redis=_FakeRedis())

    async def scenario():
        lease = await state.acquire_patrol_lease(ttl_seconds=300)
        assert lease is not None
        assert await state.acquire_patrol_lease(ttl_seconds=300) is None
        await state.release_patrol_lease(lease)
        assert await state.acquire_patrol_lease(ttl_seconds=300) is not None

        snapshot = {
            "run_status": "normal",
            "metric_count": 12,
            "notification_sent": False,
        }
        await state.save_run_snapshot(snapshot)
        assert await state.get_run_snapshot() == snapshot

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


class _RecoveryOnlyState(_ShadowState):
    async def evaluate(self, _incidents, **kwargs):
        from logmind.domain.http_access.state import AccessNotificationBatch

        return AccessNotificationBatch(
            due=[],
            recoveries=[
                AccessRecovery(
                    source="nginx",
                    site="api.elves.gyyx.cn",
                    kind="latency",
                    priority="P1",
                )
            ],
            next_state={},
            previous_state={},
            evaluated_at=kwargs["now"],
        )


def test_recovery_notification_is_disabled_by_default(monkeypatch):
    settings = SimpleNamespace(
        http_access_window_minutes=5,
        http_access_baseline_days=7,
        http_access_notification_enabled=True,
    )
    monkeypatch.setattr(
        "logmind.domain.http_access.tasks.get_settings",
        lambda: settings,
    )
    sent_messages = []

    async def fake_send(message):
        sent_messages.append(message)
        return True

    monkeypatch.setattr(
        "logmind.domain.http_access.tasks._send_notification",
        fake_send,
    )

    result = asyncio.run(
        _run_http_access_patrol(
            now=_utc(),
            service=_ShadowService(),
            alert_state=_RecoveryOnlyState(),
        )
    )

    assert sent_messages == []
    assert result["notification_sent"] is False
    assert result["recovery_count"] == 1
    assert result["notification_recovery_count"] == 0


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


def test_http_access_status_exposes_safe_settings_and_last_run(monkeypatch):
    settings = SimpleNamespace(
        http_access_patrol_enabled=True,
        http_access_notification_enabled=False,
        http_access_recovery_notification_enabled=False,
        http_access_ai_enabled=True,
        http_access_window_minutes=5,
        http_access_notification_cooldown_minutes=30,
        http_access_baseline_days=7,
        http_access_baseline_slot_minutes=60,
        http_access_index_list=(
            "nginx-log-json",
            "ingress-nginx-master-external-log",
        ),
    )
    monkeypatch.setattr(
        "logmind.domain.http_access.router.get_settings",
        lambda: settings,
    )

    async def fake_snapshot():
        return {
            "run_status": "normal",
            "metric_count": 42,
        }

    monkeypatch.setattr(
        "logmind.domain.http_access.router."
        "http_access_alert_state.get_run_snapshot",
        fake_snapshot,
    )

    status = asyncio.run(get_http_access_patrol_status())

    assert status["mode"] == "shadow"
    assert status["baseline"] == {
        "days": 7,
        "same_time_slot_minutes": 60,
    }
    assert status["last_run"]["metric_count"] == 42
    assert "webhook_url" not in status
    assert "tenant_id" not in status
