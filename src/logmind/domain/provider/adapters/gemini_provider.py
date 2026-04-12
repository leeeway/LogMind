"""
Google Gemini Provider Adapter

Uses the Gemini REST API via Google AI Studio or Vertex AI.
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


@register_provider("gemini")
class GeminiProvider(BaseProvider):
    """Google Gemini API provider."""

    def __init__(
        self,
        api_base_url: str = "https://generativelanguage.googleapis.com",
        api_key: str = "",
        default_model: str = "gemini-2.0-flash",
        **kwargs,
    ):
        super().__init__(api_base_url, api_key, default_model, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=self.api_base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    def _build_payload(self, request: ChatRequest) -> dict:
        """Build Gemini generateContent payload."""
        contents = []
        system_instruction = None

        for m in request.messages:
            if m.role == "system":
                system_instruction = {"parts": [{"text": m.content}]}
            else:
                role = "user" if m.role == "user" else "model"
                contents.append({
                    "role": role,
                    "parts": [{"text": m.content}],
                })

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
                "topP": request.top_p,
            },
        }

        if system_instruction:
            payload["systemInstruction"] = system_instruction

        return payload

    async def chat(self, request: ChatRequest) -> ChatResponse:
        model = request.model or self.default_model
        url = f"/v1beta/models/{model}:generateContent?key={self.api_key}"
        payload = self._build_payload(request)

        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        # Extract content
        candidates = data.get("candidates", [])
        content = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            content = "".join(p.get("text", "") for p in parts)

        # Extract usage
        usage_meta = data.get("usageMetadata", {})
        return ChatResponse(
            content=content,
            model=model,
            usage=TokenUsage(
                prompt_tokens=usage_meta.get("promptTokenCount", 0),
                completion_tokens=usage_meta.get("candidatesTokenCount", 0),
                total_tokens=usage_meta.get("totalTokenCount", 0),
            ),
            finish_reason=candidates[0].get("finishReason") if candidates else None,
            raw_response=data,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        model = request.model or self.default_model
        url = f"/v1beta/models/{model}:streamGenerateContent?key={self.api_key}&alt=sse"
        payload = self._build_payload(request)

        async with self._client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for p in parts:
                            if text := p.get("text"):
                                yield text
                except json.JSONDecodeError:
                    continue

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        model = request.model or "text-embedding-004"
        url = f"/v1beta/models/{model}:embedContent?key={self.api_key}"

        embeddings = []
        total_tokens = 0
        for text in request.texts:
            resp = await self._client.post(url, json={
                "model": f"models/{model}",
                "content": {"parts": [{"text": text}]},
            })
            resp.raise_for_status()
            data = resp.json()
            embeddings.append(data["embedding"]["values"])

        return EmbeddingResponse(
            embeddings=embeddings,
            model=model,
            usage=TokenUsage(total_tokens=total_tokens),
        )

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get(
                f"/v1beta/models?key={self.api_key}"
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
