import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Typography, Space, Tag, Button, Select, Input, Tooltip, Badge } from 'antd';
import {
  PlayCircleOutlined, PauseCircleOutlined, ClearOutlined,
  ThunderboltOutlined, FilterOutlined, WifiOutlined,
  DisconnectOutlined, LoadingOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { businessLineApi, type BusinessLineListItem } from '@/api/services';
import { useAuthStore } from '@/stores/authStore';
import { useQuickDiagnose } from '@/components/QuickDiagnose';

const { Title, Text } = Typography;

const levelColors: Record<string, string> = {
  ERROR: '#ff4d4f',
  WARN: '#faad14',
  INFO: '#1677ff',
  DEBUG: '#8c8c8c',
};

interface LogLine {
  id: string;
  timestamp: string;
  message: string;
  level: string;
  source: string;
}

const MAX_VISIBLE_LINES = 500;
const MAX_DEDUP_IDENTITIES = 2000;
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000];

type ConnectionState =
  | 'idle'
  | 'connecting'
  | 'loading'
  | 'streaming'
  | 'paused'
  | 'reconnecting'
  | 'error';

const LiveTail: React.FC = () => {
  const [bizLines, setBizLines] = useState<BusinessLineListItem[]>([]);
  const [selectedBiz, setSelectedBiz] = useState<string>('');
  const [keyword, setKeyword] = useState('');
  const [levelFilter, setLevelFilter] = useState<string>('');
  const [lookbackSeconds, setLookbackSeconds] = useState(300);
  const [connectionState, setConnectionState] = useState<ConnectionState>('idle');
  const [errorMessage, setErrorMessage] = useState('');
  const [paused, setPaused] = useState(false);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [rate, setRate] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [autoScroll, setAutoScroll] = useState(true);
  const [lastMessageAt, setLastMessageAt] = useState<number>(0);

  const wsRef = useRef<WebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const connectTimeoutRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const manualDisconnectRef = useRef(false);
  const mountedRef = useRef(true);
  const logIdsRef = useRef<Set<string>>(new Set());
  const logIdQueueRef = useRef<string[]>([]);
  const connectRef = useRef<(reconnecting?: boolean) => void>(() => {});
  const token = useAuthStore(s => s.token);
  const quickDiagnose = useQuickDiagnose();
  const connected = ['loading', 'streaming', 'paused'].includes(connectionState);

  const businessLineOptions = useMemo(() => bizLines.map(b => ({
    value: b.id,
    label: b.name,
    searchText: `${b.name || ''} ${b.es_index_pattern || ''} ${String(b.site || '')}`,
  })), [bizLines]);

  // Load business lines
  useEffect(() => {
    businessLineApi.listAll().then(({ data }) => {
      const items = Array.isArray(data) ? data : (data?.items || []);
      setBizLines(items);
      if (items.length > 0) {
        setSelectedBiz(items[0].id);
      }
    }).catch(() => {});
  }, []);

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  // Handle scroll — if user scrolls up, disable auto-scroll
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setAutoScroll(atBottom);
  }, []);

  const clearTimer = (ref: React.MutableRefObject<number | null>) => {
    if (ref.current !== null) {
      window.clearTimeout(ref.current);
      ref.current = null;
    }
  };

  const resetStream = useCallback(() => {
    logIdsRef.current.clear();
    logIdQueueRef.current = [];
    setLogs([]);
    setRate(0);
    setTotalCount(0);
    setAutoScroll(true);
  }, []);

  const subscribe = useCallback((socket = wsRef.current) => {
    if (!socket || socket.readyState !== WebSocket.OPEN || !selectedBiz) return;
    resetStream();
    setPaused(false);
    setConnectionState('loading');
    setErrorMessage('');
    socket.send(JSON.stringify({
      action: 'subscribe',
      business_line_id: selectedBiz,
      lookback_seconds: lookbackSeconds,
      filters: {
        keyword: keyword.trim() || undefined,
        level: levelFilter || undefined,
      },
    }));
  }, [keyword, levelFilter, lookbackSeconds, resetStream, selectedBiz]);

  // Connect WebSocket and recover transient network/proxy interruptions.
  const connect = useCallback((reconnecting = false) => {
    if (!selectedBiz) {
      setConnectionState('error');
      setErrorMessage('请先选择业务线');
      return;
    }
    if (!token) {
      setConnectionState('error');
      setErrorMessage('登录状态已失效，请重新登录');
      return;
    }

    manualDisconnectRef.current = false;
    clearTimer(reconnectTimerRef);
    clearTimer(connectTimeoutRef);

    const previous = wsRef.current;
    if (previous) {
      previous.onclose = null;
      previous.close();
    }

    setConnectionState(reconnecting ? 'reconnecting' : 'connecting');
    setErrorMessage(reconnecting ? '连接中断，正在自动重连…' : '');

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsBase = `${protocol}//${window.location.host}`;
    const ws = new WebSocket(`${wsBase}/ws/logs/live?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;

    connectTimeoutRef.current = window.setTimeout(() => {
      if (wsRef.current === ws && ws.readyState !== WebSocket.OPEN) {
        setErrorMessage('连接超时，正在重试…');
        ws.close();
      }
    }, 10000);

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      clearTimer(connectTimeoutRef);
      reconnectAttemptRef.current = 0;
      setLastMessageAt(Date.now());
      subscribe(ws);
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return;
      setLastMessageAt(Date.now());
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'logs' && Array.isArray(msg.data)) {
          const uniqueLogs: LogLine[] = [];
          for (const raw of msg.data) {
            const normalized: LogLine = {
              ...raw,
              level: String(raw.level || 'INFO').toUpperCase() === 'WARNING'
                ? 'WARN'
                : String(raw.level || 'INFO').toUpperCase(),
            };
            const identity = normalized.id
              || `${normalized.timestamp}|${normalized.source}|${normalized.message}`;
            if (!logIdsRef.current.has(identity)) {
              logIdsRef.current.add(identity);
              logIdQueueRef.current.push(identity);
              uniqueLogs.push(normalized);
            }
          }
          while (logIdQueueRef.current.length > MAX_DEDUP_IDENTITIES) {
            const expiredIdentity = logIdQueueRef.current.shift();
            if (expiredIdentity) logIdsRef.current.delete(expiredIdentity);
          }
          if (uniqueLogs.length) {
            setLogs(prev => {
              const next = [...prev, ...uniqueLogs];
              return next.length > MAX_VISIBLE_LINES
                ? next.slice(-MAX_VISIBLE_LINES)
                : next;
            });
          }
          setRate(Number(msg.rate) || 0);
          setTotalCount(Number(msg.total) || 0);
        } else if (msg.type === 'heartbeat') {
          setRate(Number(msg.rate) || 0);
          setTotalCount(Number(msg.total) || 0);
        } else if (msg.type === 'status') {
          if (msg.state === 'loading_history') {
            setConnectionState('loading');
          } else if (msg.state === 'paused') {
            setPaused(true);
            setConnectionState('paused');
          } else if (msg.state === 'streaming') {
            setPaused(false);
            setConnectionState('streaming');
            setErrorMessage('');
          }
        } else if (msg.type === 'error') {
          setConnectionState('error');
          setErrorMessage(msg.message || '实时日志查询失败');
        }
      } catch {
        setErrorMessage('收到无法识别的实时日志数据');
      }
    };

    ws.onclose = (event) => {
      if (wsRef.current !== ws) return;
      clearTimer(connectTimeoutRef);
      wsRef.current = null;
      setRate(0);
      if (manualDisconnectRef.current || !mountedRef.current) {
        setConnectionState('idle');
        return;
      }
      if (event.code === 4001) {
        setConnectionState('error');
        setErrorMessage('登录状态已失效，请重新登录');
        return;
      }

      const attempt = reconnectAttemptRef.current;
      const delay = RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)];
      reconnectAttemptRef.current += 1;
      setConnectionState('reconnecting');
      setErrorMessage(`连接已中断，${Math.round(delay / 1000)} 秒后重试…`);
      reconnectTimerRef.current = window.setTimeout(
        () => connectRef.current(true),
        delay,
      );
    };

    ws.onerror = () => {
      if (wsRef.current === ws) setErrorMessage('WebSocket 连接异常');
    };
  }, [selectedBiz, subscribe, token]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  // Disconnect
  const disconnect = useCallback(() => {
    manualDisconnectRef.current = true;
    clearTimer(reconnectTimerRef);
    clearTimer(connectTimeoutRef);
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
    reconnectAttemptRef.current = 0;
    setConnectionState('idle');
    setErrorMessage('');
    setPaused(false);
    setRate(0);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      mountedRef.current = false;
      manualDisconnectRef.current = true;
      clearTimer(reconnectTimerRef);
      clearTimer(connectTimeoutRef);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, []);

  // Changing any query option updates the active stream without reconnecting.
  useEffect(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (!selectedBiz) {
      disconnect();
      resetStream();
      return;
    }
    const timer = window.setTimeout(() => subscribe(ws), 350);
    return () => window.clearTimeout(timer);
  }, [disconnect, resetStream, selectedBiz, subscribe]);

  // Pause / Resume
  const togglePause = () => {
    if (paused) {
      wsRef.current?.send(JSON.stringify({ action: 'resume' }));
      setPaused(false);
      setConnectionState('streaming');
      setAutoScroll(true);
    } else {
      wsRef.current?.send(JSON.stringify({ action: 'pause' }));
      setPaused(true);
      setConnectionState('paused');
    }
  };

  const clearLogs = () => {
    logIdsRef.current.clear();
    logIdQueueRef.current = [];
    setLogs([]);
  };

  // Format timestamp
  const fmtTime = (ts: string) => {
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + d.getMilliseconds().toString().padStart(3, '0');
    } catch { return ts; }
  };

  // Rate bar width (0-100%)
  const rateWidth = Math.min(rate * 2, 100);
  const rateColor = rate > 20 ? '#ff4d4f' : rate > 5 ? '#faad14' : '#52c41a';

  return (
    <div className="lm-animate-in" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 80px)', margin: '-24px -24px 0' }}>
      {/* Toolbar */}
      <div style={{
        padding: '12px 24px', display: 'flex', alignItems: 'center', gap: 12,
        background: 'var(--lm-bg-container)', borderBottom: '1px solid var(--lm-border-light)',
        flexShrink: 0,
      }}>
        <Space>
          {connectionState === 'error' ? (
            <Badge status="error" />
          ) : connected ? (
            <Badge status="processing" color="#52c41a" />
          ) : ['connecting', 'reconnecting'].includes(connectionState) ? (
            <Badge status="processing" />
          ) : (
            <Badge status="default" />
          )}
          <Title level={5} style={{ margin: 0, color: 'var(--lm-text)' }}>
            实时日志流
          </Title>
        </Space>

        <Select
          value={selectedBiz || undefined}
          onChange={value => setSelectedBiz(value || '')}
          style={{ width: 220 }}
          size="small"
          placeholder="搜索并选择业务线"
          showSearch
          allowClear
          optionFilterProp="searchText"
          filterOption={(input, option) => String(
            (option as { searchText?: string } | undefined)?.searchText || '',
          ).toLowerCase().includes(input.trim().toLowerCase())}
          options={businessLineOptions}
        />

        <Input
          prefix={<FilterOutlined style={{ color: 'var(--lm-text-tertiary)' }} />}
          placeholder="关键词过滤"
          size="small"
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          style={{ width: 160 }}
          allowClear
        />

        <Select
          value={levelFilter}
          onChange={setLevelFilter}
          style={{ width: 100 }}
          size="small"
          allowClear
          placeholder="级别"
          options={[
            { value: 'error', label: 'ERROR' },
            { value: 'warning', label: 'WARN' },
            { value: 'info', label: 'INFO' },
            { value: 'debug', label: 'DEBUG' },
          ]}
        />

        <Select
          value={lookbackSeconds}
          onChange={setLookbackSeconds}
          style={{ width: 112 }}
          size="small"
          options={[
            { value: 60, label: '回看 1 分钟' },
            { value: 300, label: '回看 5 分钟' },
            { value: 900, label: '回看 15 分钟' },
            { value: 3600, label: '回看 1 小时' },
          ]}
        />

        <div style={{ flex: 1 }} />

        {connected ? (
          <Space>
            <Button
              size="small"
              icon={paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
              onClick={togglePause}
              style={paused ? { color: '#52c41a', borderColor: '#52c41a33' } : {}}
            >
              {paused ? '继续' : '暂停'}
            </Button>
            <Button size="small" icon={<ClearOutlined />} onClick={clearLogs}>
              清空
            </Button>
            <Button size="small" danger icon={<DisconnectOutlined />} onClick={disconnect}>
              断开
            </Button>
          </Space>
        ) : ['connecting', 'reconnecting'].includes(connectionState) ? (
          <Button size="small" icon={<DisconnectOutlined />} onClick={disconnect}>
            取消连接
          </Button>
        ) : (
          <Button
            type="primary"
            size="small"
            icon={connectionState === 'error' ? <ReloadOutlined /> : <WifiOutlined />}
            onClick={() => connect(false)}
            disabled={!selectedBiz}
          >
            {connectionState === 'error' ? '重新连接' : '连接'}
          </Button>
        )}
      </div>

      {/* Log Stream */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        style={{
          flex: 1, overflow: 'auto', padding: '0',
          background: 'var(--lm-bg-layout)',
          fontFamily: 'Menlo, Consolas, "Fira Code", monospace',
          fontSize: 12, lineHeight: 1.8,
        }}
      >
        {logs.length === 0 && (
          <div style={{
            textAlign: 'center', padding: '80px 24px',
            color: 'var(--lm-text-tertiary)',
          }}>
            {['connecting', 'loading', 'reconnecting'].includes(connectionState) ? (
              <LoadingOutlined style={{ fontSize: 48, marginBottom: 16, opacity: 0.5 }} />
            ) : (
              <WifiOutlined style={{ fontSize: 48, marginBottom: 16, opacity: 0.3 }} />
            )}
            <div style={{ fontSize: 16, marginBottom: 8 }}>
              {connectionState === 'connecting' && '正在建立实时连接…'}
              {connectionState === 'reconnecting' && (errorMessage || '正在重新连接…')}
              {connectionState === 'loading' && `正在加载最近 ${lookbackSeconds / 60} 分钟日志…`}
              {connectionState === 'streaming' && `最近 ${lookbackSeconds / 60} 分钟没有匹配日志，正在等待新日志`}
              {connectionState === 'paused' && '实时日志流已暂停'}
              {connectionState === 'error' && (errorMessage || '实时日志连接失败')}
              {connectionState === 'idle' && '搜索业务线并点击「连接」开始实时日志流'}
            </div>
            <div style={{ fontSize: 12 }}>
              连接后立即回放历史 · 自动重连 · 最多保留 {MAX_VISIBLE_LINES} 行
            </div>
          </div>
        )}

        {logs.map((log, i) => (
          <div
            key={log.id || i}
            style={{
              display: 'flex', gap: 0,
              padding: '1px 16px',
              borderBottom: '1px solid var(--lm-border-light)',
              animation: i >= logs.length - 5 ? 'lm-fadeSlideIn 0.15s ease-out' : undefined,
              background: log.level === 'ERROR'
                ? 'rgba(255,77,79,0.04)'
                : log.level === 'WARN'
                ? 'rgba(250,173,20,0.03)'
                : 'transparent',
            }}
            onDoubleClick={() => quickDiagnose.open({
              context: `帮我分析这条日志:\n级别: ${log.level}\n时间: ${log.timestamp}\n消息: ${log.message?.slice(0, 200)}`,
              source: '实时日志流',
            })}
          >
            {/* Timestamp */}
            <span style={{
              color: 'var(--lm-text-tertiary)', flexShrink: 0,
              width: 110, userSelect: 'none',
            }}>
              {fmtTime(log.timestamp)}
            </span>

            {/* Level */}
            <span style={{
              color: levelColors[log.level] || '#8c8c8c',
              fontWeight: 600, flexShrink: 0, width: 48,
              textAlign: 'center',
            }}>
              {log.level}
            </span>

            {/* Source */}
            {log.source && (
              <span style={{
                color: '#722ed1', flexShrink: 0, width: 120,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                opacity: 0.7, paddingRight: 8,
              }}>
                {log.source}
              </span>
            )}

            {/* Message */}
            <span style={{
              color: 'var(--lm-text)', flex: 1,
              whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            }}>
              {log.message}
            </span>
          </div>
        ))}
      </div>

      {/* Status Bar */}
      <div style={{
        padding: '6px 24px', flexShrink: 0,
        background: 'var(--lm-bg-container)',
        borderTop: '1px solid var(--lm-border-light)',
        display: 'flex', alignItems: 'center', gap: 16,
        fontSize: 11,
      }}>
        {/* Rate bar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 200 }}>
          <div style={{
            width: 120, height: 6, borderRadius: 3,
            background: 'var(--lm-bg-elevated)',
            overflow: 'hidden',
          }}>
            <div style={{
              width: `${rateWidth}%`, height: '100%',
              background: rateColor,
              borderRadius: 3,
              transition: 'width 0.5s, background 0.3s',
            }} />
          </div>
          <Text style={{ color: rateColor, fontFamily: 'monospace', fontWeight: 600, fontSize: 12 }}>
            {rate.toFixed(1)}/s
          </Text>
        </div>

        <Text style={{ color: 'var(--lm-text-tertiary)' }}>
          本次接收 {totalCount.toLocaleString()} 条
        </Text>

        <Text style={{ color: 'var(--lm-text-tertiary)' }}>
          显示 {logs.length} 行
        </Text>

        {!autoScroll && (
          <Tag color="warning" style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>
            自动滚动已暂停 (滚到底部恢复)
          </Tag>
        )}

        <div style={{ flex: 1 }} />

        {connectionState !== 'idle' && (
          <Space size={4}>
            {connectionState === 'paused' ? (
              <Tag color="orange" style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>⏸ 已暂停</Tag>
            ) : connectionState === 'error' ? (
              <Tooltip title={errorMessage}>
                <Tag color="error" style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>
                  查询异常
                </Tag>
              </Tooltip>
            ) : ['connecting', 'reconnecting', 'loading'].includes(connectionState) ? (
              <Tag color="processing" style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>
                <LoadingOutlined style={{ marginRight: 4 }} />
                {connectionState === 'loading' ? 'LOADING' : 'CONNECTING'}
              </Tag>
            ) : (
              <Tag color="green" style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>
                <LoadingOutlined style={{ marginRight: 4 }} />STREAMING
              </Tag>
            )}
          </Space>
        )}

        {lastMessageAt > 0 && connectionState !== 'idle' && (
          <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 10 }}>
            最近响应 {new Date(lastMessageAt).toLocaleTimeString('zh-CN', { hour12: false })}
          </Text>
        )}

        <Tooltip title="双击日志行可快速发起 AI 诊断">
          <ThunderboltOutlined style={{ color: '#722ed1', fontSize: 12, cursor: 'help' }} />
        </Tooltip>
      </div>
    </div>
  );
};

export default LiveTail;
