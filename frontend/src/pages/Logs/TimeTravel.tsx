import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Typography, Space, Tag, Button, Select, Spin, DatePicker, Tooltip, Empty } from 'antd';
import {
  CaretRightOutlined, PauseOutlined, StepBackwardOutlined, StepForwardOutlined,
  FastBackwardOutlined, FastForwardOutlined, HistoryOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import client from '@/api/client';
import { businessLineApi } from '@/api/services';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

interface Bucket {
  timestamp: string;
  error_count: number;
  warning_count: number;
  info_count: number;
  total: number;
  sample_logs: { severity: string; content: string; type: string }[];
}

const severityColors: Record<string, string> = {
  critical: '#ff4d4f',
  warning: '#faad14',
  info: '#1677ff',
};

const TimeTravel: React.FC = () => {
  const [services, setServices] = useState<any[]>([]);
  const [serviceId, setServiceId] = useState<string | undefined>();
  const [timeRange, setTimeRange] = useState<[dayjs.Dayjs, dayjs.Dayjs]>([
    dayjs().subtract(1, 'hour'),
    dayjs(),
  ]);
  const [buckets, setBuckets] = useState<Bucket[]>([]);
  const [loading, setLoading] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const waveCanvasRef = useRef<HTMLCanvasElement>(null);
  const playTimerRef = useRef<number>(0);

  // Load services
  useEffect(() => {
    businessLineApi.listAll().then((res: any) => setServices(res.data?.items || [])).catch(() => {});
  }, []);

  // Load replay data
  const loadReplay = useCallback(async () => {
    setLoading(true);
    setPlaying(false);
    try {
      const res = await client.get('/logs/replay', {
        params: {
          time_from: timeRange[0].toISOString(),
          time_to: timeRange[1].toISOString(),
          service_id: serviceId,
          granularity: 1,
        },
      });
      setBuckets(res.data?.buckets || []);
      setCurrentIndex(0);
    } catch { /* ignore */ }
    setLoading(false);
  }, [timeRange, serviceId]);

  useEffect(() => { loadReplay(); }, [loadReplay]);

  // Play timer
  useEffect(() => {
    if (playing && buckets.length > 0) {
      playTimerRef.current = window.setInterval(() => {
        setCurrentIndex(prev => {
          if (prev >= buckets.length - 1) {
            setPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, 1000 / speed);
    }
    return () => { if (playTimerRef.current) clearInterval(playTimerRef.current); };
  }, [playing, speed, buckets.length]);

  // Draw waveform
  useEffect(() => {
    const canvas = waveCanvasRef.current;
    if (!canvas || !buckets.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const parent = canvas.parentElement;
    canvas.width = parent?.clientWidth || 800;
    canvas.height = 100;
    const W = canvas.width;
    const H = canvas.height;

    ctx.clearRect(0, 0, W, H);

    const maxCount = Math.max(...buckets.map(b => b.error_count + b.warning_count + b.info_count), 1);
    const barW = Math.max(W / buckets.length - 1, 1);

    // Draw bars
    buckets.forEach((b, i) => {
      const x = (i / buckets.length) * W;
      const errH = (b.error_count / maxCount) * (H - 10);
      const warnH = (b.warning_count / maxCount) * (H - 10);
      const infoH = (b.info_count / maxCount) * (H - 10);

      // Info (bottom)
      ctx.fillStyle = i <= currentIndex ? 'rgba(22,119,255,0.4)' : 'rgba(22,119,255,0.12)';
      ctx.fillRect(x, H - infoH - warnH - errH, barW, infoH);

      // Warning (middle)
      ctx.fillStyle = i <= currentIndex ? 'rgba(250,173,20,0.6)' : 'rgba(250,173,20,0.15)';
      ctx.fillRect(x, H - warnH - errH, barW, warnH);

      // Error (top)
      ctx.fillStyle = i <= currentIndex ? 'rgba(255,77,79,0.8)' : 'rgba(255,77,79,0.15)';
      ctx.fillRect(x, H - errH, barW, errH);
    });

    // Playhead
    const playX = (currentIndex / buckets.length) * W;
    ctx.strokeStyle = '#fadb14';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playX, 0);
    ctx.lineTo(playX, H);
    ctx.stroke();

    // Playhead glow
    ctx.fillStyle = 'rgba(250,219,20,0.3)';
    ctx.fillRect(playX - 4, 0, 8, H);

    // Playhead triangle
    ctx.beginPath();
    ctx.moveTo(playX - 6, 0);
    ctx.lineTo(playX + 6, 0);
    ctx.lineTo(playX, 8);
    ctx.closePath();
    ctx.fillStyle = '#fadb14';
    ctx.fill();
  }, [buckets, currentIndex]);

  // Handle waveform click
  const handleWaveClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = waveCanvasRef.current;
    if (!canvas || !buckets.length) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const index = Math.floor((x / rect.width) * buckets.length);
    setCurrentIndex(Math.max(0, Math.min(index, buckets.length - 1)));
  };

  const currentBucket = buckets[currentIndex];

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <HistoryOutlined style={{ fontSize: 20, color: '#722ed1' }} />
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>日志时光回溯</Title>
        </Space>
        <Space>
          <Select
            placeholder="选择服务"
            allowClear
            value={serviceId}
            onChange={setServiceId}
            style={{ width: 200 }}
            options={services.map(s => ({ value: s.id, label: s.name }))}
          />
          <DatePicker.RangePicker
            showTime
            value={timeRange}
            onChange={(vals) => {
              if (vals?.[0] && vals?.[1]) setTimeRange([vals[0], vals[1]]);
            }}
          />
        </Space>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
      ) : !buckets.length ? (
        <Empty description="选定范围内暂无数据" />
      ) : (
        <>
          {/* Player Controls */}
          <div style={{
            background: 'var(--lm-bg-card)',
            border: '1px solid var(--lm-border-light)',
            borderRadius: 12,
            padding: '12px 20px',
            marginBottom: 12,
          }}>
            {/* Controls */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <Space size={4}>
                <Button
                  icon={<StepBackwardOutlined />}
                  size="small"
                  type="text"
                  onClick={() => setCurrentIndex(0)}
                  style={{ color: 'var(--lm-text-secondary)' }}
                />
                <Button
                  icon={<FastBackwardOutlined />}
                  size="small"
                  type="text"
                  onClick={() => setCurrentIndex(Math.max(0, currentIndex - 10))}
                  style={{ color: 'var(--lm-text-secondary)' }}
                />
                <Button
                  type="primary"
                  shape="circle"
                  icon={playing ? <PauseOutlined /> : <CaretRightOutlined />}
                  onClick={() => setPlaying(!playing)}
                  style={{ width: 36, height: 36 }}
                />
                <Button
                  icon={<FastForwardOutlined />}
                  size="small"
                  type="text"
                  onClick={() => setCurrentIndex(Math.min(buckets.length - 1, currentIndex + 10))}
                  style={{ color: 'var(--lm-text-secondary)' }}
                />
                <Button
                  icon={<StepForwardOutlined />}
                  size="small"
                  type="text"
                  onClick={() => setCurrentIndex(buckets.length - 1)}
                  style={{ color: 'var(--lm-text-secondary)' }}
                />
              </Space>

              <Select
                value={speed}
                onChange={setSpeed}
                size="small"
                style={{ width: 72 }}
                options={[
                  { value: 0.5, label: '0.5x' },
                  { value: 1, label: '1x' },
                  { value: 2, label: '2x' },
                  { value: 4, label: '4x' },
                ]}
              />

              <div style={{ flex: 1 }} />

              <Text style={{ fontFamily: 'monospace', fontSize: 16, color: '#fadb14', fontWeight: 600 }}>
                {currentBucket ? dayjs(currentBucket.timestamp).format('HH:mm:ss') : '--:--:--'}
              </Text>

              <Tag color="blue" style={{ borderRadius: 4 }}>
                {currentIndex + 1} / {buckets.length}
              </Tag>
            </div>

            {/* Waveform */}
            <div style={{ position: 'relative', cursor: 'pointer' }}>
              <canvas
                ref={waveCanvasRef}
                onClick={handleWaveClick}
                style={{ display: 'block', width: '100%', height: 100, borderRadius: 6 }}
              />
            </div>
          </div>

          {/* Content Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 12 }}>
            {/* Log Snapshot */}
            <div style={{
              background: 'var(--lm-bg-card)',
              border: '1px solid var(--lm-border-light)',
              borderRadius: 12,
              overflow: 'hidden',
            }}>
              <div style={{
                padding: '10px 16px',
                borderBottom: '1px solid var(--lm-border-light)',
                display: 'flex',
                justifyContent: 'space-between',
              }}>
                <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text-secondary)' }}>
                  📋 日志快照 · {currentBucket ? dayjs(currentBucket.timestamp).format('HH:mm') : ''}
                </Text>
                <Tag>{currentBucket?.total || 0} 条</Tag>
              </div>
              <div style={{ padding: '8px 0', maxHeight: 360, overflow: 'auto' }}>
                {currentBucket?.sample_logs?.length ? (
                  currentBucket.sample_logs.map((log, i) => (
                    <div
                      key={i}
                      style={{
                        padding: '6px 16px',
                        fontFamily: 'monospace',
                        fontSize: 12,
                        lineHeight: 1.6,
                        borderBottom: '1px solid rgba(255,255,255,0.03)',
                        animation: playing ? `lm-fadeSlideIn 0.2s ease-out` : undefined,
                      }}
                    >
                      <Tag
                        color={severityColors[log.severity] || '#8c8c8c'}
                        style={{ borderRadius: 3, fontSize: 10, padding: '0 4px', marginRight: 8 }}
                      >
                        {log.severity?.toUpperCase()}
                      </Tag>
                      <Text style={{ color: 'var(--lm-text)', fontSize: 12 }}>{log.content}</Text>
                    </div>
                  ))
                ) : (
                  <div style={{ textAlign: 'center', padding: 40, color: 'var(--lm-text-tertiary)' }}>
                    该时间段无日志样本
                  </div>
                )}
              </div>
            </div>

            {/* Stats Sidebar */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {/* Gauges */}
              <div style={{
                background: 'var(--lm-bg-card)',
                border: '1px solid var(--lm-border-light)',
                borderRadius: 12,
                padding: 16,
              }}>
                <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text-secondary)', display: 'block', marginBottom: 12 }}>
                  📊 当前时间点统计
                </Text>
                {[
                  { label: 'ERROR', count: currentBucket?.error_count || 0, color: '#ff4d4f' },
                  { label: 'WARN', count: currentBucket?.warning_count || 0, color: '#faad14' },
                  { label: 'INFO', count: currentBucket?.info_count || 0, color: '#1677ff' },
                ].map((g, i) => (
                  <div key={i} style={{ marginBottom: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <Text style={{ fontSize: 11, color: g.color, fontWeight: 600 }}>{g.label}</Text>
                      <Text style={{ fontSize: 14, fontFamily: 'monospace', fontWeight: 700, color: g.count > 0 ? g.color : 'var(--lm-text-tertiary)' }}>
                        {g.count}
                      </Text>
                    </div>
                    <div style={{
                      height: 6, borderRadius: 3,
                      background: 'rgba(255,255,255,0.05)',
                      overflow: 'hidden',
                    }}>
                      <div style={{
                        height: '100%',
                        width: `${Math.min((g.count / Math.max(currentBucket?.total || 1, 1)) * 100, 100)}%`,
                        background: g.color,
                        borderRadius: 3,
                        transition: 'width 0.3s ease',
                      }} />
                    </div>
                  </div>
                ))}
              </div>

              {/* Anomaly markers */}
              <div style={{
                background: 'var(--lm-bg-card)',
                border: '1px solid var(--lm-border-light)',
                borderRadius: 12,
                padding: 16,
                flex: 1,
              }}>
                <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text-secondary)', display: 'block', marginBottom: 12 }}>
                  ⚡ 异常时间点
                </Text>
                {buckets
                  .map((b, idx) => ({ ...b, idx }))
                  .filter(b => b.error_count > 0)
                  .slice(0, 8)
                  .map((b, i) => (
                    <div
                      key={i}
                      onClick={() => { setCurrentIndex(b.idx); setPlaying(false); }}
                      style={{
                        padding: '6px 8px',
                        marginBottom: 4,
                        borderRadius: 6,
                        cursor: 'pointer',
                        fontSize: 12,
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        background: b.idx === currentIndex ? 'rgba(250,219,20,0.08)' : 'transparent',
                        border: b.idx === currentIndex ? '1px solid rgba(250,219,20,0.2)' : '1px solid transparent',
                        transition: 'all 0.2s',
                      }}
                      onMouseEnter={e => { if (b.idx !== currentIndex) e.currentTarget.style.background = 'var(--lm-bg-elevated)'; }}
                      onMouseLeave={e => { if (b.idx !== currentIndex) e.currentTarget.style.background = 'transparent'; }}
                    >
                      <Space size={4}>
                        <ThunderboltOutlined style={{ color: '#ff4d4f', fontSize: 10 }} />
                        <span style={{ fontFamily: 'monospace', color: 'var(--lm-text)' }}>
                          {dayjs(b.timestamp).format('HH:mm')}
                        </span>
                      </Space>
                      <Tag color="#ff4d4f" style={{ borderRadius: 3, fontSize: 10 }}>{b.error_count}</Tag>
                    </div>
                  ))}
                {buckets.filter(b => b.error_count > 0).length === 0 && (
                  <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }}>无异常检测到 ✅</Text>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default TimeTravel;
