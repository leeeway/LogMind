import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, Tag, Button, Space, Typography, Card, Modal, Form, Input, Select, Switch, message, Tooltip, Progress } from 'antd';
import { PlusOutlined, EditOutlined, ReloadOutlined, ClusterOutlined, ApiOutlined, ExperimentOutlined } from '@ant-design/icons';
import { businessLineApi } from '@/api/services';

const { Title, Text } = Typography;

const languageLabels: Record<string, string> = {
  java: 'Java', csharp: 'C#', python: 'Python', go: 'Go', other: '通用',
};

const BusinessLineList: React.FC = () => {
  const navigate = useNavigate();
  const [lines, setLines] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form] = Form.useForm();

  const fetchLines = async () => {
    setLoading(true);
    try {
      const { data } = await businessLineApi.list({ page_size: 100 });
      setLines(data?.items || []);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchLines(); }, []);

  const handleSave = async (values: any) => {
    try {
      if (editId) {
        await businessLineApi.update(editId, values);
        message.success('服务已更新');
      } else {
        await businessLineApi.create(values);
        message.success('服务已创建');
      }
      setFormOpen(false);
      form.resetFields();
      setEditId(null);
      fetchLines();
    } catch (err: any) { message.error(err.response?.data?.detail || '保存失败'); }
  };

  const toggleAI = async (record: any) => {
    try {
      await businessLineApi.update(record.id, { ai_enabled: !record.ai_enabled });
      message.success(record.ai_enabled ? 'AI 分析已关闭' : 'AI 分析已开启');
      fetchLines();
    } catch { message.error('操作失败'); }
  };

  const openEdit = (record: any) => {
    setEditId(record.id);
    form.setFieldsValue(record);
    setFormOpen(true);
  };

  const columns = [
    {
      title: '服务名称', dataIndex: 'name', width: 160,
      render: (name: string, r: any) => (
        <Space>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: r.is_active ? '#52c41a' : '#8c8c8c', display: 'inline-block' }} />
          <a onClick={() => navigate(`/business-lines/${r.id}`)} style={{ color: 'var(--lm-text)', fontWeight: 600 }}>{name}</a>
        </Space>
      ),
    },
    {
      title: '语言', dataIndex: 'language', width: 80,
      render: (v: string) => <Tag style={{ borderRadius: 4 }}>{languageLabels[v] || v}</Tag>,
    },
    {
      title: 'ES 索引', dataIndex: 'es_index_pattern', width: 180, ellipsis: true,
      render: (v: string) => <code style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>{v}</code>,
    },
    {
      title: '权重', dataIndex: 'business_weight', width: 80,
      render: (v: number, r: any) => (
        <Space size={4}>
          <span style={{ fontWeight: 600, color: v >= 8 ? '#ff4d4f' : v >= 5 ? '#faad14' : 'var(--lm-text-tertiary)' }}>{v}</span>
          {r.is_core_path && <Tag color="#ff4d4f" style={{ borderRadius: 4, fontSize: 10 }}>核心</Tag>}
        </Space>
      ),
    },
    {
      title: 'DAU', dataIndex: 'estimated_dau', width: 90,
      render: (v: number) => v ? v.toLocaleString() : '-',
    },
    {
      title: '夜间策略', dataIndex: 'night_policy', width: 100,
      render: (v: string) => {
        const map: Record<string, { label: string; color: string }> = {
          always: { label: '始终通知', color: '#ff4d4f' },
          p0_only: { label: 'P0 通知', color: '#fa8c16' },
          silent: { label: '静默', color: '#52c41a' },
        };
        const m = map[v] || { label: v, color: '#8c8c8c' };
        return <Tag color={m.color} style={{ borderRadius: 4, fontSize: 11 }}>{m.label}</Tag>;
      },
    },
    {
      title: '通知级别', dataIndex: 'min_notify_priority', width: 110,
      render: (v: string) => {
        const map: Record<string, { label: string; color: string }> = {
          default: { label: '跟随全局', color: 'default' },
          P0: { label: '仅 P0', color: 'red' },
          P1: { label: 'P0 + P1', color: 'orange' },
          P2: { label: '全部通知', color: 'green' },
        };
        const m = map[v] || { label: '跟随全局', color: 'default' };
        return <Tag color={m.color} style={{ borderRadius: 4, fontSize: 11 }}>{m.label}</Tag>;
      },
    },
    {
      title: 'AI', dataIndex: 'ai_enabled', width: 60,
      render: (v: boolean, r: any) => (
        <Tooltip title={v ? '关闭 AI 分析' : '开启 AI 分析'}>
          <Switch size="small" checked={v} onChange={() => toggleAI(r)} />
        </Tooltip>
      ),
    },
    {
      title: '操作', width: 60,
      render: (_: any, r: any) => (
        <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Space>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
            <ClusterOutlined style={{ marginRight: 8 }} />服务管理
          </Title>
          <Tag color="blue">{lines.length} 个服务</Tag>
          <Tag color={lines.filter(l => l.ai_enabled).length > 0 ? '#52c41a' : '#8c8c8c'}>
            <ExperimentOutlined /> {lines.filter(l => l.ai_enabled).length} AI 启用
          </Tag>
        </Space>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditId(null); form.resetFields(); setFormOpen(true); }}>添加服务</Button>
          <Button icon={<ReloadOutlined />} onClick={fetchLines} />
        </Space>
      </div>

      <Card style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table dataSource={lines} columns={columns} rowKey="id" size="small" loading={loading} pagination={false} />
      </Card>

      {/* Form Modal */}
      <Modal title={editId ? '编辑服务' : '添加服务'} open={formOpen} onCancel={() => { setFormOpen(false); setEditId(null); }} footer={null} width={600} destroyOnClose>
        <Form form={form} layout="vertical" onFinish={handleSave}>
          <Form.Item name="name" label="服务名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="es_index_pattern" label="ES 索引模式" rules={[{ required: true }]}><Input placeholder="e.g. prod-webserver-*" /></Form.Item>
          <Form.Item name="description" label="描述"><Input.TextArea rows={2} /></Form.Item>
          <Space style={{ width: '100%' }} size={16}>
            <Form.Item name="language" label="语言" initialValue="java" style={{ flex: 1 }}>
              <Select options={Object.entries(languageLabels).map(([k, v]) => ({ value: k, label: v }))} />
            </Form.Item>
            <Form.Item name="severity_threshold" label="日志级别" initialValue="error" style={{ flex: 1 }}>
              <Select options={[
                { value: 'error', label: 'Error' },
                { value: 'warning', label: 'Warning' },
                { value: 'critical', label: 'Critical' },
              ]} />
            </Form.Item>
          </Space>
          <Space style={{ width: '100%' }} size={16}>
            <Form.Item name="business_weight" label="权重 (1-10)" initialValue={5} style={{ flex: 1 }}>
              <Select options={[1,2,3,4,5,6,7,8,9,10].map(n => ({ value: n, label: `${n}` }))} />
            </Form.Item>
            <Form.Item name="estimated_dau" label="预估 DAU" initialValue={0} style={{ flex: 1 }}>
              <Input type="number" />
            </Form.Item>
          </Space>
          <Space style={{ width: '100%' }} size={16}>
            <Form.Item name="night_policy" label="夜间策略" initialValue="p0_only" style={{ flex: 1 }}>
              <Select options={[
                { value: 'always', label: '始终通知' },
                { value: 'p0_only', label: '仅 P0 通知' },
                { value: 'silent', label: '完全静默' },
              ]} />
            </Form.Item>
            <Form.Item name="min_notify_priority" label="通知级别" initialValue="default" style={{ flex: 1 }}>
              <Select options={[
                { value: 'default', label: '跟随全局' },
                { value: 'P0', label: '仅 P0 (严重)' },
                { value: 'P1', label: 'P0 + P1 (警告)' },
                { value: 'P2', label: '全部通知' },
              ]} />
            </Form.Item>
          </Space>
          <Space style={{ width: '100%' }} size={16}>
            <Form.Item name="webhook_url" label="Webhook URL" style={{ flex: 3 }}><Input placeholder="可选 - WeChat/DingTalk/Feishu Webhook" /></Form.Item>
            <Form.Item name="is_core_path" label="核心路径" valuePropName="checked" initialValue={false} style={{ flex: 1, marginTop: 30 }}>
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item><Button type="primary" htmlType="submit" block>保存</Button></Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default BusinessLineList;
