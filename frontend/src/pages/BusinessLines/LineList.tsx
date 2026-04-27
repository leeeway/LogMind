import React, { useEffect, useState } from 'react';
import { Card, Table, Tag, Button, Space, Typography, Switch, Modal, Form, Input, Select, message } from 'antd';
import { PlusOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons';
import { businessLineApi } from '@/api/services';

const { Title } = Typography;

const BusinessLines: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [lines, setLines] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [formOpen, setFormOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form] = Form.useForm();

  const fetchData = async () => {
    setLoading(true);
    try {
      const { data } = await businessLineApi.list({ page_size: 100 });
      setLines(data.items || []);
      setTotal(data.total || 0);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchData(); }, []);

  const handleToggleAI = async (id: string, enabled: boolean) => {
    try {
      await businessLineApi.update(id, { ai_enabled: enabled });
      message.success(enabled ? 'AI 已启用' : 'AI 已禁用');
      fetchData();
    } catch { message.error('操作失败'); }
  };

  const handleSave = async (values: any) => {
    try {
      if (editId) {
        await businessLineApi.update(editId, values);
        message.success('更新成功');
      } else {
        await businessLineApi.create(values);
        message.success('创建成功');
      }
      setFormOpen(false);
      form.resetFields();
      setEditId(null);
      fetchData();
    } catch (err: any) { message.error(err.response?.data?.detail || '保存失败'); }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', width: 160 },
    { title: 'ES 索引', dataIndex: 'es_index_pattern', width: 200, ellipsis: true },
    { title: '语言', dataIndex: 'language', width: 80 },
    {
      title: 'AI', dataIndex: 'ai_enabled', width: 80,
      render: (v: boolean, r: any) => <Switch checked={v} onChange={(checked) => handleToggleAI(r.id, checked)} size="small" />,
    },
    { title: 'Webhook', dataIndex: 'webhook_url', ellipsis: true, render: (v: string) => v || <span style={{ color: 'var(--lm-text-tertiary)' }}>未配置</span> },
    {
      title: '操作', width: 80,
      render: (_: any, r: any) => (
        <Button size="small" icon={<EditOutlined />} onClick={() => { setEditId(r.id); form.setFieldsValue(r); setFormOpen(true); }}>编辑</Button>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>服务管理</Title>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditId(null); form.resetFields(); setFormOpen(true); }}>添加服务</Button>
          <Button icon={<ReloadOutlined />} onClick={fetchData} />
        </Space>
      </div>

      <Card style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table dataSource={lines} columns={columns} rowKey="id" size="small" loading={loading} pagination={false} />
      </Card>

      <Modal title={editId ? '编辑服务' : '添加服务'} open={formOpen} onCancel={() => { setFormOpen(false); setEditId(null); }} footer={null} destroyOnClose>
        <Form form={form} layout="vertical" onFinish={handleSave}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="es_index_pattern" label="ES 索引" rules={[{ required: true }]}><Input placeholder="app-logs-*" /></Form.Item>
          <Form.Item name="language" label="语言" initialValue="zh"><Select options={[{ value: 'zh', label: '中文' }, { value: 'en', label: 'English' }]} /></Form.Item>
          <Form.Item name="webhook_url" label="Webhook URL"><Input placeholder="https://..." /></Form.Item>
          <Form.Item name="severity_threshold" label="严重度阈值" initialValue="error">
            <Select options={[{ value: 'critical', label: 'Critical' }, { value: 'error', label: 'Error' }, { value: 'warning', label: 'Warning' }]} />
          </Form.Item>
          <Form.Item><Button type="primary" htmlType="submit" block>保存</Button></Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default BusinessLines;
