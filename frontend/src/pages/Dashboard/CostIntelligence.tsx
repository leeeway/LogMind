import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Space, Button, Tag, Table, Row, Col, Progress, Empty, Spin,
} from 'antd';
import {
  DollarOutlined, ReloadOutlined, ThunderboltOutlined, LineChartOutlined,
  CheckCircleOutlined, WarningOutlined,
} from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const gradeColors: Record<string, string> = {
  A: '#52c41a', B: '#1677ff', C: '#faad14', D: '#ff4d4f',
};

const CostIntelligence: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data: res } = await client.get('/dashboard/ai-cost', { params: { days: 30 } });
      setData(res);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) return <Spin style={{ display: 'block', textAlign: 'center', marginTop: 80 }} />;
  if (!data) return <Empty description="暂无 AI 使用数据" style={{ marginTop: 80 }} />;

  const svcColumns = [
    {
      title: '服务', dataIndex: 'service', key: 's', ellipsis: true,
      render: (v: string) => <Text style={{ color: 'var(--lm-text)', fontSize: 12 }}>{v}</Text>,
    },
    {
      title: 'Token', dataIndex: 'token_usage', key: 't', width: 90,
      render: (v: number) => <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{v >= 1000 ? `${(v/1000).toFixed(1)}K` : v}</Text>,
    },
    {
      title: '费用', dataIndex: 'cost_usd', key: 'c', width: 70,
      render: (v: number) => <Text strong>${v.toFixed(2)}</Text>,
    },
    {
      title: '发现', dataIndex: 'critical_findings', key: 'f', width: 50,
      render: (v: number) => <Tag color={v > 0 ? '#52c41a' : '#d9d9d9'}>{v}</Tag>,
    },
    {
      title: '效率', dataIndex: 'efficiency_grade', key: 'g', width: 50,
      render: (v: string) => <Tag color={gradeColors[v]} style={{ fontWeight: 700 }}>{v}</Tag>,
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <DollarOutlined style={{ marginRight: 8 }} />AI 成本智能
        </Title>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
      </div>

      {/* Main KPIs */}
      <Row gutter={12} style={{ marginBottom: 16 }}>
        {[
          { label: '总消耗', value: `${data.total_tokens >= 1000000 ? (data.total_tokens/1000000).toFixed(1)+'M' : data.total_tokens >= 1000 ? (data.total_tokens/1000).toFixed(1)+'K' : data.total_tokens}`, sub: 'Token', icon: <ThunderboltOutlined />, color: '#722ed1' },
          { label: '总费用', value: `$${data.total_cost_usd}`, sub: `${data.total_tasks} 次分析`, icon: <DollarOutlined />, color: '#1677ff' },
          { label: '单次成本', value: `$${data.avg_cost_per_task.toFixed(3)}`, sub: `${data.avg_tokens_per_task} Token`, icon: <LineChartOutlined />, color: '#13c2c2' },
          { label: '去重节省', value: `${data.dedup_savings_pct}%`, sub: '被去重跳过', icon: <CheckCircleOutlined />, color: '#52c41a' },
          { label: '发现率', value: `${data.finding_rate}`, sub: '每1K Token', icon: <ThunderboltOutlined />, color: '#fa8c16' },
        ].map((kpi, i) => (
          <Col flex={1} key={i}>
            <Card size="small" style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ color: kpi.color, fontSize: 20 }}>{kpi.icon}</span>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--lm-text-secondary)' }}>{kpi.label}</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: kpi.color }}>{kpi.value}</div>
                  <div style={{ fontSize: 10, color: 'var(--lm-text-tertiary)' }}>{kpi.sub}</div>
                </div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* ROI Banner */}
      <Card size="small" style={{
        background: 'rgba(82,196,26,0.06)', border: '1px solid rgba(82,196,26,0.2)',
        borderRadius: 10, marginBottom: 16,
      }}>
        <Space>
          <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 18 }} />
          <Text style={{ fontSize: 13 }}>{data.roi_summary}</Text>
        </Space>
      </Card>

      <Row gutter={16}>
        {/* Daily trend */}
        <Col span={14}>
          <Card
            title={<Space><LineChartOutlined /> 每日 Token 消耗</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 120 }}>
              {data.daily_trend?.slice(-14).map((d: any) => {
                const maxT = Math.max(...data.daily_trend.slice(-14).map((t: any) => t.token_usage || 1));
                const barH = Math.max(6, ((d.token_usage || 0) / maxT) * 110);
                return (
                  <div key={d.date} style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: 9, fontWeight: 600, marginBottom: 2 }}>
                      {d.token_usage >= 1000 ? `${(d.token_usage/1000).toFixed(0)}K` : d.token_usage}
                    </div>
                    <div style={{
                      height: barH, background: '#722ed1', borderRadius: '3px 3px 0 0',
                      opacity: 0.7, transition: 'height 0.3s',
                    }} />
                    <div style={{ fontSize: 8, color: 'var(--lm-text-tertiary)', marginTop: 2 }}>
                      {d.date.slice(5)}
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Optimizations */}
          {data.optimizations?.length > 0 && (
            <Card
              title={<Space><WarningOutlined style={{ color: '#faad14' }} /> 成本优化建议</Space>}
              size="small"
              style={{
                background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
                borderRadius: 12, marginTop: 16,
              }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
            >
              {data.optimizations.map((opt: any, i: number) => (
                <div key={i} style={{
                  padding: '8px 12px', marginBottom: 8, borderRadius: 8,
                  background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Text strong style={{ fontSize: 12 }}>{opt.service}</Text>
                    <Tag color="#ff4d4f">可节省 {opt.potential_saving_pct}%</Tag>
                  </div>
                  <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)' }}>{opt.suggestion}</Text>
                </div>
              ))}
            </Card>
          )}
        </Col>

        {/* By service */}
        <Col span={10}>
          <Card
            title="按服务消耗排名"
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ body: { padding: 0 }, header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            <Table
              dataSource={data.by_service}
              columns={svcColumns}
              rowKey="service_id"
              size="small"
              pagination={false}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default CostIntelligence;
