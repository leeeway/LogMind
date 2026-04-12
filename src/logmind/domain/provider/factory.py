"""
Provider Factory — Registration & Instantiation

Uses decorator-based registration pattern for zero-code provider addition.
"""

from typing import Type

from logmind.core.logging import get_logger
from logmind.domain.provider.base import BaseProvider, ProviderType

logger = get_logger(__name__)

# ── Global Provider Registry ─────────────────────────────
_PROVIDER_REGISTRY: dict[str, Type[BaseProvider]] = {}


def register_provider(provider_type: str):
    """
    Decorator: Register a provider implementation.

    Usage:
        @register_provider("openai")
        class OpenAIProvider(BaseProvider):
            ...
    """
    def decorator(cls: Type[BaseProvider]):
        _PROVIDER_REGISTRY[provider_type] = cls
        cls.provider_type = ProviderType(provider_type)
        logger.info("provider_registered", provider_type=provider_type, cls=cls.__name__)
        return cls
    return decorator


def create_provider(
    provider_type: str,
    api_base_url: str,
    api_key: str,
    default_model: str,
    **kwargs,
) -> BaseProvider:
    """
    Factory: Create a provider instance by type.

    Raises ValueError if the provider type is not registered.
    """
    if provider_type not in _PROVIDER_REGISTRY:
        available = ", ".join(sorted(_PROVIDER_REGISTRY.keys()))
        raise ValueError(
            f"Unknown provider type: '{provider_type}'. "
            f"Available providers: [{available}]"
        )

    provider_cls = _PROVIDER_REGISTRY[provider_type]
    return provider_cls(
        api_base_url=api_base_url,
        api_key=api_key,
        default_model=default_model,
        **kwargs,
    )


def get_registered_providers() -> list[str]:
    """Return list of all registered provider type names."""
    return sorted(_PROVIDER_REGISTRY.keys())


def is_provider_registered(provider_type: str) -> bool:
    """Check if a provider type is registered."""
    return provider_type in _PROVIDER_REGISTRY
