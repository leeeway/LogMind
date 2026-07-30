"""Redis-backed deduplication and recovery state for HTTP access incidents."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from logmind.core.config import get_settings
from logmind.core.logging import get_logger
from logmind.domain.http_access.models import AccessIncident, AccessRecovery

logger = get_logger(__name__)

_STATE_KEY = "logmind:http_access:alert_state:v1"
_PATROL_LEASE_KEY = "logmind:http_access:patrol:lease:v1"
_SUMMARY_COOLDOWN_KEY = "logmind:http_access:summary:last_sent:v1"
_SUMMARY_P0_LEASE_KEY = "logmind:http_access:summary:p0:lease:v1"
_RUN_SNAPSHOT_KEY = "logmind:http_access:last_run:v1"
_RUN_HISTORY_KEY = "logmind:http_access:run_history:v1"
_STATE_TTL_SECONDS = 14 * 24 * 60 * 60
_RUN_SNAPSHOT_TTL_SECONDS = 7 * 24 * 60 * 60
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


@dataclass(slots=True)
class AccessNotificationBatch:
    due: list[AccessIncident]
    recoveries: list[AccessRecovery]
    next_state: dict[str, dict]
    previous_state: dict[str, dict]
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class AccessLease:
    key: str
    token: str


class HttpAccessAlertState:
    """Track active incidents without creating one notification per patrol."""

    def __init__(self, redis=None):
        self._redis = redis

    @property
    def redis(self):
        if self._redis is None:
            from logmind.core.redis import get_redis_client

            self._redis = get_redis_client()
        return self._redis

    async def _load(self) -> dict[str, dict]:
        try:
            raw = await self.redis.get(_STATE_KEY)
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            logger.warning("http_access_state_load_failed", error=str(exc))
            return {}

    async def evaluate(
        self,
        incidents: list[AccessIncident],
        *,
        now: datetime | None = None,
    ) -> AccessNotificationBatch:
        current_time = now or datetime.now(UTC)
        previous = await self._load()
        current_by_key = {incident.key: incident for incident in incidents}
        next_state: dict[str, dict] = {}
        due: list[AccessIncident] = []
        recoveries: list[AccessRecovery] = []
        dedup_delta = timedelta(minutes=get_settings().http_access_dedup_minutes)

        for key, incident in current_by_key.items():
            old = previous.get(key, {})
            streak = int(old.get("streak", 0)) + 1
            required_streak = (
                2
                if incident.kind in {"traffic_drop", "latency"}
                else 1
            )
            was_active = bool(old.get("active", False))
            active = was_active or streak >= required_streak
            last_notified = _parse_datetime(old.get("last_notified"))

            should_notify = False
            if active:
                should_notify = (
                    not was_active
                    or _is_escalation(
                        incident.priority,
                        str(old.get("priority", "P2")),
                    )
                    or last_notified is None
                    or current_time - last_notified >= dedup_delta
                )
            if should_notify:
                due.append(incident)

            next_state[key] = {
                "source": incident.source,
                "site": incident.site,
                "kind": incident.kind,
                "priority": incident.priority,
                "route_key": incident.route_key,
                "active": active,
                "streak": streak,
                "normal_streak": 0,
                "first_seen": old.get("first_seen") or current_time.isoformat(),
                "last_seen": current_time.isoformat(),
                "last_notified": old.get("last_notified"),
            }

        for key, old in previous.items():
            if key in current_by_key:
                continue
            if not old.get("active"):
                continue
            normal_streak = int(old.get("normal_streak", 0)) + 1
            if normal_streak >= 2:
                recoveries.append(
                    AccessRecovery(
                        source=str(old.get("source", "")),
                        site=str(old.get("site", "")),
                        kind=str(old.get("kind", "")),
                        priority=str(old.get("priority", "P1")),
                        route_key=str(old.get("route_key", "")),
                    )
                )
                continue
            retained = dict(old)
            retained["normal_streak"] = normal_streak
            next_state[key] = retained

        return AccessNotificationBatch(
            due=due,
            recoveries=recoveries,
            next_state=next_state,
            previous_state=previous,
            evaluated_at=current_time,
        )

    async def save(
        self,
        batch: AccessNotificationBatch,
        *,
        delivered: bool,
    ) -> None:
        state = {key: dict(value) for key, value in batch.next_state.items()}
        if delivered:
            for incident in batch.due:
                if incident.key in state:
                    state[incident.key]["last_notified"] = (
                        batch.evaluated_at.isoformat()
                    )
        else:
            # Retry failed recovery notifications on the next normal window.
            for recovery in batch.recoveries:
                old = batch.previous_state.get(recovery.key)
                if old:
                    retained = dict(old)
                    retained["normal_streak"] = 1
                    state[recovery.key] = retained

        try:
            await self.redis.setex(
                _STATE_KEY,
                _STATE_TTL_SECONDS,
                json.dumps(state, ensure_ascii=False),
            )
        except Exception as exc:
            logger.warning("http_access_state_save_failed", error=str(exc))

    async def acquire_patrol_lease(self, *, ttl_seconds: int) -> AccessLease | None:
        """Prevent overlapping Beat deliveries or multiple workers from running."""
        return await self._acquire_lease(
            _PATROL_LEASE_KEY,
            ttl_seconds=max(60, ttl_seconds),
        )

    async def release_patrol_lease(self, lease: AccessLease) -> None:
        await self._release_lease(lease)

    async def reserve_summary(
        self,
        incidents: list[AccessIncident],
    ) -> AccessLease | None:
        """
        Atomically reserve one summary delivery.

        P1 uses the global cooldown key. P0 bypasses that cooldown but still
        receives a short lease so concurrent workers cannot duplicate it.
        """
        is_p0 = any(incident.priority == "P0" for incident in incidents)
        key = _SUMMARY_P0_LEASE_KEY if is_p0 else _SUMMARY_COOLDOWN_KEY
        ttl_seconds = (
            60
            if is_p0
            else getattr(
                get_settings(),
                "http_access_notification_cooldown_minutes",
                30,
            )
            * 60
        )
        return await self._acquire_lease(
            key,
            ttl_seconds=ttl_seconds,
        )

    async def finish_summary(
        self,
        lease: AccessLease,
        *,
        delivered: bool,
    ) -> None:
        # Successful reservations intentionally live until their TTL expires.
        if not delivered:
            await self._release_lease(lease)

    async def save_run_snapshot(self, result: dict) -> None:
        payload = json.dumps(result, ensure_ascii=False)
        history_limit = getattr(
            get_settings(),
            "http_access_run_history_limit",
            288,
        )
        try:
            await self.redis.setex(
                _RUN_SNAPSHOT_KEY,
                _RUN_SNAPSHOT_TTL_SECONDS,
                payload,
            )
            await self.redis.lpush(_RUN_HISTORY_KEY, payload)
            await self.redis.ltrim(_RUN_HISTORY_KEY, 0, history_limit - 1)
            await self.redis.expire(
                _RUN_HISTORY_KEY,
                _RUN_SNAPSHOT_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("http_access_run_snapshot_save_failed", error=str(exc))

    async def get_run_snapshot(self) -> dict:
        try:
            raw = await self.redis.get(_RUN_SNAPSHOT_KEY)
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            logger.warning("http_access_run_snapshot_load_failed", error=str(exc))
            return {}

    async def get_run_history(self, *, limit: int = 288) -> list[dict]:
        max_limit = getattr(
            get_settings(),
            "http_access_run_history_limit",
            288,
        )
        safe_limit = min(max(1, limit), max_limit)
        try:
            rows = await self.redis.lrange(
                _RUN_HISTORY_KEY,
                0,
                safe_limit - 1,
            )
        except Exception as exc:
            logger.warning("http_access_run_history_load_failed", error=str(exc))
            return []

        history: list[dict] = []
        for raw in rows:
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict):
                history.append(parsed)
        return history

    async def _acquire_lease(
        self,
        key: str,
        *,
        ttl_seconds: int,
    ) -> AccessLease | None:
        token = secrets.token_urlsafe(18)
        try:
            acquired = await self.redis.set(
                key,
                token,
                ex=ttl_seconds,
                nx=True,
            )
        except Exception as exc:
            logger.warning("http_access_lease_acquire_failed", key=key, error=str(exc))
            # Redis failure must not suppress a real alert.
            return AccessLease(key="", token="")
        return AccessLease(key=key, token=token) if acquired else None

    async def _release_lease(self, lease: AccessLease) -> None:
        if not lease.key or not lease.token:
            return
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        try:
            await self.redis.eval(script, 1, lease.key, lease.token)
        except Exception as exc:
            logger.warning(
                "http_access_lease_release_failed",
                key=lease.key,
                error=str(exc),
            )


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _is_escalation(current: str, previous: str) -> bool:
    return _PRIORITY_RANK.get(current, 9) < _PRIORITY_RANK.get(previous, 9)


http_access_alert_state = HttpAccessAlertState()
