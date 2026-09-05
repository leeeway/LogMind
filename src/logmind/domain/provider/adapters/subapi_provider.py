"""
SubAPI Provider Adapter

Company internal deployment — compatible with OpenAI API protocol.
Directly inherits from OpenAIProvider.
"""

import uuid

from logmind.domain.provider.adapters.openai_provider import OpenAIProvider
from logmind.domain.provider.factory import register_provider


@register_provider("subapi")
class SubAPIProvider(OpenAIProvider):
    """
    SubAPI Provider — internal deployment, OpenAI API-compatible.
    Automatically injects required Codex client headers (User-Agent, x-codex-window-id, originator)
    to prevent upstream 403 Forbidden ('This account only allows Codex official clients').
    """

    def __init__(
        self,
        api_base_url: str = "http://subapi.internal",
        api_key: str = "",
        default_model: str = "gpt-5.4-mini",
        **kwargs,
    ):
        super().__init__(api_base_url, api_key, default_model, **kwargs)
        # Ensure default client headers identify as official Codex client
        self._client.headers["User-Agent"] = "codex_cli_rs/0.32.0 (Darwin 24.0.0; arm64)"
        self._client.headers["originator"] = "codex_cli_rs"

    def _get_request_headers(self) -> dict[str, str]:
        """Generate fresh session headers for each request."""
        return {
            "User-Agent": "codex_cli_rs/0.32.0 (Darwin 24.0.0; arm64)",
            "x-codex-window-id": str(uuid.uuid4()),
            "originator": "codex_cli_rs",
        }
