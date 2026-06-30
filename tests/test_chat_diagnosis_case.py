import json
from types import SimpleNamespace

from logmind.domain.chat.service import ChatService


def test_structured_tool_summary_marks_empty_result_as_low_confidence():
    service = ChatService()

    update = service._summarize_tool_result_structured(
        "search_logs",
        {"service_name": "payment-core"},
        json.dumps({"total_hits": 0, "error_count": 0}, ensure_ascii=False),
        "E-1",
    )

    assert update["confidence_delta"] < 0
    assert update["counter_evidence"] == ["E-1"]
    assert "暂未" in update["hypothesis"]


def test_hypothesis_update_preserves_evidence_labels_and_confidence():
    service = ChatService()
    case_state = {
        "path": "service_error",
        "confidence": 20,
        "supporting_evidence": [],
        "counter_evidence": [],
        "evidence_summaries": [],
        "impact_scope": "待确认",
    }
    tool_update = service._summarize_tool_result_structured(
        "get_service_health",
        {"service_name": "payment-core"},
        json.dumps({"count": 18, "error_count": 12}, ensure_ascii=False),
        "E-2",
    )

    event = service._apply_hypothesis_update(case_state, tool_update)

    assert event["type"] == "hypothesis_update"
    assert event["supporting_evidence"] == ["E-2"]
    assert event["confidence"] > 20
    assert event["evidence_summaries"][0]["label"] == "E-2"
    assert event["missing_confirmations"]


def test_expert_answer_uses_fixed_sections_and_evidence_chain():
    service = ChatService()
    case_state = {
        "confidence": 62,
        "hypothesis": "数据库连接池耗尽可能导致 payment-core 错误升高。",
        "impact_scope": "集中在 payment-core。",
        "evidence_summaries": [{"label": "E-3", "summary": "错误计数集中在连接池超时。"}],
    }
    actions = [{"label": "继续查链路", "description": "确认上游调用影响。", "kind": "diagnose"}]

    content = service._format_expert_answer("payment-core 近期错误升高。", case_state, actions)

    for title in ["结论摘要", "证据链", "影响范围", "置信度", "建议动作", "我还需要确认什么"]:
        assert f"## {title}" in content
    assert "E-3" in content
    assert "62%" in content


def test_decision_actions_include_postmortem_and_copy():
    service = ChatService()

    actions = service._build_decision_actions(
        "分析 payment-core 最近 1 小时错误",
        [],
        "service_error",
        {"hypothesis": "payment-core 错误升高"},
        "payment-core 近期错误升高。",
    )

    labels = {item["label"] for item in actions}
    assert "生成复盘草稿" in labels
    assert "复制诊断报告" in labels


def test_resolve_business_line_matches_branch_prefixed_domain():
    service = ChatService()
    biz = SimpleNamespace(
        id="biz-1",
        name="GPay",
        es_index_pattern=".ds-master-gpay.gyyx.cn-2026.06.16-002650",
        is_active=True,
    )

    assert service._resolve_business_line([biz], "master-gpay.gyyx.cn") is biz
    assert service._resolve_business_line([biz], "gpay.gyyx.cn") is biz


def test_direct_log_search_intent_extracts_csharp_keyword_and_export():
    service = ChatService()
    biz = SimpleNamespace(
        id="biz-1",
        name="GPay",
        es_index_pattern=".ds-master-gpay.gyyx.cn-*",
        is_active=True,
    )

    intent = service._extract_direct_log_search_intent(
        "最新1小时业务线master-gpay.gyyx.cn所有日志包含限制访问：非支付宝客户端的数据，导出订单号",
        [biz],
    )

    assert intent is not None
    assert intent["service_name"] == "GPay"
    assert intent["keyword"] == "限制访问：非支付宝客户端"
    assert intent["lookback_seconds"] == 3600
    assert intent["severity"] is None
    assert intent["wants_export"] is True


def test_direct_log_search_intent_extracts_compact_service_time_keyword_query():
    service = ChatService()
    biz = SimpleNamespace(
        id="biz-1",
        name="auth-service",
        es_index_pattern=".ds-master-auth-service.gyyx.cn-*",
        is_active=True,
    )

    intent = service._extract_direct_log_search_intent(
        "查 auth-service 最近30分钟 timeout 返回200条",
        [biz],
    )

    assert intent is not None
    assert intent["service_name"] == "auth-service"
    assert intent["keyword"] == "timeout"
    assert intent["lookback_seconds"] == 1800
    assert intent["size"] == 200


def test_direct_log_search_intent_does_not_use_service_and_time_as_keyword():
    service = ChatService()
    biz = SimpleNamespace(
        id="biz-1",
        name="payment-core",
        es_index_pattern=".ds-master-payment-core.gyyx.cn-*",
        is_active=True,
    )

    intent = service._extract_direct_log_search_intent(
        "查询 payment-core 最近3小时 数据库连接失败",
        [biz],
    )

    assert intent is not None
    assert intent["keyword"] == "数据库连接失败"
    assert intent["lookback_seconds"] == 10800


def test_extract_order_ids_from_csharp_log_message():
    service = ChatService()

    ids = service._extract_order_ids(
        "调用/Recharge/AlipayPCQrcodeDesk/限制访问：非支付宝客户端，"
        "订单号：AliQr26061716280437930612，UserAgent：Mozilla/5.0"
    )

    assert ids == ["AliQr26061716280437930612"]
