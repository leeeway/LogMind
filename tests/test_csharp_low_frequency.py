import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from logmind.domain.analysis.fingerprint_stage import (
    ErrorFingerprintStage,
    _generate_fingerprint,
    delivered_unchanged,
    mark_delivered,
)
from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis.stages.log_preprocess import LogPreprocessStage
from logmind.domain.analysis.stages.quality_filter import LogQualityFilterStage
from logmind.domain.anomaly.detector import AnomalyDetector
from logmind.domain.log.csharp import parse_dotnet

CONTENT = (
    "获取用户基本信息Account_sdk异常信息为:System.NullReferenceException: "
    "Object reference not set to an instance of an object. "
    "at Newtonsoft.Json.Serialization.DefaultContractResolver.CreateContract(Type objectType) "
    "at Newtonsoft.Json.JsonConvert.DeserializeObject[T](String value, "
    "JsonSerializerSettings settings) "
    "at Gyyx.Module.Account.SDK.AccountProvider.GetUser(AccountIn model)"
)
PAYLOAD = {
    "level": "ERROR",
    "methodName": "AccountProvider.GetUser",
    "content": CONTENT,
    "traceId": None,
}
SAMPLE = "2026-09-12 12:57:54,307 [6] ERROR LogModule - " + json.dumps(PAYLOAD, ensure_ascii=False)


def context():
    return PipelineContext(
        tenant_id="t",
        task_id="task",
        business_line_id="biz",
        language="csharp",
        log_count=1,
        processed_logs=SAMPLE,
    )


@pytest.mark.parametrize(
    "message", [SAMPLE, json.dumps(PAYLOAD), CONTENT, CONTENT.replace(" at ", "\n at ")]
)
def test_exact_example_and_formats(message):
    event = parse_dotnet(message)
    assert event.concrete_fault
    assert "System.NullReferenceException" in event.exceptions
    assert "GetUser" in event.method
    assert "CreateContract" in event.message
    assert "traceId" not in event.message


def test_inner_level_and_malformed_json():
    assert LogPreprocessStage._extract_level({"message": json.dumps(PAYLOAD)}) == "ERROR"
    broken = SAMPLE[:-1]
    assert CONTENT in parse_dotnet(broken).message
    assert parse_dotnet(broken).concrete_fault


@pytest.mark.parametrize(
    "message",
    [
        "[ERROR] 参数错误",
        "[ERROR] 执行成功",
        "System.ArgumentException: bad input at Account.Validate(String value)",
        "java.lang.NullPointerException: null at com.app.Service.run(Service.java:1)",
    ],
)
def test_non_dotnet_or_expected_errors_do_not_bypass_volume(message):
    assert not parse_dotnet(message).concrete_fault


def test_success_words_do_not_swallow_real_fault():
    assert not LogQualityFilterStage._is_business_noise("执行成功 请求成功 " + CONTENT)


def test_stable_fingerprint_and_method_separation():
    newer = SAMPLE.replace("12:57:54,307 [6]", "13:01:20,100 [28]")
    assert _generate_fingerprint("b", SAMPLE) == _generate_fingerprint("b", newer)
    assert _generate_fingerprint("b", SAMPLE) != _generate_fingerprint(
        "b", SAMPLE.replace("GetUser", "GetAccount")
    )
    assert _generate_fingerprint("b", SAMPLE) != _generate_fingerprint("c", SAMPLE)
    formatted = "[2026-09-12T04:58:04Z] [ERROR] [host:TM3298] " + parse_dotnet(SAMPLE).message
    assert _generate_fingerprint("b", SAMPLE) == _generate_fingerprint("b", formatted)


