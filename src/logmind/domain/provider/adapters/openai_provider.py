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
    # Network and transport errors are always retryable
    # Note: httpx.TransportError covers RemoteProtocolError (peer closed connection midway)
    if isinstance(exception, (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.PoolTimeout,
        httpx.NetworkError,
        httpx.TransportError,
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
        self.use_responses_api = bool(kwargs.pop("use_responses_api", False))
        super().__init__(api_base_url, api_key, default_model, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=self.api_base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com",
                "X-GitHub-Api-Version": "2025-01-01",
                "Copilot-Integration-Id": "vscode-chat",
                "Editor-Version": "vscode/1.100.0",
                "Editor-Plugin-Version": "copilot/1.300.0",
                "User-Agent": "GithubCopilot/1.300.0",
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

        model = request.model or self.default_model
        extra_config = {
            k: v for k, v in getattr(self, "extra_config", {}).items()
            if k not in ("use_responses_api",)
        }
        extra_params = {
            k: v for k, v in request.extra_params.items()
            if k not in ("use_responses_api", "_raw_messages")
        }
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            **extra_config,
            **extra_params,
        }

        # Check for reasoning models (e.g. terra, sol, luna, o1, o3, gpt-6, gpt-5) or gyai.dev
        is_reasoning_or_modern = any(
            k in model.lower() for k in ("terra", "sol", "luna", "o1", "o3", "gpt-6", "gpt-5")
        ) or "gyai.dev" in self.api_base_url

        if is_reasoning_or_modern:
            if request.max_tokens is not None:
                payload["max_completion_tokens"] = request.max_tokens
            if "reasoning_effort" not in payload:
                payload["reasoning_effort"] = "none"
            if request.temperature is not None:
                payload["temperature"] = request.temperature
            if request.top_p is not None:
                payload["top_p"] = request.top_p
        else:
            if request.temperature is not None:
                payload["temperature"] = request.temperature
            if request.max_tokens is not None:
                payload["max_tokens"] = request.max_tokens
            if request.top_p is not None:
                payload["top_p"] = request.top_p

        # Add tools for function calling
        if request.tools:
            payload["tools"] = request.tools

        return payload

    def _build_responses_payload(self, request: ChatRequest, stream: bool = False) -> dict:
        """
        Build OpenAI Responses API (/v1/responses) payload.
        Places system instructions into input[0] as role: "system" so gateways
        (e.g. gyai-gateway prompt_cache) can match cache breakpoints for 12-hour prompt caching.
        """
        raw_messages = request.extra_params.pop("_raw_messages", None)
        messages = raw_messages if raw_messages else [
            {"role": m.role, "content": m.content}
            for m in request.messages
        ]

        instructions: list[str] = []
        body_items: list[dict] = []

        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system":
                if content:
                    instructions.append(content)
            elif role == "assistant":
                tool_calls = m.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        call_id = tc.get("id") or tc.get("call_id") or ""
                        func = tc.get("function", {})
                        body_items.append({
                            "type": "function_call",
                            "call_id": call_id,
                            "name": func.get("name", ""),
                            "arguments": func.get("arguments", "{}"),
                        })
                if content:
                    body_items.append({"role": "assistant", "content": content})
            elif role == "tool":
                body_items.append({
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id", ""),
                    "output": str(content or ""),
                })
            else:
                if content:
                    body_items.append({"role": "user", "content": content})

        # To enable explicit prompt caching on gateways like gyai.dev (which matches role: "system"
        # in the input message list to set 12h cache breakpoints), put instructions as a system
        # message at the head of input rather than top-level instructions.
        input_items: list[dict] = []
        if instructions:
            input_items.append({"role": "system", "content": "\n\n".join(instructions)})
        input_items.extend(body_items)

        if len(input_items) == 1 and input_items[0].get("role") == "user" and not input_items[0].get("type"):
            input_val = input_items[0].get("content", "")
        else:
            input_val = input_items

        model = request.model or self.default_model

        # Filter out internal adapter configuration and parameters unsupported by Responses API
        extra_config = {
            k: v for k, v in getattr(self, "extra_config", {}).items()
            if k not in ("use_responses_api", "temperature", "top_p", "instructions")
        }
        extra_params = {
            k: v for k, v in request.extra_params.items()
            if k not in ("use_responses_api", "_raw_messages", "temperature", "top_p", "instructions")
        }

        payload = {
            "model": model,
            "input": input_val,
            "stream": stream,
            **extra_config,
            **extra_params,
        }

        # Parameter adaptation for /v1/responses
        if request.max_tokens is not None:
            payload["max_output_tokens"] = request.max_tokens

        # Reasoning models (gpt-6.1-terra, o1, etc.) on gyai.dev do not support temperature or top_p
        is_reasoning_or_modern = any(
            k in model.lower() for k in ("terra", "sol", "luna", "o1", "o3", "gpt-6", "gpt-5")
        ) or "gyai.dev" in self.api_base_url

        if not is_reasoning_or_modern:
            if request.temperature is not None:
                payload["temperature"] = request.temperature
            if request.top_p is not None:
                payload["top_p"] = request.top_p

        # Tools formatting for Responses API
        if request.tools:
            converted_tools = []
            for t in request.tools:
                if "function" in t:
                    fn = t["function"]
                    converted_tools.append({
                        "type": "function",
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    })
                else:
                    converted_tools.append(t)
            payload["tools"] = converted_tools

        return payload

    def _parse_responses_data(self, data: dict, default_model: str) -> ChatResponse:
        """Parse response payload from /v1/responses endpoint."""
        content = ""
        tool_calls = None
        finish_reason = data.get("status") or "completed"

        for item in data.get("output", []):
            itype = item.get("type")
            if itype == "message" or item.get("role") == "assistant":
                item_content = item.get("content", "")
                if isinstance(item_content, str):
                    content += item_content
                elif isinstance(item_content, list):
                    for part in item_content:
                        if isinstance(part, dict):
                            if part.get("type") == "output_text":
                                content += part.get("text", "")
                            elif "text" in part:
                                content += part["text"]
                        elif isinstance(part, str):
                            content += part
            elif itype == "function_call":
                if tool_calls is None:
                    tool_calls = []
                call_id = item.get("call_id") or item.get("id", "")
                name = item.get("name", "")
                args = item.get("arguments", "{}")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
                    },
                })

        if not content and "output_text" in data:
            content = data["output_text"]

        usage_dict = data.get("usage", {})
        prompt_tokens = usage_dict.get("input_tokens") or usage_dict.get("prompt_tokens", 0)
        completion_tokens = usage_dict.get("output_tokens") or usage_dict.get("completion_tokens", 0)
        total_tokens = usage_dict.get("total_tokens", prompt_tokens + completion_tokens)

        return ChatResponse(
            content=content,
            model=data.get("model", default_model),
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            raw_response=data,
        )

    def _parse_chat_completion_data(self, data: dict, default_model: str) -> ChatResponse:
        """Parse response payload from /v1/chat/completions endpoint."""
        choice = data["choices"][0]
        usage = data.get("usage", {})
        message = choice.get("message", {})

        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = []
            for tc in message["tool_calls"]:
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "type": tc.get("type", "function"),
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                })

        return ChatResponse(
            content=message.get("content") or "",
            model=data.get("model", default_model),
            usage=TokenUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
            finish_reason=choice.get("finish_reason"),
            tool_calls=tool_calls,
            raw_response=data,
        )

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
        headers = self._get_request_headers()
        model_name = request.model or self.default_model

        if getattr(self, "use_responses_api", False):
            payload = self._build_responses_payload(request, stream=False)
            resp = await self._client.post(
                "/v1/responses",
                json=payload,
                headers=headers if headers else None,
            )
            resp.raise_for_status()
            data = resp.json()
            if "output" in data or "output_text" in data:
                return self._parse_responses_data(data, model_name)
            if "choices" in data:
                return self._parse_chat_completion_data(data, model_name)

        payload = self._build_payload(request)
        resp = await self._client.post(
            "/v1/chat/completions",
            json=payload,
            headers=headers if headers else None,
        )
        resp.raise_for_status()
        data = resp.json()
        return self._parse_chat_completion_data(data, model_name)

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Streaming chat completion — yields content chunks."""
        headers = self._get_request_headers()

        if getattr(self, "use_responses_api", False):
            payload = self._build_responses_payload(request, stream=True)
            async with self._client.stream(
                "POST",
                "/v1/responses",
                json=payload,
                headers=headers if headers else None,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        if chunk.get("type") == "response.output_text.delta":
                            if text := chunk.get("delta"):
                                yield text
                        else:
                            content = (
                                chunk.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content")
                            )
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        continue
            return

        payload = self._build_payload(request, stream=True)
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
                data_str = line[6:].strip()
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
