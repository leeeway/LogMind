import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Typography, Space, Tag, Button, Select, Input, Tooltip, Badge } from 'antd';
import {
  PlayCircleOutlined, PauseCircleOutlined, ClearOutlined,
  ThunderboltOutlined, FilterOutlined, WifiOutlined,
  DisconnectOutlined, LoadingOutlined,
} from '@ant-design/icons';
import { businessLineApi } from '@/api/services';
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

const LiveTail: React.FC = () => {
  const [bizLines, setBizLines] = useState<any[]>([]);
  const [selectedBiz, setSelectedBiz] = useState<string>('');
  const [keyword, setKeyword] = useState('');
  const [levelFilter, setLevelFilter] = useState<string>('');
  const [connected, setConnected] = useState(false);
  const [paused, setPaused] = useState(false);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [rate, setRate] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [autoScroll, setAutoScroll] = useState(true);

  const wsRef = useRef<WebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const token = useAuthStore(s => s.token);
  const quickDiagnose = useQuickDiagnose();

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

  // Connect WebSocket
  const connect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsBase = `${protocol}//${window.location.host}`;
    const ws = new WebSocket(`${wsBase}/ws/logs/live?token=${token}`);

    ws.onopen = () => {
      setConnected(true);
      setPaused(false);
      setLogs([]);
      setTotalCount(0);
      // Subscribe to logs
      ws.send(JSON.stringify({
        action: 'subscribe',
        business_line_id: selectedBiz,
        filters: {
          keyword: keyword || undefined,
          level: levelFilter || undefined,
        },
      }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'logs' && msg.data?.length) {
          setLogs(prev => {
            const newLogs = [...prev, ...msg.data];
            return newLogs.length > MAX_VISIBLE_LINES
              ? newLogs.slice(-MAX_VISIBLE_LINES)
              : newLogs;
          });
          setRate(msg.rate || 0);
          setTotalCount(msg.total || 0);
        } else if (msg.type === 'heartbeat') {
          setRate(msg.rate || 0);
          setTotalCount(msg.total || 0);
        } else if (msg.type === 'status') {
          setPaused(msg.state === 'paused');
        }
      } catch { /* skip */ }
    };

    ws.onclose = () => {
      setConnected(false);
      setRate(0);
    };

    ws.onerror = () => {
      setConnected(false);
    };

    wsRef.current = ws;
  }, [token, selectedBiz, keyword, levelFilter]);

  // Disconnect
  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setConnected(false);
    setRate(0);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  // Pause / Resume
  const togglePause = () => {
    if (paused) {
      wsRef.current?.send(JSON.stringify({ action: 'resume' }));
      setPaused(false);
      setAutoScroll(true);
    } else {
      wsRef.current?.send(JSON.stringify({ action: 'pause' }));
      setPaused(true);
    }
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
          {connected ? (
            <Badge status="processing" color="#52c41a" />
          ) : (
            <Badge status="default" />
          )}
          <Title level={5} style={{ margin: 0, color: 'var(--lm-text)' }}>
            实时日志流
          </Title>
        </Space>

        <Select
          value={selectedBiz}
          onChange={setSelectedBiz}
          style={{ width: 180 }}
          size="small"
          placeholder="选择服务"
          options={bizLines.map(b => ({ value: b.id, label: b.name }))}
        />

        <Input
          prefix={<FilterOutlined style={{ color: 'var(--lm-text-tertiary)' }} />}
          placeholder="关键词过滤"
          size="small"
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          style={{ width: 160 }}
          onPressEnter={() => { if (connected) { disconnect(); setTimeout(connect, 100); } }}
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
            { value: 'warn', label: 'WARN' },
            { value: 'info', label: 'INFO' },
            { value: 'debug', label: 'DEBUG' },
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
            <Button size="small" icon={<ClearOutlined />} onClick={() => setLogs([])}>
              清空
            </Button>
            <Button size="small" danger icon={<DisconnectOutlined />} onClick={disconnect}>
              断开
            </Button>
          </Space>
        ) : (
          <Button type="primary" size="small" icon={<WifiOutlined />} onClick={connect}>
            连接
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
            <WifiOutlined style={{ fontSize: 48, marginBottom: 16, opacity: 0.3 }} />
            <div style={{ fontSize: 16, marginBottom: 8 }}>
              {connected ? '等待日志...' : '选择服务后点击「连接」开始实时日志流'}
            </div>
            <div style={{ fontSize: 12 }}>
              WebSocket 实时推送 · 毫秒级延迟 · 最多保留 {MAX_VISIBLE_LINES} 行
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
          共 {totalCount.toLocaleString()} 条
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

        {connected && (
          <Space size={4}>
            {paused ? (
              <Tag color="orange" style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>⏸ 已暂停</Tag>
            ) : (
              <Tag color="green" style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>
                <LoadingOutlined style={{ marginRight: 4 }} />STREAMING
              </Tag>
            )}
          </Space>
        )}

        <Tooltip title="双击日志行可快速发起 AI 诊断">
          <ThunderboltOutlined style={{ color: '#722ed1', fontSize: 12, cursor: 'help' }} />
        </Tooltip>
      </div>
    </div>
  );
};

export default LiveTail;
