import pytest

from logmind.domain.anomaly.detector import AnomalyDetector


class FakeES:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def search(self, index: str, body: dict):
        self.calls.append({"index": index, "body": body})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_detect_uses_sync_es_client_without_fallback(monkeypatch):
    es = FakeES(
        [
            {"hits": {"total": {"value": 2}}},
            {
                "aggregations": {
                    "timeline": {
                        "buckets": [
                            {"doc_count": 1},
                            {"doc_count": 1},
                            {"doc_count": 2},
                            {"doc_count": 2},
                        ]
                    }
                }
            },
        ]
    )

    monkeypatch.setattr("logmind.core.elasticsearch.get_es_client", lambda: es)

    result = await AnomalyDetector().detect("service-*", window_minutes=5, severity_threshold="error")

    assert result.is_anomaly is False
    assert len(es.calls) == 2


def test_build_severity_filter_includes_java_filetypes():
    severity_filter = AnomalyDetector._build_severity_filter("error")
    should = severity_filter["bool"]["should"]

    assert {"term": {"gy.filetype.keyword": "error.log"}} in should
    assert {"term": {"gy.filetype.keyword": "warn.log"}} in should


def test_build_severity_filter_includes_fatal_for_critical():
    severity_filter = AnomalyDetector._build_severity_filter("critical")
    should = severity_filter["bool"]["should"]

    assert {"term": {"level.keyword": "critical"}} in should
    assert {"term": {"level.keyword": "fatal"}} in should
