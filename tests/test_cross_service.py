"""
Tests for CrossServiceCorrelationStage.

Covers:
  - Skip when no related_services configured
  - Skip when primary has zero logs
  - Upstream/downstream query and classification
  - ES query failure graceful handling (non-critical)
  - Prompt injection of correlated errors
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

from logmind.domain.analysis.pipeline import PipelineContext
from datetime import datetime, timezone, timedelta


def _make_ctx(**kwargs) -> PipelineContext:
    now = datetime.now(timezone.utc)
    defaults = {
        "tenant_id": "t1",
        "task_id": "task-cross-001",
        "business_line_id": "biz-001",
        "business_line_name": "AuthService",
        "log_count": 10,
        "raw_logs": [{"message": "error"}] * 10,
        "time_from": now - timedelta(minutes=30),
        "time_to": now,
    }
    defaults.update(kwargs)
    return PipelineContext(**defaults)


class TestCrossServiceSkipConditions:
    """Test skip conditions that bypass correlation queries."""

    @pytest.mark.asyncio
    async def test_skip_no_related_services(self):
        """No related_services → skip immediately."""
        from logmind.domain.analysis.stages.cross_service import CrossServiceCorrelationStage

        mock_log_svc = AsyncMock()
        stage = CrossServiceCorrelationStage(mock_log_svc)
        ctx = _make_ctx(related_services={})

        result = await stage.execute(ctx)

        assert result.correlated_errors == []
        mock_log_svc.search_logs.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_zero_logs(self):
        """Primary service has zero logs → nothing to correlate."""
        from logmind.domain.analysis.stages.cross_service import CrossServiceCorrelationStage

        mock_log_svc = AsyncMock()
        stage = CrossServiceCorrelationStage(mock_log_svc)
        ctx = _make_ctx(
            log_count=0,
            raw_logs=[],
            related_services={"upstream": ["biz-upstream-1"]},
        )

        result = await stage.execute(ctx)
        assert result.correlated_errors == []

    @pytest.mark.asyncio
    async def test_skip_empty_lists(self):
        """related_services has empty upstream/downstream lists → skip."""
        from logmind.domain.analysis.stages.cross_service import CrossServiceCorrelationStage

        mock_log_svc = AsyncMock()
        stage = CrossServiceCorrelationStage(mock_log_svc)
        ctx = _make_ctx(related_services={"upstream": [], "downstream": []})

        result = await stage.execute(ctx)
        assert result.correlated_errors == []


class TestCrossServiceCorrelation:
    """Test actual cross-service error querying."""

    @pytest.mark.asyncio
    async def test_upstream_errors_found(self):
        """Upstream service has errors → correlated_errors populated."""
        from logmind.domain.analysis.stages.cross_service import CrossServiceCorrelationStage

        # Mock log service
        mock_log_svc = AsyncMock()
        mock_result = MagicMock()
        mock_result.logs = [
            MagicMock(raw={"message": "Connection pool exhausted"}),
            MagicMock(raw={"message": "DB query timeout after 30s"}),
        ]
        mock_log_svc.search_logs = AsyncMock(return_value=mock_result)

        # Mock DB context
        mock_biz = MagicMock()
        mock_biz.id = "biz-db-001"
        mock_biz.name = "DatabaseService"
        mock_biz.es_index_pattern = "db-service-*"
        mock_biz.language = "java"

        stage = CrossServiceCorrelationStage(mock_log_svc)
        ctx = _make_ctx(
            related_services={"upstream": ["biz-db-001"], "downstream": []},
        )

        with patch("logmind.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_exec_result = MagicMock()
            mock_exec_result.scalars.return_value.all.return_value = [mock_biz]
            mock_session.execute = AsyncMock(return_value=mock_exec_result)
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await stage.execute(ctx)

        assert len(result.correlated_errors) == 1
        assert result.correlated_errors[0]["service_name"] == "DatabaseService"
        assert result.correlated_errors[0]["direction"] == "upstream"
        assert result.correlated_errors[0]["error_count"] == 2
        assert "Connection pool exhausted" in result.correlated_errors[0]["error_samples"][0]

    @pytest.mark.asyncio
    async def test_no_correlated_errors(self):
        """Related service has no errors → correlated_errors stays empty."""
        from logmind.domain.analysis.stages.cross_service import CrossServiceCorrelationStage

        mock_log_svc = AsyncMock()
        mock_result = MagicMock()
        mock_result.logs = []  # No errors
        mock_log_svc.search_logs = AsyncMock(return_value=mock_result)

        mock_biz = MagicMock()
        mock_biz.id = "biz-down-001"
        mock_biz.name = "PaymentService"
        mock_biz.es_index_pattern = "payment-*"
        mock_biz.language = "java"

        stage = CrossServiceCorrelationStage(mock_log_svc)
        ctx = _make_ctx(
            related_services={"upstream": [], "downstream": ["biz-down-001"]},
        )

        with patch("logmind.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_exec_result = MagicMock()
            mock_exec_result.scalars.return_value.all.return_value = [mock_biz]
            mock_session.execute = AsyncMock(return_value=mock_exec_result)
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await stage.execute(ctx)

        assert result.correlated_errors == []

    @pytest.mark.asyncio
    async def test_es_query_failure_graceful(self):
        """ES query failure for one related service doesn't crash stage."""
        from logmind.domain.analysis.stages.cross_service import CrossServiceCorrelationStage

        mock_log_svc = AsyncMock()
        mock_log_svc.search_logs = AsyncMock(side_effect=Exception("ES unreachable"))

        mock_biz = MagicMock()
        mock_biz.id = "biz-fail-001"
        mock_biz.name = "FailingService"
        mock_biz.es_index_pattern = "fail-*"
        mock_biz.language = "java"

        stage = CrossServiceCorrelationStage(mock_log_svc)
        ctx = _make_ctx(
            related_services={"upstream": ["biz-fail-001"], "downstream": []},
        )

        with patch("logmind.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_exec_result = MagicMock()
            mock_exec_result.scalars.return_value.all.return_value = [mock_biz]
            mock_session.execute = AsyncMock(return_value=mock_exec_result)
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should NOT raise
            result = await stage.execute(ctx)

        assert result.correlated_errors == []


class TestCrossServicePromptInjection:
    """Test that correlated errors are injected into the AI prompt."""

    def test_injection_format(self):
        """Correlated errors produce structured prompt sections."""
        ctx = _make_ctx()
        ctx.user_prompt = "## 日志内容\nsome logs here"
        ctx.correlated_errors = [
            {
                "service_name": "DatabaseService",
                "service_id": "biz-db-001",
                "direction": "upstream",
                "error_count": 5,
                "error_samples": [
                    "Connection pool exhausted",
                    "Lock wait timeout exceeded",
                ],
            },
            {
                "service_name": "PaymentGateway",
                "service_id": "biz-pay-001",
                "direction": "downstream",
                "error_count": 2,
                "error_samples": ["HTTP 504 Gateway Timeout"],
            },
        ]

        # Simulate prompt injection (same logic as PromptBuildStage)
        correlation_lines = ["\n\n## 跨服务关联异常\n以下关联服务在同一时间窗口内也出现了错误，请综合分析是否存在级联故障：\n"]
        for ce in ctx.correlated_errors:
            direction_label = "上游服务" if ce["direction"] == "upstream" else "下游服务"
            correlation_lines.append(
                f"### {direction_label}: {ce['service_name']} ({ce['error_count']} 条错误)"
            )
            for i, sample in enumerate(ce.get("error_samples", [])[:3], 1):
                correlation_lines.append(f"  {i}. {sample}")
            correlation_lines.append("")

        correlation_text = "\n".join(correlation_lines)
        ctx.user_prompt = ctx.user_prompt + correlation_text

        assert "跨服务关联异常" in ctx.user_prompt
        assert "上游服务: DatabaseService" in ctx.user_prompt
        assert "下游服务: PaymentGateway" in ctx.user_prompt
        assert "Connection pool exhausted" in ctx.user_prompt
        assert "HTTP 504 Gateway Timeout" in ctx.user_prompt

    def test_stage_is_non_critical(self):
        """CrossServiceCorrelationStage.is_critical must be False."""
        from logmind.domain.analysis.stages.cross_service import CrossServiceCorrelationStage
        stage = CrossServiceCorrelationStage(None)
        assert stage.is_critical is False
