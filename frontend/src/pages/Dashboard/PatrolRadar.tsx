import React, { useEffect, useState, useRef } from 'react';
import { Typography, Space, Tag, Spin, Empty, Card } from 'antd';
import { RadarChartOutlined, ThunderboltOutlined, CheckCircleOutlined } from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = {
  critical: '#ff4d4f', warning: '#faad14', normal: '#52c41a',
};

const PatrolRadar: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const angleRef = useRef(0);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await client.get('/dashboard/patrol-status');
        setData(res.data);
      } catch { /* ignore */ }
      setLoading(false);
    };
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  // Canvas radar animation
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data?.services?.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const size = Math.min(canvas.parentElement?.clientWidth || 400, 400);
    canvas.width = size;
    canvas.height = size;
    const W = size;
    const H = size;
    const cx = W / 2;
    const cy = H / 2;
    const maxR = Math.min(W, H) / 2 - 30;

    const services = data.services || [];

    // Pre-calculate stable positions (outside animation loop!)
    const servicePositions = services.map((s: any, i: number) => {
      const angle = (i / services.length) * Math.PI * 2 - Math.PI / 2;
      // Use deterministic offset based on index (golden ratio hash)
      const seed = ((i * 2654435761) >>> 0) / 4294967296; // 0~1 deterministic
      const dist = s.status === 'normal'
        ? maxR * 0.4 + seed * maxR * 0.1
        : maxR * 0.65 + seed * maxR * 0.1;
      return {
        x: cx + Math.cos(angle) * dist,
        y: cy + Math.sin(angle) * dist,
      };
    });

    const draw = () => {
      angleRef.current += 0.008;
      ctx.clearRect(0, 0, W, H);

      // Background rings
      for (let r = 1; r <= 4; r++) {
        ctx.beginPath();
        ctx.arc(cx, cy, (maxR / 4) * r, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(22,119,255,0.08)';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      // Cross lines
      for (let a = 0; a < 4; a++) {
        const ang = (a * Math.PI) / 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(ang) * maxR, cy + Math.sin(ang) * maxR);
        ctx.strokeStyle = 'rgba(22,119,255,0.06)';
        ctx.stroke();
      }

      // Scan line (rotating)
      const scanAngle = angleRef.current;
      const grad = ctx.createConicGradient(scanAngle, cx, cy);
      grad.addColorStop(0, 'rgba(22,119,255,0.25)');
      grad.addColorStop(0.15, 'rgba(22,119,255,0)');
      grad.addColorStop(1, 'rgba(22,119,255,0)');
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, maxR, scanAngle, scanAngle + 0.5);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      // Scan line
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(scanAngle) * maxR, cy + Math.sin(scanAngle) * maxR);
      ctx.strokeStyle = 'rgba(22,119,255,0.6)';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Service dots (use pre-calculated positions)
      services.forEach((s: any, i: number) => {
        const { x, y } = servicePositions[i];
        const color = statusColors[s.status] || '#52c41a';

        // Pulse for anomalies
        const pulse = s.status !== 'normal'
          ? Math.sin(angleRef.current * 4 + i) * 0.3 + 0.7
          : 1;

        // Glow
        ctx.shadowColor = color;
        ctx.shadowBlur = s.status === 'critical' ? 15 * pulse : s.status === 'warning' ? 10 : 4;

        // Dot
        const dotR = s.status === 'critical' ? 6 : s.status === 'warning' ? 5 : 3;
        ctx.beginPath();
        ctx.arc(x, y, dotR, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();

        ctx.shadowBlur = 0;

        // Label
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillStyle = 'rgba(255,255,255,0.5)';
        const label = s.service_name?.length > 10 ? s.service_name.slice(0, 9) + '…' : s.service_name;
        ctx.fillText(label || '', x, y + dotR + 12);
      });

      // Center status
      ctx.font = 'bold 12px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      if (data.anomaly_count > 0) {
        ctx.fillStyle = '#ff4d4f';
        ctx.fillText(`${data.anomaly_count} 异常`, cx, cy - 6);
        ctx.font = '10px Inter, sans-serif';
        ctx.fillStyle = 'rgba(255,255,255,0.4)';
        ctx.fillText('检测中...', cx, cy + 10);
      } else {
        ctx.fillStyle = '#52c41a';
        ctx.fillText('全部正常', cx, cy - 6);
        ctx.font = '10px Inter, sans-serif';
        ctx.fillStyle = 'rgba(255,255,255,0.4)';
        ctx.fillText('持续巡逻中', cx, cy + 10);
      }

      animRef.current = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(animRef.current);
  }, [data]);

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>;
  if (!data) return <Empty description="暂无数据" />;

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <RadarChartOutlined style={{ fontSize: 20, color: data.anomaly_count > 0 ? '#ff4d4f' : '#52c41a' }} />
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>异常巡逻雷达</Title>
          <Tag color={data.anomaly_count > 0 ? '#ff4d4f' : '#52c41a'} style={{ borderRadius: 4, fontWeight: 600 }}>
            {data.anomaly_count > 0 ? `${data.anomaly_count} 异常` : '一切正常'}
          </Tag>
        </Space>
        <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
          30s 自动刷新 · {data.services?.length || 0} 个服务
        </Text>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 16 }}>
        {/* Radar */}
        <Card style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          styles={{ body: { padding: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' } }}>
          <canvas ref={canvasRef} style={{ display: 'block', maxWidth: 400, maxHeight: 400 }} />
        </Card>

        {/* Service List */}
        <Card size="small" title="服务状态" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
          styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' }, body: { maxHeight: 400, overflow: 'auto' } }}>
          {(data.services || []).map((s: any, i: number) => (
            <div key={i} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '8px 0',
              borderBottom: i < (data.services?.length || 0) - 1 ? '1px solid rgba(255,255,255,0.03)' : 'none',
              animation: `lm-fadeSlideIn 0.3s ease-out ${i * 0.04}s both`,
            }}>
              <Space size={8}>
                <div style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: statusColors[s.status],
                  boxShadow: s.status !== 'normal' ? `0 0 8px ${statusColors[s.status]}` : 'none',
                }} />
                <Text style={{ color: 'var(--lm-text)', fontSize: 13 }}>{s.service_name}</Text>
              </Space>
              <Space size={4}>
                {s.error_count_1h > 0 && (
                  <Tag color="#ff4d4f" style={{ borderRadius: 3, fontSize: 10, margin: 0 }}>
                    <ThunderboltOutlined /> {s.error_count_1h}
                  </Tag>
                )}
                {s.error_rate_change !== 0 && (
                  <Text style={{
                    fontSize: 10, fontFamily: 'monospace',
                    color: s.error_rate_change > 0 ? '#ff4d4f' : '#52c41a',
                  }}>
                    {s.error_rate_change > 0 ? '↑' : '↓'}{Math.abs(s.error_rate_change)}%
                  </Text>
                )}
                {s.status === 'normal' && s.error_count_1h === 0 && (
                  <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 12 }} />
                )}
              </Space>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
};

export default PatrolRadar;