@pytest.mark.asyncio
async def test_one_error_triggers_diagnosis_not_volume(monkeypatch):
    es = SimpleNamespace(
        search=AsyncMock(
            side_effect=[
                {"hits": {"total": {"value": 1}}},
                {"aggregations": {"timeline": {"buckets": []}}},
                {
                    "hits": {
                        "hits": [{"_id": "doc", "_index": "site", "_source": {"message": SAMPLE}}]
                    }
                },
            ]
        )
    )
    monkeypatch.setattr("logmind.core.elasticsearch.get_es_client", lambda: es)
    result = await AnomalyDetector().detect("site", inspect_concrete_faults=True)
    assert result.is_anomaly and result.trigger == "concrete_exception"
    assert result.current_errors == result.concrete_faults == 1
    assert result.evidence_refs == [{"index": "site", "id": "doc"}]
    assert es.search.call_args_list[0].kwargs["body"]["track_total_hits"] is True
    histogram = es.search.call_args_list[1].kwargs["body"]["aggs"]["timeline"]["date_histogram"]
    assert histogram["min_doc_count"] == 0 and "extended_bounds" in histogram


@pytest.mark.asyncio
async def test_es_failure_not_reported_as_zero_or_normal(monkeypatch):
    es = SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr("logmind.core.elasticsearch.get_es_client", lambda: es)
    result = await AnomalyDetector().detect("site")
    assert result.detection_failed
    assert not result.is_anomaly


@pytest.mark.asyncio
async def test_exact_count_above_es_default_cap():
    es = SimpleNamespace(search=AsyncMock(return_value={"hits": {"total": {"value": 15001}}}))
    now = datetime.now(UTC)
    count = await AnomalyDetector()._count_errors(es, "site", now, now)
    assert count == 15001
    assert es.search.call_args.kwargs["body"]["track_total_hits"] is True


@pytest.mark.asyncio
async def test_delivery_only_fingerprints_after_success(monkeypatch):
    memory = {}

    async def get(key):
        return memory.get(key)

    async def setex(key, ttl, value):
        memory[key] = value

    redis = SimpleNamespace(get=get, setex=setex, expire=AsyncMock())
    monkeypatch.setattr("logmind.core.redis.get_redis_client", lambda: redis)
    ctx = context()
    ctx.priority_decision = {"priority": "P1"}
    await ErrorFingerprintStage().execute(ctx)
    assert not memory  # Analysis started, failed, or notification pending.
    assert not await delivered_unchanged(ctx)
    await mark_delivered(ctx)
    assert await delivered_unchanged(ctx)
    ctx.log_count = 2
    key = ctx.log_metadata["fingerprint_keys"][0]
    ctx.log_metadata["fingerprint_counts"][key] = 2
    assert not await delivered_unchanged(ctx)
    ctx.log_count = 1
    ctx.log_metadata["fingerprint_counts"][key] = 1
    ctx.log_metadata["matched_count"] = 1000
    assert await delivered_unchanged(ctx)  # Unrelated scan volume is not impact.
    ctx.priority_decision["priority"] = "P0"
    assert not await delivered_unchanged(ctx)
    memory.clear()  # Recovered/expired event may alert again.
    assert not await delivered_unchanged(ctx)


def test_checkpoint_does_not_store_raw_logs_or_credentials():
    from logmind.domain.analysis.delivery import restore, snapshot

    ctx = context()
    ctx.raw_logs = [{"password": "secret"}]
    ctx.system_prompt = "private prompt"
    data = snapshot(ctx)
    assert "raw_logs" not in data and "system_prompt" not in data
    assert restore(data).task_id == ctx.task_id


@pytest.mark.asyncio
async def test_scroll_reaches_rare_error_after_10000_candidates():
    es = SimpleNamespace(
        search=AsyncMock(
            return_value={
                "_scroll_id": "s",
                "hits": {
                    "hits": [
                        {"_id": str(i), "_source": {"message": "[ERROR] 参数错误"}}
                        for i in range(10000)
                    ]
                },
            }
        ),
        scroll=AsyncMock(
            side_effect=[
                {
                    "_scroll_id": "s",
                    "hits": {"hits": [{"_id": "rare", "_source": {"message": SAMPLE}}]},
                },
                {"_scroll_id": "s", "hits": {"hits": []}},
            ]
        ),
        clear_scroll=AsyncMock(),
    )
    now = datetime.now(UTC)
    count, refs = await AnomalyDetector()._concrete_faults(es, "site", now, now)
    assert count == 1 and refs[0]["id"] == "rare"
    es.clear_scroll.assert_awaited_once_with(scroll_id="s")
