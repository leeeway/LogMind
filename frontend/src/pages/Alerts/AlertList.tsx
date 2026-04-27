import React, { useEffect, useState, useCallback } from 'react';
import { Table, Tag, Button, Space, Typography, Card, Tabs, message, Badge, Tooltip, Select, Popconfirm } from 'antd';
import { CheckOutlined, CheckCircleOutlined, ReloadOutlined, AlertOutlined, FilterOutlined, BellOutlined } from '@ant-design/icons';
import { alertsApi } from '@/api/alerts';
import { usePolling } from '@/hooks/usePolling';
import RefreshIndicator from '@/components/RefreshIndicator';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

const { Title, Text } = Typography;

const priorityColors: Record<string, string> = { P0: '#ff4d4f', P1: '#fa8c16', P2: '#fadb14', P3: '#8c8c8c' };
const statusLabels: Record<string, { color: string; label: string }> = {
  fired: { color: '#ff4d4f', label: '触发中' },
  acknowledged: { color: '#fa8c16', label: '已确认' },
  resolved: { color: '#52c41a', label: '已解决' },
};

const AlertList: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [rules, setRules] = useState<any[]>([]);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);

  // Stats
  const firedCount = alerts.filter(a => a.status === 'fired').length;
  const ackedCount = alerts.filter(a => a.status === 'acknowledged').length;

  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await alertsApi.listHistory({ page, page_size: 15 });
      const items = data.items || [];
      setAlerts(statusFilter ? items.filter((a: any) => a.status === statusFilter) : items);
      setTotal(data.total || 0);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  }, [page, statusFilter]);

  const fetchRules = async () => {
    try {
      const { data } = await alertsApi.listRules();
      setRules(data.items || data || []);
    } catch (err) { console.error(err); }
  };

  useEffect(() => { fetchAlerts(); fetchRules(); }, [fetchAlerts]);

  const handleAck = async (id: string) => {
    try { await alertsApi.ackAlert(id); message.success('已确认'); fetchAlerts(); }
    catch { message.error('确认失败'); }
  };

  const handleResolve = async (id: string) => {
    try { await alertsApi.resolveAlert(id); message.success('已解决'); fetchAlerts(); }
    catch { message.error('解决失败'); }
  };

  const alertColumns = [
    {
      title: '优先级', dataIndex: 'priority', width: 70,
      render: (p: string) => <Tag color={priorityColors[p] || '#8c8c8c'} style={{ borderRadius: 4, fontWeight: 600 }}>{p}</Tag>,
    },
    {
      title: '严重度', dataIndex: 'severity', width: 80,
      render: (s: string) => <Tag color={s === 'critical' ? '#ff4d4f' : s === 'warning' ? '#faad14' : '#1677ff'} style={{ borderRadius: 4 }}>{s}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: (s: string) => {
        const m = statusLabels[s] || { color: '#8c8c8c', label: s };
        return (
          <Tag color={m.color} style={{ borderRadius: 4 }}>
            {s === 'fired' && <span className="lm-running-dot" style={{ background: '#ff4d4f' }} />}
            {m.label}
          </Tag>
        );
      },
    },
    { title: '告警信息', dataIndex: 'message', ellipsis: true },
    {
      title: '触发时间', dataIndex: 'fired_at', width: 150,
      render: (v: string) => v ? (
        <Tooltip title={dayjs(v).format('YYYY-MM-DD HH:mm:ss')}>
          <span style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
            {dayjs(v).fromNow()}
          </span>
        </Tooltip>
      ) : '-',
    },
    {
      title: '操作', width: 160,
      render: (_: any, r: any) => (
        <Space>
          {r.status === 'fired' && (
            <Popconfirm title="确认此告警？" onConfirm={() => handleAck(r.id)} okText="确认" cancelText="取消">
              <Button size="small" icon={<CheckOutlined />}>确认</Button>
            </Popconfirm>
          )}
          {r.status !== 'resolved' && (
            <Popconfirm title="标记为已解决？" onConfirm={() => handleResolve(r.id)} okText="确认" cancelText="取消">
              <Button size="small" type="primary" icon={<CheckCircleOutlined />}>解决</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  const ruleColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'rule_type', width: 100 },
    { title: '严重度', dataIndex: 'severity', width: 80, render: (s: string) => <Tag color={s === 'critical' ? '#ff4d4f' : '#faad14'}>{s}</Tag> },
    { title: 'Cron', dataIndex: 'cron_expression', width: 140, render: (v: string) => <code style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>{v}</code> },
    { title: '状态', dataIndex: 'is_active', width: 80, render: (v: boolean) => <Tag color={v ? '#52c41a' : '#8c8c8c'}>{v ? '启用' : '禁用'}</Tag> },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Space>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
            <AlertOutlined style={{ marginRight: 8 }} />告警管理
          </Title>
          {firedCount > 0 && (
            <Badge count={firedCount} style={{ background: '#ff4d4f' }}>
              <Tag color="#ff4d4f" style={{ borderRadius: 4 }}>待处理</Tag>
            </Badge>
          )}
          {ackedCount > 0 && (
            <Tag color="#fa8c16" style={{ borderRadius: 4 }}>{ackedCount} 待解决</Tag>
          )}
        </Space>
        <Button icon={<ReloadOutlined />} onClick={fetchAlerts} />
      </div>
      <Card style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}>
        <Tabs items={[
          {
            key: 'history',
            label: (
              <Space>
                <BellOutlined />告警记录
                {total > 0 && <Badge count={total} size="small" />}
              </Space>
            ),
            children: (
              <>
                <div style={{ marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
                  <FilterOutlined style={{ color: 'var(--lm-text-tertiary)' }} />
                  <Select
                    allowClear
                    placeholder="状态筛选"
                    value={statusFilter}
                    onChange={setStatusFilter}
                    options={[
                      { value: 'fired', label: '触发中' },
                      { value: 'acknowledged', label: '已确认' },
                      { value: 'resolved', label: '已解决' },
                    ]}
                    style={{ width: 140 }}
                  />
                </div>
                <Table dataSource={alerts} columns={alertColumns} rowKey="id" size="small" loading={loading}
                  pagination={{ current: page, total, pageSize: 15, onChange: setPage, showTotal: (t) => `共 ${t} 条` }} />
              </>
            ),
          },
          {
            key: 'rules', label: '告警规则',
            children: <Table dataSource={rules} columns={ruleColumns} rowKey="id" size="small" pagination={false} />,
          },
        ]} />
      </Card>
    </div>
  );
};

export default AlertList;
