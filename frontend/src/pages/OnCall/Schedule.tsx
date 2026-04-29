import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Space, Button, Tag, Table, Modal, Form, Input, Select,
  DatePicker, Row, Col, Empty, message, Spin, Badge, Tooltip,
} from 'antd';
import {
  TeamOutlined, PlusOutlined, ReloadOutlined, UserOutlined,
  SwapOutlined, PhoneOutlined, ClockCircleOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons';
import client from '@/api/client';
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const roleColors: Record<string, string> = {
  primary: '#1677ff', backup: '#faad14', manager: '#722ed1',
};
const roleLabels: Record<string, string> = {
  primary: '主值班', backup: '副值班', manager: '经理',
};

const Schedule: React.FC = () => {
  const [schedules, setSchedules] = useState<any[]>([]);
  const [currentOnCall, setCurrentOnCall] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [bizLines, setBizLines] = useState<any[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [schedRes, currentRes, bizRes] = await Promise.all([
        client.get('/oncall/schedules'),
        client.get('/oncall/current'),
        client.get('/business-lines').catch(() => ({ data: [] })),
      ]);
      setSchedules(schedRes.data || []);
      setCurrentOnCall(currentRes.data || []);
      setBizLines(Array.isArray(bizRes.data) ? bizRes.data : bizRes.data?.items || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async (values: any) => {
    try {
      const [start, end] = values.time_range;
      await client.post('/oncall/schedules', {
        business_line_id: values.business_line_id,
        user_name: values.user_name,
        user_contact: values.user_contact || '',
        role: values.role,
        start_time: start.toISOString(),
        end_time: end.toISOString(),
      });
      message.success('排班已创建');
      setCreateOpen(false);
      form.resetFields();
      load();
    } catch { message.error('创建失败'); }
  };

  const handleDelete = async (id: string) => {
    try {
      await client.delete(`/oncall/schedules/${id}`);
      message.success('已删除');
      load();
    } catch { message.error('删除失败'); }
  };

  const columns = [
    {
      title: '服务', dataIndex: 'business_line_name', key: 'biz',
      render: (v: string) => <Text strong style={{ color: 'var(--lm-text)' }}>{v}</Text>,
    },
    {
      title: '值班人', dataIndex: 'user_name', key: 'user',
      render: (v: string, r: any) => (
        <Space>
          <UserOutlined />
          <span>{v}</span>
          {r.is_override && <Tag color="#fa8c16" style={{ borderRadius: 3, fontSize: 10 }}>临时换班</Tag>}
        </Space>
      ),
    },
    {
      title: '角色', dataIndex: 'role', key: 'role',
      render: (v: string) => <Tag color={roleColors[v]} style={{ borderRadius: 4 }}>{roleLabels[v] || v}</Tag>,
    },
    {
      title: '开始时间', dataIndex: 'start_time', key: 'start',
      render: (v: string) => <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{dayjs(v).format('MM-DD HH:mm')}</Text>,
    },
    {
      title: '结束时间', dataIndex: 'end_time', key: 'end',
      render: (v: string) => <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{dayjs(v).format('MM-DD HH:mm')}</Text>,
    },
    {
      title: '状态', key: 'status',
      render: (_: any, r: any) => {
        const now = dayjs();
        const start = dayjs(r.start_time);
        const end = dayjs(r.end_time);
        if (now.isAfter(end)) return <Tag>已结束</Tag>;
        if (now.isBefore(start)) return <Tag color="blue">待开始</Tag>;
        return <Tag color="green">值班中</Tag>;
      },
    },
    {
      title: '操作', key: 'action', width: 80,
      render: (_: any, r: any) => (
        <Button size="small" danger type="link" onClick={() => handleDelete(r.id)}>删除</Button>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <TeamOutlined style={{ marginRight: 8 }} />值班排班
        </Title>
        <Space>
          <Button icon={<PlusOutlined />} type="primary" onClick={() => setCreateOpen(true)}>
            新增排班
          </Button>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </Space>
      </div>

      {/* Current On-Call Cards */}
      <Row gutter={12} style={{ marginBottom: 16 }}>
        {currentOnCall.map((item: any) => (
          <Col span={8} key={item.business_line_id}>
            <Card
              size="small"
              title={<Space><SafetyCertificateOutlined />{item.business_line_name}</Space>}
              style={{
                background: 'var(--lm-bg-card)',
                border: '1px solid var(--lm-border-light)',
                borderRadius: 12,
              }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
            >
              {['primary', 'backup', 'manager'].map(role => {
                const person = (item as any)[role];
                return (
                  <div key={role} style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
                    borderBottom: '1px solid var(--lm-border-light)',
                  }}>
                    <Tag color={roleColors[role]} style={{ borderRadius: 3, margin: 0, minWidth: 52, textAlign: 'center' }}>
                      {roleLabels[role]}
                    </Tag>
                    {person ? (
                      <Space size={4}>
                        <UserOutlined style={{ fontSize: 12 }} />
                        <Text style={{ fontSize: 13, color: 'var(--lm-text)' }}>{person.user_name}</Text>
                        {person.is_override && (
                          <Tooltip title="临时换班">
                            <SwapOutlined style={{ color: '#fa8c16', fontSize: 11 }} />
                          </Tooltip>
                        )}
                      </Space>
                    ) : (
                      <Text type="secondary" style={{ fontSize: 12 }}>未排班</Text>
                    )}
                  </div>
                );
              })}
            </Card>
          </Col>
        ))}
        {currentOnCall.length === 0 && !loading && (
          <Col span={24}>
            <Empty description="暂无排班数据" style={{ padding: 40 }} />
          </Col>
        )}
      </Row>

      {/* Schedule Table */}
      <Card
        title="排班列表"
        size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ body: { padding: 0 }, header: { borderBottom: '1px solid var(--lm-border-light)' } }}
      >
        <Table
          dataSource={schedules}
          columns={columns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
        />
      </Card>

      {/* Create Modal */}
      <Modal
        title="新增排班"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="创建"
      >
        <Form form={form} onFinish={handleCreate} layout="vertical">
          <Form.Item name="business_line_id" label="服务" rules={[{ required: true }]}>
            <Select
              placeholder="选择服务"
              options={bizLines.map((b: any) => ({ value: b.id, label: b.name }))}
            />
          </Form.Item>
          <Form.Item name="user_name" label="值班人" rules={[{ required: true }]}>
            <Input prefix={<UserOutlined />} placeholder="姓名" />
          </Form.Item>
          <Form.Item name="user_contact" label="联系方式">
            <Input prefix={<PhoneOutlined />} placeholder="Webhook URL 或手机号" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={[
              { value: 'primary', label: '🔵 主值班' },
              { value: 'backup', label: '🟡 副值班' },
              { value: 'manager', label: '🟣 经理' },
            ]} />
          </Form.Item>
          <Form.Item name="time_range" label="值班时间" rules={[{ required: true }]}>
            <RangePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default Schedule;
