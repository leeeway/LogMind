import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.analysis.sensitive_masker import mask_sensitive
from logmind.domain.analysis.stages.log_fetch import LogFetchStage
from logmind.domain.analysis.stages.log_preprocess import LogPreprocessStage
from logmind.domain.log.error_signals import EXCEPTION_SIGNALS
from logmind.domain.log.events import event_key, is_concrete_fault, metadata
from logmind.domain.log.service import LogService, build_base_severity_filter


def test_python_exception_classes_use_concrete_fault_channel():
    for exception in ("RuntimeError", "TypeError", "ValueError", "AttributeError"):
        assert exception in EXCEPTION_SIGNALS


def test_multilanguage_concrete_fault_requires_stack_evidence():
    assert is_concrete_fault(
        'Traceback (most recent call last):\n File "service.py", line 12, in run\n'
        "RuntimeError: failed"
    )
    assert is_concrete_fault(
        r'{"exception":"Traceback (most recent call last):\n '
        r'File \"service.py\", line 12, in run\nRuntimeError: failed"}'
    )
    assert is_concrete_fault(
        "java.lang.IllegalStateException: failed\n at app.Service.run(Service.java:12)"
    )
    assert not is_concrete_fault("INFO retrying after RuntimeError was handled")
    assert is_concrete_fault("image request failed status_code=502 Bad Gateway")
    assert not is_concrete_fault("image request failed status_code=400 content rejected")


@pytest.mark.parametrize(
    "a,b",
    [
        ("POST /pay status=400", "POST /pay status=500"),
        ("SQLSTATE=22001 table=orders", "SQLSTATE=23000 table=orders"),
        ("failure " + "x" * 250 + " table=orders", "failure " + "x" * 250 + " table=users"),
        (
            'Traceback (most recent call last):\n File "a.py", line 1\nValueError: bad input',
            'Traceback (most recent call last):\n File "b.py", line 2\nValueError: wrong config',
        ),
        (
            "System.Exception: failed at Account.GetUser()",
            "System.Exception: failed at Account.GetOrders()",
        ),
        ("ERROR rate=1.5", "ERROR rate=2.5"),
    ],
)
def test_fault_discriminators_never_collapse(a, b):
    assert event_key(a) != event_key(b)


@pytest.mark.parametrize(
    "a,b",
    [
        (
            "2026-09-20 12:00:00.123 [http-nio-8081-exec-9] ERROR request_id=abc status=500",
            "2026-09-21 13:20:01.332 [http-nio-8081-exec-8] ERROR request_id=def status=500",
        ),
        (
            'ERROR user_id="12345" order_id=6789 failed',
            'ERROR user_id="98765" order_id=1234 failed',
        ),
        (
            "[2026-09-20T00:00:00Z] [ERROR] [host:a] [occurrences:10] upload failed",
            "[2026-09-21T00:00:00Z] [ERROR] [host:b] [occurrences:20] upload failed",
        ),
    ],
)
def test_explicit_volatile_context_collapses(a, b):
    assert event_key(a) == event_key(b)


def test_production_dynamic_transport_fields_do_not_split_incident():
    left = (
        "ERROR upload failed request_id=a client_ip=113.17.22.60 "
        "file_name=segment-a-99.webm order_id=100"
    )
    right = (
        "ERROR upload failed request_id=b client_ip=180.141.39.253 "
        "file_name=segment-b-28.webm order_id=200"
    )
    assert event_key(left) == event_key(right)


def test_endpoint_ip_does_not_split_same_connection_failure_but_port_does():
    assert event_key("connection refused 101.1.2.3:9150") == event_key(
        "connection refused 182.9.8.7:9150"
    )
    assert event_key("connection refused 101.1.2.3:9150") != event_key(
        "connection refused 101.1.2.3:3306"
    )


def test_chinese_order_labels_are_normalized():
    assert event_key("订单号:202609141611128675 交易状态:Fail") == event_key(
        "订单号:202609141703529262 交易状态:Fail"
    )


def test_runtime_counters_and_ephemeral_source_ports_do_not_split_incident():
    left = "count=1 max_execution_time=34 read tcp 10.0.0.1:38246->10.0.0.2:8123"
    right = "count=16 max_execution_time=32 read tcp 10.0.0.3:39999->10.0.0.4:8123"
    assert event_key(left) == event_key(right)


def test_object_storage_keys_keep_file_type_but_not_object_identity():
    left = "COS failed key=sc/2026/9/14/one-id.webm err=context canceled"
    right = "COS failed key=sc/2026/9/15/other-id.webm err=context canceled"
    assert event_key(left) == event_key(right)
    assert event_key(left) != event_key(
        "COS failed key=sc/2026/9/15/other-id.jpg err=context canceled"
    )


def test_shard_number_does_not_split_same_table_failure():
    assert event_key("table=upload_log_shard_17 deadlock") == event_key(
        "table=upload_log_shard_76 deadlock"
    )
    assert event_key("table=upload_log_shard_17 deadlock") != event_key(
        "table=order_log_shard_17 deadlock"
    )


@pytest.mark.parametrize(
    "text,secrets",
    [
        ("pwd=ab", ["ab"]),
        ("salt=xy", ["xy"]),
        ('password="hello world"', ["hello", "world"]),
        ("SecurityUserInfo(triEncryptPwd=abcde, biSalt=xy)", ["abcde", "xy"]),
        ("Authorization: Bearer example-credential", ["example-credential"]),
        ("Cookie: a=example-one; b=example-two", ["example-one", "example-two"]),
        ("Set-Cookie: sessionid=example-three; Path=/", ["example-three"]),
        ('prefix {"password":"short secret","nested":{"salt":"xy"}}', ["short secret", "xy"]),
        (
            "Post http://service-user:service-password@example.internal/path",
            ["service-user", "service-password"],
        ),
    ],
)
def test_credentials_fully_redacted_and_idempotent(text, secrets):
    masked = mask_sensitive(text)
    assert "[REDACTED]" in masked
    for secret in secrets:
        assert secret not in masked
    assert mask_sensitive(masked) == masked


