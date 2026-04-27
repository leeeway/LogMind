import React, { useEffect, useState } from 'react';
import { Card, Tabs, Table, Tag, Button, Space, Typography, Modal, Form, Input, Select, message, Descriptions, Badge } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, HeartOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons';
import { providerApi, promptApi, systemApi } from '@/api/services';

const { Title, Text, Paragraph } = Typography;

const Settings: React.FC = () => {
  // Providers
  const [providers, setProviders] = useState<any[]>([]);
  const [providerHealth, setProviderHealth] = useState<any[]>([]);
  const [provLoading, setProvLoading] = useState(false);
  const [provFormOpen, setProvFormOpen] = useState(false);
  const [provForm] = Form.useForm();
  const [registeredProviders, setRegisteredProviders] = useState<string[]>([]);

  // Prompts
  const [prompts, setPrompts] = useState<any[]>([]);
  const [promptFormOpen, setPromptFormOpen] = useState(false);
  const [promptForm] = Form.useForm();
  const [editPromptId, setEditPromptId] = useState<string | null>(null);

  // System
  const [systemHealth, setSystemHealth] = useState<any>(null);

  const fetchProviders = async () => {
    setProvLoading(true);
    try {
      const [pRes, hRes, rRes] = await Promise.all([
        providerApi.list(),
        providerApi.health().catch(() => ({ data: [] })),
        providerApi.getRegistered().catch(() => ({ data: { providers: [] } })),
      ]);
      setProviders(pRes.data?.items || []);
      setProviderHealth(hRes.data || []);
      setRegisteredProviders(rRes.data?.providers || []);
    } catch (err) { console.error(err); }
    finally { setProvLoading(false); }
  };

  const fetchPrompts = async () => {
    try {
      const { data } = await promptApi.list();
      setPrompts(data?.items || []);
    } catch (err) { console.error(err); }
  };

  const fetchHealth = async () => {
    try {
      const { data } = await systemApi.health();
      setSystemHealth(data);
    } catch (err) { console.error(err); }
  };

  useEffect(() => { fetchProviders(); fetchPrompts(); fetchHealth(); }, []);

  const handleSaveProvider = async (values: any) => {
    try {
      await providerApi.create(values);
      message.success('Provider 已创建');
      setProvFormOpen(false);
      provForm.resetFields();
      fetchProviders();
    } catch (err: any) { message.error(err.response?.data?.detail || '创建失败'); }
  };

  const handleDeleteProvider = async (id: string) => {
    try { await providerApi.remove(id); message.success('已删除'); fetchProviders(); }
    catch { message.error('删除失败'); }
  };

  const handleSavePrompt = async (values: any) => {
    try {
      if (editPromptId) {
        await promptApi.update(editPromptId, values);
        message.success('模板已更新');
      } else {
        await promptApi.create(values);
        message.success('模板已创建');
      }
      setPromptFormOpen(false);
      promptForm.resetFields();
      setEditPromptId(null);
      fetchPrompts();
    } catch (err: any) { message.error(err.response?.data?.detail || '保存失败'); }
  };

  const providerColumns = [
    { title: '名称', dataIndex: 'name', width: 140 },
    { title: 'Provider', dataIndex: 'provider_type', width: 120 },
    { title: '模型', dataIndex: 'model_name', width: 150 },
    {
      title: '状态', dataIndex: 'is_active', width: 80,
      render: (v: boolean) => <Tag color={v ? '#52c41a' : '#8c8c8c'}>{v ? '启用' : '禁用'}</Tag>,
    },
    {
      title: '健康', width: 80,
      render: (_: any, r: any) => {
        const h = providerHealth.find((p: any) => p.provider_id === r.id);
        if (!h) return <Badge status="default" text="未知" />;
        return h.is_healthy ? <Badge status="success" text="正常" /> : <Badge status="error" text="异常" />;
      },
    },
    {
      title: '操作', width: 80,
      render: (_: any, r: any) => (
        <Button size="small" danger icon={<DeleteOutlined />}
          onClick={() => Modal.confirm({ title: '确认删除?', onOk: () => handleDeleteProvider(r.id) })} />
      ),
    },
  ];

  const promptColumns = [
    { title: '名称', dataIndex: 'name', width: 160 },
    { title: '类型', dataIndex: 'template_type', width: 100 },
    { title: '语言', dataIndex: 'language', width: 80 },
    {
      title: '操作', width: 100,
      render: (_: any, r: any) => (
        <Button size="small" icon={<EditOutlined />} onClick={() => {
          setEditPromptId(r.id);
          promptForm.setFieldsValue(r);
          setPromptFormOpen(true);
        }}>编辑</Button>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <Title level={4} style={{ margin: '0 0 20px', color: 'var(--lm-text)' }}>
        <SettingOutlined style={{ marginRight: 8 }} />系统设置
      </Title>

      <Card style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}>
        <Tabs items={[
          {
            key: 'providers', label: 'AI Provider',
            children: (
              <>
                <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setProvFormOpen(true)}>添加 Provider</Button>
                  <Button icon={<ReloadOutlined />} onClick={fetchProviders} />
                </div>
                <Table dataSource={providers} columns={providerColumns} rowKey="id" size="small" loading={provLoading} pagination={false} />
              </>
            ),
          },
          {
            key: 'prompts', label: 'Prompt 模板',
            children: (
              <>
                <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'flex-end' }}>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditPromptId(null); promptForm.resetFields(); setPromptFormOpen(true); }}>添加模板</Button>
                </div>
                <Table dataSource={prompts} columns={promptColumns} rowKey="id" size="small" pagination={false} />
              </>
            ),
          },
          {
            key: 'health', label: '系统健康',
            children: systemHealth ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 16 }}>
                {Object.entries(systemHealth.components || {}).map(([name, comp]: any) => (
                  <div key={name} style={{
                    padding: 16,
                    background: 'var(--lm-bg-elevated)',
                    borderRadius: 8,
                    border: '1px solid var(--lm-border-light)',
                    textAlign: 'center',
                  }}>
                    <Badge status={comp.status === 'up' ? 'success' : 'error'} />
                    <Text style={{ marginLeft: 8, color: 'var(--lm-text)' }}>{name}</Text>
                    <div style={{ marginTop: 4 }}>
                      <Tag color={comp.status === 'up' ? '#52c41a' : '#ff4d4f'} style={{ borderRadius: 4 }}>{comp.status}</Tag>
                    </div>
                  </div>
                ))}
              </div>
            ) : <Text>加载中...</Text>,
          },
        ]} />
      </Card>

      {/* Provider Form */}
      <Modal title="添加 AI Provider" open={provFormOpen} onCancel={() => setProvFormOpen(false)} footer={null} destroyOnClose>
        <Form form={provForm} layout="vertical" onFinish={handleSaveProvider}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="provider_type" label="Provider 类型" rules={[{ required: true }]}>
            <Select options={registeredProviders.map(p => ({ value: p, label: p }))} placeholder="选择 Provider" />
          </Form.Item>
          <Form.Item name="model_name" label="模型名称" rules={[{ required: true }]}><Input placeholder="gpt-4o / deepseek-chat" /></Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: true }]}><Input.Password /></Form.Item>
          <Form.Item name="api_base" label="API Base URL"><Input placeholder="可选" /></Form.Item>
          <Form.Item><Button type="primary" htmlType="submit" block>保存</Button></Form.Item>
        </Form>
      </Modal>

      {/* Prompt Form */}
      <Modal title={editPromptId ? '编辑 Prompt 模板' : '添加 Prompt 模板'} open={promptFormOpen} onCancel={() => { setPromptFormOpen(false); setEditPromptId(null); }} footer={null} width={700} destroyOnClose>
        <Form form={promptForm} layout="vertical" onFinish={handleSavePrompt}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="template_type" label="类型" initialValue="analysis"><Select options={[{ value: 'analysis', label: 'Analysis' }, { value: 'alert', label: 'Alert' }]} /></Form.Item>
          <Form.Item name="system_prompt" label="System Prompt" rules={[{ required: true }]}><Input.TextArea rows={4} /></Form.Item>
          <Form.Item name="user_prompt_template" label="User Prompt 模板" rules={[{ required: true }]}><Input.TextArea rows={6} /></Form.Item>
          <Form.Item name="language" label="语言" initialValue="zh"><Select options={[{ value: 'zh', label: '中文' }, { value: 'en', label: 'English' }]} /></Form.Item>
          <Form.Item><Button type="primary" htmlType="submit" block>保存</Button></Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default Settings;
