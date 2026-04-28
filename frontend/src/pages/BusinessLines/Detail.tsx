import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, Typography, Space, Button, Spin, Descriptions, Tag, Table, Row, Col, Switch, Tooltip, message } from 'antd';
import {
  ArrowLeftOutlined, ClusterOutlined, ExperimentOutlined, EditOutlined,
  ClockCircleOutlined, BugOutlined, RiseOutlined,
} from '@ant-design/icons';
import { Line } from '@ant-design/charts';
import { businessLineApi } from '@/api/services';
import { analysisApi } from '@/api/analysis';
import { dashboardApi } from '@/api/dashboard';
import { knownIssuesApi } from '@/api/knownIssues';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = { completed: '#52c41a', running: '#1677ff', failed: '#ff4d4f', pending: '#8c8c8c' };
const languageLabels: Record<string, string> = { java: 'Java', csharp: 'C#', python: 'Python', go: 'Go', other: '通用' };
const nightPolicyLabels: Record<string, { label: string; color: string }> = {
  always: { label: '始终通知', color: '#ff4d4f' },
  p0_only: { label: '仅 P0', color: '#fa8c16' },
  silent: { label: '静默', color: '#52c41a' },
};

const BusinessLineDetail: React.FC = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [biz, setBiz] = useState<any>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [trendData, setTrendData] = useState<any[]>([]);
  const [issues, setIssues] = useState<any[]>([]);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      // Fetch business line info from the list (no dedicated detail API)
      const [bizRes, taskRes, trendRes, issueRes] = await Promise.all([
        businessLineApi.list({ page_size: 100 }),
        analysisApi.listTasks({ business_line_id: id, page_size: 10 }),
        dashboardApi.getTrends(7, id).catch(() => ({ data: { data: [] } })),
        knownIssuesApi.list({ business_line_id: id, page_size: 10, sort_by: 'last_seen' }).catch(() => ({ data: { items: [] } })),
      ]);
      const found = (bizRes.data?.items || []).find((b: any) => b.id === id);
      setBiz(found || null);
      setTasks(taskRes.data?.items || []);
      setIssues(issueRes.data?.items || []);

      // Build trend line
      const points = (trendRes.data?.data || []).map((item: any) => ({
        time: dayjs(item.period).format('MM-DD'),
        value: item.log_count || 0,
      }));
      setTrendData(points);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const toggleAI = async () => {
    if (!biz) return;
    try {
      await businessLineApi.update(biz.id, { ai_enabled: !biz.ai_enabled });
      message.success(biz.ai_enabled ? 'AI 分析已关闭' : 'AI 分析已开启');
      load();
    } catch { message.error('操作失败'); }
  };

  if (loading) return <div style={{ textAlign: 'center', paddingTop: 120 }}><Spin size="large" /></div>;
  if (!biz) return <div style={{ textAlign: 'center', paddingTop: 120 }}><Text>服务不存在</Text></div>;

  const np = nightPolicyLabels[biz.night_policy] || { label: biz.night_policy, color: '#8c8c8c' };

  const taskColumns = [
    {
      title: '状态', dataIndex: 'status', width: 80,
      render: (s: string) => <Tag color={statusColors[s] || '#8c8c8c'} style={{ borderRadius: 4 }}>{s === 'completed' ? '完成' : s === 'running' ? '运行中' : s === 'failed' ? '失败' : '等待'}</Tag>,
    },
    { title: '类型', dataIndex: 'task_type', width: 80 },
    { title: '日志数', dataIndex: 'log_count', width: 80, render: (v: number) => v?.toLocaleString() },
    { title: 'Token', dataIndex: 'token_usage', width: 80, render: (v: number) => v?.toLocaleString() },
    { title: '时间', dataIndex: 'created_at', width: 140, render: (v: string) => v ? dayjs(v).format('MM-DD HH:mm') : '-' },
  ];

  const issueColumns = [
    {
      title: '状态', dataIndex: 'status', width: 80,
      render: (s: string) => <Tag color={s === 'open' ? '#ff4d4f' : s === 'resolved' ? '#52c41a' : '#8c8c8c'} style={{ borderRadius: 4 }}>{s === 'open' ? '活跃' : s === 'resolved' ? '已解决' : '已忽略'}</Tag>,
    },
    {
      title: '严重度', dataIndex: 'severity', width: 80,
      render: (s: string) => <Tag color={s === 'critical' ? '#ff4d4f' : s === 'warning' ? '#faad14' : '#1677ff'} style={{ borderRadius: 4 }}>{s}</Tag>,
    },
    { title: '错误签名', dataIndex: 'error_signature', ellipsis: true },
    { title: '命中', dataIndex: 'hit_count', width: 60 },
  ];

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/business-lines')}>返回</Button>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
            <ClusterOutlined style={{ marginRight: 8 }} />{biz.name}
          </Title>
          <Tag style={{ borderRadius: 4 }}>{languageLabels[biz.language] || biz.language}</Tag>
          {biz.is_core_path && <Tag color="#ff4d4f" style={{ borderRadius: 4, fontSize: 10 }}>核心路径</Tag>}
        </Space>
        <Space>
          <Tooltip title={biz.ai_enabled ? '关闭 AI 分析' : '开启 AI 分析'}>
            <Space>
              <ExperimentOutlined style={{ color: biz.ai_enabled ? '#52c41a' : 'var(--lm-text-tertiary)' }} />
              <Switch size="small" checked={biz.ai_enabled} onChange={toggleAI} />
            </Space>
          </Tooltip>
        </Space>
      </div>

      {/* Config Card */}
      <Card size="small" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}>
        <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }}>
          <Descriptions.Item label="ES 索引"><code style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>{biz.es_index_pattern}</code></Descriptions.Item>
          <Descriptions.Item label="权重"><Text strong style={{ color: biz.business_weight >= 8 ? '#ff4d4f' : 'var(--lm-text)' }}>{biz.business_weight}</Text></Descriptions.Item>
          <Descriptions.Item label="预估 DAU">{biz.estimated_dau?.toLocaleString() || '-'}</Descriptions.Item>
          <Descriptions.Item label="夜间策略"><Tag color={np.color} style={{ borderRadius: 4 }}>{np.label}</Tag></Descriptions.Item>
          <Descriptions.Item label="告警级别"><Tag>{biz.severity_threshold || 'error'}</Tag></Descriptions.Item>
          <Descriptions.Item label="状态"><Tag color={biz.is_active ? '#52c41a' : '#8c8c8c'}>{biz.is_active ? '启用' : '禁用'}</Tag></Descriptions.Item>
          {biz.description && <Descriptions.Item label="描述" span={2}>{biz.description}</Descriptions.Item>}
        </Descriptions>
      </Card>

      <Row gutter={[16, 16]}>
        {/* Error Trend Sparkline */}
        <Col xs={24} lg={12}>
          <Card
            title={<Space><RiseOutlined /> 最近 7 天日志趋势</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            {trendData.length > 0 ? (
              <Line
                data={trendData}
                xField="time" yField="value"
                height={180} smooth
                color="#1677ff" theme="classicDark"
                area={{ style: { fillOpacity: 0.1 } }}
                point={{ size: 3 }}
              />
            ) : (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--lm-text-tertiary)' }}>暂无趋势数据</div>
            )}
          </Card>
        </Col>

        {/* Recent Tasks */}
        <Col xs={24} lg={12}>
          <Card
            title={<Space><ClockCircleOutlined /> 最近分析任务</Space>}
            extra={<Button type="link" size="small" onClick={() => navigate('/analysis')}>查看全部</Button>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            <Table
              dataSource={tasks}
              columns={taskColumns}
              rowKey="id"
              size="small"
              pagination={false}
              scroll={{ y: 180 }}
              onRow={(record: any) => ({
                onClick: () => navigate(`/analysis/${record.id}`),
                style: { cursor: 'pointer' },
              })}
            />
          </Card>
        </Col>

        {/* Known Issues */}
        <Col span={24}>
          <Card
            title={<Space><BugOutlined /> 关联已知问题</Space>}
            extra={<Button type="link" size="small" onClick={() => navigate('/known-issues')}>查看全部</Button>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            <Table
              dataSource={issues}
              columns={issueColumns}
              rowKey="id"
              size="small"
              pagination={false}
              locale={{ emptyText: '暂无已知问题' }}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default BusinessLineDetail;
