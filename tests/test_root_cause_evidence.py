"""Tests for evidence-based root cause graph and candidate ranking."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace


def _result(**overrides):
    defaults = {
        "id": "result-001",
        "result_type": "root_cause",
        "content": "AuthService 大量请求超时，根因疑似 Redis 连接池耗尽",
        "severity": "critical",
        "confidence_score": 0.9,
        "structured_data": "{}",
        "source_log_refs": "[]",
        "created_at": datetime(2026, 6, 30, 10, 5, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_evidence_builder_ranks_upstream_change_point_candidate_first():
    """Upstream + change point + log refs should produce a high-confidence candidate."""
    from logmind.domain.analysis.evidence import build_root_cause_evidence

    result = _result(
        structured_data=json.dumps({
            "root_cause": "Redis 连接池耗尽",
            "upstream_service": "RedisCluster",
            "change_points": [{
                "timestamp": "2026-06-30T10:02:00+00:00",
                "before_rate": 2,
                "after_rate": 120,
                "z_score": 7.8,
                "bucket_count": 120,
            }],
            "correlated_errors": [{
                "service_name": "RedisCluster",
                "direction": "upstream",
                "error_count": 18,
                "error_samples": ["ERR max number of clients reached"],
            }],
            "next_verifications": ["检查 Redis maxclients 和当前连接数"],
        }, ensure_ascii=False),
        source_log_refs=json.dumps(["log-a", "log-b"]),
    )

    summary = build_root_cause_evidence([result])

    assert summary["candidates"][0]["service"] == "RedisCluster"
    assert summary["candidates"][0]["score"] >= 0.8
    assert "检查 Redis maxclients" in summary["next_verifications"][0]
    assert {item["kind"] for item in summary["evidence"]} >= {
        "log_sample",
        "change_point",
        "cross_service",
    }


def test_rootcause_graph_parses_persisted_structured_json():
    """Root cause graph should parse structured_data JSON persisted as text."""
    from logmind.domain.analysis.rootcause_router import build_rootcause_graph

    result = _result(
        id="result-abc12345",
        structured_data=json.dumps({
            "root_cause": "数据库连接池耗尽",
            "upstream_service": "DatabaseService",
            "next_verifications": ["查看数据库连接池活跃连接"],
        }, ensure_ascii=False),
        source_log_refs=json.dumps(["log-db-1"]),
    )

    graph = build_rootcause_graph("task-001", [result])

    assert graph.candidates[0].service == "DatabaseService"
    assert graph.nodes[0].node_type == "candidate"
    assert "数据库连接池耗尽" in graph.nodes[0].detail
    assert graph.evidence[0].log_refs == ["log-db-1"]
    assert graph.next_verifications == ["查看数据库连接池活跃连接"]
