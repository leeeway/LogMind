import React, { useEffect, useState } from 'react';
import { Card, Typography, Row, Col, Select, Spin, Statistic, Space, Tag, Table, Empty } from 'antd';
import { ThunderboltOutlined, RiseOutlined, ToolOutlined, TrophyOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { Line, Column } from '@ant-design/charts';
import { dashboardApi } from '@/api/dashboard';
import { useTheme } from '@/hooks/useTheme';

const { Title, Text } = Typography;

const AIInsights: React.FC = () => {
  const [days, setDays] = useState(7);
  const { isDark } = useTheme();
  const [loading, setLoading] = useState(true);
  const [effectiveness, setEffectiveness] = useState<any>(null);
  const [agentStats, setAgentStats] = useState<any>(null);
  const [dedupStats, setDedupStats] = useState<any>(null);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const [effRes, agentRes, dedupRes] = await Promise.all([
          dashboardApi.getAIEffectiveness(days),
          dashboardApi.getAgentAnalytics(days),
          dashboardApi.getDedupStats(days),
        ]);
        setEffectiveness(effRes.data);
        setAgentStats(agentRes.data);
        setDedupStats(dedupRes.data);
      } catch (err) { console.error(err); }
      finally { setLoading(false); }
    };
    load();
  }, [days]);

  if (loading) return <div style={{ textAlign: 'center', paddingTop: 120 }}><Spin size="large" /></div>;

  const toolColumns = [
    { title: '工具', dataIndex: 'tool_name' },
    { title: '调用次数', dataIndex: 'call_count', width: 100, sorter: (a: any, b: any) => a.call_count - b.call_count },
    {
      title: '成功率', dataIndex: 'success_rate', width: 100,
      render: (v: number) => <Text style={{ color: v >= 0.9 ? '#52c41a' : v >= 0.7 ? '#faad14' : '#ff4d4f' }}>{(v * 100).toFixed(1)}%</Text>,
    },
    { title: '平均耗时', dataIndex: 'avg_duration_ms', width: 100, render: (v: number) => `${v?.toFixed(0)}ms` },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>AI 洞察</Title>
        <Select value={days} onChange={setDays} style={{ width: 140 }} options={[
          { value: 7, label: '最近 7 天' }, { value: 14, label: '最近 14 天' }, { value: 30, label: '最近 30 天' },
        ]} />
      </div>

      {/* KPI Row */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <div className="lm-stat-card">
            <Space><TrophyOutlined className="stat-icon" style={{ color: '#52c41a' }} /><span className="stat-label">AI 准确率</span></Space>
            <div className="stat-value" style={{ color: '#52c41a' }}>{effectiveness?.overall_accuracy ? `${(effectiveness.overall_accuracy * 100).toFixed(1)}%` : '-'}</div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div className="lm-stat-card">
            <Space><ClockCircleOutlined className="stat-icon" style={{ color: '#1677ff' }} /><span className="stat-label">平均 MTTR</span></Space>
            <div className="stat-value" style={{ color: '#1677ff' }}>{effectiveness?.avg_mttr_minutes ? `${effectiveness.avg_mttr_minutes.toFixed(0)}min` : '-'}</div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div className="lm-stat-card">
            <Space><ThunderboltOutlined className="stat-icon" style={{ color: '#722ed1' }} /><span className="stat-label">Token 节省</span></Space>
            <div className="stat-value" style={{ color: '#722ed1' }}>{(effectiveness?.total_tokens_saved || 0).toLocaleString()}</div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div className="lm-stat-card">
            <Space><RiseOutlined className="stat-icon" style={{ color: '#fa8c16' }} /><span className="stat-label">去重率</span></Space>
            <div className="stat-value" style={{ color: '#fa8c16' }}>{dedupStats?.overall_dedup_rate ? `${dedupStats.overall_dedup_rate.toFixed(1)}%` : '-'}</div>
          </div>
        </Col>
      </Row>

      {/* Accuracy Trend + Top Errors */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={14}>
          <Card title="准确率趋势" size="small" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            {effectiveness?.accuracy_trend?.length > 0 ? (
              <Line data={effectiveness.accuracy_trend.map((item: any) => ({ ...item, value: item.accuracy_rate * 100 }))}
                xField="date" yField="value" height={220} smooth color="#52c41a" theme={isDark ? 'classicDark' : 'classic'}
                yAxis={{ label: { formatter: (v: string) => `${v}%` } }} />
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="Top 错误模式" size="small" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            {effectiveness?.top_error_patterns?.length > 0 ? (
              effectiveness.top_error_patterns.slice(0, 5).map((p: any, i: number) => (
                <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid var(--lm-border-light)', display: 'flex', justifyContent: 'space-between' }}>
                  <Text ellipsis style={{ maxWidth: '70%', color: 'var(--lm-text-secondary)', fontSize: 13 }}>{p.pattern}</Text>
                  <Tag color="#1677ff" style={{ borderRadius: 4 }}>{p.count}</Tag>
                </div>
              ))
            ) : <Empty description="暂无数据" />}
          </Card>
        </Col>
      </Row>

      {/* Agent Tool Analytics */}
      <Card title={<Space><ToolOutlined /> Agent 工具分析</Space>} size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
        <Table dataSource={agentStats?.tool_usage || []} columns={toolColumns} rowKey="tool_name" size="small" pagination={false} />
      </Card>
    </div>
  );
};

export default AIInsights;
