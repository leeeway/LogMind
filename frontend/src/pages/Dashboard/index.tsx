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

  // Build multi-series trend data from API response: trends.data[]
  const trendData: any[] = [];
  (trends?.data || []).forEach((item: any) => {
    const time = dayjs(item.period).format('MM-DD');
    if (item.task_count > 0) trendData.push({ time, value: item.task_count, type: '任务数' });
    if (item.failed_count > 0 || item.task_count > 0) trendData.push({ time, value: item.failed_count || 0, type: '失败数' });
    trendData.push({ time, value: Math.round((item.token_usage || 0) / 1000), type: 'Token(K)' });
  });

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
            <Space><DollarOutlined className="stat-icon" style={{ color: '#52c41a' }} /><span className="stat-label">AI 任务</span></Space>
            <div className="stat-value" style={{ color: '#52c41a' }}>{cost?.ai_tasks || 0}</div>
            <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>平均 {(cost?.avg_tokens_per_task || 0).toLocaleString()} tokens/任务</Text>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={4}>
          <div className="lm-stat-card">
            <Space><SaveOutlined className="stat-icon" style={{ color: '#13c2c2' }} /><span className="stat-label">去重节省</span></Space>
            <div className="stat-value" style={{ color: '#13c2c2' }}>{cost?.dedup_savings?.savings_percentage ? `${cost.dedup_savings.savings_percentage.toFixed(0)}%` : '-'}</div>
            <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
              ≈ {(cost?.dedup_savings?.estimated_tokens_saved || 0).toLocaleString()} tokens
            </Text>
          </div>
        </Col>
        <Col xs={24} sm={12} lg={4}>
          <div className="lm-stat-card">
            <Space><RiseOutlined className="stat-icon" style={{ color: '#eb2f96' }} /><span className="stat-label">完成率</span></Space>
            <div className="stat-value" style={{ color: '#eb2f96' }}>{completionRate}%</div>
            <Progress percent={Number(completionRate)} showInfo={false} strokeColor="#eb2f96" railColor="rgba(255,255,255,0.06)" size="small" />
          </div>
        </Col>
      </Row>

      {/* Charts Row */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={16}>
          <Card title="运维趋势" size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            {trendData.length > 0 ? (
              <Line data={trendData} xField="time" yField="value" seriesField="type" height={260} smooth
                color={['#1677ff', '#ff4d4f', '#722ed1']} point={{ size: 2 }} theme="classicDark"
                area={{ style: { fillOpacity: 0.08 } }}
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
            {(health?.items?.length > 0) ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
                {health.items.map((biz: any) => {
                  // API returns health_score as 0.0-1.0, convert to 0-100
                  const rawScore = biz.health_score ?? 0;
                  const score = Math.round(rawScore <= 1 ? rawScore * 100 : rawScore);
                  const successPct = Math.round((biz.success_rate || 0) * 100);
                  const color = successPct >= 80 ? '#52c41a' : successPct >= 50 ? '#faad14' : '#ff4d4f';
                  const errorCount = (biz.critical_count || 0) + (biz.warning_count || 0);
                  return (
                    <div key={biz.business_line_id} style={{
                      padding: '10px 14px', background: 'var(--lm-bg-elevated)', borderRadius: 8,
                      border: '1px solid var(--lm-border-light)', cursor: 'pointer', transition: 'all 0.2s',
                    }}
                    onClick={() => navigate(`/business-lines/${biz.business_line_id}`)}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = color; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--lm-border-light)'; }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                        <Text ellipsis style={{ maxWidth: 120, color: 'var(--lm-text)', fontSize: 13 }}>{biz.business_line_name}</Text>
                        <Tag color={color} style={{ borderRadius: 4, margin: 0, fontSize: 11 }}>{successPct}%</Tag>
                      </div>
                      <Progress percent={successPct} showInfo={false} strokeColor={color} railColor="rgba(255,255,255,0.06)" size="small" />
                      <div style={{ marginTop: 3, fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                        {biz.total_tasks || 0} 任务 · {errorCount} 异常 · {(biz.total_logs || 0).toLocaleString()} 日志
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

      {/* Cost Analysis Panel — Phase 3 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card
            title={<Space><DollarOutlined style={{ color: '#52c41a' }} /> 成本分析 · 去重漏斗</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            {cost ? (
              <Row gutter={[16, 16]}>
                {/* Dedup Funnel */}
                <Col xs={24} lg={10}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {[
                      { label: '总任务', value: cost.total_tasks, color: '#1677ff', pct: 100 },
                      { label: '质量过滤跳过', value: cost.dedup_savings?.quality_filtered_tasks || 0, color: '#722ed1', pct: cost.total_tasks > 0 ? ((cost.dedup_savings?.quality_filtered_tasks || 0) / cost.total_tasks * 100) : 0 },
                      { label: '指纹去重跳过', value: cost.dedup_savings?.fingerprint_skipped_tasks || 0, color: '#13c2c2', pct: cost.total_tasks > 0 ? ((cost.dedup_savings?.fingerprint_skipped_tasks || 0) / cost.total_tasks * 100) : 0 },
                      { label: '语义去重命中', value: cost.dedup_savings?.semantic_dedup_tasks || 0, color: '#fa8c16', pct: cost.total_tasks > 0 ? ((cost.dedup_savings?.semantic_dedup_tasks || 0) / cost.total_tasks * 100) : 0 },
                      { label: 'AI 实际推理', value: cost.ai_tasks || 0, color: '#ff4d4f', pct: cost.total_tasks > 0 ? ((cost.ai_tasks || 0) / cost.total_tasks * 100) : 0 },
                    ].map((step, i) => (
                      <Tooltip key={i} title={`${step.label}: ${step.value} 个任务 (${step.pct.toFixed(1)}%)`}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ width: 100, fontSize: 12, color: 'var(--lm-text-secondary)', textAlign: 'right', flexShrink: 0 }}>{step.label}</span>
                          <div style={{ flex: 1, background: 'rgba(255,255,255,0.04)', borderRadius: 4, height: 22, overflow: 'hidden' }}>
                            <div style={{
                              width: `${Math.max(step.pct, 2)}%`,
                              height: '100%',
                              background: `linear-gradient(90deg, ${step.color}, ${step.color}88)`,
                              borderRadius: 4,
                              display: 'flex', alignItems: 'center', paddingLeft: 6,
                              transition: 'width 1s ease',
                            }}>
                              <span style={{ fontSize: 11, color: '#fff', fontWeight: 600, whiteSpace: 'nowrap' }}>
                                {step.value}
                              </span>
                            </div>
                          </div>
                        </div>
                      </Tooltip>
                    ))}
                  </div>
                </Col>

                {/* Token by Business Line */}
                <Col xs={24} lg={8}>
                  <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)', marginBottom: 8, display: 'block' }}>Token 按业务线分布</Text>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {(cost.by_business_line || []).slice(0, 5).map((b: any, i: number) => {
                      const maxTokens = Math.max(...(cost.by_business_line || []).map((x: any) => x.tokens_used || 0), 1);
                      const pct = (b.tokens_used / maxTokens) * 100;
                      return (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <Text ellipsis style={{ width: 80, fontSize: 11, color: 'var(--lm-text-tertiary)' }}>{b.business_line_name}</Text>
                          <div style={{ flex: 1, background: 'rgba(255,255,255,0.04)', borderRadius: 3, height: 14, overflow: 'hidden' }}>
                            <div style={{
                              width: `${pct}%`, height: '100%',
                              background: 'linear-gradient(90deg, #722ed1, #1677ff)',
                              borderRadius: 3,
                            }} />
                          </div>
                          <span style={{ fontSize: 10, color: 'var(--lm-text-tertiary)', width: 50, textAlign: 'right' }}>
                            {(b.tokens_used / 1000).toFixed(1)}K
                          </span>
                        </div>
                      );
                    })}
                    {(!cost.by_business_line || cost.by_business_line.length === 0) && (
                      <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>暂无数据</Text>
                    )}
                  </div>
                </Col>

                {/* Summary Stats */}
                <Col xs={24} lg={6}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <div style={{ padding: '10px 14px', background: 'var(--lm-bg-elevated)', borderRadius: 8, border: '1px solid var(--lm-border-light)' }}>
                      <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', display: 'block' }}>总 Token 消耗</Text>
                      <Text strong style={{ color: '#722ed1', fontSize: 18 }}>{(cost.total_tokens / 1000).toFixed(1)}K</Text>
                    </div>
                    <div style={{ padding: '10px 14px', background: 'var(--lm-bg-elevated)', borderRadius: 8, border: '1px solid var(--lm-border-light)' }}>
                      <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', display: 'block' }}>节省率</Text>
                      <Text strong style={{ color: '#52c41a', fontSize: 18 }}>
                        {cost.dedup_savings?.savings_percentage?.toFixed(1) || '0'}%
                      </Text>
                    </div>
                    <div style={{ padding: '10px 14px', background: 'var(--lm-bg-elevated)', borderRadius: 8, border: '1px solid var(--lm-border-light)' }}>
                      <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', display: 'block' }}>预估节省</Text>
                      <Text strong style={{ color: '#13c2c2', fontSize: 18 }}>
                        ≈{((cost.dedup_savings?.estimated_tokens_saved || 0) / 1000).toFixed(1)}K
                      </Text>
                    </div>
                  </div>
                </Col>
              </Row>
            ) : (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--lm-text-tertiary)' }}>暂无成本数据</div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;

