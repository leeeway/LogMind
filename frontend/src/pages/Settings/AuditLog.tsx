import React, { useEffect, useState } from 'react';
import { Table, Tag, Select, Space, Typography, Input } from 'antd';
import { AuditOutlined } from '@ant-design/icons';
import { auditApi } from '@/api/users';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const actionColors: Record<string, string> = {
  'alert.ack': '#1677ff',
  'alert.resolve': '#52c41a',
  'issue.resolve': '#52c41a',
  'issue.ignore': '#faad14',
  'rule.create': '#722ed1',
  'user.create': '#13c2c2',
  'user.login': '#1677ff',
};

const AuditLog: React.FC = () => {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState<string | undefined>();

  const load = async (p = page, action = actionFilter) => {
    setLoading(true);
    try {
      const { data } = await auditApi.list({ page: p, page_size: 20, action });
      setLogs(data?.items || []);
      setTotal(data?.total || 0);
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [page, actionFilter]);

  const columns = [
    {
      title: '时间', dataIndex: 'created_at', width: 170,
      render: (v: string) => (
        <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--lm-text-tertiary)' }}>
          {dayjs(v).format('MM-DD HH:mm:ss')}
        </span>
      ),
    },
    {
      title: '用户', dataIndex: 'username', width: 120,
      render: (v: string) => <span style={{ fontWeight: 600, color: 'var(--lm-text)' }}>{v || '-'}</span>,
    },
    {
      title: '操作', dataIndex: 'action', width: 140,
      render: (v: string) => <Tag color={actionColors[v] || '#8c8c8c'} style={{ borderRadius: 4 }}>{v}</Tag>,
    },
    {
      title: '资源类型', dataIndex: 'resource_type', width: 120,
      render: (v: string) => <Tag style={{ borderRadius: 4 }}>{v || '-'}</Tag>,
    },
    {
      title: '资源 ID', dataIndex: 'resource_id', width: 200, ellipsis: true,
      render: (v: string) => <Text copyable={{ text: v }} style={{ fontSize: 12, color: 'var(--lm-text-secondary)' }}>{v?.slice(0, 16) || '-'}...</Text>,
    },
    {
      title: 'IP', dataIndex: 'ip_address', width: 130,
      render: (v: string) => <span style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>{v || '-'}</span>,
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, alignItems: 'center' }}>
        <Title level={5} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <AuditOutlined style={{ marginRight: 8 }} />操作审计
        </Title>
        <Space>
          <Select
            allowClear
            placeholder="筛选操作类型"
            value={actionFilter}
            onChange={(v) => { setActionFilter(v); setPage(1); }}
            style={{ width: 180 }}
            options={[
              { value: 'alert.ack', label: '告警确认' },
              { value: 'alert.resolve', label: '告警解决' },
              { value: 'issue.resolve', label: '问题解决' },
              { value: 'issue.ignore', label: '问题忽略' },
              { value: 'rule.create', label: '规则创建' },
              { value: 'user.create', label: '用户创建' },
              { value: 'user.login', label: '用户登录' },
            ]}
          />
        </Space>
      </div>

      <Table
        dataSource={logs}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />
    </div>
  );
};

export default AuditLog;
