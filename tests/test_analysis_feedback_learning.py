from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from logmind.domain.analysis import router


@pytest.mark.asyncio
async def test_positive_feedback_dispatches_verified_knowledge_document(monkeypatch):
    result = SimpleNamespace(
        id="result-12345678",
        task_id="task-12345678",
        severity="warning",
        content="System.TimeoutException 导致支付请求失败。",
        structured_data="{}",
        feedback_score=None,
        feedback_comment=None,
    )
    task = SimpleNamespace(
        id="task-12345678",
        business_line_id="biz-1",
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=result),
        flush=AsyncMock(),
    )
    user = SimpleNamespace(tenant_id="tenant-1")
    update_feedback = AsyncMock()
    index_delay = MagicMock()

    monkeypatch.setattr(
        router.task_repo,
        "get_by_id",
        AsyncMock(return_value=task),
    )
    monkeypatch.setattr(router, "_update_vector_feedback", update_feedback)
    monkeypatch.setattr(
        "logmind.domain.rag.tasks.index_document_chunks.delay",
        index_delay,
    )

    response = await router.submit_result_feedback(
        result.id,
        session,
        user,
        score=1,
        comment="已确认是支付网关超时",
    )

    assert result.feedback_score == 1
    assert result.feedback_comment == "已确认是支付网关超时"
    update_feedback.assert_awaited_once_with(task.id, "verified")
    index_delay.assert_called_once()
    kwargs = index_delay.call_args.kwargs
    assert kwargs["tenant_id"] == "tenant-1"
    assert "System.TimeoutException" in kwargs["content"]
    assert "已确认是支付网关超时" in kwargs["content"]
    assert kwargs["metadata"]["source"] == "verified_analysis_feedback"
    assert "Added to verified knowledge base" in response.message
