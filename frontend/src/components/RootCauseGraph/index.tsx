import React, { useEffect, useState, useRef, useCallback } from 'react';
import { Spin, Empty, Tag, Space, Typography, Tooltip } from 'antd';
import { AimOutlined, LinkOutlined } from '@ant-design/icons';
import client from '@/api/client';

const { Text } = Typography;

interface GNode {
  id: string;
  label: string;
  severity: string;
  service: string;
  timestamp: string;
  detail: string;
  node_type?: string;
  score?: number;
  evidence_count?: number;
}
interface GEdge { source: string; target: string; relation: string; confidence?: number; }
interface Candidate {
  id: string;
  title: string;
  service: string;
  reason: string;
  severity: string;
  score: number;
  evidence_refs: string[];
  next_verifications: string[];
}
interface Evidence {
  id: string;
  kind: string;
  title: string;
  detail: string;
  service: string;
  severity: string;
  log_refs: string[];
}

const sevColor: Record<string, string> = { critical: '#ff4d4f', warning: '#faad14', info: '#1677ff' };
const relColor: Record<string, string> = { '触发': '#ff4d4f', '导致': '#faad14', '关联': '#1677ff', '支撑': '#52c41a' };
const nodeTypeLabels: Record<string, string> = {
  candidate: '候选',
  log_sample: '日志',
  change_point: '变点',
  cross_service: '跨服务',
  history_match: '历史',
  knowledge_match: '知识库',
  regression: '回归',
  ai_finding: '发现',
};

interface Props { taskId: string; }

