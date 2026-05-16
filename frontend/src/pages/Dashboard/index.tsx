import React, { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AlertOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FireOutlined,
  LineChartOutlined,
  PlusOutlined,
  RadarChartOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Line, Pie } from '@ant-design/charts';
import dayjs from 'dayjs';

import RefreshIndicator from '@/components/RefreshIndicator';
import { dashboardApi } from '@/api/dashboard';
import { usePolling } from '@/hooks/usePolling';
import { useTheme } from '@/hooks/useTheme';

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

const palette = ['#1677ff', '#13c2c2', '#fa8c16', '#52c41a', '#eb2f96', '#722ed1'];

interface SeverityItem {
  severity: string;
  count: number;
}

interface RecentTask {
  id: string;
  status: string;
  task_type: string;
  log_count: number;
  token_usage: number;
  created_at: string;
}

interface DashboardOverview {
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  total_alerts: number;
  total_tokens_used: number;
  total_business_lines: number;
  severity_distribution: SeverityItem[];
  recent_tasks: RecentTask[];
}

interface TrendItem {
  period: string;
  task_count: number;
  failed_count: number;
  token_usage: number;
}

interface DashboardTrends {
  data: TrendItem[];
}

interface BusinessHealthItem {
  business_line_id: string;
  business_line_name: string;
  health_score?: number;
  success_rate?: number;
  critical_count?: number;
  warning_count?: number;
  total_tasks?: number;
  total_logs?: number;
}

interface NormalizedService extends BusinessHealthItem {
  score: number;
  successPct: number;
  errorCount: number;
  color: string;
}

interface BusinessHealthResponse {
  items: BusinessHealthItem[];
}

interface CostByBusinessLine {
  business_line_id: string;
  business_line_name: string;
  tokens_used: number;
  task_count: number;
  avg_tokens_per_task: number;
}

interface DedupSavings {
  quality_filtered_tasks: number;
  fingerprint_skipped_tasks: number;
  semantic_dedup_tasks: number;
  total_dedup_tasks: number;
  avg_tokens_per_ai_task: number;
  estimated_tokens_saved: number;
  savings_percentage: number;
}

interface CostAnalysis {
  total_tokens: number;
  total_tasks: number;
  ai_tasks: number;
  avg_tokens_per_task: number;
  by_business_line: CostByBusinessLine[];
  dedup_savings: DedupSavings;
}

interface DashboardData {
  overview: DashboardOverview;
  trends: DashboardTrends;
  health: BusinessHealthResponse;
  cost: CostAnalysis;
}

interface TrendChartPoint {
  time: string;
  value: number;
  type: string;
}

