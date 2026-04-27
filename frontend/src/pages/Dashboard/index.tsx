import React, { useEffect, useState, useCallback } from 'react';
import { Row, Col, Card, Statistic, Table, Tag, Select, Spin, Typography, Space, Progress, Tooltip } from 'antd';
import {
  ExperimentOutlined,
  AlertOutlined,
  DollarOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ClusterOutlined,
  RiseOutlined,
} from '@ant-design/icons';
import { Line, Pie } from '@ant-design/charts';
import { dashboardApi } from '@/api/dashboard';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = {
  completed: '#52c41a',
  running: '#1677ff',
  failed: '#ff4d4f',
  pending: '#8c8c8c',
};

const severityColors: Record<string, string> = {
  critical: '#ff4d4f',
  warning: '#faad14',
  info: '#1677ff',
  error: '#ff4d4f',
};

const Dashboard: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [overview, setOverview] = useState<any>(null);
  const [trends, setTrends] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [cost, setCost] = useState<any>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [ovRes, trRes, hlRes, coRes] = await Promise.all([
        dashboardApi.getOverview(days),
        dashboardApi.getTrends(days),
        dashboardApi.getBusinessHealth(days),
        dashboardApi.getCostAnalysis(days),
      ]);
      setOverview(ovRes.data);
      setTrends(trRes.data);
      setHealth(hlRes.data);
      setCost(coRes.data);
    } catch (err) {
      console.error('Dashboard fetch failed', err);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60000);
    return () => clearInterval(timer);
  }, [fetchData]);

  if (loading && !overview) {
    return <div style={{ textAlign: 'center', paddingTop: 120 }}><Spin size="large" /></div>;
  }

  // Build trend chart data
  const trendData = trends?.error_trend?.map((item: any) => ({
    time: dayjs(item.date || item.time).format('MM-DD HH:mm'),
    value: item.count || item.value || 0,
    type: '错误数',
  })) || [];

  // Severity pie data
  const severityData = overview?.severity_distribution?.map((item: any) => ({
    type: item.severity,
    value: item.count,
  })) || [];

  // Recent tasks columns
  const taskColumns = [
    {
      title: '状态',
      dataIndex: 'status',
      width: 80,
      render: (s: string) => (
        <Tag color={statusColors[s] || '#8c8c8c'} style={{ borderRadius: 4 }}>
          {s === 'completed' ? '完成' : s === 'running' ? '运行中' : s === 'failed' ? '失败' : '等待'}
        </Tag>
      ),
    },
    { title: '类型', dataIndex: 'task_type', width: 80 },
    {
      title: '日志数',
      dataIndex: 'log_count',
      width: 80,
      render: (v: number) => v?.toLocaleString(),
    },
    {
      title: 'Token',
      dataIndex: 'token_usage',
      width: 80,
      render: (v: number) => v?.toLocaleString(),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 140,
      render: (v: string) => v ? dayjs(v).format('MM-DD HH:mm') : '-',
    },
  ];

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          运维总览
        </Title>
        <Select
          value={days}
          onChange={setDays}
          options={[
            { value: 1, label: '最近 1 天' },
            { value: 7, label: '最近 7 天' },
            { value: 14, label: '最近 14 天' },
            { value: 30, label: '最近 30 天' },
          ]}
          style={{ width: 140 }}
        />
      </div>

      {/* KPI Cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <div className="lm-stat-card">
            <Space>
              <ExperimentOutlined className="stat-icon" style={{ color: '#1677ff' }} />
              <span className="stat-label">分析任务</span>
            </Space>
            <div className="stat-value" style={{ color: '#1677ff' }}>
              {overview?.total_tasks?.toLocaleString() || 0}
            </div>
            <Space size={16}>
              <Text style={{ color: 'var(--lm-success)', fontSize: 12 }}>
                <CheckCircleOutlined /> {overview?.completed_tasks || 0}
              </Text>
              <Text style={{ color: 'var(--lm-critical)', fontSize: 12 }}>
                <CloseCircleOutlined /> {overview?.failed_tasks || 0}
              </Text>
            </Space>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div className="lm-stat-card">
            <Space>
              <AlertOutlined className="stat-icon" style={{ color: '#fa8c16' }} />
              <span className="stat-label">告警数</span>
            </Space>
            <div className="stat-value" style={{ color: '#fa8c16' }}>
              {overview?.total_alerts?.toLocaleString() || 0}
            </div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div className="lm-stat-card">
            <Space>
              <ThunderboltOutlined className="stat-icon" style={{ color: '#722ed1' }} />
              <span className="stat-label">Token 消耗</span>
            </Space>
            <div className="stat-value" style={{ color: '#722ed1' }}>
              {overview?.total_tokens_used?.toLocaleString() || 0}
            </div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div className="lm-stat-card">
            <Space>
              <DollarOutlined className="stat-icon" style={{ color: '#52c41a' }} />
              <span className="stat-label">AI 成本</span>
            </Space>
            <div className="stat-value" style={{ color: '#52c41a' }}>
              ${cost?.total_cost_usd?.toFixed(2) || '0.00'}
            </div>
          </div>
        </Col>
      </Row>

      {/* Charts Row */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={16}>
          <Card
            title="错误趋势"
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            {trendData.length > 0 ? (
              <Line
                data={trendData}
                xField="time"
                yField="value"
                seriesField="type"
                height={260}
                smooth
                color={['#1677ff']}
                point={{ size: 2 }}
                theme="classicDark"
              />
            ) : (
              <div style={{ textAlign: 'center', padding: 60, color: 'var(--lm-text-tertiary)' }}>暂无数据</div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card
            title="严重度分布"
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            {severityData.length > 0 ? (
              <Pie
                data={severityData}
                angleField="value"
                colorField="type"
                height={260}
                innerRadius={0.65}
                color={({ type }: any) => severityColors[type] || '#8c8c8c'}
                label={{ text: 'type', style: { fontSize: 12, fill: 'rgba(255,255,255,0.6)' } }}
                legend={false}
                theme="classicDark"
              />
            ) : (
              <div style={{ textAlign: 'center', padding: 60, color: 'var(--lm-text-tertiary)' }}>暂无数据</div>
            )}
          </Card>
        </Col>
      </Row>

      {/* Health Matrix + Recent Tasks */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card
            title={<Space><ClusterOutlined /> 服务健康</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            {health?.business_lines?.length > 0 ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
                {health.business_lines.map((biz: any) => {
                  const score = biz.health_score ?? 100;
                  const color = score >= 80 ? '#52c41a' : score >= 50 ? '#faad14' : '#ff4d4f';
                  return (
                    <div key={biz.id} style={{
                      padding: '12px 16px',
                      background: 'var(--lm-bg-elevated)',
                      borderRadius: 8,
                      border: '1px solid var(--lm-border-light)',
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                        <Text ellipsis style={{ maxWidth: 120, color: 'var(--lm-text)' }}>{biz.name}</Text>
                        <Tag color={color} style={{ borderRadius: 4, margin: 0 }}>{score}%</Tag>
                      </div>
                      <Progress percent={score} showInfo={false} strokeColor={color} trailColor="rgba(255,255,255,0.06)" size="small" />
                      <div style={{ marginTop: 4, fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                        {biz.total_tasks || 0} 任务 · {biz.error_count || 0} 错误
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--lm-text-tertiary)' }}>暂无服务数据</div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            title={<Space><ClockCircleOutlined /> 最近任务</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            <Table
              dataSource={overview?.recent_tasks || []}
              columns={taskColumns}
              rowKey="id"
              size="small"
              pagination={false}
              scroll={{ y: 240 }}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;
