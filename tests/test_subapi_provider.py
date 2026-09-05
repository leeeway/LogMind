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
