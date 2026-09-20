"""
Tests for SubAPI Provider Adapter
"""

import pytest
from logmind.domain.provider.adapters.subapi_provider import SubAPIProvider
from logmind.domain.provider.factory import create_provider


def test_subapi_provider_initialization():
    provider = SubAPIProvider(
        api_base_url="https://aiproxy.example.com",
        api_key="test-key",
    )
    assert provider.default_model == "gpt-5.4-mini"
    headers = provider._get_request_headers()
    assert "codex_cli_rs" in headers["User-Agent"]
    assert headers["originator"] == "codex_cli_rs"
    assert "x-codex-window-id" in headers
    assert len(headers["x-codex-window-id"]) > 10


def test_subapi_provider_factory_registration():
    provider = create_provider(
        provider_type="subapi",
        api_base_url="https://aiproxy.example.com",
        api_key="test-key",
        default_model="gpt-5.4-mini",
    )
    assert isinstance(provider, SubAPIProvider)
    headers = provider._get_request_headers()
    assert headers["originator"] == "codex_cli_rs"


@pytest.mark.asyncio
async def test_provider_manager_timeout_error_formatting():
    from unittest.mock import AsyncMock, MagicMock, patch
    import httpx
    from logmind.domain.provider.manager import ProviderManager
    from logmind.domain.provider.models import ProviderConfig
    from logmind.domain.provider.base import ChatRequest, ChatMessage
    from logmind.core.exceptions import AllProvidersFailedError

    manager = ProviderManager()
    cfg = ProviderConfig(
        id="test-cfg-1",
        tenant_id="tenant-1",
        provider_type="subapi",
        name="公司内部 SubAPI Codex",
        api_base_url="https://aiproxy.example.com",
        default_model="gpt-5.4-mini",
        priority=0,
        rate_limit_rpm=0,
        is_active=True,
    )

    mock_provider = MagicMock()
    mock_provider.chat = AsyncMock(side_effect=httpx.ReadTimeout(""))

    with patch.object(manager, "_get_sorted_configs", return_value=[cfg]):
        with patch.object(manager, "_create_or_get_cached", return_value=mock_provider):
            session = AsyncMock()
            req = ChatRequest(messages=[ChatMessage(role="user", content="hello")])
            with pytest.raises(AllProvidersFailedError) as exc_info:
                await manager.chat_with_fallback(session, "tenant-1", req)

            err_text = str(exc_info.value)
            assert "公司内部 SubAPI Codex (subapi)" in err_text
            assert "ReadTimeout" in err_text
            assert "请求 AI 网关超时" in err_text