function formatCompactNumber(value?: number, digits = 1) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(digits)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(digits)}K`;
  return n.toLocaleString();
}

function formatTokenDisplay(value?: number) {
  const tokens = Number(value || 0);
  const windows = Math.round(tokens / 4000);
  const textBlocks = Math.round(tokens / 1000);
  return {
    value: formatCompactNumber(tokens),
    windows: windows > 0 ? `${windows.toLocaleString()} 个 4K 分析窗口` : '不足 1 个 4K 分析窗口',
    textBlocks: textBlocks > 0 ? `${textBlocks.toLocaleString()} 千 token 级上下文` : '低消耗',
  };
}

function serviceScoreColor(score: number) {
  if (score >= 85) return '#52c41a';
  if (score >= 65) return '#13c2c2';
  if (score >= 40) return '#faad14';
  return '#ff4d4f';
}

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [days, setDays] = useState(7);
  const [autoEscalated, setAutoEscalated] = useState(false);
  const { isDark } = useTheme();

  const fetcher = useCallback(async () => {
    const fetchAll = async (d: number) => {
      const [ovRes, trRes, hlRes, coRes] = await Promise.all([
        dashboardApi.getOverview(d),
        dashboardApi.getTrends(d),
        dashboardApi.getBusinessHealth(d),
        dashboardApi.getCostAnalysis(d),
      ]);
      return {
        overview: ovRes.data as DashboardOverview,
        trends: trRes.data as DashboardTrends,
        health: hlRes.data as BusinessHealthResponse,
        cost: coRes.data as CostAnalysis,
      };
    };

    let result = await fetchAll(days);
    if (
      days <= 7 &&
      result.overview?.total_tasks === 0 &&
      result.overview?.recent_tasks?.length > 0
    ) {
      result = await fetchAll(30);
      setAutoEscalated(true);
    } else {
      setAutoEscalated(false);
    }
    return result;
  }, [days]);

  const { data, loading, lastUpdated, secondsUntilRefresh, refresh } = usePolling<DashboardData>(fetcher, {
    interval: 60000,
    enabled: true,
  });

  const overview = data?.overview;
  const trends = data?.trends;
  const health = data?.health;
  const cost = data?.cost;

  const services = useMemo(() => {
    return (health?.items || []).map((biz): NormalizedService => {
      const successPct = Math.round((biz.success_rate || 0) * 100);
      const rawScore = biz.health_score ?? successPct;
      const score = Math.round(rawScore <= 1 ? rawScore * 100 : rawScore);
      const errorCount = (biz.critical_count || 0) + (biz.warning_count || 0);
      return {
        ...biz,
        score,
        successPct,
        errorCount,
        color: serviceScoreColor(successPct),
      };
    });
  }, [health]);

  const riskServices = useMemo(() => {
    return [...services]
      .sort((a, b) => {
        if (a.successPct !== b.successPct) return a.successPct - b.successPct;
        return b.errorCount - a.errorCount;
      })
      .slice(0, 5);
  }, [services]);

  const topTokenServices = useMemo(() => {
    return (cost?.by_business_line || []).slice(0, 6);
  }, [cost]);

  if (loading && !data) {
    return (
      <div className="lm-dashboard-page">
        <div className="lm-dashboard-skeleton lm-pulse" />
        <Row gutter={[14, 14]}>
          {[1, 2, 3, 4].map((item) => (
            <Col xs={24} sm={12} lg={6} key={item}>
              <div className="lm-stat-card lm-pulse" style={{ height: 118 }} />
            </Col>
          ))}
        </Row>
      </div>
    );
  }

  const totalTasks = overview?.total_tasks || 0;
  const completedTasks = overview?.completed_tasks || 0;
  const failedTasks = overview?.failed_tasks || 0;
  const completionRate = totalTasks ? Math.round((completedTasks / totalTasks) * 1000) / 10 : 0;
  const criticalCount = overview?.severity_distribution?.find((d) => d.severity === 'critical')?.count || 0;
  const warningCount = overview?.severity_distribution?.find((d) => d.severity === 'warning')?.count || 0;
  const totalTokens = cost?.total_tokens ?? overview?.total_tokens_used ?? 0;
  const tokenInfo = formatTokenDisplay(totalTokens);
  const savedTokenInfo = formatTokenDisplay(cost?.dedup_savings?.estimated_tokens_saved || 0);
  const serviceCount = overview?.total_business_lines || services.length || 0;
  const healthyServices = services.filter((s) => s.successPct >= 80).length;
  const aiTaskRate = cost?.total_tasks ? Math.round(((cost.ai_tasks || 0) / cost.total_tasks) * 100) : 0;

  const trendData: TrendChartPoint[] = [];
  (trends?.data || []).forEach((item) => {
    const time = dayjs(item.period).format('MM-DD');
    trendData.push({ time, value: item.task_count || 0, type: '任务' });
    trendData.push({ time, value: item.failed_count || 0, type: '失败' });
    trendData.push({ time, value: Math.round((item.token_usage || 0) / 1000), type: 'Token(K)' });
  });

  const severityData = overview?.severity_distribution?.map((item) => ({
    type: item.severity,
    value: item.count,
  })) || [];

  const taskColumns: ColumnsType<RecentTask> = [
    {
      title: '状态',
      dataIndex: 'status',
      width: 84,
      render: (status: string) => (
        <Tag color={statusColors[status] || '#8c8c8c'} style={{ borderRadius: 4, margin: 0 }}>
          {status === 'completed' ? '完成' : status === 'running' ? '运行中' : status === 'failed' ? '失败' : '等待'}
        </Tag>
      ),
    },
    {
      title: '日志',
      dataIndex: 'log_count',
      width: 78,
      render: (value: number) => formatCompactNumber(value, 0),
    },
    {
      title: 'Token',
      dataIndex: 'token_usage',
      width: 92,
      render: (value: number) => formatCompactNumber(value),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 118,
      render: (value: string) => (value ? dayjs(value).format('MM-DD HH:mm') : '-'),
    },
  ];

  const headlineStatus = failedTasks > 0 || criticalCount > 0 ? '需要关注' : '运行平稳';
  const headlineColor = failedTasks > 0 || criticalCount > 0 ? '#fa8c16' : '#52c41a';

  return (
    <div className="lm-dashboard-page lm-animate-in">
      <div className="lm-dashboard-header">
        <div>
          <Title level={3} className="lm-dashboard-title">运维总览</Title>
          <Text className="lm-dashboard-subtitle">
            {days} 天窗口 · {serviceCount} 个服务 · 自动刷新
          </Text>
        </div>
        <Space wrap>
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
            style={{ width: 132 }}
          />
        </Space>
      </div>

      {autoEscalated && (
        <Alert
          type="info"
          showIcon
          closable
          banner
          className="lm-dashboard-alert"
          message={
            <span>
              最近 7 天暂无分析数据，已自动展示最近 30 天数据。
              <Button type="link" size="small" onClick={() => setDays(30)} style={{ padding: '0 4px' }}>
                切换为 30 天
              </Button>
            </span>
          }
        />
      )}

      <section className="lm-dashboard-hero">
        <div className="lm-hero-status-panel">
          <div className="lm-hero-status-top">
            <div>
              <Text className="lm-panel-kicker">当前态势</Text>
              <div className="lm-hero-status-title" style={{ color: headlineColor }}>{headlineStatus}</div>
            </div>
            <Progress
              type="circle"
              percent={completionRate}
              size={88}
              strokeWidth={8}
              strokeColor={completionRate >= 90 ? '#52c41a' : completionRate >= 70 ? '#13c2c2' : '#fa8c16'}
              trailColor={isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.06)'}
            />
          </div>
          <div className="lm-hero-metrics">
            <div>
              <span>任务</span>
              <strong>{totalTasks.toLocaleString()}</strong>
            </div>
            <div>
              <span>告警</span>
              <strong>{(overview?.total_alerts || 0).toLocaleString()}</strong>
            </div>
            <div>
              <span>异常</span>
              <strong>{(criticalCount + warningCount).toLocaleString()}</strong>
            </div>
          </div>
          <div className="lm-action-strip">
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/analysis')}>分析</Button>
            <Button icon={<SearchOutlined />} onClick={() => navigate('/logs')}>日志</Button>
            <Button icon={<AlertOutlined />} onClick={() => navigate('/alerts')}>告警</Button>
          </div>
        </div>

        <Card
          className="lm-dashboard-card lm-trend-panel"
          title={<Space><LineChartOutlined /> 趋势</Space>}
          size="small"
          styles={{ body: { padding: '10px 12px 8px' } }}
        >
          {trendData.length > 0 ? (
            <Line
              data={trendData}
              xField="time"
              yField="value"
              seriesField="type"
              height={244}
              smooth
              color={['#1677ff', '#ff4d4f', '#13c2c2']}
              point={{ size: 2 }}
              theme={isDark ? 'classicDark' : 'classic'}
              legend={{ position: 'top-right' }}
              axis={{ y: { grid: true } }}
              animation={{ appear: { animation: 'wave-in', duration: 700 } }}
            />
          ) : (
            <div className="lm-empty-panel">暂无趋势数据</div>
          )}
        </Card>

        <div className="lm-token-panel">
          <div className="lm-token-main">
            <Text className="lm-panel-kicker">Token 消耗换算</Text>
            <div className="lm-token-value">{tokenInfo.value}</div>
            <Text className="lm-token-copy">约 {tokenInfo.windows}</Text>
          </div>
          <div className="lm-token-grid">
            <div>
              <span>平均任务</span>
              <strong>{formatCompactNumber(cost?.avg_tokens_per_task || 0)}</strong>
            </div>
            <div>
              <span>AI 占比</span>
              <strong>{aiTaskRate}%</strong>
            </div>
            <div>
              <span>节省换算</span>
              <strong>{savedTokenInfo.value}</strong>
            </div>
            <div>
              <span>节省率</span>
              <strong>{(cost?.dedup_savings?.savings_percentage || 0).toFixed(1)}%</strong>
            </div>
          </div>
          <Text className="lm-token-footnote">去重节省约 {savedTokenInfo.windows}</Text>
        </div>
      </section>

      <div className="lm-kpi-strip">
        {[
          { label: '完成', value: completedTasks, icon: <CheckCircleOutlined />, color: '#52c41a' },
          { label: '失败', value: failedTasks, icon: <CloseCircleOutlined />, color: '#ff4d4f' },
          { label: '服务健康', value: `${healthyServices}/${serviceCount || 0}`, icon: <SafetyCertificateOutlined />, color: '#13c2c2' },
          { label: 'AI 任务', value: cost?.ai_tasks || 0, icon: <ExperimentOutlined />, color: '#1677ff' },
          { label: '去重节省', value: formatCompactNumber(cost?.dedup_savings?.estimated_tokens_saved || 0), icon: <SaveOutlined />, color: '#fa8c16' },
          { label: '日志量', value: formatCompactNumber(services.reduce((sum, s) => sum + (s.total_logs || 0), 0)), icon: <DatabaseOutlined />, color: '#eb2f96' },
        ].map((item) => (
          <div className="lm-kpi-item" key={item.label}>
            <span style={{ color: item.color }}>{item.icon}</span>
            <div>
              <Text>{item.label}</Text>
              <strong style={{ color: item.color }}>{typeof item.value === 'number' ? item.value.toLocaleString() : item.value}</strong>
            </div>
          </div>
        ))}
      </div>

      <Row gutter={[14, 14]}>
        <Col xs={24} xl={8}>
          <Card
            className="lm-dashboard-card"
            title={<Space><FireOutlined /> 风险服务</Space>}
            extra={<Button type="link" size="small" onClick={() => navigate('/business-lines')}>服务管理</Button>}
            size="small"
          >
            {riskServices.length > 0 ? (
              <div className="lm-risk-list">
                {riskServices.map((svc, index) => (
                  <div
                    key={svc.business_line_id}
                    className="lm-risk-row"
                    onClick={() => navigate(`/business-lines/${svc.business_line_id}`)}
                  >
                    <div className="lm-risk-rank" style={{ color: palette[index % palette.length] }}>
                      {String(index + 1).padStart(2, '0')}
                    </div>
                    <div className="lm-risk-body">
                      <div className="lm-risk-title">
                        <Text ellipsis>{svc.business_line_name}</Text>
                        <Tag color={svc.color} style={{ margin: 0, borderRadius: 4 }}>{svc.successPct}%</Tag>
                      </div>
                      <Progress
                        percent={svc.successPct}
                        showInfo={false}
                        strokeColor={svc.color}
                        railColor={isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)'}
                        size="small"
                      />
                      <Text className="lm-risk-meta">
                        {svc.total_tasks || 0} 任务 · {svc.errorCount} 异常 · {formatCompactNumber(svc.total_logs || 0)} 日志
                      </Text>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="lm-empty-panel">暂无服务数据</div>
            )}
          </Card>
        </Col>

        <Col xs={24} xl={9}>
          <Card
            className="lm-dashboard-card"
            title={<Space><ClusterOutlined /> 服务健康矩阵</Space>}
            size="small"
          >
            {services.length > 0 ? (
              <>
                <div className="lm-service-grid">
                  {services.slice(0, 12).map((svc) => (
                    <Tooltip
                      key={svc.business_line_id}
                      title={`${svc.business_line_name}: ${svc.successPct}% 成功率，${svc.errorCount} 个异常`}
                    >
                      <button
                        className="lm-service-tile"
                        type="button"
                        onClick={() => navigate(`/business-lines/${svc.business_line_id}`)}
                        style={{ borderColor: `${svc.color}55` }}
                      >
                        <span className="lm-service-dot" style={{ background: svc.color }} />
                        <Text ellipsis>{svc.business_line_name}</Text>
                        <strong style={{ color: svc.color }}>{svc.successPct}</strong>
                      </button>
                    </Tooltip>
                  ))}
                </div>
                {services.length > 12 && (
                  <Button block type="text" className="lm-service-more" onClick={() => navigate('/business-lines')}>
                    还有 {services.length - 12} 个服务，进入服务管理查看
                  </Button>
                )}
              </>
            ) : (
              <div className="lm-empty-panel">暂无服务数据</div>
            )}
          </Card>
        </Col>

        <Col xs={24} xl={7}>
          <Card
            className="lm-dashboard-card"
            title={<Space><ClockCircleOutlined /> 最近任务</Space>}
            extra={<Button type="link" size="small" onClick={() => navigate('/analysis')}>查看全部</Button>}
            size="small"
            styles={{ body: { padding: 0 } }}
          >
            <Table
              dataSource={overview?.recent_tasks || []}
              columns={taskColumns}
              rowKey="id"
              size="small"
              pagination={false}
              scroll={{ y: 260 }}
              onRow={(record) => ({
                onClick: () => navigate(`/analysis/${record.id}`),
                style: { cursor: 'pointer' },
              })}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[14, 14]} style={{ marginTop: 14 }}>
        <Col xs={24} lg={9}>
          <Card
            className="lm-dashboard-card"
            title={<Space><RadarChartOutlined /> 严重度</Space>}
            size="small"
          >
            {severityData.length > 0 ? (
              <Pie
                data={severityData}
                angleField="value"
                colorField="type"
                height={236}
                innerRadius={0.66}
                color={({ type }: { type: string }) => severityColors[type] || '#8c8c8c'}
                label={{ text: 'type', style: { fontSize: 12, fill: isDark ? 'rgba(255,255,255,0.65)' : 'rgba(0,0,0,0.65)' } }}
                legend={false}
                theme={isDark ? 'classicDark' : 'classic'}
                statistic={{
                  title: { style: { color: isDark ? 'rgba(255,255,255,0.48)' : 'rgba(0,0,0,0.45)', fontSize: '12px' }, content: '总计' },
                  content: { style: { color: isDark ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.88)', fontSize: '22px' } },
                }}
              />
            ) : (
              <div className="lm-empty-panel">暂无严重度数据</div>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={15}>
          <Card
            className="lm-dashboard-card"
            title={<Space><ThunderboltOutlined /> Token 与去重效率</Space>}
            size="small"
          >
            {cost ? (
              <div className="lm-cost-layout">
                <div className="lm-cost-funnel">
                  {[
                    { label: '总任务', value: cost.total_tasks, color: '#1677ff', pct: 100 },
                    { label: '质量过滤', value: cost.dedup_savings?.quality_filtered_tasks || 0, color: '#13c2c2', pct: cost.total_tasks > 0 ? ((cost.dedup_savings?.quality_filtered_tasks || 0) / cost.total_tasks) * 100 : 0 },
                    { label: '指纹去重', value: cost.dedup_savings?.fingerprint_skipped_tasks || 0, color: '#fa8c16', pct: cost.total_tasks > 0 ? ((cost.dedup_savings?.fingerprint_skipped_tasks || 0) / cost.total_tasks) * 100 : 0 },
                    { label: '语义命中', value: cost.dedup_savings?.semantic_dedup_tasks || 0, color: '#52c41a', pct: cost.total_tasks > 0 ? ((cost.dedup_savings?.semantic_dedup_tasks || 0) / cost.total_tasks) * 100 : 0 },
                    { label: 'AI 推理', value: cost.ai_tasks || 0, color: '#eb2f96', pct: cost.total_tasks > 0 ? ((cost.ai_tasks || 0) / cost.total_tasks) * 100 : 0 },
                  ].map((step) => (
                    <Tooltip key={step.label} title={`${step.label}: ${step.value} 个任务 (${step.pct.toFixed(1)}%)`}>
                      <div className="lm-funnel-row">
                        <span>{step.label}</span>
                        <div>
                          <i style={{ width: `${Math.max(step.pct, step.value > 0 ? 4 : 0)}%`, background: step.color }} />
                          <strong>{step.value}</strong>
                        </div>
                      </div>
                    </Tooltip>
                  ))}
                </div>

                <div className="lm-token-services">
                  <Text className="lm-section-caption">Token Top 服务</Text>
                  {topTokenServices.length > 0 ? topTokenServices.map((item, index) => {
                    const maxTokens = Math.max(...topTokenServices.map((x) => x.tokens_used || 0), 1);
                    const pct = ((item.tokens_used || 0) / maxTokens) * 100;
                    return (
                      <div className="lm-token-service-row" key={item.business_line_id || index}>
                        <Text ellipsis>{item.business_line_name}</Text>
                        <div><i style={{ width: `${pct}%`, background: palette[index % palette.length] }} /></div>
                        <strong>{formatCompactNumber(item.tokens_used)}</strong>
                      </div>
                    );
                  }) : (
                    <Text className="lm-muted">暂无业务线消耗数据</Text>
                  )}
                </div>
              </div>
            ) : (
              <div className="lm-empty-panel">暂无成本数据</div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;
