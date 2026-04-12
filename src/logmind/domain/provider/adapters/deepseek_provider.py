"""
DeepSeek Provider Adapter

DeepSeek uses an OpenAI-compatible API, so this inherits from OpenAIProvider.
"""

from logmind.domain.provider.adapters.openai_provider import OpenAIProvider
from logmind.domain.provider.factory import register_provider


@register_provider("deepseek")
class DeepSeekProvider(OpenAIProvider):
    """
    DeepSeek API provider — OpenAI-compatible.
    Inherits all functionality from OpenAIProvider.
    """

    def __init__(
        self,
        api_base_url: str = "https://api.deepseek.com",
        api_key: str = "",
        default_model: str = "deepseek-chat",
        **kwargs,
    ):
        super().__init__(api_base_url, api_key, default_model, **kwargs)
