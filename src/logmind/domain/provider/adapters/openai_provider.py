"""
OpenAI Provider Adapter

Supports OpenAI API and any OpenAI-compatible endpoint.
Used as base class for SubAPI and DeepSeek providers.
"""

import json
import logging
from typing import AsyncIterator

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    before_sleep_log,
)

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
from logmind.core.config import get_settings

logger = logging.getLogger(__name__)


def _is_retryable_error(exception: BaseException) -> bool:
    """Determine if an error should trigger a retry."""
    # Network errors are always retryable
    if isinstance(exception, (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.PoolTimeout,
        httpx.NetworkError,
    )):
        return True

    # HTTP status errors
    if isinstance(exception, httpx.HTTPStatusError):
        status = exception.response.status_code
        # Retry: 404 (proxy issue), 429 (rate limit), 5xx (server errors), 408 (timeout)
        return status in (404, 408, 429) or (500 <= status < 600)

    return False


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
        # Use raw messages if provided (for tool calling flows)
        raw_messages = request.extra_params.pop("_raw_messages", None)
        if raw_messages:
            messages = raw_messages
        else:
            messages = [
                {"role": m.role, "content": m.content}
                for m in request.messages
            ]

        payload = {
            "model": request.model or self.default_model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "top_p": request.top_p,
            "stream": stream,
            **request.extra_params,
        }

        # Add tools for function calling
        if request.tools:
            payload["tools"] = request.tools

        return payload

    def _get_request_headers(self) -> dict[str, str]:
        """Hook for subclasses to supply extra per-request headers."""
        return {}

    @retry(
        retry=retry_if_exception(_is_retryable_error),
        wait=wait_exponential(multiplier=1.5, min=2, max=15),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Synchronous chat completion (supports function calling)."""
        payload = self._build_payload(request)
        headers = self._get_request_headers()
        resp = await self._client.post(
            "/v1/chat/completions",
            json=payload,
            headers=headers if headers else None,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})
        message = choice.get("message", {})

        # Parse tool calls if present
        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = []
            for tc in message["tool_calls"]:
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                })

        return ChatResponse(
            content=message.get("content") or "",
            model=data.get("model", request.model or self.default_model),
            usage=TokenUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
            finish_reason=choice.get("finish_reason"),
            tool_calls=tool_calls,
            raw_response=data,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Streaming chat completion — yields content chunks."""
        payload = self._build_payload(request, stream=True)
        headers = self._get_request_headers()
        async with self._client.stream(
            "POST",
            "/v1/chat/completions",
            json=payload,
            headers=headers if headers else None,
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
        """OpenAI text embedding with retry logic."""
        settings = get_settings()

        if not settings.embedding_retry_enabled:
            return await self._embed_impl(request)

        # Create retry decorator dynamically based on settings
        retry_decorator = retry(
            stop=stop_after_attempt(settings.embedding_retry_max_attempts),
            wait=wait_exponential(
                multiplier=settings.embedding_retry_multiplier,
                min=settings.embedding_retry_initial_wait,
                max=settings.embedding_retry_max_wait,
            ),
            retry=retry_if_exception(_is_retryable_error),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

        return await retry_decorator(self._embed_impl)(request)

    async def _embed_impl(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Internal embedding implementation."""
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
