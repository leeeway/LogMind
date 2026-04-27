import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, Tag, Button, Select, Space, Typography, Card, Input, DatePicker, Modal, Form, message } from 'antd';
import { PlusOutlined, ReloadOutlined, SwapOutlined, EyeOutlined } from '@ant-design/icons';
import { analysisApi } from '@/api/analysis';
import { businessLineApi } from '@/api/services';
import dayjs from 'dayjs';

const { Title } = Typography;
const { RangePicker } = DatePicker;

const statusMap: Record<string, { color: string; label: string }> = {
  completed: { color: '#52c41a', label: '完成' },
  running: { color: '#1677ff', label: '运行中' },
  failed: { color: '#ff4d4f', label: '失败' },
  pending: { color: '#8c8c8c', label: '等待' },
};

const TaskList: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [tasks, setTasks] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [bizLines, setBizLines] = useState<any[]>([]);
  const [bizFilter, setBizFilter] = useState<string | undefined>();
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [triggerLoading, setTriggerLoading] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await analysisApi.listTasks({
        page,
        page_size: 15,
        status: statusFilter,
        business_line_id: bizFilter,
      });
      setTasks(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, bizFilter]);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  useEffect(() => {
    businessLineApi.list({ page_size: 100 }).then(res => {
      setBizLines(res.data?.items || []);
    });
  }, []);

  const handleTrigger = async (values: any) => {
    setTriggerLoading(true);
    try {
      const [from, to] = values.timeRange;
      await analysisApi.createTask({
        business_line_id: values.business_line_id,
        time_from: from.toISOString(),
        time_to: to.toISOString(),
        severity: values.severity,
      });
      message.success('分析任务已创建');
      setTriggerOpen(false);
      fetchTasks();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败');
    } finally {
      setTriggerLoading(false);
    }
  };

  const columns = [
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => {
        const m = statusMap[s] || { color: '#8c8c8c', label: s };
        return <Tag color={m.color} style={{ borderRadius: 4 }}>{m.label}</Tag>;
      },
    },
    { title: '类型', dataIndex: 'task_type', width: 80 },
    {
      title: '日志数',
      dataIndex: 'log_count',
      width: 90,
      render: (v: number) => v?.toLocaleString(),
    },
    {
      title: 'Token',
      dataIndex: 'token_usage',
      width: 90,
      render: (v: number) => v?.toLocaleString(),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 150,
      render: (v: string) => v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '-',
    },
    {
      title: '完成时间',
      dataIndex: 'completed_at',
      width: 150,
      render: (v: string) => v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '-',
    },
    {
      title: '操作',
      width: 100,
      render: (_: any, record: any) => (
        <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => navigate(`/analysis/${record.id}`)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>分析任务</Title>
        <Space>
          <Button icon={<SwapOutlined />} onClick={() => setCompareOpen(true)}>对比分析</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setTriggerOpen(true)}>触发分析</Button>
          <Button icon={<ReloadOutlined />} onClick={fetchTasks} />
        </Space>
      </div>

      <Card
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ body: { padding: 0 } }}
      >
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--lm-border-light)', display: 'flex', gap: 12 }}>
          <Select
            allowClear
            placeholder="状态"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'completed', label: '完成' },
              { value: 'running', label: '运行中' },
              { value: 'failed', label: '失败' },
              { value: 'pending', label: '等待' },
            ]}
            style={{ width: 120 }}
          />
          <Select
            allowClear
            showSearch
            placeholder="业务线"
            value={bizFilter}
            onChange={setBizFilter}
            options={bizLines.map(b => ({ value: b.id, label: b.name }))}
            filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
            style={{ width: 200 }}
          />
        </div>
        <Table
          dataSource={tasks}
          columns={columns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ current: page, total, pageSize: 15, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
        />
      </Card>

      {/* Trigger Analysis Modal */}
      <Modal title="触发手动分析" open={triggerOpen} onCancel={() => setTriggerOpen(false)} footer={null} destroyOnClose>
        <Form layout="vertical" onFinish={handleTrigger}>
          <Form.Item name="business_line_id" label="业务线" rules={[{ required: true }]}>
            <Select placeholder="选择业务线" options={bizLines.map(b => ({ value: b.id, label: b.name }))} />
          </Form.Item>
          <Form.Item name="timeRange" label="时间范围" rules={[{ required: true }]}>
            <RangePicker showTime style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="severity" label="日志级别" initialValue="error">
            <Select options={[
              { value: 'error', label: 'Error' },
              { value: 'warning', label: 'Warning' },
              { value: 'critical', label: 'Critical' },
            ]} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={triggerLoading} block>创建分析任务</Button>
          </Form.Item>
        </Form>
      </Modal>

      {/* Compare Modal */}
      <Modal title="分析对比" open={compareOpen} onCancel={() => setCompareOpen(false)} footer={null} destroyOnClose>
        <Form layout="vertical" onFinish={(v) => { setCompareOpen(false); navigate(`/analysis/compare?a=${v.task_a}&b=${v.task_b}`); }}>
          <Form.Item name="task_a" label="基线任务 ID (A)" rules={[{ required: true }]}>
            <Input placeholder="输入 Task A UUID" />
          </Form.Item>
          <Form.Item name="task_b" label="当前任务 ID (B)" rules={[{ required: true }]}>
            <Input placeholder="输入 Task B UUID" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block icon={<SwapOutlined />}>开始对比</Button>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default TaskList;