def test_json_shape_and_escaping_preserved():
    raw = {
        "password": 'ab\\" cd',
        "items": [{"salt": "xy"}],
        "status": 500,
        "message": "pwd='secret phrase' status=400",
    }
    clean = json.loads(mask_sensitive(json.dumps(raw)))
    assert clean["password"] == clean["items"][0]["salt"] == "[REDACTED]"
    assert clean["status"] == 500
    assert "secret phrase" not in clean["message"] and "status=400" in clean["message"]


@pytest.mark.parametrize(
    "filename", ["Info.Log.txt", "info.log.txt", "INFO.LOG.TXT", "Error.Log.txt"]
)
@pytest.mark.parametrize("level", ["ERROR", "INFO", "DEBUG"])
def test_level_precedence_shared(filename, level):
    source = {
        "gy": {"filetype": filename},
        "message": f"2026-09-20 12:00:00,123 [1] {level} LogModule - event",
    }
    assert LogService._extract_level(source) == level.lower()
    assert LogPreprocessStage._extract_level(source) == level


def test_file_fallback_is_case_insensitive_and_guarded():
    query = build_base_severity_filter("error")
    rules = [x["bool"] for x in query["bool"]["should"] if "bool" in x]
    assert rules
    assert all(
        r["filter"][0]["term"]["gy.filetype.keyword"]["case_insensitive"] and r["must_not"]
        for r in rules
    )


def log(message, offset=0, host="host-a", request="same", filename="/app/error.log"):
    return {
        "message": message,
        "@timestamp": "2026-09-20T00:00:00Z",
        "_es_index": "logs-app",
        "host": {"name": host},
        "request_id": request,
        "log": {"file": {"path": filename}, "offset": offset},
    }


@pytest.mark.parametrize(
    "change", [{"host": "other"}, {"request": "different"}, {"filename": "/other.log"}]
)
def test_different_stream_or_request_never_merges(change):
    events = [log("panic: upload failed"), log("\t/app/upload.go:135", offset=100, **change)]
    assert len(LogPreprocessStage()._merge_stack_traces(events)) == 2


def test_descending_go_capture_orders_and_assembles():
    events = [
        log("panic: upload failed", 0),
        log("goroutine 123 [running]:", 50),
        log("main.upload(0xc00001)", 100),
        log("\t/app/upload.go:135", 150),
    ]
    result = LogPreprocessStage()._merge_stack_traces(list(reversed(events)))
    assert len(result) == 1
    assert result[0]["message"].endswith(".go:135")


def test_python_trace_keeps_final_exception_and_code_line():
    events = [
        log("Traceback (most recent call last):", 0),
        log('  File "app.py", line 12, in run', 40),
        log('    raise ValueError("bad")', 80),
        log("ValueError: bad", 100),
    ]
    result = LogPreprocessStage()._merge_stack_traces(events)
    assert len(result) == 1 and result[0]["message"].endswith("ValueError: bad")


def test_missing_identity_does_not_guess_stack_owner():
    result = LogPreprocessStage()._merge_stack_traces(
        [{"message": "panic: failed"}, {"message": "\t/app/x.go:5"}]
    )
    assert len(result) == 2


def test_version_source_validated_and_consistent():
    source = {
        "gy": {"podname": "pod_1.2.3"},
        "image": {"version": "2.0.0"},
        "agent": {"name": "TM1"},
    }
    assert metadata(source)["image_version"] == "2.0.0"
    assert metadata(source)["version_source"] == "image.version"
    assert metadata(source)["host_source"] == "agent.name"
    assert metadata({"gy": {"podname": "pod_not-a-version"}})["image_version"] == ""


@pytest.mark.asyncio
async def test_rolling_deployment_metadata_not_first_document_only():
    logs = [
        SimpleNamespace(
            raw={"message": "event", "gy": {"podname": "pod_1.2.3"}, "image": {"version": v}}
        )
        for v in ("2.0.0", "2.0.1")
    ]
    service = SimpleNamespace(
        search_logs=AsyncMock(return_value=SimpleNamespace(logs=logs, total=2))
    )
    now = datetime.now(UTC)
    ctx = PipelineContext(
        tenant_id="t", task_id="task", business_line_id="b", time_from=now, time_to=now
    )
    await LogFetchStage(service).execute(ctx)
    assert ctx.image_version == ""
    assert ctx.log_metadata["image_versions"] == ["2.0.0", "2.0.1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,expected", [("java", "java"), ("auto", "csharp")])
async def test_manual_language_not_overridden_and_conflict_visible(
    configured, expected, monkeypatch
):
    # Sampling budget must not reach Redis in a unit test.
    monkeypatch.setattr(
        "logmind.domain.analysis.adaptive_sampler.compute_adaptive_budget", lambda **kwargs: 20
    )
    source = log("System.NullReferenceException: failed at Account.GetUser()")
    source["gy"] = {"filetype": "Error.Log.txt"}
    ctx = PipelineContext(
        tenant_id="t",
        task_id="task",
        business_line_id="b",
        language=configured,
        raw_logs=[source],
        log_count=1,
    )
    await LogPreprocessStage().execute(ctx)
    assert ctx.language == expected
    assert ctx.log_metadata["language_conflict"] == (configured == "java")
    assert ctx.log_metadata["event_evidence"][0]["exceptions"] == ["System.NullReferenceException"]
