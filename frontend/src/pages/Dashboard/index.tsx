import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Row, Col, Card, Table, Tag, Select, Spin, Typography, Space, Progress, Tooltip, Button } from 'antd';
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
  SearchOutlined,
  PlusOutlined,
  SaveOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
} from '@ant-design/icons';
import { Line, Pie } from '@ant-design/charts';
import { dashboardApi } from '@/api/dashboard';
import { usePolling } from '@/hooks/usePolling';
import RefreshIndicator from '@/components/RefreshIndicator';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = {
  completed: '#52c41a', running: '#1677ff', failed: '#ff4d4f', pending: '#8c8c8c',
};
const severityColors: Record<string, string> = {
  critical: '#ff4d4f', warning: '#faad14', info: '#1677ff', error: '#ff4d4f',
};

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [days, setDays] = useState(7);

  const fetcher = useCallback(async () => {
    const [ovRes, trRes, hlRes, coRes] = await Promise.all([
      dashboardApi.getOverview(days),
      dashboardApi.getTrends(days),
      dashboardApi.getBusinessHealth(days),
      dashboardApi.getCostAnalysis(days),
    ]);
    return {
      overview: ovRes.data,
      trends: trRes.data,
      health: hlRes.data,
      cost: coRes.data,
    };
  }, [days]);

  const { data, loading, lastUpdated, secondsUntilRefresh, refresh } = usePolling(fetcher, {
    interval: 60000,
    enabled: true,
  });

  const overview = data?.overview;
  const trends = data?.trends;
  const health = data?.health;
  const cost = data?.cost;

  if (loading && !data) {
    return (
      <div style={{ padding: '80px 0' }}>
        <Row gutter={[16, 16]}>
          {[1, 2, 3, 4].map((i) => (
            <Col xs={24} sm={12} lg={6} key={i}>
              <div className="lm-stat-card lm-pulse" style={{ height: 100 }} />
            </Col>
          ))}
        </Row>
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col span={16}><div className="lm-card lm-pulse" style={{ height: 300 }} /></Col>
          <Col span={8}><div className="lm-card lm-pulse" style={{ height: 300 }} /></Col>
        </Row>
      </div>
    );
  }

  const trendData = trends?.error_trend?.map((item: any) => ({
    time: dayjs(item.date || item.time).format('MM-DD HH:mm'),
    value: item.count || item.value || 0,
    type: '错误数',
  })) || [];

  const severityData = overview?.severity_distribution?.map((item: any) => ({
    type: item.severity,
    value: item.count,
  })) || [];

  const completionRate = overview?.total_tasks
    ? ((overview.completed_tasks / overview.total_tasks) * 100).toFixed(1)
    : '0';

  const taskColumns = [
    {
      title: '状态', dataIndex: 'status', width: 80,
      render: (s: string) => {
        const isRunning = s === 'running';
        return (
          <Tag color={statusColors[s] || '#8c8c8c'} style={{ borderRadius: 4 }}>
            {isRunning && <span className="lm-pulse" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#1677ff', marginRight: 4 }} />}
            {s === 'completed' ? '完成' : s === 'running' ? '运行中' : s === 'failed' ? '失败' : '等待'}
          </Tag>
        );
      },
    },
    { title: '类型', dataIndex: 'task_type', width: 80 },
    { title: '日志数', dataIndex: 'log_count', width: 80, render: (v: number) => v?.toLocaleString() },
    { title: 'Token', dataIndex: 'token_usage', width: 80, render: (v: number) => v?.toLocaleString() },
    { title: '时间', dataIndex: 'created_at', width: 140, render: (v: string) => v ? dayjs(v).format('MM-DD HH:mm') : '-' },
  ];

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>运维总览</Title>
        <Space>
          <RefreshIndicator lastUpdated={lastUpdated} secondsUntilRefresh={secondsUntilRefresh} loading={loading} onRefresh={refresh} />
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
        </Space>
      </div>

      {/* Quick Action Bar */}
      <div style={{
        display: 'flex', gap: 12, marginBottom: 20, padding: '12px 16px',
        background: 'var(--lm-bg-card)', borderRadius: 10,
        border: '1px solid var(--lm-border-light)',
      }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/analysis')}>触发分析</Button>
        <Button icon={<SearchOutlined />} onClick={() => navigate('/logs')}>搜索日志</Button>
        <Button icon={<AlertOutlined />} onClick={() => navigate('/alerts')}>告警管理</Button>
        <div style={{ flex: 1 }} />
        <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 12, lineHeight: '32px' }}>
          任务完成率 <span style={{ color: 'var(--lm-success)', fontWeight: 600 }}>{completionRate}%</span>
          {overview?.total_business_lines > 0 && <> · {overview.total_business_lines} 个服务</>}
        </Text>
      </div>

      {/* KPI Cards — 6 cards now */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={4}>
          <div className="lm-stat-card">
            <Space><ExperimentOutlined className="stat-icon" style={{ color: '#1677ff' }} /><span className="stat-label">分析任务</span></Space>
            <div className="stat-value" style={{ color: '#1677ff' }}>{overview?.total_tasks?.toLocaleString() || 0}</div>
            <Space size={12}>
              <Text style={{ color: 'var(--lm-success)', fontSize: 12 }}><CheckCircleOutlined /> {overview?.completed_tasks || 0}</Text>
              <Text style={{ color: 'var(--lm-critical)', fontSize: 12 }}><CloseCircleOutlined /> {overview?.failed_tasks || 0}</Text>
            </Space>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={4}>
          <div className="lm-stat-card" style={{ cursor: 'pointer' }} onClick={() => navigate('/alerts')}>
            <Space><AlertOutlined className="stat-icon" style={{ color: '#fa8c16' }} /><span className="stat-label">告警数</span></Space>
            <div className="stat-value" style={{ color: '#fa8c16' }}>{overview?.total_alerts?.toLocaleString() || 0}</div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={4}>
          <div className="lm-stat-card">
            <Space><ThunderboltOutlined className="stat-icon" style={{ color: '#722ed1' }} /><span className="stat-label">Token 消耗</span></Space>
            <div className="stat-value" style={{ color: '#722ed1' }}>{overview?.total_tokens_used?.toLocaleString() || 0}</div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={4}>
          <div className="lm-stat-card">
            <Space><DollarOutlined className="stat-icon" style={{ color: '#52c41a' }} /><span className="stat-label">AI 成本</span></Space>
            <div className="stat-value" style={{ color: '#52c41a' }}>${cost?.total_cost_usd?.toFixed(2) || '0.00'}</div>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={4}>
          <div className="lm-stat-card">
            <Space><SaveOutlined className="stat-icon" style={{ color: '#13c2c2' }} /><span className="stat-label">去重节省</span></Space>
            <div className="stat-value" style={{ color: '#13c2c2' }}>{cost?.savings_pct ? `${cost.savings_pct.toFixed(0)}%` : '-'}</div>
            <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
              ≈ {(cost?.estimated_saved_tokens || 0).toLocaleString()} tokens
            </Text>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={4}>
          <div className="lm-stat-card">
            <Space><RiseOutlined className="stat-icon" style={{ color: '#eb2f96' }} /><span className="stat-label">完成率</span></Space>
            <div className="stat-value" style={{ color: '#eb2f96' }}>{completionRate}%</div>
            <Progress percent={Number(completionRate)} showInfo={false} strokeColor="#eb2f96" trailColor="rgba(255,255,255,0.06)" size="small" />
          </div>
        </Col>
      </Row>

      {/* Charts Row */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={16}>
          <Card title="错误趋势" size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            {trendData.length > 0 ? (
              <Line data={trendData} xField="time" yField="value" seriesField="type" height={260} smooth
                color={['#1677ff']} point={{ size: 2 }} theme="classicDark"
                area={{ style: { fillOpacity: 0.15 } }}
                animation={{ appear: { animation: 'wave-in', duration: 800 } }} />
            ) : (
              <div style={{ textAlign: 'center', padding: 60, color: 'var(--lm-text-tertiary)' }}>暂无数据</div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="严重度分布" size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            {severityData.length > 0 ? (
              <Pie data={severityData} angleField="value" colorField="type" height={260} innerRadius={0.65}
                color={({ type }: any) => severityColors[type] || '#8c8c8c'}
                label={{ text: 'type', style: { fontSize: 12, fill: 'rgba(255,255,255,0.6)' } }}
                legend={false} theme="classicDark"
                statistic={{
                  title: { style: { color: 'rgba(255,255,255,0.45)', fontSize: '12px' }, content: '总计' },
                  content: { style: { color: 'rgba(255,255,255,0.88)', fontSize: '20px' } },
                }} />
            ) : (
              <div style={{ textAlign: 'center', padding: 60, color: 'var(--lm-text-tertiary)' }}>暂无数据</div>
            )}
          </Card>
        </Col>
      </Row>

      {/* Health Matrix + Recent Tasks */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title={<Space><ClusterOutlined /> 服务健康</Space>} size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            {health?.business_lines?.length > 0 ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
                {health.business_lines.map((biz: any) => {
                  const score = biz.health_score ?? 100;
                  const color = score >= 80 ? '#52c41a' : score >= 50 ? '#faad14' : '#ff4d4f';
                  const trend = biz.prev_score != null ? score - biz.prev_score : null;
                  return (
                    <div key={biz.id} style={{
                      padding: '10px 14px', background: 'var(--lm-bg-elevated)', borderRadius: 8,
                      border: '1px solid var(--lm-border-light)', cursor: 'pointer', transition: 'all 0.2s',
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = color; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--lm-border-light)'; }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                        <Text ellipsis style={{ maxWidth: 100, color: 'var(--lm-text)', fontSize: 13 }}>{biz.name}</Text>
                        <Space size={4}>
                          {trend != null && trend !== 0 && (
                            <Text style={{ fontSize: 11, color: trend > 0 ? '#52c41a' : '#ff4d4f' }}>
                              {trend > 0 ? <ArrowUpOutlined /> : <ArrowDownOutlined />}{Math.abs(trend)}
                            </Text>
                          )}
                          <Tag color={color} style={{ borderRadius: 4, margin: 0, fontSize: 11 }}>{score}%</Tag>
                        </Space>
                      </div>
                      <Progress percent={score} showInfo={false} strokeColor={color} trailColor="rgba(255,255,255,0.06)" size="small" />
                      <div style={{ marginTop: 3, fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
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
            extra={<Button type="link" size="small" onClick={() => navigate('/analysis')}>查看全部</Button>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            <Table
              dataSource={overview?.recent_tasks || []}
              columns={taskColumns}
              rowKey="id"
              size="small"
              pagination={false}
              scroll={{ y: 240 }}
              onRow={(record: any) => ({
                onClick: () => navigate(`/analysis/${record.id}`),
                style: { cursor: 'pointer' },
              })}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;
