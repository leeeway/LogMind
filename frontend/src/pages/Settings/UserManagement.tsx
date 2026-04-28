import React, { useEffect, useState } from 'react';
import { Table, Button, Tag, Space, Modal, Form, Input, Select, message, Typography, Switch } from 'antd';
import { UserAddOutlined, ReloadOutlined } from '@ant-design/icons';
import { usersApi } from '@/api/users';
import { useAuthStore } from '@/stores/authStore';
import dayjs from 'dayjs';

const { Title } = Typography;

const roleColors: Record<string, string> = { admin: '#722ed1', operator: '#1677ff', analyst: '#1677ff', viewer: '#8c8c8c' };

const UserManagement: React.FC = () => {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [form] = Form.useForm();
  const tenantId = useAuthStore((s) => s.user?.tenant_id || '');

  const load = async () => {
    if (!tenantId) return;
    setLoading(true);
    try {
      const { data } = await usersApi.list(tenantId, { page_size: 100 });
      setUsers(data?.items || []);
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [tenantId]);

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      await usersApi.create(tenantId, values);
      message.success('用户创建成功');
      setFormOpen(false);
      form.resetFields();
      load();
    } catch (err: any) {
      if (err.response?.data?.detail) message.error(err.response.data.detail);
    }
  };

  const columns = [
    {
      title: '用户名', dataIndex: 'username', key: 'username',
      render: (v: string) => <span style={{ fontWeight: 600, color: 'var(--lm-text)' }}>{v}</span>,
    },
    { title: '邮箱', dataIndex: 'email', key: 'email', ellipsis: true },
    {
      title: '角色', dataIndex: 'role', key: 'role',
      render: (v: string) => <Tag color={roleColors[v] || '#8c8c8c'} style={{ borderRadius: 4 }}>{v}</Tag>,
    },
    {
      title: '状态', dataIndex: 'is_active', key: 'is_active',
      render: (v: boolean) => <Tag color={v ? '#52c41a' : '#ff4d4f'}>{v ? '活跃' : '禁用'}</Tag>,
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at',
      render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm'),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={5} style={{ margin: 0, color: 'var(--lm-text)' }}>用户管理</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<UserAddOutlined />} onClick={() => setFormOpen(true)}>添加用户</Button>
        </Space>
      </div>

      <Table
        dataSource={users}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={false}
      />

      <Modal
        title="添加用户"
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        onOk={handleCreate}
        okText="创建"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="username" />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}>
            <Input placeholder="user@example.com" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: 6, message: '密码至少6位' }]}>
            <Input.Password placeholder="••••••" />
          </Form.Item>
          <Form.Item name="role" label="角色" initialValue="viewer">
            <Select options={[
              { value: 'admin', label: '管理员' },
              { value: 'analyst', label: '分析员' },
              { value: 'viewer', label: '查看者' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default UserManagement;
