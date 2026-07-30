"""
Live Log Tail — Real-time Log Streaming via WebSocket

Provides a WebSocket endpoint that streams new Elasticsearch logs
to connected clients in near real-time (1s polling).

Protocol:
  Client → Server:
    {"action": "subscribe", "business_line_id": "...",
     "lookback_seconds": 300, "filters": {...}}
    {"action": "pause"}
    {"action": "resume"}
    "ping"

  Server → Client:
    {"type": "logs", "data": [...], "rate": N, "total": N}
    {"type": "status", "state": "loading_history|streaming|paused", "rate": N}
    {"type": "heartbeat", "ts": "..."}
    {"type": "pong"}
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import Query, WebSocket, WebSocketDisconnect

from logmind.core.elasticsearch import get_es_client
from logmind.core.logging import get_logger
from logmind.core.security import decode_access_token
from logmind.domain.log.service import LogService, build_base_severity_filter

logger = get_logger(__name__)

POLL_INTERVAL = 1.0  # seconds
MAX_LOGS_PER_PUSH = 200
INITIAL_LOG_LIMIT = 200
DEFAULT_LOOKBACK_SECONDS = 300
MIN_LOOKBACK_SECONDS = 30
MAX_LOOKBACK_SECONDS = 3600
INGESTION_DELAY_SECONDS = 60
_QUERY_STRING_SPECIAL_RE = re.compile(r'([+\-=&|><!(){}\[\]^"~*?:\\/])')


def _escape_query_string(value: str) -> str:
    return _QUERY_STRING_SPECIAL_RE.sub(r"\\\1", value)


async def _fetch_latest_logs(
    es_index: str,
    since: datetime,
    filters: dict | None = None,
    size: int = MAX_LOGS_PER_PUSH,
    *,
    newest_first: bool = False,
) -> tuple[list[dict], datetime, str | None]:
    """Fetch logs newer than ``since``.

    Initial history queries request the newest records and reverse them before
    returning so a busy service does not spend many polling cycles replaying
    stale logs. The third return value contains a query error for the WebSocket
    client; an empty result is otherwise a valid response.
    """
    es = get_es_client()
    if not es:
        return [], since, "Elasticsearch 未配置或当前不可用"

    must = [{"range": {"@timestamp": {"gt": since.isoformat()}}}]

    if filters:
        if filters.get("keyword"):
            keyword = str(filters["keyword"]).strip()[:200]
            if keyword:
                must.append(
                    {
                        "bool": {
                            "should": [
                                {"match_phrase": {"message": keyword}},
                                {
                                    "wildcard": {
                                        "message.keyword": {
                                            "value": f"*{keyword}*",
                                            "case_insensitive": True,
                                        }
                                    }
                                },
                                {
                                    "query_string": {
                                        "query": f"*{_escape_query_string(keyword)}*",
                                        "default_field": "message",
                                        "analyze_wildcard": True,
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )
        if filters.get("level"):
            must.append(build_base_severity_filter(str(filters["level"])))

    sort_order = "desc" if newest_first else "asc"
    body = {
        "query": {"bool": {"must": must}},
        "sort": [{"@timestamp": {"order": sort_order, "unmapped_type": "date"}}],
        "size": max(1, min(size, MAX_LOGS_PER_PUSH)),
        "_source": [
            "@timestamp",
            "message",
            "msg",
            "log",
            "content",
            "level",
            "severity",
            "loglevel",
            "log.level",
            "gy.filetype",
            "gy.domain",
            "gy.hostname",
            "host.name",
            "kubernetes.container.name",
        ],
    }

    try:
        resp = await es.search(index=es_index, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        if newest_first:
            hits = list(reversed(hits))
        logs = []
        new_cursor = since
        for h in hits:
            src = h.get("_source", {})
            ts_str = src.get("@timestamp", "")
            gy = src.get("gy", {}) if isinstance(src.get("gy"), dict) else {}
            kubernetes = (
                src.get("kubernetes", {})
                if isinstance(src.get("kubernetes"), dict)
                else {}
            )
            container = (
                kubernetes.get("container", {})
                if isinstance(kubernetes.get("container"), dict)
                else {}
            )
            host = src.get("host", {}) if isinstance(src.get("host"), dict) else {}
            logs.append(
                {
                    "id": h.get("_id", ""),
                    "timestamp": ts_str,
                    "message": LogService._extract_message(src),
                    "level": _extract_level(src),
                    "source": (
                        gy.get("domain", "")
                        or container.get("name", "")
                        or host.get("name", "")
                    ),
                }
            )
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts_dt > new_cursor:
                        new_cursor = ts_dt
                except (ValueError, TypeError):
                    pass
        return logs, new_cursor, None
    except Exception as e:
        logger.warning("live_tail_es_error", error=str(e))
        return [], since, f"实时日志查询失败: {str(e)[:200]}"


def _clamp_lookback_seconds(value: object) -> int:
    """Parse a client lookback value while enforcing a safe query window."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_LOOKBACK_SECONDS
    return max(MIN_LOOKBACK_SECONDS, min(seconds, MAX_LOOKBACK_SECONDS))


