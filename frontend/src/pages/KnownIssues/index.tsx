import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Table, Tag, Button, Space, Typography, Card, Select, Input, message,
  Badge, Tooltip, Drawer, Descriptions, Popconfirm, Tabs, Empty, Modal,
} from 'antd';
import {
  BugOutlined, SearchOutlined, ReloadOutlined, DeleteOutlined,
  CheckCircleOutlined, StopOutlined, UndoOutlined, EyeOutlined,
  ClockCircleOutlined, FireOutlined, FieldTimeOutlined,
} from '@ant-design/icons';
import { knownIssuesApi } from '@/api/knownIssues';
import { businessLineApi } from '@/api/services';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

const { Title, Text, Paragraph } = Typography;

const severityColors: Record<string, string> = {
  critical: '#ff4d4f', warning: '#faad14', info: '#1677ff', error: '#ff4d4f',
};
const statusConfig: Record<string, { color: string; label: string; icon: React.ReactNode }> = {
  open: { color: '#ff4d4f', label: '活跃', icon: <FireOutlined /> },
  resolved: { color: '#52c41a', label: '已解决', icon: <CheckCircleOutlined /> },
  ignored: { color: '#8c8c8c', label: '已忽略', icon: <StopOutlined /> },
};

const KnownIssues: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [issues, setIssues] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(15);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [severityFilter, setSeverityFilter] = useState<string | undefined>(undefined);
  const [bizFilter, setBizFilter] = useState<string | undefined>(undefined);
  const [searchText, setSearchText] = useState('');
  const [sortBy, setSortBy] = useState('last_seen');

  // Business lines for filter dropdown
  const [bizLines, setBizLines] = useState<any[]>([]);

  // Detail Drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Stats
  const openCount = issues.filter(i => i.status === 'open').length;

  const fetchIssues = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await knownIssuesApi.list({
        page,
        page_size: pageSize,
        status: statusFilter,
        severity: severityFilter,
        business_line_id: bizFilter,
        search: searchText || undefined,
        sort_by: sortBy,
        sort_order: 'desc',
      });
      setIssues(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, statusFilter, severityFilter, bizFilter, searchText, sortBy]);

  const fetchBizLines = async () => {
    try {
      const { data } = await businessLineApi.list({ page_size: 100 });
      setBizLines(data?.items || []);
    } catch { /* ignore */ }
  };

  useEffect(() => { fetchIssues(); }, [fetchIssues]);
  useEffect(() => { fetchBizLines(); }, []);

  const openDetail = async (id: string) => {
    setDrawerOpen(true);
    setDetailLoading(true);
    try {
      const { data } = await knownIssuesApi.get(id);
      setDetail(data);
    } catch {
      message.error('加载详情失败');
    } finally {
      setDetailLoading(false);
    }
  };

  const handleStatusChange = async (id: string, newStatus: string) => {
    try {
      await knownIssuesApi.updateStatus(id, newStatus);
      message.success('状态已更新');
      fetchIssues();
      if (detail?.id === id) {
        setDetail({ ...detail, status: newStatus });
      }
    } catch {
      message.error('更新失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await knownIssuesApi.remove(id);
      message.success('已删除');
      fetchIssues();
      if (detail?.id === id) {
        setDrawerOpen(false);
      }
    } catch {
      message.error('删除失败');
    }
  };

  const columns = [
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (s: string) => {
        const cfg = statusConfig[s] || { color: '#8c8c8c', label: s, icon: null };
        return (
          <Tag color={cfg.color} style={{ borderRadius: 4 }}>
            {cfg.icon} {cfg.label}
          </Tag>
        );
      },
    },
    {
      title: '严重度', dataIndex: 'severity', width: 80,
      render: (s: string) => (
        <Tag color={severityColors[s] || '#8c8c8c'} style={{ borderRadius: 4, textTransform: 'uppercase' as const }}>
          {s}
        </Tag>
      ),
    },
    {
      title: '错误签名', dataIndex: 'error_signature', ellipsis: true,
      render: (text: string, r: any) => (
        <a onClick={() => openDetail(r.id)} style={{ color: 'var(--lm-text)', fontSize: 13 }}>
          {text || '(empty)'}
        </a>
      ),
    },
    {
      title: '命中', dataIndex: 'hit_count', width: 70,
      render: (v: number) => (
        <Tooltip title={`该错误模式被匹配 ${v} 次`}>
          <Badge count={v} showZero style={{
            background: v > 10 ? '#ff4d4f' : v > 3 ? '#fa8c16' : 'rgba(255,255,255,0.08)',
            color: v > 3 ? '#fff' : 'var(--lm-text-secondary)',
            fontWeight: 600,
          }} />
        </Tooltip>
      ),
    },
    {
      title: '最近出现', dataIndex: 'last_seen', width: 130,
      render: (v: string) => v ? (
        <Tooltip title={dayjs(v).format('YYYY-MM-DD HH:mm:ss')}>
          <Space size={4} style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
            <ClockCircleOutlined />
            {dayjs(v).fromNow()}
          </Space>
        </Tooltip>
      ) : '-',
    },
    {
      title: '首次发现', dataIndex: 'first_seen', width: 130,
      render: (v: string) => v ? (
        <span style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
          {dayjs(v).format('MM-DD HH:mm')}
        </span>
      ) : '-',
    },
    {
      title: '操作', width: 160,
      render: (_: any, r: any) => (
        <Space size={4}>
          <Tooltip title="查看详情">
            <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r.id)} />
          </Tooltip>
          {r.status === 'open' && (
            <>
              <Tooltip title="标记已解决（触发回归检测）">
                <Popconfirm title="标记为已解决？再次出现将触发回归 P0" onConfirm={() => handleStatusChange(r.id, 'resolved')}>
                  <Button size="small" icon={<CheckCircleOutlined />} style={{ color: '#52c41a' }} />
                </Popconfirm>
              </Tooltip>
              <Tooltip title="忽略（排除 KNN 匹配）">
                <Popconfirm title="忽略该问题？将不再参与语义匹配" onConfirm={() => handleStatusChange(r.id, 'ignored')}>
                  <Button size="small" icon={<StopOutlined />} />
                </Popconfirm>
              </Tooltip>
            </>
          )}
          {(r.status === 'resolved' || r.status === 'ignored') && (
            <Tooltip title="重新打开">
              <Button size="small" icon={<UndoOutlined />} onClick={() => handleStatusChange(r.id, 'open')} />
            </Tooltip>
          )}
          <Popconfirm title="永久删除该已知问题？" onConfirm={() => handleDelete(r.id)} okType="danger">
            <Tooltip title="删除">
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Space>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
            <BugOutlined style={{ marginRight: 8 }} />已知问题库
          </Title>
          <Tag color="blue">{total} 个问题</Tag>
          {openCount > 0 && (
            <Badge count={openCount} style={{ background: '#ff4d4f' }}>
              <Tag color="#ff4d4f" style={{ borderRadius: 4 }}>活跃</Tag>
            </Badge>
          )}
        </Space>
        <Button icon={<ReloadOutlined />} onClick={fetchIssues} />
      </div>

      {/* Filter Bar */}
      <div style={{
        display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16, padding: '12px 16px',
        background: 'var(--lm-bg-card)', borderRadius: 10,
        border: '1px solid var(--lm-border-light)',
      }}>
        <Select
          allowClear placeholder="状态" value={statusFilter} onChange={(v) => { setStatusFilter(v); setPage(1); }}
          style={{ width: 120 }}
          options={[
            { value: 'open', label: '🔴 活跃' },
            { value: 'resolved', label: '✅ 已解决' },
            { value: 'ignored', label: '⏭ 已忽略' },
          ]}
        />
        <Select
          allowClear placeholder="严重度" value={severityFilter} onChange={(v) => { setSeverityFilter(v); setPage(1); }}
          style={{ width: 120 }}
          options={[
            { value: 'critical', label: '🔴 Critical' },
            { value: 'warning', label: '🟡 Warning' },
            { value: 'info', label: '🔵 Info' },
          ]}
        />
        <Select
          allowClear placeholder="业务线" value={bizFilter} onChange={(v) => { setBizFilter(v); setPage(1); }}
          style={{ width: 180 }} showSearch optionFilterProp="label"
          options={bizLines.map(b => ({ value: b.id, label: b.name }))}
        />
        <Select
          value={sortBy} onChange={setSortBy} style={{ width: 140 }}
          options={[
            { value: 'last_seen', label: '最近出现' },
            { value: 'hit_count', label: '命中次数' },
            { value: 'created_at', label: '创建时间' },
            { value: 'severity', label: '严重度' },
          ]}
        />
        <Input.Search
          placeholder="搜索错误签名..." allowClear style={{ width: 240 }}
          onSearch={(v) => { setSearchText(v); setPage(1); }}
          enterButton={<SearchOutlined />}
        />
      </div>

      {/* Table */}
      <Card style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={issues}
          columns={columns}
          rowKey="id"
          size="small"
          loading={loading}
          pagination={{
            current: page, total, pageSize,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 个问题`,
          }}
          onRow={(record: any) => ({
            style: {
              cursor: 'pointer',
              opacity: record.status === 'ignored' ? 0.5 : 1,
            },
          })}
        />
      </Card>

      {/* Detail Drawer */}
      <Drawer
        title={
          <Space>
            <BugOutlined />
            <span>问题详情</span>
            {detail && (
              <Tag color={statusConfig[detail.status]?.color || '#8c8c8c'} style={{ borderRadius: 4 }}>
                {statusConfig[detail.status]?.label || detail.status}
              </Tag>
            )}
          </Space>
        }
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={640}
        styles={{ body: { background: 'var(--lm-bg-layout)' } }}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 60, color: 'var(--lm-text-tertiary)' }}>加载中...</div>
        ) : detail ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Meta Info */}
            <Card size="small" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}>
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="严重度">
                  <Tag color={severityColors[detail.severity]}>{detail.severity}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="业务线">{detail.business_line_name || detail.business_line_id?.slice(0, 8)}</Descriptions.Item>
                <Descriptions.Item label="命中次数">
                  <Text strong style={{ color: detail.hit_count > 10 ? '#ff4d4f' : 'var(--lm-text)' }}>
                    {detail.hit_count} 次
                  </Text>
                </Descriptions.Item>
                <Descriptions.Item label="反馈质量">
                  <Tag color={detail.feedback_quality === 'verified' ? '#52c41a' : detail.feedback_quality === 'poor' ? '#ff4d4f' : '#8c8c8c'}>
                    {detail.feedback_quality || '未评价'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="首次发现">{detail.first_seen ? dayjs(detail.first_seen).format('YYYY-MM-DD HH:mm') : '-'}</Descriptions.Item>
                <Descriptions.Item label="最近出现">{detail.last_seen ? dayjs(detail.last_seen).fromNow() : '-'}</Descriptions.Item>
                {detail.resolved_at && (
                  <Descriptions.Item label="解决时间">{dayjs(detail.resolved_at).format('YYYY-MM-DD HH:mm')}</Descriptions.Item>
                )}
                <Descriptions.Item label="TTL 过期">
                  {detail.ttl_expire_at ? dayjs(detail.ttl_expire_at).format('YYYY-MM-DD') : '-'}
                </Descriptions.Item>
              </Descriptions>
            </Card>

            {/* Life Cycle Bar */}
            {detail.first_seen && detail.last_seen && (
              <div style={{
                padding: '10px 16px', background: 'var(--lm-bg-card)',
                border: '1px solid var(--lm-border-light)', borderRadius: 10,
              }}>
                <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)', marginBottom: 6, display: 'block' }}>
                  <FieldTimeOutlined /> 生命周期
                </Text>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', whiteSpace: 'nowrap' }}>
                    {dayjs(detail.first_seen).format('MM-DD')}
                  </Text>
                  <div style={{
                    flex: 1, height: 6, borderRadius: 3,
                    background: `linear-gradient(90deg, ${severityColors[detail.severity] || '#1677ff'}, rgba(22,119,255,0.2))`,
                  }} />
                  <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', whiteSpace: 'nowrap' }}>
                    {dayjs(detail.last_seen).format('MM-DD')}
                  </Text>
                </div>
              </div>
            )}

            {/* Error Signature */}
            <Card size="small" title="错误签名" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
              <pre style={{
                margin: 0, padding: 12, fontSize: 12, lineHeight: 1.6,
                background: 'var(--lm-bg-layout)', borderRadius: 8,
                color: '#ff7875', overflow: 'auto', maxHeight: 160, whiteSpace: 'pre-wrap',
              }}>
                {detail.error_signature}
              </pre>
            </Card>

            {/* AI Analysis Content */}
            <Card size="small" title="AI 分析结论" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
              <Paragraph style={{
                color: 'var(--lm-text-secondary)', whiteSpace: 'pre-wrap',
                fontSize: 13, lineHeight: 1.8, margin: 0,
              }}>
                {detail.analysis_content || '暂无分析内容'}
              </Paragraph>
            </Card>

            {/* Actions */}
            <div style={{ display: 'flex', gap: 8, padding: '8px 0' }}>
              {detail.status === 'open' && (
                <>
                  <Button type="primary" icon={<CheckCircleOutlined />} style={{ background: '#52c41a', borderColor: '#52c41a' }}
                    onClick={() => handleStatusChange(detail.id, 'resolved')}>
                    标记已解决
                  </Button>
                  <Button icon={<StopOutlined />} onClick={() => handleStatusChange(detail.id, 'ignored')}>
                    忽略
                  </Button>
                </>
              )}
              {(detail.status === 'resolved' || detail.status === 'ignored') && (
                <Button icon={<UndoOutlined />} onClick={() => handleStatusChange(detail.id, 'open')}>
                  重新打开
                </Button>
              )}
              {detail.task_id && (
                <Button type="link" onClick={() => { setDrawerOpen(false); navigate(`/analysis/${detail.task_id}`); }}>
                  查看关联分析 →
                </Button>
              )}
            </div>
          </div>
        ) : (
          <Empty description="未找到" />
        )}
      </Drawer>
    </div>
  );
};

export default KnownIssues;
