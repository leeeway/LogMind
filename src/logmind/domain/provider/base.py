"""
AI Provider — Abstract Base Class

All provider implementations must extend BaseProvider.
Supports chat completion, streaming, and embeddings.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator


class ProviderType(str, Enum):
    OPENAI = "openai"
    CLAUDE = "claude"
    GEMINI = "gemini"
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"
    SUBAPI = "subapi"


@dataclass
class ChatMessage:
    """A single message in a conversation."""
    role: str       # "system" | "user" | "assistant"
    content: str


@dataclass
class ChatRequest:
    """Chat completion request."""
    messages: list[ChatMessage]
    model: str | None = None          # Override default model
    temperature: float = 0.3
    max_tokens: int = 4096
    top_p: float = 0.9
    stream: bool = False
    extra_params: dict = field(default_factory=dict)


@dataclass
class TokenUsage:
    """Token usage statistics."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResponse:
    """Chat completion response."""
    content: str
    model: str
    usage: TokenUsage
    finish_reason: str | None = None
    raw_response: dict | None = None


@dataclass
class EmbeddingRequest:
    """Embedding generation request."""
    texts: list[str]
    model: str | None = None


@dataclass
class EmbeddingResponse:
    """Embedding generation response."""
    embeddings: list[list[float]]
    model: str
    usage: TokenUsage


class BaseProvider(ABC):
    """
    AI Provider Abstract Base Class.

    All provider adapters must implement:
    - chat(): synchronous chat completion
    - chat_stream(): streaming chat completion
    - health_check(): provider availability check

    Optional:
    - embed(): text embedding for RAG
    """

    provider_type: ProviderType

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        default_model: str,
        **kwargs,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.extra_config = kwargs

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Synchronous chat completion."""
        ...

    @abstractmethod
    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Streaming chat completion — yields content chunks."""
        ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """
        Text embedding (optional — for RAG).
        Override in providers that support embeddings.
        """
        raise NotImplementedError(
            f"Provider [{self.provider_type.value}] does not support embeddings"
        )

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is available."""
        ...

    async def close(self) -> None:
        """Release resources (HTTP clients, etc.)."""
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"type={self.provider_type.value} "
            f"model={self.default_model}>"
        )
