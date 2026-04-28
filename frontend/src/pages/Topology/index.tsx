import React, { useRef, useEffect, useState, useCallback } from 'react';
import { Typography, Spin, Button, Space, Tag, Card } from 'antd';
import { ReloadOutlined, FullscreenOutlined, ApartmentOutlined } from '@ant-design/icons';
import { topologyApi } from '@/api/chat';

const { Title, Text } = Typography;

// ── Physics Constants ────────────────────────────
const REPULSION = 8000;
const SPRING_K = 0.008;
const SPRING_LEN = 220;
const DAMPING = 0.85;
const DT = 0.6;
const MIN_SPEED = 0.01;

// ── Visual Constants ─────────────────────────────
const NODE_RADIUS = 32;
const PARTICLE_COUNT = 3;
const PARTICLE_SPEED = 0.004;

interface TopoNode {
  id: string; name: string; language: string;
  is_core_path: boolean; business_weight: number; ai_enabled: boolean;
  error_count: number; warning_count: number; alert_count: number;
  health: string;
  // Physics
  x: number; y: number; vx: number; vy: number;
  // Interaction
  dragging?: boolean;
}

interface TopoEdge {
  source: string; target: string; direction: string;
}

interface Particle {
  edge: number; t: number; speed: number;
}

const healthColors: Record<string, { fill: string; glow: string; border: string }> = {
  healthy: { fill: '#1a3a2a', glow: '#52c41a', border: '#52c41a' },
  warning: { fill: '#3a2e1a', glow: '#faad14', border: '#faad14' },
  critical: { fill: '#3a1a1a', glow: '#ff4d4f', border: '#ff4d4f' },
  unknown: { fill: '#1a1a2a', glow: '#8c8c8c', border: '#8c8c8c' },
};

