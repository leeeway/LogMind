"""
Live Log Tail — Real-time Log Streaming via WebSocket

Provides a WebSocket endpoint that streams new Elasticsearch logs
to connected clients in near real-time (1s polling).

Protocol:
  Client → Server:
    {"action": "subscribe", "index_pattern": "...", "filters": {...}}
    {"action": "pause"}
    {"action": "resume"}
    "ping"

  Server → Client:
    {"type": "logs", "data": [...], "rate": N, "total": N}
    {"type": "status", "state": "streaming|paused", "rate": N}
    {"type": "heartbeat", "ts": "..."}
    {"type": "pong"}
"""

import json
import asyncio
from datetime import datetime, timezone, timedelta

from fastapi import WebSocket, WebSocketDisconnect, Query

from logmind.core.logging import get_logger
from logmind.core.security import decode_access_token
from logmind.core.elasticsearch import get_es_client

logger = get_logger(__name__)

POLL_INTERVAL = 1.0  # seconds
MAX_LOGS_PER_PUSH = 50
MAX_IDLE_SECONDS = 300  # disconnect after 5 min idle


async def _fetch_latest_logs(
    es_index: str,
    since: datetime,
    filters: dict | None = None,
    size: int = MAX_LOGS_PER_PUSH,
) -> tuple[list[dict], datetime]:
    """Fetch logs newer than `since` from ES, return (logs, new_cursor)."""
    es = get_es_client()
    if not es:
        return [], since

    must = [{"range": {"@timestamp": {"gt": since.isoformat()}}}]

    if filters:
        if filters.get("keyword"):
            must.append({"query_string": {"query": f"*{filters['keyword']}*", "default_field": "message"}})
        if filters.get("level"):
            lvl = filters["level"]
            must.append({"bool": {"should": [
                {"term": {"gy.filetype.keyword": f"{lvl}.log"}},
                {"match_phrase": {"message": lvl.upper()}},
            ]}})

    body = {
        "query": {"bool": {"must": must}},
        "sort": [{"@timestamp": "asc"}],
        "size": size,
        "_source": ["@timestamp", "message", "gy.filetype", "gy.domain", "gy.hostname", "kubernetes.container.name"],
    }

    try:
        resp = await es.search(index=es_index, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        logs = []
        new_cursor = since
        for h in hits:
            src = h.get("_source", {})
            ts_str = src.get("@timestamp", "")
            logs.append({
                "id": h.get("_id", ""),
                "timestamp": ts_str,
                "message": src.get("message", ""),
                "level": _extract_level(src),
                "source": src.get("gy", {}).get("domain", "") or src.get("kubernetes", {}).get("container", {}).get("name", ""),
            })
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts_dt > new_cursor:
                        new_cursor = ts_dt
                except (ValueError, TypeError):
                    pass
        return logs, new_cursor
    except Exception as e:
        logger.warning("live_tail_es_error", error=str(e))
        return [], since


def _extract_level(src: dict) -> str:
    """Extract log level from source."""
    filetype = src.get("gy", {}).get("filetype", "")
    if "error" in filetype:
        return "ERROR"
    if "warn" in filetype:
        return "WARN"
    if "info" in filetype:
        return "INFO"
    if "debug" in filetype:
        return "DEBUG"

    msg = (src.get("message", "") or "")[:200].upper()
    if "ERROR" in msg or "EXCEPTION" in msg:
        return "ERROR"
    if "WARN" in msg:
        return "WARN"
    if "DEBUG" in msg:
        return "DEBUG"
    return "INFO"


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
    index_pattern = "*"
    filters: dict = {}
    paused = False
    cursor = datetime.now(timezone.utc) - timedelta(seconds=60)  # 60s lookback for Filebeat ingestion delay
    log_count = 0
    rate_window: list[int] = []

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

                action = msg.get("action", "")

                if action == "subscribe":
                    index_pattern = msg.get("index_pattern", "*")
                    filters = msg.get("filters", {})
                    cursor = datetime.now(timezone.utc) - timedelta(seconds=60)  # 60s lookback for Filebeat delay
                    paused = False
                    log_count = 0
                    rate_window = []
                    await websocket.send_text(json.dumps({
                        "type": "status", "state": "streaming", "rate": 0,
                        "index": index_pattern,
                    }, ensure_ascii=False))

                elif action == "pause":
                    paused = True
                    await websocket.send_text(json.dumps({
                        "type": "status", "state": "paused", "rate": 0,
                    }))

                elif action == "resume":
                    paused = False
                    cursor = datetime.now(timezone.utc) - timedelta(seconds=30)  # 30s for Filebeat delay
                    await websocket.send_text(json.dumps({
                        "type": "status", "state": "streaming", "rate": 0,
                    }))

            except asyncio.TimeoutError:
                pass

            # Push new logs if not paused
            if not paused:
                logs, new_cursor = await _fetch_latest_logs(
                    index_pattern, cursor, filters
                )
                if logs:
                    cursor = new_cursor
                    log_count += len(logs)

                # Track rate (logs per second)
                rate_window.append(len(logs))
                if len(rate_window) > 10:
                    rate_window.pop(0)
                rate = sum(rate_window) / max(len(rate_window), 1)

                if logs:
                    await websocket.send_text(json.dumps({
                        "type": "logs",
                        "data": logs,
                        "rate": round(rate, 1),
                        "total": log_count,
                    }, ensure_ascii=False, default=str))
                else:
                    # Send heartbeat periodically even with no logs
                    await websocket.send_text(json.dumps({
                        "type": "heartbeat",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "rate": round(rate, 1),
                        "total": log_count,
                    }))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("live_tail_error", error=str(e))
    finally:
        logger.info("live_tail_disconnected", tenant_id=tenant_id, total_logs=log_count)
