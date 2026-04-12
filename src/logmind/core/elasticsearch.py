"""
Elasticsearch Client

Async ES client for log retrieval and vector search.
Supports configurable arbitrary index patterns per business line.
"""

from functools import lru_cache

from elasticsearch import AsyncElasticsearch

from logmind.core.config import get_settings


@lru_cache
def get_es_client() -> AsyncElasticsearch:
    """Create a cached Elasticsearch async client."""
    settings = get_settings()

    kwargs: dict = {
        "hosts": settings.es_hosts_list,
        "verify_certs": settings.es_verify_certs,
        "request_timeout": settings.es_request_timeout,
    }

    if settings.es_username and settings.es_password:
        kwargs["basic_auth"] = (settings.es_username, settings.es_password)

    return AsyncElasticsearch(**kwargs)


async def close_es() -> None:
    """Close ES client on shutdown."""
    client = get_es_client()
    await client.close()


async def check_es_health() -> dict:
    """Check ES cluster health status."""
    client = get_es_client()
    try:
        health = await client.cluster.health()
        return {
            "status": health["status"],
            "cluster_name": health["cluster_name"],
            "number_of_nodes": health["number_of_nodes"],
        }
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}
