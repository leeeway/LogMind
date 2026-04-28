"""
WebSocket Event Manager

Manages real-time event broadcasting to connected clients.
Supports JWT auth, auto-reconnect heartbeat, and tenant-scoped events.
"""

import json
import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect, Query
from starlette.websockets import WebSocketState

from logmind.core.logging import get_logger
from logmind.core.security import decode_access_token

logger = get_logger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections, scoped by tenant."""

    def __init__(self):
        # {tenant_id: [WebSocket, ...]}
        self._connections: dict[str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, tenant_id: str):
        await websocket.accept()
        async with self._lock:
            if tenant_id not in self._connections:
                self._connections[tenant_id] = []
            self._connections[tenant_id].append(websocket)
        logger.info("ws_connected", tenant_id=tenant_id, total=len(self._connections.get(tenant_id, [])))

    async def disconnect(self, websocket: WebSocket, tenant_id: str):
        async with self._lock:
            conns = self._connections.get(tenant_id, [])
            if websocket in conns:
                conns.remove(websocket)
            if not conns:
                self._connections.pop(tenant_id, None)
        logger.info("ws_disconnected", tenant_id=tenant_id)

    async def broadcast(self, tenant_id: str, event_type: str, data: dict[str, Any]):
        """Broadcast an event to all connections for a tenant."""
        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, default=str)

        conns = self._connections.get(tenant_id, [])
        dead = []
        for ws in conns:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(message)
            except Exception:
                dead.append(ws)

        # Cleanup dead connections
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self._connections.get(tenant_id, []):
                        self._connections[tenant_id].remove(ws)

    @property
    def total_connections(self) -> int:
        return sum(len(conns) for conns in self._connections.values())


# Singleton
ws_manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    """
    WebSocket endpoint with JWT authentication.

    Connect: ws://host/ws/events?token=<jwt>

    Events:
      - task.progress: {task_id, stage, status}
      - task.result: {task_id, result}
      - alert.fired: {alert_id, severity, message}
      - heartbeat: {ts}
    """
    # Authenticate
    try:
        payload = decode_access_token(token)
        tenant_id = payload.get("tenant_id", "")
        if not tenant_id:
            await websocket.close(code=4001, reason="Invalid token")
            return
    except Exception:
        await websocket.accept()
        await websocket.close(code=4001, reason="Authentication failed")
        return

    await ws_manager.connect(websocket, tenant_id)

    try:
        while True:
            # Wait for client messages (ping/pong or close)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_text(json.dumps({
                        "type": "pong",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }))
            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_text(json.dumps({
                        "type": "heartbeat",
                        "data": {"ts": datetime.now(timezone.utc).isoformat()},
                    }))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("ws_error", error=str(e))
    finally:
        await ws_manager.disconnect(websocket, tenant_id)
