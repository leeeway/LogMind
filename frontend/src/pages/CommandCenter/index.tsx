import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Space, Tag, Button } from 'antd';
import {
  FullscreenOutlined, FullscreenExitOutlined,
  AlertOutlined, CheckCircleOutlined, ClockCircleOutlined,
  ThunderboltOutlined, RocketOutlined, FireOutlined,
  RadarChartOutlined, CloudOutlined,
} from '@ant-design/icons';
import { dashboardApi } from '@/api/dashboard';
import dayjs from 'dayjs';

interface SeverityPoint {
  severity: string;
  count: number;
}

interface OverviewData {
  total_tasks?: number;
  total_alerts?: number;
  active_incidents?: number;
  today_anomalies?: number;
  storm_count?: number;
  ai_insight?: string;
  severity_distribution?: SeverityPoint[];
}

interface HealthItem {
  business_line_id?: string;
  business_line_name?: string;
  name?: string;
  critical_count?: number;
  warning_count?: number;
  critical?: number;
  warning?: number;
  success_rate?: number;
  total_tasks?: number;
}

interface CommandEvent {
  time: string;
  level: 'critical' | 'warning' | 'info' | 'success';
  message: string;
}

interface TrendPoint {
  task_count?: number;
  failed_count?: number;
}

// ── Color System ─────────────────────────────────
const COLORS = {
  bg: '#040810',
  card: 'rgba(10,18,40,0.85)',
  border: 'rgba(22,119,255,0.12)',
  borderActive: 'rgba(22,119,255,0.35)',
  red: '#ff4d4f',
  orange: '#fa8c16',
  green: '#52c41a',
  blue: '#1677ff',
  purple: '#722ed1',
};

