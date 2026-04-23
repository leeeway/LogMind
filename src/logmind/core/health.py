"""
Health Check — Deep Component Probing

Probes all infrastructure dependencies and returns per-component
status with latency measurements. Designed for:
  - Kubernetes liveness/readiness probes
  - Load balancer health checks
  - Operational dashboards

Components checked:
  - Database (PostgreSQL): SELECT 1
  - Redis: PING
  - Elasticsearch: cluster health
  - Celery: inspect active workers (best-effort, non-blocking)
"""

import time
from dataclasses import dataclass, field

from logmind.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ComponentHealth:
    """Health status of a single infrastructure component."""
    status: str = "ok"          # ok / degraded / down
    latency_ms: float = 0.0
    detail: str | dict = ""
    error: str | None = None


@dataclass
class SystemHealth:
    """Aggregated system health."""
    status: str = "ok"          # ok / degraded / down
    version: str = "1.9.0"
    components: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "version": self.version,
            "components": self.components,
        }


async def check_database() -> ComponentHealth:
    """Probe database connectivity with SELECT 1."""
    try:
        from sqlalchemy import text
        from logmind.core.database import async_session_factory

        t0 = time.monotonic()
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        latency = (time.monotonic() - t0) * 1000

        return ComponentHealth(status="ok", latency_ms=round(latency, 1))
    except Exception as e:
        logger.error("health_db_failed", error=str(e))
        return ComponentHealth(status="down", error=str(e)[:200])


async def check_redis() -> ComponentHealth:
    """Probe Redis with PING."""
    try:
        from logmind.core.redis import get_redis_client

        t0 = time.monotonic()
        r = get_redis_client()
        pong = await r.ping()
        latency = (time.monotonic() - t0) * 1000

        if pong:
            return ComponentHealth(status="ok", latency_ms=round(latency, 1))
        return ComponentHealth(status="degraded", detail="PING returned False")
    except Exception as e:
        logger.error("health_redis_failed", error=str(e))
        return ComponentHealth(status="down", error=str(e)[:200])


async def check_elasticsearch() -> ComponentHealth:
    """Probe Elasticsearch cluster health."""
    try:
        from logmind.core.elasticsearch import get_es_client

        t0 = time.monotonic()
        es = get_es_client()
        info = await es.cluster.health()
        latency = (time.monotonic() - t0) * 1000

        cluster_status = info.get("status", "unknown")
        status_map = {"green": "ok", "yellow": "degraded", "red": "down"}

        return ComponentHealth(
            status=status_map.get(cluster_status, "degraded"),
            latency_ms=round(latency, 1),
            detail={
                "cluster": cluster_status,
                "nodes": info.get("number_of_nodes", 0),
                "active_shards": info.get("active_shards", 0),
            },
        )
    except Exception as e:
        logger.error("health_es_failed", error=str(e))
        return ComponentHealth(status="down", error=str(e)[:200])


def check_celery() -> ComponentHealth:
    """
    Probe Celery worker availability (best-effort).

    Uses synchronous inspect with a short timeout.
    Returns degraded (not down) on failure since Celery
    workers may be temporarily busy.
    """
    try:
        from logmind.core.celery_app import celery_app

        t0 = time.monotonic()
        inspector = celery_app.control.inspect(timeout=2.0)
        active = inspector.active()
        latency = (time.monotonic() - t0) * 1000

        if active is None:
            return ComponentHealth(
                status="degraded",
                latency_ms=round(latency, 1),
                detail="No workers responded (may be starting up)",
            )

        worker_count = len(active)
        active_tasks = sum(len(tasks) for tasks in active.values())

        return ComponentHealth(
            status="ok",
            latency_ms=round(latency, 1),
            detail={
                "workers": worker_count,
                "active_tasks": active_tasks,
            },
        )
    except Exception as e:
        logger.warning("health_celery_failed", error=str(e))
        return ComponentHealth(
            status="degraded",
            error=str(e)[:200],
            detail="Celery inspect failed (workers may still be operational)",
        )


async def get_system_health() -> SystemHealth:
    """
    Run all health checks and return aggregated status.

    Overall status logic:
      - "down" if any critical component (DB, ES) is down
      - "degraded" if any component is degraded or Celery/Redis is down
      - "ok" if all components are healthy
    """
    db = await check_database()
    redis = await check_redis()
    es = await check_elasticsearch()
    # Celery inspect is synchronous — run in thread to avoid blocking
    import asyncio
    celery = await asyncio.to_thread(check_celery)

    components = {
        "database": _component_to_dict(db),
        "redis": _component_to_dict(redis),
        "elasticsearch": _component_to_dict(es),
        "celery": _component_to_dict(celery),
    }

    # Determine overall status
    statuses = [db.status, redis.status, es.status, celery.status]
    if db.status == "down" or es.status == "down":
        overall = "down"
    elif any(s != "ok" for s in statuses):
        overall = "degraded"
    else:
        overall = "ok"

    return SystemHealth(status=overall, components=components)


def _component_to_dict(c: ComponentHealth) -> dict:
    """Convert ComponentHealth to JSON-serializable dict."""
    result = {"status": c.status}
    if c.latency_ms > 0:
        result["latency_ms"] = c.latency_ms
    if c.detail:
        result["detail"] = c.detail
    if c.error:
        result["error"] = c.error
    return result