const ServiceTopology: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const animRef = useRef<number>(0);
  const nodesRef = useRef<TopoNode[]>([]);
  const edgesRef = useRef<TopoEdge[]>([]);
  const particlesRef = useRef<Particle[]>([]);
  const mouseRef = useRef({ x: 0, y: 0, down: false, dragNode: -1 });
  const transformRef = useRef({ x: 0, y: 0, scale: 1 });
  const hoverNodeRef = useRef<number>(-1);
  const [loading, setLoading] = useState(true);
  const [nodeInfo, setNodeInfo] = useState<TopoNode | null>(null);
  const timeRef = useRef(0);

  // ── Load Data ─────────────────────────────────
  const loadTopology = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await topologyApi.getTopology();
      const nodes: TopoNode[] = (data.nodes || []).map((n: any, i: number) => ({
        ...n,
        x: 400 + Math.cos(i * 2.4) * 200 + Math.random() * 80,
        y: 300 + Math.sin(i * 2.4) * 200 + Math.random() * 80,
        vx: 0, vy: 0,
      }));
      nodesRef.current = nodes;
      edgesRef.current = data.edges || [];

      // Create particles for each edge
      const particles: Particle[] = [];
      (data.edges || []).forEach((_: any, idx: number) => {
        for (let p = 0; p < PARTICLE_COUNT; p++) {
          particles.push({ edge: idx, t: p / PARTICLE_COUNT, speed: PARTICLE_SPEED + Math.random() * 0.002 });
        }
      });
      particlesRef.current = particles;
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { loadTopology(); }, [loadTopology]);

  // ── Canvas Resize ────────────────────────────
  useEffect(() => {
    const resize = () => {
      const canvas = canvasRef.current;
      const container = containerRef.current;
      if (!canvas || !container) return;
      canvas.width = container.clientWidth;
      canvas.height = container.clientHeight;
    };
    resize();
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);

  // ── Physics Simulation + Render Loop ──────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const getNodeIndex = (id: string) => nodesRef.current.findIndex(n => n.id === id);

    const loop = () => {
      const W = canvas.width;
      const H = canvas.height;
      const nodes = nodesRef.current;
      const edges = edgesRef.current;
      const particles = particlesRef.current;
      const transform = transformRef.current;
      timeRef.current += 0.016;

      // ── Physics Step ───────────────────────
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].dragging) continue;
        let fx = 0, fy = 0;

        // Repulsion between all node pairs
        for (let j = 0; j < nodes.length; j++) {
          if (i === j) continue;
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = REPULSION / (dist * dist);
          fx += (dx / dist) * force;
          fy += (dy / dist) * force;
        }

        // Spring force along edges
        for (const edge of edges) {
          const si = getNodeIndex(edge.source);
          const ti = getNodeIndex(edge.target);
          if (si === i || ti === i) {
            const other = si === i ? ti : si;
            if (other < 0) continue;
            const dx = nodes[other].x - nodes[i].x;
            const dy = nodes[other].y - nodes[i].y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const displacement = dist - SPRING_LEN;
            fx += (dx / dist) * displacement * SPRING_K;
            fy += (dy / dist) * displacement * SPRING_K;
          }
        }

        // Center gravity
        fx += (W / 2 - nodes[i].x) * 0.0003;
        fy += (H / 2 - nodes[i].y) * 0.0003;

        nodes[i].vx = (nodes[i].vx + fx * DT) * DAMPING;
        nodes[i].vy = (nodes[i].vy + fy * DT) * DAMPING;

        if (Math.abs(nodes[i].vx) < MIN_SPEED) nodes[i].vx = 0;
        if (Math.abs(nodes[i].vy) < MIN_SPEED) nodes[i].vy = 0;

        nodes[i].x += nodes[i].vx;
        nodes[i].y += nodes[i].vy;
      }

      // ── Render ────────────────────────────
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.translate(transform.x, transform.y);
      ctx.scale(transform.scale, transform.scale);

      // Background grid
      ctx.strokeStyle = 'rgba(255,255,255,0.015)';
      ctx.lineWidth = 1;
      for (let x = 0; x < W * 2; x += 40) {
        ctx.beginPath(); ctx.moveTo(x, -H); ctx.lineTo(x, H * 2); ctx.stroke();
      }
      for (let y = 0; y < H * 2; y += 40) {
        ctx.beginPath(); ctx.moveTo(-W, y); ctx.lineTo(W * 2, y); ctx.stroke();
      }

      // ── Draw Edges ─────────────────────────
      for (let e = 0; e < edges.length; e++) {
        const si = getNodeIndex(edges[e].source);
        const ti = getNodeIndex(edges[e].target);
        if (si < 0 || ti < 0) continue;
        const s = nodes[si];
        const t = nodes[ti];

        const isHighlighted = hoverNodeRef.current === si || hoverNodeRef.current === ti;

        // Bezier curve
        const mx = (s.x + t.x) / 2;
        const my = (s.y + t.y) / 2 - 30;

        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.quadraticCurveTo(mx, my, t.x, t.y);
        ctx.strokeStyle = isHighlighted ? 'rgba(22,119,255,0.6)' : 'rgba(255,255,255,0.08)';
        ctx.lineWidth = isHighlighted ? 2.5 : 1.5;
        ctx.stroke();

        // Arrowhead
        const angle = Math.atan2(t.y - my, t.x - mx);
        const arrowDist = NODE_RADIUS + 8;
        const ax = t.x - Math.cos(angle) * arrowDist;
        const ay = t.y - Math.sin(angle) * arrowDist;
        ctx.save();
        ctx.translate(ax, ay);
        ctx.rotate(angle);
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(-10, -5);
        ctx.lineTo(-10, 5);
        ctx.closePath();
        ctx.fillStyle = isHighlighted ? 'rgba(22,119,255,0.6)' : 'rgba(255,255,255,0.15)';
        ctx.fill();
        ctx.restore();
      }

      // ── Draw Particles ──────────────────────
      for (const p of particles) {
        const edge = edges[p.edge];
        if (!edge) continue;
        const si = getNodeIndex(edge.source);
        const ti = getNodeIndex(edge.target);
        if (si < 0 || ti < 0) continue;
        const s = nodes[si];
        const t = nodes[ti];

        p.t += p.speed;
        if (p.t > 1) p.t -= 1;

        const mx = (s.x + t.x) / 2;
        const my = (s.y + t.y) / 2 - 30;

        // Quadratic bezier interpolation
        const tt = p.t;
        const px = (1 - tt) * (1 - tt) * s.x + 2 * (1 - tt) * tt * mx + tt * tt * t.x;
        const py = (1 - tt) * (1 - tt) * s.y + 2 * (1 - tt) * tt * my + tt * tt * t.y;

        const isError = nodes[si].health === 'critical' || nodes[ti].health === 'critical';
        const particleColor = isError ? '#ff4d4f' : '#1677ff';

        ctx.beginPath();
        ctx.arc(px, py, 2.5, 0, Math.PI * 2);
        ctx.fillStyle = particleColor;
        ctx.fill();

        // Glow
        ctx.beginPath();
        ctx.arc(px, py, 5, 0, Math.PI * 2);
        ctx.fillStyle = isError ? 'rgba(255,77,79,0.3)' : 'rgba(22,119,255,0.2)';
        ctx.fill();
      }

      // ── Draw Nodes ─────────────────────────
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const colors = healthColors[n.health] || healthColors.unknown;
        const isHover = hoverNodeRef.current === i;
        const r = isHover ? NODE_RADIUS + 4 : NODE_RADIUS;
        const pulse = n.health === 'critical' ? Math.sin(timeRef.current * 4) * 0.3 + 0.7 : 1;

        // Outer glow
        if (n.health !== 'healthy' || isHover) {
          const gradient = ctx.createRadialGradient(n.x, n.y, r, n.x, n.y, r * 2.5);
          gradient.addColorStop(0, `${colors.glow}${Math.round(pulse * 25).toString(16).padStart(2, '0')}`);
          gradient.addColorStop(1, 'rgba(0,0,0,0)');
          ctx.beginPath();
          ctx.arc(n.x, n.y, r * 2.5, 0, Math.PI * 2);
          ctx.fillStyle = gradient;
          ctx.fill();
        }

        // Ripple effect for critical
        if (n.health === 'critical') {
          const rippleR = r + 10 + (timeRef.current * 30) % 40;
          const rippleAlpha = Math.max(0, 1 - ((timeRef.current * 30) % 40) / 40) * 0.4;
          ctx.beginPath();
          ctx.arc(n.x, n.y, rippleR, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(255,77,79,${rippleAlpha})`;
          ctx.lineWidth = 2;
          ctx.stroke();
        }

        // Node circle
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = colors.fill;
        ctx.fill();
        ctx.strokeStyle = `${colors.border}${Math.round(pulse * 180).toString(16).padStart(2, '0')}`;
        ctx.lineWidth = 2;
        ctx.stroke();

        // Core path badge
        if (n.is_core_path) {
          ctx.beginPath();
          ctx.arc(n.x + r * 0.7, n.y - r * 0.7, 6, 0, Math.PI * 2);
          ctx.fillStyle = '#ff4d4f';
          ctx.fill();
          ctx.strokeStyle = '#0d1220';
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }

        // Name
        ctx.fillStyle = '#fff';
        ctx.font = `${isHover ? '600' : '500'} 11px Inter, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const displayName = n.name.length > 12 ? n.name.slice(0, 11) + '…' : n.name;
        ctx.fillText(displayName, n.x, n.y - 2);

        // Language tag
        ctx.font = '9px Inter, sans-serif';
        ctx.fillStyle = 'rgba(255,255,255,0.4)';
        ctx.fillText(n.language, n.x, n.y + 12);

        // Alert count badge
        if (n.alert_count > 0) {
          const badgeX = n.x + r * 0.6;
          const badgeY = n.y + r * 0.6;
          ctx.beginPath();
          ctx.arc(badgeX, badgeY, 10, 0, Math.PI * 2);
          ctx.fillStyle = n.health === 'critical' ? '#ff4d4f' : '#faad14';
          ctx.fill();
          ctx.fillStyle = '#fff';
          ctx.font = 'bold 9px Inter, sans-serif';
          ctx.fillText(String(n.alert_count), badgeX, badgeY + 1);
        }
      }

      ctx.restore();
      animRef.current = requestAnimationFrame(loop);
    };

    animRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(animRef.current);
  }, [loading]);

  // ── Mouse Interaction ─────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const getCanvasPos = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const t = transformRef.current;
      return { x: (e.clientX - rect.left - t.x) / t.scale, y: (e.clientY - rect.top - t.y) / t.scale };
    };

    const findNode = (pos: { x: number; y: number }) => {
      for (let i = nodesRef.current.length - 1; i >= 0; i--) {
        const n = nodesRef.current[i];
        const dx = pos.x - n.x, dy = pos.y - n.y;
        if (dx * dx + dy * dy < NODE_RADIUS * NODE_RADIUS * 1.5) return i;
      }
      return -1;
    };

    const onMouseDown = (e: MouseEvent) => {
      const pos = getCanvasPos(e);
      const idx = findNode(pos);
      mouseRef.current = { x: e.clientX, y: e.clientY, down: true, dragNode: idx };
      if (idx >= 0) {
        nodesRef.current[idx].dragging = true;
        setNodeInfo(nodesRef.current[idx]);
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      const pos = getCanvasPos(e);
      hoverNodeRef.current = findNode(pos);
      canvas.style.cursor = hoverNodeRef.current >= 0 ? 'pointer' : mouseRef.current.down ? 'grabbing' : 'grab';

      if (mouseRef.current.down) {
        if (mouseRef.current.dragNode >= 0) {
          const n = nodesRef.current[mouseRef.current.dragNode];
          n.x = pos.x;
          n.y = pos.y;
          n.vx = 0;
          n.vy = 0;
        } else {
          transformRef.current.x += e.clientX - mouseRef.current.x;
          transformRef.current.y += e.clientY - mouseRef.current.y;
          mouseRef.current.x = e.clientX;
          mouseRef.current.y = e.clientY;
        }
      }
    };

    const onMouseUp = () => {
      if (mouseRef.current.dragNode >= 0) {
        nodesRef.current[mouseRef.current.dragNode].dragging = false;
      }
      mouseRef.current.down = false;
      mouseRef.current.dragNode = -1;
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.92 : 1.08;
      const t = transformRef.current;
      t.scale = Math.min(3, Math.max(0.3, t.scale * delta));
    };

    canvas.addEventListener('mousedown', onMouseDown);
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseup', onMouseUp);
    canvas.addEventListener('mouseleave', onMouseUp);
    canvas.addEventListener('wheel', onWheel, { passive: false });

    return () => {
      canvas.removeEventListener('mousedown', onMouseDown);
      canvas.removeEventListener('mousemove', onMouseMove);
      canvas.removeEventListener('mouseup', onMouseUp);
      canvas.removeEventListener('mouseleave', onMouseUp);
      canvas.removeEventListener('wheel', onWheel);
    };
  }, []);

  return (
    <div className="lm-animate-in" style={{ height: 'calc(100vh - 56px)', margin: '-24px', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{
        padding: '12px 24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        borderBottom: '1px solid var(--lm-border-light)', background: 'var(--lm-bg-container)',
      }}>
        <Space>
          <ApartmentOutlined style={{ fontSize: 18, color: '#1677ff' }} />
          <Title level={5} style={{ margin: 0, color: 'var(--lm-text)' }}>服务拓扑</Title>
          <Tag color="blue">{nodesRef.current.length} 个服务</Tag>
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadTopology} size="small">刷新</Button>
        </Space>
      </div>

      {/* Canvas */}
      <div ref={containerRef} style={{ flex: 1, position: 'relative', background: '#060a13' }}>
        {loading && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 }}>
            <Spin size="large" />
          </div>
        )}
        <canvas ref={canvasRef} style={{ display: 'block' }} />

        {/* Node Info Card */}
        {nodeInfo && (
          <Card
            size="small"
            style={{
              position: 'absolute', top: 16, right: 16, width: 240, zIndex: 20,
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
              borderRadius: 12, boxShadow: 'var(--lm-shadow-elevated)',
            }}
            title={<Text style={{ color: 'var(--lm-text)', fontWeight: 600 }}>{nodeInfo.name}</Text>}
            extra={<Tag color={healthColors[nodeInfo.health]?.border}>{nodeInfo.health}</Tag>}
          >
            <Space direction="vertical" size={4} style={{ width: '100%', fontSize: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <Text style={{ color: 'var(--lm-text-tertiary)' }}>语言</Text>
                <Text style={{ color: 'var(--lm-text)' }}>{nodeInfo.language}</Text>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <Text style={{ color: 'var(--lm-text-tertiary)' }}>权重</Text>
                <Text style={{ color: 'var(--lm-text)' }}>{nodeInfo.business_weight}</Text>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <Text style={{ color: 'var(--lm-text-tertiary)' }}>告警数</Text>
                <Text style={{ color: nodeInfo.alert_count > 0 ? '#ff4d4f' : '#52c41a', fontWeight: 600 }}>{nodeInfo.alert_count}</Text>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <Text style={{ color: 'var(--lm-text-tertiary)' }}>核心路径</Text>
                <Tag color={nodeInfo.is_core_path ? '#ff4d4f' : '#8c8c8c'} style={{ borderRadius: 4, fontSize: 10 }}>
                  {nodeInfo.is_core_path ? '是' : '否'}
                </Tag>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <Text style={{ color: 'var(--lm-text-tertiary)' }}>AI 分析</Text>
                <Tag color={nodeInfo.ai_enabled ? '#52c41a' : '#8c8c8c'} style={{ borderRadius: 4, fontSize: 10 }}>
                  {nodeInfo.ai_enabled ? '开启' : '关闭'}
                </Tag>
              </div>
            </Space>
          </Card>
        )}

        {/* Legend */}
        <div style={{
          position: 'absolute', bottom: 16, left: 16, display: 'flex', gap: 16,
          padding: '8px 16px', borderRadius: 10,
          background: 'rgba(13,18,32,0.85)', border: '1px solid var(--lm-border-light)',
          fontSize: 11, color: 'var(--lm-text-tertiary)',
        }}>
          <Space><div style={{ width: 10, height: 10, borderRadius: '50%', background: '#52c41a' }} />健康</Space>
          <Space><div style={{ width: 10, height: 10, borderRadius: '50%', background: '#faad14' }} />告警</Space>
          <Space><div style={{ width: 10, height: 10, borderRadius: '50%', background: '#ff4d4f', animation: 'lm-pulse 1.5s infinite' }} />故障</Space>
          <Space><div style={{ width: 10, height: 10, borderRadius: '50%', background: '#1677ff', boxShadow: '0 0 6px #1677ff' }} />请求流</Space>
        </div>
      </div>
    </div>
  );
};

export default ServiceTopology;
