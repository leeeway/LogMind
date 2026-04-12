"""
OpenAI Provider Adapter

Supports OpenAI API and any OpenAI-compatible endpoint.
Used as base class for SubAPI and DeepSeek providers.
"""

import json
from typing import AsyncIterator

import httpx

from logmind.domain.provider.base import (
    BaseProvider,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    TokenUsage,
)
from logmind.domain.provider.factory import register_provider


@register_provider("openai")
class OpenAIProvider(BaseProvider):
    """OpenAI / OpenAI-compatible API provider."""

    def __init__(
        self,
        api_base_url: str = "https://api.openai.com",
        api_key: str = "",
        default_model: str = "gpt-4o",
        **kwargs,
    ):
        super().__init__(api_base_url, api_key, default_model, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=self.api_base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    def _build_payload(self, request: ChatRequest, stream: bool = False) -> dict:
        """Build the API request payload."""
        return {
            "model": request.model or self.default_model,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "top_p": request.top_p,
            "stream": stream,
            **request.extra_params,
        }

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Synchronous chat completion."""
        payload = self._build_payload(request)
        resp = await self._client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ChatResponse(
            content=choice["message"]["content"],
            model=data.get("model", request.model or self.default_model),
            usage=TokenUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
            finish_reason=choice.get("finish_reason"),
            raw_response=data,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Streaming chat completion — yields content chunks."""
        payload = self._build_payload(request, stream=True)
        async with self._client.stream(
            "POST", "/v1/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if content := delta.get("content"):
                        yield content
                except json.JSONDecodeError:
                    continue

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """OpenAI text embedding."""
        payload = {
            "model": request.model or "text-embedding-3-small",
            "input": request.texts,
        }
        resp = await self._client.post("/v1/embeddings", json=payload)
        resp.raise_for_status()
        data = resp.json()

        return EmbeddingResponse(
            embeddings=[item["embedding"] for item in data["data"]],
            model=data.get("model", "text-embedding-3-small"),
            usage=TokenUsage(
                total_tokens=data.get("usage", {}).get("total_tokens", 0)
            ),
        )

    async def health_check(self) -> bool:
        """Check if the OpenAI API is reachable."""
        try:
            resp = await self._client.get("/v1/models")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