const CommandCenter: React.FC = () => {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [clock, setClock] = useState(dayjs().format('HH:mm:ss'));
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [health, setHealth] = useState<HealthItem[]>([]);
  const [events, setEvents] = useState<CommandEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const healthCanvasRef = useRef<HTMLCanvasElement>(null);
  const waveCanvasRef = useRef<HTMLCanvasElement>(null);
  const waveDataRef = useRef<number[]>([]);
  const animRef = useRef<number>(0);
  const timeRef = useRef(0);

  // ── Clock ──────────────────────────────────────
  useEffect(() => {
    const t = setInterval(() => setClock(dayjs().format('HH:mm:ss')), 1000);
    return () => clearInterval(t);
  }, []);

  // ── Fullscreen ─────────────────────────────────
  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      containerRef.current?.requestFullscreen();
      setIsFullscreen(true);
    } else {
      document.exitFullscreen();
      setIsFullscreen(false);
    }
  };

  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);

  // ── Data Loading ───────────────────────────────
  const loadData = useCallback(async () => {
    try {
      const [ovRes, hlRes, trRes] = await Promise.all([
        dashboardApi.getOverview(1),
        dashboardApi.getBusinessHealth(1),
        dashboardApi.getTrends(1),
      ]);
      const nextOverview = ovRes.data as OverviewData;
      setOverview(nextOverview);
      const services = ((hlRes.data?.items || []) as HealthItem[]).map((s) => ({
        ...s,
        name: s.business_line_name,
        critical: s.critical_count || 0,
        warning: s.warning_count || 0,
      }));
      setHealth(services);

      const newEvents: CommandEvent[] = [];
      services.forEach((s) => {
        const name = s.name || s.business_line_name || '未知服务';
        if (s.critical > 0) {
          newEvents.push({
            time: dayjs().subtract(Math.random() * 60, 'minute').format('HH:mm'),
            level: 'critical',
            message: `${name}: ${s.critical} 个严重异常待处理`,
          });
        }
        if (s.warning > 0) {
          newEvents.push({
            time: dayjs().subtract(Math.random() * 120, 'minute').format('HH:mm'),
            level: 'warning',
            message: `${name}: ${s.warning} 个告警需要确认`,
          });
        }
      });
      if (newEvents.length === 0 && services.length > 0) {
        newEvents.push({
          time: dayjs().format('HH:mm'),
          level: 'success',
          message: `巡检完成：${services.length} 个服务暂无严重异常`,
        });
      }
      if ((nextOverview.total_tasks || 0) > 0) {
        newEvents.push({
          time: dayjs().format('HH:mm'),
          level: 'info',
          message: `今日已完成 ${nextOverview.total_tasks || 0} 个分析任务，告警 ${nextOverview.total_alerts || 0} 条`,
        });
      }
      newEvents.sort((a, b) => b.time.localeCompare(a.time));
      setEvents(prev => [...newEvents.slice(0, 20), ...prev].slice(0, 50));

      const trendData = ((trRes.data?.data || []) as TrendPoint[]).map((item) => {
        const total = Math.max(item.task_count || 0, 1);
        return Math.round(((item.failed_count || 0) / total) * 100);
      });
      if (trendData.length >= 2) {
        waveDataRef.current = trendData.slice(-120);
      } else {
        const critical = nextOverview.severity_distribution?.find((d) => d.severity === 'critical')?.count || 0;
        const warning = nextOverview.severity_distribution?.find((d) => d.severity === 'warning')?.count || 0;
        const base = critical * 8 + warning * 2;
        waveDataRef.current = Array.from({ length: 48 }, (_, i) => {
          const pulse = Math.max(0, Math.sin(i / 3) * 4);
          return Math.max(0, base + pulse + (i % 11 === 0 ? critical * 5 : 0));
        });
      }
      const criticalRate = nextOverview.severity_distribution?.find((d) => d.severity === 'critical')?.count || 0;
      waveDataRef.current.push(criticalRate + Math.random() * 3);
      if (waveDataRef.current.length > 120) waveDataRef.current.shift();
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => {
      void loadData();
    }, 0);
    const interval = setInterval(loadData, 30000);
    return () => {
      window.clearTimeout(initialTimer);
      clearInterval(interval);
    };
  }, [loadData]);

  // ── Health Matrix Canvas ───────────────────────
  useEffect(() => {
    const canvas = healthCanvasRef.current;
    if (!canvas || !health.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.parentElement?.getBoundingClientRect();
    canvas.width = rect?.width || 500;
    canvas.height = rect?.height || 300;
    const W = canvas.width;
    const H = canvas.height;

    const cols = Math.ceil(Math.sqrt(health.length * (W / H)));
    const rows = Math.ceil(health.length / cols);
    const cellW = (W - 20) / cols;
    const cellH = (H - 20) / rows;
    const size = Math.min(cellW, cellH) - 6;

    const draw = () => {
      timeRef.current += 0.016;
      ctx.clearRect(0, 0, W, H);

      health.forEach((s, i: number) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        const cx = 10 + col * cellW + cellW / 2;
        const cy = 10 + row * cellH + cellH / 2;

        const isCritical = (s.critical || 0) > 0;
        const isWarning = (s.warning || 0) > 0;
        const color = isCritical ? COLORS.red : isWarning ? COLORS.orange : COLORS.green;

        // Breathing glow
        const breathe = isCritical
          ? Math.sin(timeRef.current * 3 + i) * 0.4 + 0.6
          : isWarning
          ? Math.sin(timeRef.current * 2 + i) * 0.2 + 0.8
          : 1;

        // Shadow glow
        ctx.shadowColor = color;
        ctx.shadowBlur = isCritical ? 20 * breathe : 8;

        // Rounded rect
        const r = 4;
        const half = size / 2;
        ctx.beginPath();
        ctx.roundRect(cx - half, cy - half, size, size, r);
        ctx.fillStyle = color + Math.round(breathe * 200).toString(16).padStart(2, '0');
        ctx.fill();

        ctx.shadowBlur = 0;

        // Service name
        ctx.fillStyle = '#fff';
        ctx.font = `${Math.min(10, size / 6)}px Inter, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const serviceName = s.name || s.business_line_name || '';
        const label = serviceName.length > 8 ? serviceName.slice(0, 7) + '…' : serviceName;
        ctx.fillText(label, cx, cy);
      });

      animRef.current = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(animRef.current);
  }, [health]);

  // ── Wave Canvas (ECG-style) ────────────────────
  useEffect(() => {
    const canvas = waveCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.parentElement?.getBoundingClientRect();
    canvas.width = rect?.width || 800;
    canvas.height = 80;
    const W = canvas.width;
    const H = canvas.height;

    const drawWave = () => {
      ctx.clearRect(0, 0, W, H);
      const data = waveDataRef.current;
      if (data.length < 2) return;

      const max = Math.max(...data, 1);
      const step = W / (data.length - 1);

      // Gradient fill
      const grad = ctx.createLinearGradient(0, 0, 0, H);
      grad.addColorStop(0, 'rgba(22,119,255,0.3)');
      grad.addColorStop(1, 'rgba(22,119,255,0)');

      ctx.beginPath();
      ctx.moveTo(0, H);
      data.forEach((v, i) => {
        const x = i * step;
        const y = H - (v / max) * (H - 10);
        if (i === 0) ctx.lineTo(x, y);
        else {
          const prevX = (i - 1) * step;
          const prevY = H - (data[i - 1] / max) * (H - 10);
          const cpX = (prevX + x) / 2;
          ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
        }
      });
      ctx.lineTo(W, H);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      // Line
      ctx.beginPath();
      data.forEach((v, i) => {
        const x = i * step;
        const y = H - (v / max) * (H - 10);
        if (i === 0) ctx.moveTo(x, y);
        else {
          const prevX = (i - 1) * step;
          const prevY = H - (data[i - 1] / max) * (H - 10);
          const cpX = (prevX + x) / 2;
          ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
        }
      });
      ctx.strokeStyle = '#1677ff';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Glow dot at end
      const lastX = (data.length - 1) * step;
      const lastY = H - (data[data.length - 1] / max) * (H - 10);
      ctx.beginPath();
      ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
      ctx.fillStyle = '#1677ff';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(lastX, lastY, 8, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(22,119,255,0.3)';
      ctx.fill();
    };

    const interval = setInterval(drawWave, 500);
    drawWave();
    return () => clearInterval(interval);
  }, [loading]);

  // Stats
  const criticalCount = overview?.severity_distribution?.find((d) => d.severity === 'critical')?.count || health.reduce((s, h) => s + (h.critical || 0), 0);
  const warningCount = overview?.severity_distribution?.find((d) => d.severity === 'warning')?.count || health.reduce((s, h) => s + (h.warning || 0), 0);
  const healthyCount = health.filter((s) => !s.critical && !s.warning).length;
  const totalServices = health.length;
  const activeIncidents = overview?.active_incidents || 0;

  const statusText = criticalCount > 0 ? '局部降级' : warningCount > 0 ? '关注告警' : '运行正常';
  const statusColor = criticalCount > 0 ? COLORS.red : warningCount > 0 ? COLORS.orange : COLORS.green;

  return (
    <div
      ref={containerRef}
      style={{
        position: isFullscreen ? 'fixed' : 'relative',
        inset: isFullscreen ? 0 : undefined,
        height: isFullscreen ? '100vh' : 'calc(100vh - 56px)',
        margin: '-24px',
        background: COLORS.bg,
        color: '#fff',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        fontFamily: 'Inter, -apple-system, sans-serif',
        zIndex: isFullscreen ? 9999 : undefined,
      }}
    >
      {/* ── Top Bar ─────────────────────────────── */}
      <div style={{
        padding: '8px 24px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        borderBottom: `1px solid ${COLORS.border}`,
        background: 'rgba(10,18,40,0.95)',
      }}>
        <Space size={12}>
          <RocketOutlined style={{ fontSize: 20, color: COLORS.blue }} />
          <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: 1 }}>LogMind 指挥中心</span>
        </Space>
        <Space size={16}>
          <Tag
            style={{
              background: 'transparent',
              border: `1px solid ${statusColor}`,
              color: statusColor,
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: 1,
              borderRadius: 4,
            }}
          >
            ● {statusText}
          </Tag>
          <span style={{ fontFamily: 'monospace', fontSize: 20, fontWeight: 300, color: 'rgba(255,255,255,0.6)' }}>
            {clock}
          </span>
          <Button
            type="text"
            icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
            onClick={toggleFullscreen}
            style={{ color: 'rgba(255,255,255,0.5)' }}
          />
        </Space>
      </div>

      {/* ── KPI Row ───────────────────────────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(118px, 1fr))',
        gap: 10,
        padding: '10px 24px',
      }}>
        {[
          { label: '严重异常', value: criticalCount, icon: <AlertOutlined />, color: COLORS.red, pulse: criticalCount > 0 },
          { label: '告警关注', value: warningCount, icon: <ThunderboltOutlined />, color: COLORS.orange, pulse: false },
          { label: '健康服务', value: `${healthyCount}/${totalServices}`, icon: <CheckCircleOutlined />, color: COLORS.green, pulse: false },
          { label: '分析任务', value: overview?.total_tasks || 0, icon: <ClockCircleOutlined />, color: COLORS.blue, pulse: false },
          { label: '活跃故障', value: activeIncidents, icon: <FireOutlined />, color: activeIncidents > 0 ? COLORS.red : COLORS.purple, pulse: activeIncidents > 0 },
          { label: '今日异常', value: overview?.today_anomalies || 0, icon: <RadarChartOutlined />, color: COLORS.orange, pulse: false },
          { label: '告警风暴', value: overview?.storm_count || 0, icon: <CloudOutlined />, color: COLORS.blue, pulse: false },
        ].map((kpi, i) => (
          <div
            key={i}
            style={{
              background: COLORS.card,
              border: `1px solid ${kpi.pulse ? kpi.color + '60' : COLORS.border}`,
              borderRadius: 8,
              padding: '10px 12px',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              animation: kpi.pulse ? 'lm-pulse 1.5s infinite' : undefined,
            }}
          >
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: kpi.color + '15',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 16, color: kpi.color,
            }}>
              {kpi.icon}
            </div>
            <div>
              <div style={{ fontSize: 20, fontWeight: 700, fontFamily: 'monospace', color: kpi.color, lineHeight: 1 }}>
                {kpi.value}
              </div>
              <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.4)', letterSpacing: 1.5, marginTop: 2 }}>
                {kpi.label}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* ── AI Insight Bar ─────────────────────── */}
      {overview?.ai_insight && (
        <div style={{
          padding: '0 24px 8px',
        }}>
          <div style={{
            background: 'linear-gradient(90deg, rgba(114,46,209,0.12), rgba(22,119,255,0.08))',
            border: `1px solid rgba(114,46,209,0.25)`,
            borderRadius: 8,
            padding: '8px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            overflow: 'hidden',
          }}>
            <span style={{ fontSize: 14, flexShrink: 0 }}>🤖</span>
            <span style={{
              fontSize: 11, color: 'rgba(255,255,255,0.7)', letterSpacing: 0.5,
              fontWeight: 500, whiteSpace: 'nowrap',
            }}>
              AI 洞察
            </span>
            <span style={{ width: 1, height: 14, background: 'rgba(255,255,255,0.1)', flexShrink: 0 }} />
            <div style={{
              flex: 1, overflow: 'hidden', position: 'relative',
            }}>
              <div style={{
                fontSize: 12, color: 'rgba(255,255,255,0.6)', whiteSpace: 'nowrap',
                animation: overview.ai_insight.length > 80 ? 'lm-scroll-left 20s linear infinite' : undefined,
              }}>
                {overview.ai_insight}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Main Grid ───────────────────────────── */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 340px', gap: 12, padding: '0 24px 12px', minHeight: 0 }}>
        {/* Left: Health Matrix */}
        <div style={{
          background: COLORS.card,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 8,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}>
          <div style={{
            padding: '10px 16px',
            borderBottom: `1px solid ${COLORS.border}`,
            fontSize: 12,
            color: 'rgba(255,255,255,0.5)',
            letterSpacing: 1,
            fontWeight: 600,
          }}>
            服务健康矩阵
          </div>
          <div style={{ flex: 1, position: 'relative' }}>
            <canvas ref={healthCanvasRef} style={{ display: 'block', width: '100%', height: '100%' }} />
          </div>
        </div>

        {/* Right: Event Feed */}
        <div style={{
          background: COLORS.card,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 8,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}>
          <div style={{
            padding: '10px 16px',
            borderBottom: `1px solid ${COLORS.border}`,
            fontSize: 12,
            color: 'rgba(255,255,255,0.5)',
            letterSpacing: 1,
            fontWeight: 600,
            display: 'flex',
            justifyContent: 'space-between',
          }}>
            <span>实时事件流</span>
            <span style={{ color: COLORS.green }}>● 实时</span>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}>
            {events.map((e, i) => (
              <div
                key={i}
                style={{
                  padding: '6px 16px',
                  display: 'flex',
                  gap: 8,
                  alignItems: 'flex-start',
                  fontSize: 12,
                  borderBottom: `1px solid rgba(255,255,255,0.03)`,
                  animation: i < 3 ? `lm-fadeSlideIn 0.3s ease-out ${i * 0.1}s both` : undefined,
                }}
              >
                <span style={{ fontFamily: 'monospace', color: 'rgba(255,255,255,0.3)', flexShrink: 0, fontSize: 11 }}>
                  {e.time}
                </span>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', flexShrink: 0, marginTop: 4,
                  background: e.level === 'critical' ? COLORS.red : e.level === 'warning' ? COLORS.orange : e.level === 'info' ? COLORS.blue : COLORS.green,
                  boxShadow: `0 0 6px ${e.level === 'critical' ? COLORS.red : e.level === 'warning' ? COLORS.orange : e.level === 'info' ? COLORS.blue : COLORS.green}`,
                }} />
                <span style={{ color: 'rgba(255,255,255,0.7)', lineHeight: 1.4 }}>{e.message}</span>
              </div>
            ))}
            {events.length === 0 && (
              <div style={{ textAlign: 'center', padding: 40, color: 'rgba(255,255,255,0.2)' }}>
                暂无实时事件，系统处于静默观察中
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Wave Bottom ─────────────────────────── */}
      <div style={{
        padding: '0 24px 12px',
      }}>
        <div style={{
          background: COLORS.card,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 8,
          padding: '8px 16px 4px',
        }}>
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)', letterSpacing: 1, marginBottom: 4, fontWeight: 600 }}>
            错误率波形
          </div>
          <div style={{ position: 'relative' }}>
            <canvas ref={waveCanvasRef} style={{ display: 'block', width: '100%', height: 80 }} />
          </div>
        </div>
      </div>

      {/* Scroll animation for AI insight */}
      <style>{`
        @keyframes lm-scroll-left {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
      `}</style>
    </div>
  );
};

export default CommandCenter;
