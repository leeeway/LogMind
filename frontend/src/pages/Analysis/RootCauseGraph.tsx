import React, { useRef, useEffect, useState, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { Typography, Spin, Button, Space, Tag, Empty } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined, AimOutlined, NodeIndexOutlined } from '@ant-design/icons';
import { analysisApi } from '@/api/analysis';

const { Title, Text } = Typography;

// Physics
const REPULSION = 6000;
const SPRING_K = 0.01;
const SPRING_LEN = 180;
const DAMPING = 0.88;
const DT = 0.5;
const NODE_R = 28;

interface RCNode {
  id: string; label: string; severity: string; service: string;
  timestamp: string; detail: string;
  x: number; y: number; vx: number; vy: number;
}
interface RCEdge { source: string; target: string; relation: string; }
interface Particle { edgeIdx: number; t: number; speed: number; }

const sevStyle: Record<string, { fill: string; glow: string; border: string; icon: string }> = {
  critical: { fill: '#3a1a1a', glow: '#ff4d4f', border: '#ff4d4f', icon: '🔴' },
  warning:  { fill: '#3a2e1a', glow: '#faad14', border: '#faad14', icon: '🟡' },
  info:     { fill: '#1a1a3a', glow: '#1677ff', border: '#1677ff', icon: '🔵' },
  suggestion: { fill: '#1a3a1a', glow: '#52c41a', border: '#52c41a', icon: '💡' },
};

const relationColors: Record<string, string> = {
  '导致': '#ff4d4f',
  '触发': '#faad14',
  '关联': '#1677ff',
};