async def _resolve_live_tail_index(tenant_id: str, business_line_id: str | None) -> str:
    """Resolve a live-tail subscription to a tenant-owned business line index."""
    from logmind.core.database import get_db_context
    from logmind.domain.tenant.access import get_active_business_line_or_404

    async with get_db_context() as session:
        biz = await get_active_business_line_or_404(
            session,
            tenant_id,
            business_line_id,
        )
        if not biz.es_index_pattern:
            raise ValueError("Business line has no ES index pattern")
        return biz.es_index_pattern


def _extract_level(src: dict) -> str:
    """Use the canonical Java/C# level parser used by normal log search."""
    level = LogService._extract_level(src).upper()
    return "WARN" if level == "WARNING" else (level or "INFO")


async def live_tail_endpoint(websocket: WebSocket, token: str = Query(...)):
    """WebSocket endpoint for live log tailing."""
    # Authenticate
    try:
        payload = decode_access_token(token)
        tenant_id = payload.get("tenant_id", "")
        if not tenant_id:
            await websocket.close(code=4001, reason="Invalid token")
            return
    except Exception:
        try:
            await websocket.accept()
            await websocket.close(code=4001, reason="Authentication failed")
        except Exception:
            pass
        return

    await websocket.accept()
    logger.info("live_tail_connected", tenant_id=tenant_id)

    # State
    index_pattern = ""
    filters: dict = {}
    paused = True
    cursor = datetime.now(timezone.utc) - timedelta(seconds=DEFAULT_LOOKBACK_SECONDS)
    initial_load = False
    log_count = 0
    rate_window: list[int] = []
    last_query_error = ""

    try:
        while True:
            # Check for incoming messages (non-blocking)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=POLL_INTERVAL)

                if data == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                    continue

                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    await websocket.send_text(
                        json.dumps(
                            {"type": "error", "message": "无效的订阅请求"},
                            ensure_ascii=False,
                        )
                    )
                    continue

                action = msg.get("action", "")

                if action == "subscribe":
                    try:
                        index_pattern = await _resolve_live_tail_index(
                            tenant_id,
                            msg.get("business_line_id"),
                        )
                    except Exception as e:
                        paused = True
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": str(e),
                                },
                                ensure_ascii=False,
                            )
                        )
                        continue

                    raw_filters = msg.get("filters")
                    filters = raw_filters if isinstance(raw_filters, dict) else {}
                    lookback_seconds = _clamp_lookback_seconds(
                        msg.get("lookback_seconds")
                    )
                    cursor = datetime.now(timezone.utc) - timedelta(
                        seconds=lookback_seconds
                    )
                    paused = False
                    initial_load = True
                    log_count = 0
                    rate_window = []
                    last_query_error = ""
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "status",
                                "state": "loading_history",
                                "rate": 0,
                                "index": index_pattern,
                                "lookback_seconds": lookback_seconds,
                            },
                            ensure_ascii=False,
                        )
                    )

                elif action == "pause":
                    paused = True
                    await websocket.send_text(
                        json.dumps(
                            {"type": "status", "state": "paused", "rate": 0}
                        )
                    )

                elif action == "resume":
                    if index_pattern:
                        paused = False
                        await websocket.send_text(
                            json.dumps(
                                {"type": "status", "state": "streaming", "rate": 0}
                            )
                        )

            except asyncio.TimeoutError:
                pass

            # Push new logs if not paused
            if not paused:
                was_initial_load = initial_load
                logs, new_cursor, query_error = await _fetch_latest_logs(
                    index_pattern,
                    cursor,
                    filters,
                    size=INITIAL_LOG_LIMIT if was_initial_load else MAX_LOGS_PER_PUSH,
                    newest_first=was_initial_load,
                )

                if query_error:
                    if query_error != last_query_error:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": query_error,
                                    "recoverable": True,
                                },
                                ensure_ascii=False,
                            )
                        )
                        last_query_error = query_error
                    continue

                recovered = bool(last_query_error)
                last_query_error = ""
                if logs:
                    cursor = new_cursor
                    log_count += len(logs)
                if was_initial_load:
                    # Avoid rescanning a quiet service's entire lookback window
                    # on every poll while retaining a Filebeat ingestion buffer.
                    ingestion_cursor = datetime.now(timezone.utc) - timedelta(
                        seconds=INGESTION_DELAY_SECONDS
                    )
                    if cursor < ingestion_cursor:
                        cursor = ingestion_cursor
                    initial_load = False

                # Track rate (logs per second)
                rate_window.append(0 if was_initial_load else len(logs))
                if len(rate_window) > 10:
                    rate_window.pop(0)
                rate = sum(rate_window) / max(len(rate_window), 1)

                if logs:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "logs",
                                "data": logs,
                                "rate": round(rate, 1),
                                "total": log_count,
                                "initial": was_initial_load,
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                    )
                else:
                    # Send heartbeat periodically even with no logs
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "heartbeat",
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "rate": round(rate, 1),
                                "total": log_count,
                            }
                        )
                    )

                if was_initial_load or recovered:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "status",
                                "state": "streaming",
                                "rate": round(rate, 1),
                                "history_count": (
                                    len(logs) if was_initial_load else None
                                ),
                            },
                            ensure_ascii=False,
                        )
                    )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("live_tail_error", error=str(e))
    finally:
        logger.info("live_tail_disconnected", tenant_id=tenant_id, total_logs=log_count)