const RootCauseGraph: React.FC<Props> = ({ taskId }) => {
  const [nodes, setNodes] = useState<GNode[]>([]);
  const [edges, setEdges] = useState<GEdge[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [nextVerifications, setNextVerifications] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [hovered, setHovered] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const timeRef = useRef(0);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await client.get(`/analysis/${taskId}/rootcause-chain`);
        setNodes(res.data?.nodes || []);
        setEdges(res.data?.edges || []);
        setCandidates(res.data?.candidates || []);
        setEvidence(res.data?.evidence || []);
        setNextVerifications(res.data?.next_verifications || []);
      } catch { /* ignore */ }
      setLoading(false);
    };
    load();
  }, [taskId]);

  // Layout + render
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !nodes.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const parent = canvas.parentElement;
    canvas.width = parent?.clientWidth || 800;
    canvas.height = Math.max(280, nodes.length * 60);
    const W = canvas.width;
    const H = canvas.height;

    // Calculate node positions (left-to-right timeline layout)
    const nodeMap: Record<string, { x: number; y: number; node: GNode }> = {};
    const paddingX = 100;
    const stepX = (W - paddingX * 2) / Math.max(nodes.length - 1, 1);
    const centerY = H / 2;

    nodes.forEach((n, i) => {
      const x = paddingX + i * stepX;
      // Stagger Y to avoid overlap
      const yOff = (i % 2 === 0 ? -1 : 1) * 30;
      nodeMap[n.id] = { x, y: centerY + yOff, node: n };
    });

    const draw = () => {
      timeRef.current += 0.016;
      ctx.clearRect(0, 0, W, H);

      // Timeline axis
      ctx.strokeStyle = 'rgba(255,255,255,0.05)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(paddingX - 20, centerY);
      ctx.lineTo(W - paddingX + 20, centerY);
      ctx.stroke();

      // Draw edges with animated particles
      edges.forEach(e => {
        const src = nodeMap[e.source];
        const tgt = nodeMap[e.target];
        if (!src || !tgt) return;

        const color = relColor[e.relation] || '#1677ff';

        // Curved line
        ctx.beginPath();
        const cpY = (src.y + tgt.y) / 2 - 40;
        ctx.moveTo(src.x, src.y);
        ctx.quadraticCurveTo((src.x + tgt.x) / 2, cpY, tgt.x, tgt.y);
        ctx.strokeStyle = color + '60';
        ctx.lineWidth = 2;
        ctx.stroke();

        // Arrow head
        const angle = Math.atan2(tgt.y - cpY, tgt.x - (src.x + tgt.x) / 2);
        const arrowLen = 10;
        ctx.beginPath();
        ctx.moveTo(tgt.x, tgt.y);
        ctx.lineTo(tgt.x - arrowLen * Math.cos(angle - 0.3), tgt.y - arrowLen * Math.sin(angle - 0.3));
        ctx.moveTo(tgt.x, tgt.y);
        ctx.lineTo(tgt.x - arrowLen * Math.cos(angle + 0.3), tgt.y - arrowLen * Math.sin(angle + 0.3));
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();

        // Animated particle along curve
        const t = (timeRef.current * 0.3 + edges.indexOf(e) * 0.2) % 1;
        const pt = 1 - t;
        const px = pt * pt * src.x + 2 * pt * t * ((src.x + tgt.x) / 2) + t * t * tgt.x;
        const py = pt * pt * src.y + 2 * pt * t * cpY + t * t * tgt.y;
        ctx.beginPath();
        ctx.arc(px, py, 3, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(px, py, 6, 0, Math.PI * 2);
        ctx.fillStyle = color + '30';
        ctx.fill();

        // Relation label
        const midX = (src.x + tgt.x) / 2;
        const midY = cpY - 8;
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillStyle = color + 'aa';
        ctx.fillText(e.relation, midX, midY);
      });

      // Draw nodes
      Object.values(nodeMap).forEach(({ x, y, node }) => {
        const isCandidate = node.node_type === 'candidate';
        const color = isCandidate ? '#52c41a' : sevColor[node.severity] || '#1677ff';
        const isHov = hovered === node.id;
        const breathe = Math.sin(timeRef.current * 2) * 0.15 + 0.85;
        const radius = isCandidate ? (isHov ? 34 : 28) : (isHov ? 28 : 22);

        // Glow
        ctx.shadowColor = color;
        ctx.shadowBlur = isHov ? 25 : 12 * breathe;

        // Circle
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fillStyle = color + (isHov ? 'dd' : isCandidate ? 'aa' : '88');
        ctx.fill();

        // Border
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.shadowBlur = 0;

        // Label
        ctx.font = `${isHov ? 11 : 10}px Inter, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#fff';
        const label = node.label.length > (isCandidate ? 14 : 12)
          ? node.label.slice(0, isCandidate ? 13 : 11) + '…'
          : node.label;
        ctx.fillText(label, x, y);

        if (isCandidate && node.score) {
          ctx.font = '9px monospace';
          ctx.fillStyle = 'rgba(255,255,255,0.75)';
          ctx.fillText(`${Math.round(node.score * 100)}%`, x, y + radius + 14);
        }

        // Timestamp below
        if (node.timestamp && !isCandidate) {
          ctx.font = '9px monospace';
          ctx.fillStyle = 'rgba(255,255,255,0.4)';
          ctx.fillText(
            new Date(node.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
            x, y + radius + 14,
          );
        }
      });

      animRef.current = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(animRef.current);
  }, [nodes, edges, hovered]);

  // Handle hover
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas || !nodes.length) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const paddingX = 100;
    const stepX = (canvas.width - paddingX * 2) / Math.max(nodes.length - 1, 1);
    const centerY = canvas.height / 2;

    let found: string | null = null;
    nodes.forEach((n, i) => {
      const x = paddingX + i * stepX;
      const yOff = (i % 2 === 0 ? -1 : 1) * 30;
      const ny = centerY + yOff;
      const dist = Math.sqrt((mx - x) ** 2 + (my - ny) ** 2);
      if (dist < 28) found = n.id;
    });
    setHovered(found);
  }, [nodes]);

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>;
  if (!nodes.length) return <Empty description="暂无根因链数据" />;

  return (
    <div>
      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 8, paddingLeft: 8 }}>
        {Object.entries(relColor).map(([label, color]) => (
          <Space key={label} size={4}>
            <div style={{ width: 20, height: 2, background: color, borderRadius: 1 }} />
            <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>{label}</Text>
          </Space>
        ))}
        <div style={{ flex: 1 }} />
        <Tag color="blue">{nodes.length} 节点 · {edges.length} 关系</Tag>
        {candidates.length > 0 && <Tag color="green">{candidates.length} 候选</Tag>}
      </div>
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHovered(null)}
        style={{ display: 'block', width: '100%', cursor: hovered ? 'pointer' : 'default', borderRadius: 8 }}
      />
      {/* Hovered node detail */}
      {hovered && (() => {
        const n = nodes.find(n => n.id === hovered);
        if (!n) return null;
        return (
          <div style={{
            marginTop: 8, padding: '8px 12px',
            background: 'var(--lm-bg-elevated)',
            border: `1px solid ${sevColor[n.severity] || '#1677ff'}30`,
            borderRadius: 8, fontSize: 12,
            animation: 'lm-fadeSlideIn 0.2s ease-out',
          }}>
            <Tag color={sevColor[n.severity]} style={{ borderRadius: 3 }}>{n.severity}</Tag>
            <Tag style={{ borderRadius: 3 }}>{nodeTypeLabels[n.node_type || ''] || n.node_type || '发现'}</Tag>
            {n.score != null && <Tag color="green" style={{ borderRadius: 3 }}>评分 {Math.round(n.score * 100)}%</Tag>}
            <Text style={{ color: 'var(--lm-text)' }}>{n.detail}</Text>
          </div>
        );
      })()}
      {(candidates.length > 0 || evidence.length > 0) && (
        <div style={{
          marginTop: 12, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
          gap: 12,
        }}>
          <div style={{
            padding: 12, borderRadius: 8,
            background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
          }}>
            <Space size={6} style={{ marginBottom: 8 }}>
              <AimOutlined style={{ color: '#52c41a' }} />
              <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 12, fontWeight: 600 }}>候选根因</Text>
            </Space>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {candidates.slice(0, 4).map(candidate => (
                <Tooltip key={candidate.id} title={candidate.reason}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                    <Tag color={sevColor[candidate.severity] || 'green'} style={{ borderRadius: 4, flexShrink: 0 }}>
                      {Math.round(candidate.score * 100)}%
                    </Tag>
                    <Text style={{ color: 'var(--lm-text)', fontSize: 12 }} ellipsis>
                      {candidate.service || candidate.title}
                    </Text>
                    <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 11, flexShrink: 0 }}>
                      {candidate.evidence_refs?.length || 0} 证据
                    </Text>
                  </div>
                </Tooltip>
              ))}
            </div>
          </div>
          <div style={{
            padding: 12, borderRadius: 8,
            background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
          }}>
            <Space size={6} style={{ marginBottom: 8 }}>
              <LinkOutlined style={{ color: '#1677ff' }} />
              <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 12, fontWeight: 600 }}>证据与验证</Text>
            </Space>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {evidence.slice(0, 8).map(item => (
                <Tooltip key={item.id} title={item.detail}>
                  <Tag style={{ borderRadius: 4 }}>
                    {nodeTypeLabels[item.kind] || item.kind}
                    {item.service ? ` · ${item.service}` : ''}
                  </Tag>
                </Tooltip>
              ))}
            </div>
            {nextVerifications.length > 0 && (
              <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
                {nextVerifications.slice(0, 3).map(item => (
                  <Text key={item} style={{ color: 'var(--lm-text-secondary)', fontSize: 12 }} ellipsis>
                    {item}
                  </Text>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default RootCauseGraph;
