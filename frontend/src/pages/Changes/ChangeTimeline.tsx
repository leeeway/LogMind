import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Space, Button, Tag, Table, Spin, Empty, message,
  Modal, Form, Input, Select, Drawer, Descriptions, Progress, Tooltip,
} from 'antd';
import {
  DeploymentUnitOutlined, PlusOutlined, ReloadOutlined, ThunderboltOutlined,
  ArrowUpOutlined, ArrowDownOutlined, MinusOutlined, RocketOutlined,
  ExclamationCircleOutlined, ClockCircleOutlined,
} from '@ant-design/icons';
import client from '@/api/client';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const typeColors: Record<string, string> = {
  deploy: '#1677ff', config: '#722ed1', rollback: '#ff4d4f',
  scale: '#52c41a', hotfix: '#fa8c16',
};
const typeLabels: Record<string, string> = {
  deploy: '部署', config: '配置', rollback: '回滚',
  scale: '扩缩容', hotfix: '热修复',
};

const ChangeTimeline: React.FC = () => {
  const [changes, setChanges] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [impactDrawer, setImpactDrawer] = useState<any>(null);
  const [impactData, setImpactData] = useState<any>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/changes/timeline', { params: { days: 7 } });
      setChanges(data.changes || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async (values: any) => {
    try {
      await client.post('/changes', values);
      message.success('变更事件已记录');
      setCreateOpen(false);
      form.resetFields();
      load();
    } catch { message.error('记录失败'); }
  };

  const showImpact = async (change: any) => {
    setImpactDrawer(change);
    setImpactLoading(true);
    try {
      const { data } = await client.get(`/changes/${change.id}/impact`);
      setImpactData(data);
    } catch { message.error('影响分析失败'); }
    setImpactLoading(false);
  };

  const columns = [
    {
      title: '时间', dataIndex: 'timestamp', key: 'ts', width: 170,
      render: (v: string) => (
        <Text style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--lm-text-secondary)' }}>
          {dayjs(v).format('MM-DD HH:mm:ss')}
        </Text>
      ),
    },
    {
      title: '类型', dataIndex: 'change_type', key: 'type', width: 100,
      render: (v: string) => (
        <Tag color={typeColors[v] || '#8c8c8c'} style={{ borderRadius: 4 }}>
          {typeLabels[v] || v}
        </Tag>
      ),
    },
    {
      title: '服务', dataIndex: 'service_name', key: 'svc',
      render: (v: string) => <Text strong style={{ color: 'var(--lm-text)' }}>{v}</Text>,
    },
    {
      title: '版本', dataIndex: 'version', key: 'ver',
      render: (v: string) => v ? <Tag style={{ borderRadius: 4, fontFamily: 'monospace' }}>{v}</Tag> : '-',
    },
    {
      title: '操作人', dataIndex: 'operator', key: 'op', width: 120,
      render: (v: string) => <Text style={{ color: 'var(--lm-text-secondary)' }}>{v || '-'}</Text>,
    },
    {
      title: '关联异常', dataIndex: 'correlated_spikes', key: 'spikes', width: 100,
      render: (v: number) => (
        <span style={{ color: v > 0 ? '#ff4d4f' : '#52c41a', fontWeight: 600 }}>
          {v > 0 && <ThunderboltOutlined style={{ marginRight: 4 }} />}
          {v}
        </span>
      ),
      sorter: (a: any, b: any) => a.correlated_spikes - b.correlated_spikes,
    },
    {
      title: '操作', key: 'action', width: 120,
      render: (_: any, r: any) => (
        <Button size="small" type="link" onClick={() => showImpact(r)}>
          <ExclamationCircleOutlined /> 影响分析
        </Button>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <DeploymentUnitOutlined style={{ marginRight: 8 }} />变更追踪
        </Title>
        <Space>
          <Button icon={<PlusOutlined />} type="primary" onClick={() => setCreateOpen(true)}>
            记录变更
          </Button>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </Space>
      </div>

      {/* Timeline Table */}
      <Card
        size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ body: { padding: 0 } }}
      >
        <Table
          dataSource={changes}
          columns={columns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
          locale={{ emptyText: <Empty description="暂无变更记录，点击「记录变更」或配置 CI Webhook" /> }}
        />
      </Card>

      {/* Create Modal */}
      <Modal
        title="记录变更事件"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="记录"
      >
        <Form form={form} onFinish={handleCreate} layout="vertical">
          <Form.Item name="service_name" label="服务名称" rules={[{ required: true }]}>
            <Input placeholder="例: order-service" />
          </Form.Item>
          <Form.Item name="change_type" label="变更类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'deploy', label: '🚀 部署' },
              { value: 'config', label: '⚙️ 配置变更' },
              { value: 'rollback', label: '🔙 回滚' },
              { value: 'scale', label: '📈 扩缩容' },
              { value: 'hotfix', label: '🔧 热修复' },
            ]} />
          </Form.Item>
          <Form.Item name="version" label="版本号">
            <Input placeholder="例: v2.3.1" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="变更内容描述" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Impact Drawer */}
      <Drawer
        title={<Space><ExclamationCircleOutlined /> 变更影响分析</Space>}
        open={!!impactDrawer}
        onClose={() => { setImpactDrawer(null); setImpactData(null); }}
        width={520}
      >
        {impactLoading ? <Spin style={{ display: 'block', textAlign: 'center', marginTop: 60 }} /> : impactData && (
          <div>
            {/* Change Info */}
            <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="服务">{impactData.change.service_name}</Descriptions.Item>
              <Descriptions.Item label="类型">
                <Tag color={typeColors[impactData.change.change_type]}>{typeLabels[impactData.change.change_type]}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="版本">{impactData.change.version || '-'}</Descriptions.Item>
              <Descriptions.Item label="操作人">{impactData.change.operator}</Descriptions.Item>
            </Descriptions>

            {/* Risk Score */}
            <div style={{
              padding: '12px 16px', borderRadius: 8, marginBottom: 16,
              background: impactData.risk_score > 60 ? 'rgba(255,77,79,0.08)' : impactData.risk_score > 30 ? 'rgba(250,173,20,0.08)' : 'rgba(82,196,26,0.08)',
              border: `1px solid ${impactData.risk_score > 60 ? '#ff4d4f20' : impactData.risk_score > 30 ? '#faad1420' : '#52c41a20'}`,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <Text strong>风险评分</Text>
                <span style={{
                  fontSize: 24, fontWeight: 700,
                  color: impactData.risk_score > 60 ? '#ff4d4f' : impactData.risk_score > 30 ? '#faad14' : '#52c41a',
                }}>
                  {impactData.risk_score}
                </span>
              </div>
              <Progress
                percent={impactData.risk_score}
                strokeColor={impactData.risk_score > 60 ? '#ff4d4f' : impactData.risk_score > 30 ? '#faad14' : '#52c41a'}
                trailColor="rgba(255,255,255,0.06)"
                showInfo={false}
              />
              <div style={{ marginTop: 8, fontSize: 13, color: 'var(--lm-text-secondary)' }}>
                {impactData.ai_assessment}
              </div>
            </div>

            {/* Blast Radius */}
            <Text strong style={{ display: 'block', marginBottom: 8 }}>
              爆炸半径 ({impactData.blast_radius?.length || 0} 个服务受影响)
            </Text>
            {impactData.blast_radius?.map((b: any, i: number) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px',
                background: 'var(--lm-bg-elevated)', borderRadius: 6, marginBottom: 6,
                border: '1px solid var(--lm-border-light)',
              }}>
                <Text style={{ minWidth: 100, fontWeight: 500 }}>{b.service}</Text>
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Tag style={{ borderRadius: 3 }}>{b.error_count_before}</Tag>
                  <span style={{ color: 'var(--lm-text-tertiary)' }}>→</span>
                  <Tag color={b.impact_pct > 50 ? '#ff4d4f' : b.impact_pct > 0 ? '#faad14' : '#52c41a'} style={{ borderRadius: 3 }}>
                    {b.error_count_after}
                  </Tag>
                </div>
                <span style={{
                  fontWeight: 700, fontSize: 13,
                  color: b.impact_pct > 50 ? '#ff4d4f' : b.impact_pct > 0 ? '#faad14' : '#52c41a',
                }}>
                  {b.impact_pct > 0 ? '+' : ''}{b.impact_pct}%
                </span>
              </div>
            ))}
            {(!impactData.blast_radius || impactData.blast_radius.length === 0) && (
              <Empty description="无受影响服务" />
            )}

            {/* Related Alerts */}
            <div style={{ marginTop: 16 }}>
              <Text strong>关联告警: </Text>
              <Tag color={impactData.correlated_alerts > 0 ? '#ff4d4f' : '#52c41a'}>
                {impactData.correlated_alerts} 个
              </Tag>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};

export default ChangeTimeline;