const RootCauseGraph: React.FC = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const taskId = params.get('taskId') || '';
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const animRef = useRef(0);
  const nodesRef = useRef<RCNode[]>([]);
  const edgesRef = useRef<RCEdge[]>([]);
  const particlesRef = useRef<Particle[]>([]);
  const mouseRef = useRef({ x: 0, y: 0, down: false, dragNode: -1 });
  const hoverRef = useRef(-1);
  const [loading, setLoading] = useState(true);
  const [empty, setEmpty] = useState(false);
  const [selectedNode, setSelectedNode] = useState<RCNode | null>(null);
  const timeRef = useRef(0);

  const loadData = useCallback(async () => {
    if (!taskId) { setEmpty(true); setLoading(false); return; }
    setLoading(true);
    try {
      const { data } = await analysisApi.getRootcauseChain(taskId);
      if (!data.nodes?.length) { setEmpty(true); setLoading(false); return; }

      const cx = 500, cy = 350;
      const nodes: RCNode[] = data.nodes.map((n: any, i: number) => ({
        ...n,
        x: cx + Math.cos(i * (2 * Math.PI / data.nodes.length)) * 200 + (Math.random() - 0.5) * 60,
        y: cy + Math.sin(i * (2 * Math.PI / data.nodes.length)) * 200 + (Math.random() - 0.5) * 60,
        vx: 0, vy: 0,
      }));
      nodesRef.current = nodes;
      edgesRef.current = data.edges || [];

      // Create particles
      const pts: Particle[] = [];
      (data.edges || []).forEach((_: any, i: number) => {
        for (let j = 0; j < 2; j++) {
          pts.push({ edgeIdx: i, t: Math.random(), speed: 0.003 + Math.random() * 0.002 });
        }
      });
      particlesRef.current = pts;
      setEmpty(false);
    } catch { setEmpty(true); }
    setLoading(false);
  }, [taskId]);

  useEffect(() => { loadData(); }, [loadData]);

  // Physics + Render loop
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = container.clientWidth;
      canvas.height = container.clientHeight;
    };
    resize();
    window.addEventListener('resize', resize);

    const getIdx = (id: string) => nodesRef.current.findIndex(n => n.id === id);

    const tick = () => {
      const W = canvas.width, H = canvas.height;
      const nodes = nodesRef.current;
      const edges = edgesRef.current;

      // Physics
      for (let i = 0; i < nodes.length; i++) {
        if (mouseRef.current.dragNode === i) continue;
        let fx = 0, fy = 0;
        // Repulsion
        for (let j = 0; j < nodes.length; j++) {
          if (i === j) continue;
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const d2 = dx * dx + dy * dy + 1;
          const f = REPULSION / d2;
          fx += dx * f; fy += dy * f;
        }
        // Spring
        for (const e of edges) {
          const si = getIdx(e.source), ti = getIdx(e.target);
          if (si < 0 || ti < 0) continue;
          const other = si === i ? ti : ti === i ? si : -1;
          if (other < 0) continue;
          const dx = nodes[i].x - nodes[other].x;
          const dy = nodes[i].y - nodes[other].y;
          const dist = Math.sqrt(dx * dx + dy * dy) + 0.1;
          const f = SPRING_K * (dist - SPRING_LEN);
          fx -= (dx / dist) * f; fy -= (dy / dist) * f;
        }
        // Center gravity
        fx += (W / 2 - nodes[i].x) * 0.0005;
        fy += (H / 2 - nodes[i].y) * 0.0005;

        nodes[i].vx = (nodes[i].vx + fx * DT) * DAMPING;
        nodes[i].vy = (nodes[i].vy + fy * DT) * DAMPING;
        nodes[i].x += nodes[i].vx * DT;
        nodes[i].y += nodes[i].vy * DT;
        nodes[i].x = Math.max(NODE_R, Math.min(W - NODE_R, nodes[i].x));
        nodes[i].y = Math.max(NODE_R, Math.min(H - NODE_R, nodes[i].y));
      }

      // Clear
      ctx.fillStyle = '#0a0a14';
      ctx.fillRect(0, 0, W, H);

      // Grid
      ctx.strokeStyle = 'rgba(255,255,255,0.03)';
      ctx.lineWidth = 1;
      for (let x = 0; x < W; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
      for (let y = 0; y < H; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

      timeRef.current += 0.016;

      // Draw edges
      edges.forEach((e, ei) => {
        const si = getIdx(e.source), ti = getIdx(e.target);
        if (si < 0 || ti < 0) return;
        const s = nodes[si], t = nodes[ti];
        const color = relationColors[e.relation] || '#555';

        // Line
        ctx.beginPath();
        ctx.strokeStyle = color + '60';
        ctx.lineWidth = 2;
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
        ctx.stroke();

        // Arrow
        const dx = t.x - s.x, dy = t.y - s.y;
        const len = Math.sqrt(dx * dx + dy * dy);
        if (len < 1) return;
        const nx = dx / len, ny = dy / len;
        const ax = t.x - nx * (NODE_R + 6), ay = t.y - ny * (NODE_R + 6);
        const arrowSize = 10;
        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.moveTo(ax, ay);
        ctx.lineTo(ax - nx * arrowSize - ny * 5, ay - ny * arrowSize + nx * 5);
        ctx.lineTo(ax - nx * arrowSize + ny * 5, ay - ny * arrowSize - nx * 5);
        ctx.fill();

        // Relation label
        const mx = (s.x + t.x) / 2, my = (s.y + t.y) / 2;
        ctx.font = '11px Inter, sans-serif';
        ctx.fillStyle = color + 'cc';
        ctx.textAlign = 'center';
        ctx.fillText(e.relation, mx, my - 8);
      });

      // Draw particles
      particlesRef.current.forEach(p => {
        if (p.edgeIdx >= edges.length) return;
        const e = edges[p.edgeIdx];
        const si = getIdx(e.source), ti = getIdx(e.target);
        if (si < 0 || ti < 0) return;
        const s = nodes[si], t = nodes[ti];
        const x = s.x + (t.x - s.x) * p.t;
        const y = s.y + (t.y - s.y) * p.t;
        const color = relationColors[e.relation] || '#fff';
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, 8, 0, Math.PI * 2);
        ctx.fillStyle = color + '20';
        ctx.fill();
        p.t += p.speed;
        if (p.t > 1) p.t = 0;
      });

      // Draw nodes
      nodes.forEach((n, i) => {
        const style = sevStyle[n.severity] || sevStyle.info;
        const isHover = hoverRef.current === i;
        const isSelected = selectedNode?.id === n.id;
        const r = NODE_R + (isHover ? 4 : 0);

        // Glow
        const glow = ctx.createRadialGradient(n.x, n.y, r * 0.5, n.x, n.y, r * 2.5);
        glow.addColorStop(0, style.glow + '30');
        glow.addColorStop(1, style.glow + '00');
        ctx.beginPath();
        ctx.arc(n.x, n.y, r * 2.5, 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();

        // Node body
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = style.fill;
        ctx.fill();
        ctx.strokeStyle = isSelected ? '#fff' : style.border;
        ctx.lineWidth = isSelected ? 3 : 2;
        ctx.stroke();

        // Icon
        ctx.font = '16px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(style.icon, n.x, n.y + 5);

        // Label
        ctx.font = `${isHover ? 'bold ' : ''}11px Inter, sans-serif`;
        ctx.fillStyle = '#e0e0e0';
        ctx.textAlign = 'center';
        const label = n.label.length > 30 ? n.label.slice(0, 30) + '…' : n.label;
        ctx.fillText(label, n.x, n.y + r + 16);
      });

      // Title
      ctx.font = 'bold 14px Inter, sans-serif';
      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      ctx.textAlign = 'left';
      ctx.fillText(`根因链图谱 — ${nodes.length} 节点 · ${edges.length} 关系`, 16, 24);

      // Legend
      const legends = [
        { color: '#ff4d4f', label: '导致' },
        { color: '#faad14', label: '触发' },
        { color: '#1677ff', label: '关联' },
      ];
      legends.forEach((l, i) => {
        const lx = W - 120, ly = 20 + i * 22;
        ctx.beginPath();
        ctx.moveTo(lx, ly); ctx.lineTo(lx + 20, ly);
        ctx.strokeStyle = l.color; ctx.lineWidth = 2; ctx.stroke();
        ctx.font = '11px Inter, sans-serif';
        ctx.fillStyle = l.color;
        ctx.textAlign = 'left';
        ctx.fillText(l.label, lx + 26, ly + 4);
      });

      animRef.current = requestAnimationFrame(tick);
    };

    animRef.current = requestAnimationFrame(tick);

    // Mouse handlers
    const onMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      mouseRef.current.x = mx; mouseRef.current.y = my;

      if (mouseRef.current.dragNode >= 0) {
        const n = nodesRef.current[mouseRef.current.dragNode];
        if (n) { n.x = mx; n.y = my; n.vx = 0; n.vy = 0; }
        return;
      }

      let found = -1;
      nodesRef.current.forEach((n, i) => {
        const d = Math.sqrt((mx - n.x) ** 2 + (my - n.y) ** 2);
        if (d < NODE_R + 4) found = i;
      });
      hoverRef.current = found;
      canvas.style.cursor = found >= 0 ? 'pointer' : 'default';
    };

    const onDown = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      nodesRef.current.forEach((n, i) => {
        if (Math.sqrt((mx - n.x) ** 2 + (my - n.y) ** 2) < NODE_R + 4) {
          mouseRef.current.dragNode = i;
          mouseRef.current.down = true;
        }
      });
    };

    const onUp = () => {
      if (mouseRef.current.dragNode >= 0 && !mouseRef.current.down) {
        // was a click
      }
      if (hoverRef.current >= 0) {
        setSelectedNode(nodesRef.current[hoverRef.current] || null);
      }
      mouseRef.current.dragNode = -1;
      mouseRef.current.down = false;
    };

    canvas.addEventListener('mousemove', onMove);
    canvas.addEventListener('mousedown', onDown);
    canvas.addEventListener('mouseup', onUp);

    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener('resize', resize);
      canvas.removeEventListener('mousemove', onMove);
      canvas.removeEventListener('mousedown', onDown);
      canvas.removeEventListener('mouseup', onUp);
    };
  }, [loading, selectedNode]);

  if (loading) return <div style={{ textAlign: 'center', paddingTop: 120 }}><Spin size="large" /></div>;
  if (empty) return (
    <div className="lm-animate-in" style={{ padding: 24 }}>
      <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
      <div style={{ marginTop: 60 }}><Empty description="无根因链数据，请先完成 AI 分析" /></div>
    </div>
  );

  return (
    <div className="lm-animate-in" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ padding: '12px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
            <NodeIndexOutlined style={{ marginRight: 8 }} />根因链图谱
          </Title>
          <Tag color="#722ed1">Task: {taskId.slice(0, 8)}</Tag>
        </Space>
        <Space>
          <Button icon={<AimOutlined />} onClick={() => {
            nodesRef.current.forEach((n, i) => {
              n.x = 500 + Math.cos(i * (2 * Math.PI / nodesRef.current.length)) * 200;
              n.y = 350 + Math.sin(i * (2 * Math.PI / nodesRef.current.length)) * 200;
              n.vx = 0; n.vy = 0;
            });
          }}>重置布局</Button>
          <Button icon={<ReloadOutlined />} onClick={loadData}>刷新</Button>
        </Space>
      </div>

      {/* Canvas + Detail Panel */}
      <div style={{ flex: 1, display: 'flex', position: 'relative' }}>
        <div ref={containerRef} style={{ flex: 1, position: 'relative' }}>
          <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
        </div>

        {/* Detail Panel */}
        {selectedNode && (
          <div style={{
            width: 320, background: 'var(--lm-bg-card)', borderLeft: '1px solid var(--lm-border-light)',
            padding: 16, overflowY: 'auto', animation: 'lm-slide-in 0.3s ease',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <Tag color={(sevStyle[selectedNode.severity] || sevStyle.info).border}>
                {selectedNode.severity.toUpperCase()}
              </Tag>
              <Button size="small" type="text" onClick={() => setSelectedNode(null)}>✕</Button>
            </div>
            <Text strong style={{ fontSize: 14, color: 'var(--lm-text)', display: 'block', marginBottom: 8 }}>
              {selectedNode.label}
            </Text>
            <div style={{
              padding: 12, background: 'var(--lm-bg-elevated)', borderRadius: 8, marginBottom: 12,
              fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6, whiteSpace: 'pre-wrap',
            }}>
              {selectedNode.detail || '无详细信息'}
            </div>
            {selectedNode.timestamp && (
              <Text type="secondary" style={{ fontSize: 11 }}>
                时间: {new Date(selectedNode.timestamp).toLocaleString()}
              </Text>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default RootCauseGraph;
