import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Typography, Space, Tag, Button, Tooltip } from 'antd';
import {
  FullscreenOutlined, FullscreenExitOutlined, ReloadOutlined,
  AlertOutlined, CheckCircleOutlined, ClockCircleOutlined,
  ThunderboltOutlined, RocketOutlined, FireOutlined,
  RadarChartOutlined, CloudOutlined,
} from '@ant-design/icons';
import { dashboardApi } from '@/api/dashboard';
import dayjs from 'dayjs';

const { Text } = Typography;

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
  const [overview, setOverview] = useState<any>(null);
  const [health, setHealth] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
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
      const [ovRes, hlRes] = await Promise.all([
        dashboardApi.getOverview(1),
        dashboardApi.getBusinessHealth(1),
      ]);
      setOverview(ovRes.data);
      const services = (hlRes.data?.items || []).map((s: any) => ({
        ...s,
        name: s.business_line_name,
        critical: s.critical_count || 0,
        warning: s.warning_count || 0,
      }));
      setHealth(services);

      // Build synthetic event feed from data
      const newEvents: any[] = [];
      (hlRes.data?.services || []).forEach((s: any) => {
        if (s.critical > 0) {
          newEvents.push({
            time: dayjs().subtract(Math.random() * 60, 'minute').format('HH:mm'),
            level: 'critical',
            message: `${s.name}: ${s.critical} 个严重异常`,
          });
        }
        if (s.warning > 0) {
          newEvents.push({
            time: dayjs().subtract(Math.random() * 120, 'minute').format('HH:mm'),
            level: 'warning',
            message: `${s.name}: ${s.warning} 个告警`,
          });
        }
      });
      newEvents.sort((a, b) => b.time.localeCompare(a.time));
      setEvents(prev => [...newEvents.slice(0, 20), ...prev].slice(0, 50));

      // Feed wave data
      const errorRate = ovRes.data?.severity_distribution?.find((d: any) => d.severity === 'critical')?.count || 0;
      waveDataRef.current.push(errorRate + Math.random() * 5);
      if (waveDataRef.current.length > 120) waveDataRef.current.shift();
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
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

      health.forEach((s: any, i: number) => {
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
        const label = s.name?.length > 8 ? s.name.slice(0, 7) + '…' : s.name || '';
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
  const criticalCount = overview?.severity_distribution?.find((d: any) => d.severity === 'critical')?.count || health.reduce((s: number, h: any) => s + (h.critical || 0), 0);
  const warningCount = overview?.severity_distribution?.find((d: any) => d.severity === 'warning')?.count || health.reduce((s: number, h: any) => s + (h.warning || 0), 0);
  const healthyCount = health.filter((s: any) => !s.critical && !s.warning).length;
  const totalServices = health.length;

  const statusText = criticalCount > 0 ? 'DEGRADED' : warningCount > 0 ? 'WARNING' : 'OPERATIONAL';
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
          <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: 2 }}>LOGMIND COMMAND CENTER</span>
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
        gridTemplateColumns: 'repeat(7, 1fr)',
        gap: 10,
        padding: '10px 24px',
      }}>
        {[
          { label: 'CRITICAL', value: criticalCount, icon: <AlertOutlined />, color: COLORS.red, pulse: criticalCount > 0 },
          { label: 'WARNING', value: warningCount, icon: <ThunderboltOutlined />, color: COLORS.orange, pulse: false },
          { label: 'HEALTHY', value: `${healthyCount}/${totalServices}`, icon: <CheckCircleOutlined />, color: COLORS.green, pulse: false },
          { label: 'TASKS', value: overview?.total_tasks || 0, icon: <ClockCircleOutlined />, color: COLORS.blue, pulse: false },
          { label: 'INCIDENTS', value: overview?.active_incidents || 0, icon: <FireOutlined />, color: overview?.active_incidents > 0 ? COLORS.red : COLORS.purple, pulse: (overview?.active_incidents || 0) > 0 },
          { label: 'ANOMALIES', value: overview?.today_anomalies || 0, icon: <RadarChartOutlined />, color: COLORS.orange, pulse: false },
          { label: 'STORMS', value: overview?.storm_count || 0, icon: <CloudOutlined />, color: COLORS.blue, pulse: false },
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
              AI INSIGHT
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
            SERVICE HEALTH MATRIX
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
            <span>LIVE EVENT FEED</span>
            <span style={{ color: COLORS.green }}>● LIVE</span>
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
                  background: e.level === 'critical' ? COLORS.red : e.level === 'warning' ? COLORS.orange : COLORS.green,
                  boxShadow: `0 0 6px ${e.level === 'critical' ? COLORS.red : e.level === 'warning' ? COLORS.orange : COLORS.green}`,
                }} />
                <span style={{ color: 'rgba(255,255,255,0.7)', lineHeight: 1.4 }}>{e.message}</span>
              </div>
            ))}
            {events.length === 0 && (
              <div style={{ textAlign: 'center', padding: 40, color: 'rgba(255,255,255,0.2)' }}>
                等待事件...
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
            ERROR RATE WAVEFORM
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
