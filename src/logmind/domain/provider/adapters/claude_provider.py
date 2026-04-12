"""
Claude (Anthropic) Provider Adapter

Implements the Anthropic Messages API format.
"""

import json
from typing import AsyncIterator

import httpx

from logmind.domain.provider.base import (
    BaseProvider,
    ChatRequest,
    ChatResponse,
    TokenUsage,
)
from logmind.domain.provider.factory import register_provider


@register_provider("claude")
class ClaudeProvider(BaseProvider):
    """Anthropic Claude API provider."""

    def __init__(
        self,
        api_base_url: str = "https://api.anthropic.com",
        api_key: str = "",
        default_model: str = "claude-sonnet-4-20250514",
        **kwargs,
    ):
        super().__init__(api_base_url, api_key, default_model, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=self.api_base_url,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    def _build_payload(self, request: ChatRequest, stream: bool = False) -> dict:
        """Build Anthropic Messages API payload."""
        # Separate system message from user/assistant messages
        system_content = ""
        messages = []
        for m in request.messages:
            if m.role == "system":
                system_content += m.content + "\n"
            else:
                messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": request.model or self.default_model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": stream,
        }

        if system_content.strip():
            payload["system"] = system_content.strip()

        return payload

    async def chat(self, request: ChatRequest) -> ChatResponse:
        payload = self._build_payload(request)
        resp = await self._client.post("/v1/messages", json=payload)
        resp.raise_for_status()
        data = resp.json()

        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block["text"]

        usage = data.get("usage", {})
        return ChatResponse(
            content=content,
            model=data.get("model", self.default_model),
            usage=TokenUsage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                total_tokens=(
                    usage.get("input_tokens", 0)
                    + usage.get("output_tokens", 0)
                ),
            ),
            finish_reason=data.get("stop_reason"),
            raw_response=data,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        payload = self._build_payload(request, stream=True)
        async with self._client.stream(
            "POST", "/v1/messages", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta", {})
                        if text := delta.get("text"):
                            yield text
                except json.JSONDecodeError:
                    continue

    async def health_check(self) -> bool:
        try:
            # Claude doesn't have a models endpoint; do a minimal request
            resp = await self._client.post(
                "/v1/messages",
                json={
                    "model": self.default_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                },
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
