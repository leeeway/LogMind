"""
Ollama Provider Adapter

Local LLM inference via Ollama API.
Uses Ollama's native /api/chat endpoint.
"""

import json
from typing import AsyncIterator

import httpx

from logmind.domain.provider.base import (
    BaseProvider,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    TokenUsage,
)
from logmind.domain.provider.factory import register_provider


@register_provider("ollama")
class OllamaProvider(BaseProvider):
    """Ollama local model provider."""

    def __init__(
        self,
        api_base_url: str = "http://localhost:11434",
        api_key: str = "",  # Ollama doesn't require API key
        default_model: str = "llama3",
        **kwargs,
    ):
        super().__init__(api_base_url, api_key, default_model, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=self.api_base_url,
            timeout=httpx.Timeout(300.0, connect=10.0),  # Longer timeout for local inference
        )

    def _build_messages(self, request: ChatRequest) -> list[dict]:
        return [
            {"role": m.role, "content": m.content}
            for m in request.messages
        ]

    async def chat(self, request: ChatRequest) -> ChatResponse:
        payload = {
            "model": request.model or self.default_model,
            "messages": self._build_messages(request),
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "top_p": request.top_p,
                "num_predict": request.max_tokens,
            },
        }
        resp = await self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

        return ChatResponse(
            content=data.get("message", {}).get("content", ""),
            model=data.get("model", self.default_model),
            usage=TokenUsage(
                prompt_tokens=data.get("prompt_eval_count", 0),
                completion_tokens=data.get("eval_count", 0),
                total_tokens=(
                    data.get("prompt_eval_count", 0)
                    + data.get("eval_count", 0)
                ),
            ),
            finish_reason="stop" if data.get("done") else None,
            raw_response=data,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        payload = {
            "model": request.model or self.default_model,
            "messages": self._build_messages(request),
            "stream": True,
            "options": {
                "temperature": request.temperature,
                "top_p": request.top_p,
                "num_predict": request.max_tokens,
            },
        }
        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if content := data.get("message", {}).get("content"):
                        yield content
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        embeddings = []
        for text in request.texts:
            resp = await self._client.post("/api/embeddings", json={
                "model": request.model or "nomic-embed-text",
                "prompt": text,
            })
            resp.raise_for_status()
            data = resp.json()
            embeddings.append(data["embedding"])

        return EmbeddingResponse(
            embeddings=embeddings,
            model=request.model or "nomic-embed-text",
            usage=TokenUsage(),
        )

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/api/tags")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
