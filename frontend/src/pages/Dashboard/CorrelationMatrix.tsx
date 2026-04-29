import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Card, Typography, Space, Button, Row, Col, Tag, Empty, Spin, Tooltip,
} from 'antd';
import {
  ApartmentOutlined, ReloadOutlined, WarningOutlined,
} from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const CorrelationMatrix: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data: res } = await client.get('/dashboard/service-correlation', { params: { days: 14 } });
      setData(res);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Draw heatmap matrix on canvas
  useEffect(() => {
    if (!data || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d')!;
    const { services, matrix } = data;
    const n = services.length;
    if (n === 0) return;

    const cellSize = Math.min(50, Math.max(28, Math.floor(500 / n)));
    const labelWidth = 100;
    const w = labelWidth + n * cellSize + 20;
    const h = labelWidth + n * cellSize + 20;
    canvas.width = w * 2; canvas.height = h * 2;
    canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
    ctx.scale(2, 2);

    ctx.fillStyle = 'rgba(0,0,0,0)';
    ctx.clearRect(0, 0, w, h);

    // Find max value for color scaling
    let maxVal = 1;
    for (const row of matrix) for (const v of row) if (v > maxVal) maxVal = v;

    // Draw cells
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const x = labelWidth + j * cellSize;
        const y = labelWidth + i * cellSize;
        const val = matrix[i][j];
        const intensity = val / maxVal;

        if (i === j) {
          ctx.fillStyle = 'rgba(100,100,100,0.15)';
        } else if (val === 0) {
          ctx.fillStyle = 'rgba(255,255,255,0.03)';
        } else {
          // Blue → Purple → Red gradient
          const r = Math.floor(intensity * 220 + 35);
          const g = Math.floor((1 - intensity) * 50);
          const b = Math.floor((1 - intensity) * 180 + 75);
          ctx.fillStyle = `rgba(${r},${g},${b},${0.3 + intensity * 0.7})`;
        }
        ctx.fillRect(x + 1, y + 1, cellSize - 2, cellSize - 2);

        // Cell value
        if (val > 0 && i !== j) {
          ctx.fillStyle = 'rgba(255,255,255,0.9)';
          ctx.font = `${Math.max(10, cellSize * 0.35)}px Inter, sans-serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(String(val), x + cellSize / 2, y + cellSize / 2);
        }
      }
    }

    // Labels
    ctx.fillStyle = 'rgba(255,255,255,0.7)';
    ctx.font = '11px Inter, sans-serif';
    for (let i = 0; i < n; i++) {
      const label = services[i].length > 12 ? services[i].slice(0, 12) + '…' : services[i];
      // Row labels (left)
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, labelWidth - 6, labelWidth + i * cellSize + cellSize / 2);
      // Column labels (top, rotated)
      ctx.save();
      ctx.translate(labelWidth + i * cellSize + cellSize / 2, labelWidth - 6);
      ctx.rotate(-Math.PI / 4);
      ctx.textAlign = 'left';
      ctx.fillText(label, 0, 0);
      ctx.restore();
    }
  }, [data]);

  if (loading && !data) return <Spin style={{ display: 'block', textAlign: 'center', marginTop: 80 }} />;

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <ApartmentOutlined style={{ marginRight: 8 }} />服务故障关联矩阵
        </Title>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
      </div>

      <Row gutter={16}>
        {/* Matrix */}
        <Col span={16}>
          <Card
            title="共现热力图 (±30min 窗口)"
            size="small"
            style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
            }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data?.services?.length ? (
              <div style={{ overflow: 'auto' }}>
                <canvas ref={canvasRef} />
              </div>
            ) : (
              <Empty description="暂无关联数据" />
            )}
          </Card>
        </Col>

        {/* Cascade Risk */}
        <Col span={8}>
          <Card
            title={<Space><WarningOutlined /> 级联风险 Top 5</Space>}
            size="small"
            style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
            }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data?.cascade_risks?.length ? data.cascade_risks.slice(0, 5).map((r: any, i: number) => (
              <div key={i} style={{
                padding: '10px 12px', marginBottom: 8, borderRadius: 8,
                background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <Text strong style={{ color: 'var(--lm-text)', fontSize: 13 }}>{r.service}</Text>
                  <span style={{
                    fontSize: 18, fontWeight: 700,
                    color: r.impact_score > 60 ? '#ff4d4f' : r.impact_score > 30 ? '#faad14' : '#52c41a',
                  }}>
                    {r.impact_score}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--lm-text-secondary)', marginBottom: 4 }}>
                  影响 {r.downstream_count} 个下游 · 平均延迟 {r.avg_cascade_delay_min}min
                </div>
                <div>
                  {r.cascade_chain?.map((s: string, j: number) => (
                    <React.Fragment key={j}>
                      {j > 0 && <span style={{ color: 'var(--lm-text-tertiary)', margin: '0 4px' }}>→</span>}
                      <Tag style={{ borderRadius: 3, fontSize: 10, margin: 0 }}>{s}</Tag>
                    </React.Fragment>
                  ))}
                </div>
              </div>
            )) : (
              <Empty description="暂无级联风险" />
            )}
          </Card>

          {/* Top Correlations */}
          <Card
            title="强关联 Top 5"
            size="small"
            style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
              borderRadius: 12, marginTop: 16,
            }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data?.correlations?.slice(0, 5).map((c: any, i: number) => (
              <div key={i} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '6px 0', borderBottom: '1px solid var(--lm-border-light)',
              }}>
                <Space size={4}>
                  <Text style={{ fontSize: 12 }}>{c.service_a}</Text>
                  <span style={{ color: 'var(--lm-text-tertiary)' }}>⇄</span>
                  <Text style={{ fontSize: 12 }}>{c.service_b}</Text>
                </Space>
                <Tag color={c.co_occurrence_count > 5 ? '#ff4d4f' : '#1677ff'} style={{ borderRadius: 3 }}>
                  {c.co_occurrence_count}
                </Tag>
              </div>
            ))}
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default CorrelationMatrix;
