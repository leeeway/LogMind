"""
Tests for P0 data field fixes:
  - cost_usd estimation
  - source_log_refs extraction
  - content_hash dedup in RAG

Tests for _estimate_cost_usd and ResultParseStage source_log_refs.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ── Cost Estimation Tests ────────────────────────────────

class TestEstimateCostUsd:
    """Tests for _estimate_cost_usd in tasks.py."""

    def test_none_token_usage(self):
        from logmind.domain.analysis.tasks import _estimate_cost_usd
        assert _estimate_cost_usd(None) == 0.0

    def test_zero_tokens(self):
        from logmind.domain.analysis.tasks import _estimate_cost_usd
        usage = MagicMock(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        assert _estimate_cost_usd(usage) == 0.0

    def test_default_pricing(self):
        """Without provider_config_id, uses default pricing."""
        from logmind.domain.analysis.tasks import _estimate_cost_usd, _DEFAULT_PRICE
        usage = MagicMock(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        expected = (1000 / 1000.0) * _DEFAULT_PRICE["input"] + \
                   (500 / 1000.0) * _DEFAULT_PRICE["output"]
        result = _estimate_cost_usd(usage)
        assert result == round(expected, 6)

    def test_known_model_pricing(self):
        """With a known model, uses model-specific pricing."""
        from logmind.domain.analysis.tasks import _estimate_cost_usd, _MODEL_PRICING
        usage = MagicMock(prompt_tokens=2000, completion_tokens=1000, total_tokens=3000)

        # Mock provider_manager with a deepseek model
        mock_entry = MagicMock()
        mock_entry.config.default_model = "deepseek-chat-v2"

        with patch("logmind.domain.provider.manager.provider_manager") as mock_pm:
            mock_pm._cache = {"cfg-1": mock_entry}
            result = _estimate_cost_usd(usage, "cfg-1")

        deepseek_price = _MODEL_PRICING["deepseek"]
        expected = (2000 / 1000.0) * deepseek_price["input"] + \
                   (1000 / 1000.0) * deepseek_price["output"]
        assert result == round(expected, 6)

    def test_unknown_model_uses_default(self):
        """Unknown model falls back to default pricing."""
        from logmind.domain.analysis.tasks import _estimate_cost_usd, _DEFAULT_PRICE
        usage = MagicMock(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)

        mock_entry = MagicMock()
        mock_entry.config.default_model = "some-unknown-model-v3"

        with patch("logmind.domain.provider.manager.provider_manager") as mock_pm:
            mock_pm._cache = {"cfg-x": mock_entry}
            result = _estimate_cost_usd(usage, "cfg-x")

        expected = (1000 / 1000.0) * _DEFAULT_PRICE["input"] + \
                   (1000 / 1000.0) * _DEFAULT_PRICE["output"]
        assert result == round(expected, 6)

    def test_cost_never_negative(self):
        """Cost should never be negative."""
        from logmind.domain.analysis.tasks import _estimate_cost_usd
        usage = MagicMock(prompt_tokens=500, completion_tokens=200, total_tokens=700)
        result = _estimate_cost_usd(usage)
        assert result >= 0.0

    def test_exception_returns_zero(self):
        """Internal errors should return 0.0, not crash."""
        from logmind.domain.analysis.tasks import _estimate_cost_usd
        # Passing a bad object that will raise on attribute access
        result = _estimate_cost_usd("not-a-token-usage")
        assert result == 0.0


# ── Source Log Refs Extraction Tests ─────────────────────

class TestSourceLogRefs:
    """Tests for source_log_refs extraction in ResultParseStage."""

    @pytest.mark.asyncio
    async def test_refs_extracted_from_ai_output(self):
        """source_log_refs from AI output are captured."""
        from logmind.domain.analysis.stages.result_parse import ResultParseStage
        from logmind.domain.analysis.pipeline import PipelineContext

        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.ai_response = json.dumps([{
            "result_type": "root_cause",
            "content": "NullPointerException in UserService",
            "severity": "critical",
            "confidence_score": 0.9,
            "source_log_refs": ["log-id-001", "log-id-002"],
        }])

        result = await ResultParseStage().execute(ctx)
        refs = json.loads(result.analysis_results[0]["source_log_refs"])
        assert refs == ["log-id-001", "log-id-002"]

    @pytest.mark.asyncio
    async def test_refs_empty_when_not_provided(self):
        """source_log_refs defaults to empty list when AI doesn't provide them."""
        from logmind.domain.analysis.stages.result_parse import ResultParseStage
        from logmind.domain.analysis.pipeline import PipelineContext

        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.ai_response = json.dumps([{
            "result_type": "summary",
            "content": "No critical issues found",
            "severity": "info",
        }])

        result = await ResultParseStage().execute(ctx)
        refs = json.loads(result.analysis_results[0]["source_log_refs"])
        assert refs == []

    @pytest.mark.asyncio
    async def test_refs_normalized_and_capped(self):
        """source_log_refs are capped at 20 entries."""
        from logmind.domain.analysis.stages.result_parse import ResultParseStage
        from logmind.domain.analysis.pipeline import PipelineContext

        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.ai_response = json.dumps([{
            "result_type": "anomaly",
            "content": "Many errors",
            "severity": "warning",
            "source_log_refs": [f"log-{i}" for i in range(50)],
        }])

        result = await ResultParseStage().execute(ctx)
        refs = json.loads(result.analysis_results[0]["source_log_refs"])
        assert len(refs) == 20

    @pytest.mark.asyncio
    async def test_refs_handles_non_list(self):
        """Gracefully handle non-list source_log_refs from AI."""
        from logmind.domain.analysis.stages.result_parse import ResultParseStage
        from logmind.domain.analysis.pipeline import PipelineContext

        ctx = PipelineContext(
            tenant_id="t1", task_id="t1", business_line_id="b1",
            business_line_name="Test", log_count=10, raw_logs=[],
        )
        ctx.ai_response = json.dumps([{
            "result_type": "anomaly",
            "content": "Error found",
            "severity": "warning",
            "source_log_refs": "not-a-list",
        }])

        result = await ResultParseStage().execute(ctx)
        refs = json.loads(result.analysis_results[0]["source_log_refs"])
        assert refs == []


# ── Model Price Table Tests ──────────────────────────────

class TestModelPricing:
    """Tests for the model pricing table completeness."""

    def test_all_prices_positive(self):
        """All pricing values must be positive."""
        from logmind.domain.analysis.tasks import _MODEL_PRICING, _DEFAULT_PRICE
        for model, rates in _MODEL_PRICING.items():
            assert rates["input"] > 0, f"{model} input price must be positive"
            assert rates["output"] > 0, f"{model} output price must be positive"
        assert _DEFAULT_PRICE["input"] > 0
        assert _DEFAULT_PRICE["output"] > 0

    def test_output_more_expensive_than_input(self):
        """For most models, output tokens cost more than input tokens."""
        from logmind.domain.analysis.tasks import _MODEL_PRICING
        for model, rates in _MODEL_PRICING.items():
            assert rates["output"] >= rates["input"], \
                f"{model}: output ({rates['output']}) should be >= input ({rates['input']})"
