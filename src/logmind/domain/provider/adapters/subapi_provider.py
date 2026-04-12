"""
SubAPI Provider Adapter

Company internal deployment — compatible with OpenAI API protocol.
Directly inherits from OpenAIProvider.
"""

from logmind.domain.provider.adapters.openai_provider import OpenAIProvider
from logmind.domain.provider.factory import register_provider


@register_provider("subapi")
class SubAPIProvider(OpenAIProvider):
    """
    SubAPI Provider — internal deployment, OpenAI API-compatible.
    Reuses all OpenAI Provider logic, only changes defaults.
    """

    def __init__(
        self,
        api_base_url: str = "http://subapi.internal",
        api_key: str = "",
        default_model: str = "default",
        **kwargs,
    ):
        super().__init__(api_base_url, api_key, default_model, **kwargs)
