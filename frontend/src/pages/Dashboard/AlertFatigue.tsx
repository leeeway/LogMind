import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Space, Button, Tag, Table, Row, Col, Progress, Empty, Spin,
} from 'antd';
import {
  AlertOutlined, ReloadOutlined, SoundOutlined, ThunderboltOutlined,
  CheckCircleOutlined, ClockCircleOutlined, WarningOutlined,
} from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const levelColors: Record<string, string> = {
  healthy: '#52c41a', warning: '#faad14', critical: '#ff4d4f',
};
const levelLabels: Record<string, string> = {
  healthy: '健康', warning: '疲劳', critical: '严重',
};

const AlertFatigue: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data: res } = await client.get('/dashboard/alert-fatigue', { params: { days: 7 } });
      setData(res);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) return <Spin style={{ display: 'block', textAlign: 'center', marginTop: 80 }} />;
  if (!data) return <Empty description="暂无告警数据" style={{ marginTop: 80 }} />;

  const m = data.metrics;
  const th = data.team_health;

  const noiseColumns = [
    {
      title: '告警模式', dataIndex: 'pattern', key: 'p', ellipsis: true,
      render: (v: string) => <Text style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--lm-text)' }}>{v}</Text>,
    },
    {
      title: '触发', dataIndex: 'count', key: 'cnt', width: 60,
      render: (v: number) => <Tag color={v > 10 ? '#ff4d4f' : '#1677ff'}>{v}</Tag>,
    },
    {
      title: '确认率', dataIndex: 'ack_rate_pct', key: 'ack', width: 80,
      render: (v: number) => (
        <span style={{ color: v < 20 ? '#ff4d4f' : v < 50 ? '#faad14' : '#52c41a', fontWeight: 600 }}>
          {v}%
        </span>
      ),
    },
    {
      title: '建议', dataIndex: 'suggestion', key: 'sug',
      render: (v: string) => <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)' }}>{v}</Text>,
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <SoundOutlined style={{ marginRight: 8 }} />告警疲劳度仪表盘
        </Title>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
      </div>

      {/* Fatigue Index */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card style={{
            background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
            textAlign: 'center', padding: '16px 0',
          }}>
            <div style={{ fontSize: 48, fontWeight: 800, color: levelColors[m.fatigue_level] }}>
              {m.fatigue_index}
            </div>
            <Tag color={levelColors[m.fatigue_level]} style={{ borderRadius: 4, fontSize: 13 }}>
              {levelLabels[m.fatigue_level]}
            </Tag>
            <div style={{ marginTop: 8 }}>
              <Progress
                percent={m.fatigue_index}
                strokeColor={levelColors[m.fatigue_level]}
                trailColor="rgba(255,255,255,0.06)"
                showInfo={false}
              />
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>疲劳指数 (0=健康 100=严重)</Text>
          </Card>
        </Col>

        <Col span={16}>
          <Row gutter={[12, 12]}>
            {[
              { label: '噪音比率', value: `${m.noise_ratio_pct}%`, icon: <SoundOutlined />, color: m.noise_ratio_pct > 60 ? '#ff4d4f' : '#faad14' },
              { label: '平均确认延迟', value: `${m.avg_ack_delay_minutes} min`, icon: <ClockCircleOutlined />, color: m.avg_ack_delay_minutes > 30 ? '#ff4d4f' : '#1677ff' },
              { label: '虚假警率', value: `${m.false_alarm_rate_pct}%`, icon: <WarningOutlined />, color: m.false_alarm_rate_pct > 50 ? '#ff4d4f' : '#faad14' },
              { label: '告警风暴', value: `${m.storm_count} 次`, icon: <ThunderboltOutlined />, color: m.storm_count > 3 ? '#ff4d4f' : '#52c41a' },
              { label: '较上周', value: `${th.improvement_vs_last_week > 0 ? '↓ 改善' : '↑ 恶化'} ${Math.abs(th.improvement_vs_last_week)}%`, icon: <CheckCircleOutlined />, color: th.improvement_vs_last_week > 0 ? '#52c41a' : '#ff4d4f' },
            ].map((item, i) => (
              <Col span={8} key={i}>
                <Card size="small" style={{
                  background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 8,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ color: item.color, fontSize: 18 }}>{item.icon}</span>
                    <div>
                      <div style={{ fontSize: 11, color: 'var(--lm-text-secondary)' }}>{item.label}</div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: item.color }}>{item.value}</div>
                    </div>
                  </div>
                </Card>
              </Col>
            ))}
          </Row>
        </Col>
      </Row>

      {/* Daily Trend */}
      <Card
        title="7日噪音趋势"
        size="small"
        style={{
          background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
          marginBottom: 16,
        }}
        styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
      >
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', height: 120 }}>
          {data.daily_trends?.map((d: any) => {
            const barH = Math.max(8, d.noise_ratio_pct * 1.1);
            return (
              <div key={d.date} style={{ flex: 1, textAlign: 'center' }}>
                <div style={{
                  height: barH, background: d.noise_ratio_pct > 60 ? '#ff4d4f' : d.noise_ratio_pct > 30 ? '#faad14' : '#52c41a',
                  borderRadius: '4px 4px 0 0', transition: 'height 0.3s',
                }} />
                <div style={{ fontSize: 10, color: 'var(--lm-text-secondary)', marginTop: 4 }}>
                  {d.date.slice(5)}
                </div>
                <div style={{ fontSize: 10, fontWeight: 600 }}>{d.noise_ratio_pct}%</div>
              </div>
            );
          })}
        </div>
      </Card>

      {/* Top Noise Sources */}
      <Card
        title={<Space><AlertOutlined /> Top 噪音源 — 触发多但无人确认</Space>}
        size="small"
        style={{
          background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
        }}
        styles={{ body: { padding: 0 }, header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
      >
        <Table
          dataSource={data.top_noise}
          columns={noiseColumns}
          rowKey="pattern"
          size="small"
          pagination={false}
          locale={{ emptyText: <Empty description="没有噪音源，团队告警质量优秀 🎉" /> }}
        />
      </Card>
    </div>
  );
};

export default AlertFatigue;
