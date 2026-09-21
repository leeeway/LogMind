import pytest

from logmind.domain.anomaly.detector import AnomalyDetector
from logmind.domain.log.service import build_base_severity_filter


def test_severity_message_markers_use_keyword_wildcards_not_analyzed_phrases():
    predicate = build_base_severity_filter("error")
    serialized = str(predicate)
    assert "match_phrase" not in serialized
    assert "message.keyword" in serialized
    assert "*[ERROR]*" in serialized
    assert "Exception:" not in serialized


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

    result = await AnomalyDetector().detect(
        "service-*", window_minutes=5, severity_threshold="error"
    )

    assert result.is_anomaly is False
    assert len(es.calls) == 2


def test_build_severity_filter_includes_java_filetypes():
    severity_filter = AnomalyDetector._build_severity_filter("error")
    should = severity_filter["bool"]["should"]

    fallbacks = [c["bool"] for c in should if "bool" in c]
    for filename in ("error.log", "warn.log"):
        assert any(
            {"term": {"gy.filetype.keyword": {"value": filename, "case_insensitive": True}}}
            in c["filter"]
            and c["must_not"]
            for c in fallbacks
        )


def test_build_severity_filter_includes_fatal_for_critical():
    severity_filter = AnomalyDetector._build_severity_filter("critical")
    should = severity_filter["bool"]["should"]

    assert {"term": {"level.keyword": "critical"}} in should
    assert {"term": {"level.keyword": "fatal"}} in should
