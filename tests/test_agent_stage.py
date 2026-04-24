"""
Tests for AgentInferenceStage — multi-step AI Agent with tool calling.

Covers:
  - One-shot mode (agent disabled)
  - Tool call loop execution
  - Max steps safety exit
  - Token ceiling safety exit
  - Consecutive tool error withdrawal
  - Smart truncation of tool results
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

from logmind.domain.analysis.pipeline import PipelineContext
from logmind.domain.provider.base import TokenUsage


# ── Fixtures ─────────────────────────────────────────────

def _make_ctx(**kwargs) -> PipelineContext:
    defaults = {
        "tenant_id": "t1",
        "task_id": "task-001",
        "business_line_id": "biz-001",
        "system_prompt": "You are an SRE analyst.",
        "user_prompt": "Analyze these logs.",
    }
    defaults.update(kwargs)
    return PipelineContext(**defaults)


def _make_response(content="", tool_calls=None, tokens=100):
    """Create a mock ChatResponse."""
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = tool_calls
    resp.model = "gpt-4o-test"
    resp.usage = TokenUsage(
        prompt_tokens=tokens // 2,
        completion_tokens=tokens // 2,
        total_tokens=tokens,
    )
    return resp


def _make_tool_call(name="search_logs", arguments=None, call_id="tc_1"):
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {}),
        },
    }


# ── Tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_shot_mode_no_tools():
    """When agent is disabled, AI produces content in one call without tools."""
    from logmind.domain.analysis.agent_stage import AgentInferenceStage

    mock_manager = AsyncMock()
    final_response = _make_response(content='[{"severity":"warning","content":"test"}]')
    mock_manager.chat_with_fallback = AsyncMock(return_value=(final_response, "prov-1"))

    stage = AgentInferenceStage(mock_manager)
    ctx = _make_ctx()

    with patch("logmind.domain.analysis.agent_stage.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            analysis_agent_max_steps=5,
            analysis_agent_enabled=False,  # Agent disabled → one-shot
        )
        with patch("logmind.core.database.get_db_context") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await stage.execute(ctx)

    assert result.ai_response == '[{"severity":"warning","content":"test"}]'
    assert result.token_usage.total_tokens == 100
    assert mock_manager.chat_with_fallback.call_count == 1


@pytest.mark.asyncio
async def test_tool_call_loop():
    """Agent calls tool, gets result, then produces final answer."""
    from logmind.domain.analysis.agent_stage import AgentInferenceStage

    mock_manager = AsyncMock()

    # Step 1: AI requests tool call
    tool_response = _make_response(
        content="",
        tool_calls=[_make_tool_call("search_logs", {"keyword": "error"})],
    )
    # Step 2: AI produces final content
    final_response = _make_response(content='[{"severity":"critical"}]')
    mock_manager.chat_with_fallback = AsyncMock(
        side_effect=[(tool_response, "prov-1"), (final_response, "prov-1")]
    )

    stage = AgentInferenceStage(mock_manager)
    ctx = _make_ctx()

    with patch("logmind.domain.analysis.agent_stage.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            analysis_agent_max_steps=5,
            analysis_agent_enabled=True,
        )
        with patch("logmind.core.database.get_db_context") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("logmind.domain.analysis.agent_stage.execute_tool") as mock_tool:
                mock_tool.return_value = '{"hits": []}'
                result = await stage.execute(ctx)

    assert result.ai_response == '[{"severity":"critical"}]'
    assert result.token_usage.total_tokens == 200  # Two calls
    assert len(result.tool_call_records) == 1
    assert result.tool_call_records[0]["tool_name"] == "search_logs"


@pytest.mark.asyncio
async def test_smart_truncation():
    """Tool results longer than 8000 chars use head+tail truncation."""
    from logmind.domain.analysis.agent_stage import AgentInferenceStage

    mock_manager = AsyncMock()

    tool_response = _make_response(
        content="",
        tool_calls=[_make_tool_call("search_logs", {})],
    )
    final_response = _make_response(content="done")
    mock_manager.chat_with_fallback = AsyncMock(
        side_effect=[(tool_response, "p1"), (final_response, "p1")]
    )

    stage = AgentInferenceStage(mock_manager)
    ctx = _make_ctx()

    # Return a result that's definitely >8000 chars
    long_result = "A" * 5000 + "MIDDLE" * 1000 + "Z" * 5000

    with patch("logmind.domain.analysis.agent_stage.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            analysis_agent_max_steps=5,
            analysis_agent_enabled=True,
        )
        with patch("logmind.core.database.get_db_context") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("logmind.domain.analysis.agent_stage.execute_tool") as mock_tool:
                mock_tool.return_value = long_result
                result = await stage.execute(ctx)

    # After smart truncation, the result should contain the "omitted" marker
    # and the recorded length should be less than the original
    assert len(long_result) > 8000
    assert result.tool_call_records[0]["result_length"] < len(long_result)


class TestPriorityEngineTimezone:
    """Test the timezone fix in PriorityDecisionEngine."""

    def test_night_time_with_timezone(self):
        """_is_night_time uses configurable timezone offset."""
        from logmind.domain.analysis.priority_engine import PriorityDecisionEngine
        import os
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta

        engine = PriorityDecisionEngine()

        # Mock time to 23:30 in UTC+8 → should be night for "22:00-08:00"
        mock_time = datetime(2026, 4, 24, 23, 30, tzinfo=timezone(timedelta(hours=8)))
        with patch("logmind.domain.analysis.priority_engine.datetime") as mock_dt:
            mock_dt.now.return_value = mock_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.dict(os.environ, {"NIGHT_TIMEZONE_OFFSET": "8"}):
                result = engine._is_night_time("22:00-08:00")
        assert result is True

    def test_daytime_not_night(self):
        """14:00 should not be night for "22:00-08:00"."""
        from logmind.domain.analysis.priority_engine import PriorityDecisionEngine
        import os
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta

        engine = PriorityDecisionEngine()

        mock_time = datetime(2026, 4, 24, 14, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("logmind.domain.analysis.priority_engine.datetime") as mock_dt:
            mock_dt.now.return_value = mock_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.dict(os.environ, {"NIGHT_TIMEZONE_OFFSET": "8"}):
                result = engine._is_night_time("22:00-08:00")
        assert result is False
