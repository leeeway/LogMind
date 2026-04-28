import { useEffect, useRef, useCallback, useState } from 'react';
import { useAuthStore } from '@/stores/authStore';

interface WSEvent {
  type: string;
  data: any;
  timestamp: string;
}

type EventHandler = (event: WSEvent) => void;

/**
 * WebSocket hook with auto-reconnect, heartbeat, and tenant-scoped events.
 */
export function useWebSocket(handlers?: Record<string, EventHandler>) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [connected, setConnected] = useState(false);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const token = useAuthStore((s) => s.token);

  const connect = useCallback(() => {
    if (!token) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/ws/events?token=${token}`;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        console.log('[WS] Connected');
      };

      ws.onmessage = (evt) => {
        try {
          const event: WSEvent = JSON.parse(evt.data);
          // Dispatch to handler
          const handler = handlersRef.current?.[event.type];
          if (handler) handler(event);
          // Also handle pong/heartbeat silently
        } catch (e) {
          console.warn('[WS] Parse error', e);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        console.log('[WS] Disconnected, reconnecting in 5s...');
        reconnectTimer.current = setTimeout(connect, 5000);
      };

      ws.onerror = () => {
        ws.close();
      };

      // Ping every 25 seconds to keep alive
      const pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send('ping');
        }
      }, 25000);

      ws.addEventListener('close', () => clearInterval(pingInterval));
    } catch {
      reconnectTimer.current = setTimeout(connect, 5000);
    }
  }, [token]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { connected, send };
}
