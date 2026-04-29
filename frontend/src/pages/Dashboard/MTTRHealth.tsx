import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Space, Button, Tag, Table, Row, Col, Progress, Empty, Spin,
} from 'antd';
import {
  MedicineBoxOutlined, ReloadOutlined, ClockCircleOutlined,
  ArrowUpOutlined, ArrowDownOutlined, CheckCircleOutlined, FieldTimeOutlined,
} from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const MTTRHealth: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data: res } = await client.get('/dashboard/mttr-health', { params: { days: 30 } });
      setData(res);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) return <Spin style={{ display: 'block', textAlign: 'center', marginTop: 80 }} />;
  if (!data) return <Empty description="暂无 MTTR 数据" style={{ marginTop: 80 }} />;

  const improving = data.overall_mttr_vs_last_week < 0;

  const priorityColors: Record<string, string> = { P0: '#ff4d4f', P1: '#faad14', P2: '#1677ff' };

  const bottleneckColumns = [
    {
      title: '告警', dataIndex: 'message', key: 'msg', ellipsis: true,
      render: (v: string) => <Text style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--lm-text)' }}>{v}</Text>,
    },
    {
      title: '优先级', dataIndex: 'priority', key: 'p', width: 60,
      render: (v: string) => <Tag color={priorityColors[v]}>{v}</Tag>,
    },
    {
      title: '总耗时', dataIndex: 'total_minutes', key: 'total', width: 80,
      render: (v: number) => (
        <Text strong style={{ color: v > 120 ? '#ff4d4f' : v > 30 ? '#faad14' : '#52c41a' }}>
          {v > 60 ? `${(v / 60).toFixed(1)}h` : `${v}m`}
        </Text>
      ),
    },
    {
      title: '瓶颈', dataIndex: 'bottleneck_phase', key: 'bn', width: 80,
      render: (v: string) => (
        <Tag color={v === 'detection' ? '#722ed1' : '#1677ff'} style={{ borderRadius: 3 }}>
          {v === 'detection' ? '发现慢' : '修复慢'}
        </Tag>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <MedicineBoxOutlined style={{ marginRight: 8 }} />MTTR 健康仪表盘
        </Title>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
      </div>

      {/* Main KPIs */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card style={{
            background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
            textAlign: 'center',
          }}>
            <div style={{ fontSize: 36, fontWeight: 800, color: data.overall_mttr_minutes > 60 ? '#ff4d4f' : '#1677ff' }}>
              {data.overall_mttr_minutes > 60 ? `${(data.overall_mttr_minutes / 60).toFixed(1)}h` : `${data.overall_mttr_minutes}m`}
            </div>
            <Text type="secondary">整体 MTTR</Text>
            <div style={{ marginTop: 4 }}>
              {improving ? (
                <Tag color="#52c41a"><ArrowDownOutlined /> 改善 {Math.abs(data.overall_mttr_vs_last_week).toFixed(0)}m</Tag>
              ) : (
                <Tag color="#ff4d4f"><ArrowUpOutlined /> 恶化 {Math.abs(data.overall_mttr_vs_last_week).toFixed(0)}m</Tag>
              )}
            </div>
          </Card>
        </Col>

        {/* Priority MTTR */}
        {data.by_priority?.map((p: any) => (
          <Col span={6} key={p.priority}>
            <Card style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
              textAlign: 'center',
            }}>
              <Tag color={priorityColors[p.priority]} style={{ fontSize: 14, padding: '2px 12px', marginBottom: 8 }}>
                {p.priority}
              </Tag>
              <div style={{ fontSize: 24, fontWeight: 700, color: priorityColors[p.priority] }}>
                {p.mttr_minutes > 60 ? `${(p.mttr_minutes / 60).toFixed(1)}h` : `${p.mttr_minutes}m`}
              </div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {p.resolved_count}/{p.count} 已修复
              </Text>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        {/* Distribution */}
        <Col span={12}>
          <Card
            title={<Space><FieldTimeOutlined /> 修复时间分布</Space>}
            size="small"
            style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
            }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data.distribution?.map((d: any) => (
              <div key={d.bucket} style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '4px 0',
              }}>
                <Text style={{ minWidth: 70, fontSize: 12, color: 'var(--lm-text-secondary)' }}>{d.bucket}</Text>
                <Progress
                  percent={d.percentage}
                  size="small"
                  format={() => `${d.count}`}
                  strokeColor={d.bucket === '>8h' ? '#ff4d4f' : d.bucket === '2h-8h' ? '#faad14' : '#1677ff'}
                  style={{ flex: 1 }}
                />
              </div>
            ))}
          </Card>
        </Col>

        {/* Weekly Trend */}
        <Col span={12}>
          <Card
            title={<Space><ClockCircleOutlined /> 周趋势</Space>}
            size="small"
            style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
            }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', height: 140, padding: '0 8px' }}>
              {data.weekly_trends?.map((w: any) => {
                const maxMttr = Math.max(...data.weekly_trends.map((t: any) => t.mttr_minutes), 1);
                const barH = Math.max(12, (w.mttr_minutes / maxMttr) * 120);
                return (
                  <div key={w.week_label} style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--lm-text)', marginBottom: 4 }}>
                      {w.mttr_minutes > 60 ? `${(w.mttr_minutes / 60).toFixed(1)}h` : `${w.mttr_minutes}m`}
                    </div>
                    <div style={{
                      height: barH, background: w.mttr_minutes > 60 ? '#ff4d4f' : w.mttr_minutes > 30 ? '#faad14' : '#52c41a',
                      borderRadius: '4px 4px 0 0', transition: 'height 0.3s',
                    }} />
                    <div style={{ fontSize: 10, color: 'var(--lm-text-secondary)', marginTop: 4 }}>{w.week_label}</div>
                    <div style={{ fontSize: 9, color: 'var(--lm-text-tertiary)' }}>{w.start_date}</div>
                  </div>
                );
              })}
            </div>
          </Card>
        </Col>
      </Row>

      {/* Service Ranking */}
      <Row gutter={16}>
        <Col span={12}>
          <Card
            title="按服务排名"
            size="small"
            style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
            }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data.by_service?.slice(0, 8).map((s: any, i: number) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0',
                borderBottom: '1px solid var(--lm-border-light)',
              }}>
                <Tag style={{ borderRadius: 3, minWidth: 20, textAlign: 'center' }}>{i + 1}</Tag>
                <Text style={{ flex: 1, fontSize: 12 }}>{s.service}</Text>
                <Text strong style={{
                  fontSize: 13, color: s.mttr_minutes > 60 ? '#ff4d4f' : s.mttr_minutes > 30 ? '#faad14' : '#52c41a',
                }}>
                  {s.mttr_minutes > 60 ? `${(s.mttr_minutes / 60).toFixed(1)}h` : `${s.mttr_minutes}m`}
                </Text>
                <Text type="secondary" style={{ fontSize: 11 }}>({s.resolved_count}/{s.incident_count})</Text>
              </div>
            ))}
          </Card>
        </Col>

        {/* Bottlenecks */}
        <Col span={12}>
          <Card
            title={<Space><ClockCircleOutlined style={{ color: '#ff4d4f' }} /> 修复最慢 Top 10</Space>}
            size="small"
            style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
            }}
            styles={{ body: { padding: 0 }, header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            <Table
              dataSource={data.bottlenecks}
              columns={bottleneckColumns}
              rowKey="alert_id"
              size="small"
              pagination={false}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default MTTRHealth;
