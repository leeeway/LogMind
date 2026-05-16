import json

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
