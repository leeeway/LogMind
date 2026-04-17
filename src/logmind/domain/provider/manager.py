"""
Provider Manager — Load Balancing, Failover & Caching

Manages provider instances:
- Priority-based selection
- Automatic failover to next provider on failure
- Instance caching (reuse HTTP clients)
- Health monitoring
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from logmind.core.exceptions import AllProvidersFailedError, ProviderError
from logmind.core.logging import get_logger
from logmind.domain.provider.base import BaseProvider, ChatRequest, ChatResponse
from logmind.domain.provider.factory import create_provider
import logmind.domain.provider.adapters  # Eagerly load to run decorators
from logmind.domain.provider.models import ProviderConfig
from logmind.shared.encryption import decrypt_value

logger = get_logger(__name__)

# Cache provider instances by config_id
_provider_cache: dict[str, BaseProvider] = {}


class ProviderManager:
    """
    Manages AI provider lifecycle, selection, and failover.

    - Retrieves provider configs from DB (tenant-scoped)
    - Creates/caches provider instances
    - Routes requests with priority-based fallback
    """

    async def get_provider(
        self,
        session: AsyncSession,
        tenant_id: str,
        provider_config_id: str | None = None,
    ) -> BaseProvider:
        """
        Get a specific or highest-priority active provider for a tenant.
        """
        if provider_config_id:
            config = await self._get_config_by_id(session, provider_config_id, tenant_id)
            if not config:
                raise ProviderError("unknown", f"Config {provider_config_id} not found")
            return self._create_or_get_cached(config)

        configs = await self._get_sorted_configs(session, tenant_id)
        if not configs:
            raise ProviderError("none", f"No active providers for tenant {tenant_id}")
        return self._create_or_get_cached(configs[0])

    async def chat_with_fallback(
        self,
        session: AsyncSession,
        tenant_id: str,
        request: ChatRequest,
        preferred_provider_id: str | None = None,
    ) -> tuple[ChatResponse, str]:
        """
        Chat with automatic failover.

        Tries the preferred provider first, then falls back through
        remaining providers in priority order.

        Returns: (response, provider_config_id)
        """
        configs = await self._get_sorted_configs(session, tenant_id)
        if not configs:
            raise AllProvidersFailedError(tenant_id)

        # Move preferred provider to front
        if preferred_provider_id:
            configs = sorted(
                configs,
                key=lambda c: (c.id != preferred_provider_id, -c.priority),
            )

        errors: list[str] = []
        for config in configs:
            try:
                provider = self._create_or_get_cached(config)
                response = await provider.chat(request)
                logger.info(
                    "provider_chat_success",
                    provider=config.name,
                    model=response.model,
                    tokens=response.usage.total_tokens,
                )
                return response, config.id
            except Exception as e:
                error_msg = f"{config.name} ({config.provider_type}): {e}"
                errors.append(error_msg)
                logger.warning("provider_chat_failed", provider=config.name, error=str(e))
                # Invalidate cached instance on failure
                _provider_cache.pop(config.id, None)
                continue

        raise AllProvidersFailedError(tenant_id)

    async def health_check_all(
        self, session: AsyncSession, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Check health of all active providers for a tenant."""
        configs = await self._get_sorted_configs(session, tenant_id)
        results = []
        for config in configs:
            try:
                provider = self._create_or_get_cached(config)
                is_healthy = await provider.health_check()
                results.append({
                    "provider_id": config.id,
                    "provider_type": config.provider_type,
                    "name": config.name,
                    "is_healthy": is_healthy,
                    "error": None,
                })
            except Exception as e:
                results.append({
                    "provider_id": config.id,
                    "provider_type": config.provider_type,
                    "name": config.name,
                    "is_healthy": False,
                    "error": str(e),
                })
        return results

    # ── Internal ─────────────────────────────────────────

    async def _get_config_by_id(
        self, session: AsyncSession, config_id: str, tenant_id: str
    ) -> ProviderConfig | None:
        stmt = select(ProviderConfig).where(
            ProviderConfig.id == config_id,
            ProviderConfig.tenant_id == tenant_id,
            ProviderConfig.is_active == True,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_sorted_configs(
        self, session: AsyncSession, tenant_id: str
    ) -> list[ProviderConfig]:
        """Get all active providers sorted by priority (desc)."""
        stmt = (
            select(ProviderConfig)
            .where(
                ProviderConfig.tenant_id == tenant_id,
                ProviderConfig.is_active == True,
            )
            .order_by(ProviderConfig.priority.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    def _create_or_get_cached(self, config: ProviderConfig) -> BaseProvider:
        """Create a provider instance or return cached one."""
        if config.id in _provider_cache:
            return _provider_cache[config.id]

        # Decrypt API key
        api_key = ""
        if config.api_key_encrypted:
            try:
                api_key = decrypt_value(config.api_key_encrypted)
            except Exception:
                api_key = config.api_key_encrypted  # Fallback: use as-is

        # Parse model params
        model_params = {}
        if config.model_params:
            try:
                model_params = json.loads(config.model_params)
            except json.JSONDecodeError:
                pass

        provider = create_provider(
            provider_type=config.provider_type,
            api_base_url=config.api_base_url,
            api_key=api_key,
            default_model=config.default_model,
            **model_params,
        )

        _provider_cache[config.id] = provider
        return provider

    @staticmethod
    async def clear_cache():
        """Close all cached providers and clear the cache."""
        for provider in _provider_cache.values():
            await provider.close()
        _provider_cache.clear()


# Singleton
provider_manager = ProviderManager()
