import React, { useEffect, useState, useCallback } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Tabs, message, Badge, Tooltip,
  Select, Popconfirm, Row, Col, Drawer, Descriptions, Modal, Form, Input, Spin,
} from 'antd';
import {
  CheckOutlined, CheckCircleOutlined, ReloadOutlined, AlertOutlined,
  FilterOutlined, BellOutlined, PlusOutlined, EyeOutlined,
  FireOutlined, ExclamationCircleOutlined, BulbOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { useQuickDiagnose } from '@/components/QuickDiagnose';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { alertsApi } from '@/api/alerts';
import client from '@/api/client';
import { businessLineApi } from '@/api/services';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

const { Title, Text, Paragraph } = Typography;

const priorityColors: Record<string, string> = { P0: '#ff4d4f', P1: '#fa8c16', P2: '#fadb14', P3: '#8c8c8c' };
const statusLabels: Record<string, { color: string; label: string }> = {
  fired: { color: '#ff4d4f', label: '触发中' },
  acknowledged: { color: '#fa8c16', label: '已确认' },
  resolved: { color: '#52c41a', label: '已解决' },
};

const AlertList: React.FC = () => {
  const quickDiagnose = useQuickDiagnose();
  const [loading, setLoading] = useState(false);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [rules, setRules] = useState<any[]>([]);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [priorityFilter, setPriorityFilter] = useState<string | undefined>(undefined);
  const [bizLines, setBizLines] = useState<any[]>([]);

  // Detail Drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailAlert, setDetailAlert] = useState<any>(null);

  // Smart Context
  const [alertContext, setAlertContext] = useState<any>(null);
  const [contextLoading, setContextLoading] = useState(false);

  // Auto-load context when drawer opens
  useEffect(() => {
    if (drawerOpen && detailAlert?.id) {
      setContextLoading(true);
      client.get(`/alerts/${detailAlert.id}/context`)
        .then(res => setAlertContext(res.data))
        .catch(() => setAlertContext(null))
        .finally(() => setContextLoading(false));
    }
  }, [drawerOpen, detailAlert?.id]);

  // Rule create modal
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [ruleForm] = Form.useForm();

  // Stats
  const allAlerts = alerts;
  const firedCount = allAlerts.filter(a => a.status === 'fired').length;
  const ackedCount = allAlerts.filter(a => a.status === 'acknowledged').length;
  const resolvedCount = allAlerts.filter(a => a.status === 'resolved').length;

  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await alertsApi.listHistory({ page, page_size: 15 });
      let items = data.items || [];
      if (statusFilter) items = items.filter((a: any) => a.status === statusFilter);
      if (priorityFilter) items = items.filter((a: any) => a.priority === priorityFilter);
      setAlerts(items);
      setTotal(data.total || 0);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  }, [page, statusFilter, priorityFilter]);

  const fetchRules = async () => {
    try {
      const { data } = await alertsApi.listRules();
      setRules(data.items || data || []);
    } catch (err) { console.error(err); }
  };

  const fetchBizLines = async () => {
    try {
      const { data } = await businessLineApi.list({ page_size: 100 });
      setBizLines(data?.items || []);
    } catch { /* ignore */ }
  };

  useEffect(() => { fetchAlerts(); fetchRules(); fetchBizLines(); }, [fetchAlerts]);

  const handleAck = async (id: string) => {
    try { await alertsApi.ackAlert(id); message.success('已确认'); fetchAlerts(); }
    catch { message.error('确认失败'); }
  };

  const handleResolve = async (id: string) => {
    try { await alertsApi.resolveAlert(id); message.success('已解决'); fetchAlerts(); }
    catch { message.error('解决失败'); }
  };

  const handleCreateRule = async (values: any) => {
    try {
      await alertsApi.createRule(values);
      message.success('规则已创建');
      setRuleModalOpen(false);
      ruleForm.resetFields();
      fetchRules();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败');
    }
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
    {
      title: '告警信息', dataIndex: 'message', ellipsis: true,
      render: (text: string, r: any) => (
        <a onClick={() => { setDetailAlert(r); setDrawerOpen(true); }} style={{ color: 'var(--lm-text)', display: 'flex', alignItems: 'center', gap: 6 }}>
          {r.alert_type === 'predictive' && (
            <Tooltip title="预测告警 — 趋势上升，尚未触发阈值">
              <Tag color="#722ed1" style={{ borderRadius: 4, fontSize: 10, margin: 0, flexShrink: 0 }}>预测</Tag>
            </Tooltip>
          )}
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{text}</span>
        </a>
      ),
    },
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
      title: '操作', width: 200,
      render: (_: any, r: any) => (
        <Space>
          <Tooltip title="AI 排查">
            <Button size="small" icon={<ThunderboltOutlined />}
              style={{ color: '#722ed1', borderColor: '#722ed133' }}
              onClick={() => quickDiagnose.open({ context: `帮我排查这个告警: ${r.message?.slice(0, 100)}\n严重度: ${r.severity}\n触发时间: ${r.fired_at}`, source: '告警列表' })}
            />
          </Tooltip>
          <Tooltip title="查看详情">
            <Button size="small" icon={<EyeOutlined />} onClick={() => { setDetailAlert(r); setDrawerOpen(true); }} />
          </Tooltip>
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
        </Space>
        <Button icon={<ReloadOutlined />} onClick={fetchAlerts} />
      </div>

      {/* Stats Cards */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={8}>
          <div className="lm-stat-card" onClick={() => { setStatusFilter('fired'); setPage(1); }} style={{ cursor: 'pointer' }}>
            <Space><FireOutlined className="stat-icon" style={{ color: '#ff4d4f' }} /><span className="stat-label">触发中</span></Space>
            <div className="stat-value" style={{ color: '#ff4d4f' }}>{firedCount}</div>
          </div>
        </Col>
        <Col xs={8}>
          <div className="lm-stat-card" onClick={() => { setStatusFilter('acknowledged'); setPage(1); }} style={{ cursor: 'pointer' }}>
            <Space><ExclamationCircleOutlined className="stat-icon" style={{ color: '#fa8c16' }} /><span className="stat-label">已确认</span></Space>
            <div className="stat-value" style={{ color: '#fa8c16' }}>{ackedCount}</div>
          </div>
        </Col>
        <Col xs={8}>
          <div className="lm-stat-card" onClick={() => { setStatusFilter('resolved'); setPage(1); }} style={{ cursor: 'pointer' }}>
            <Space><CheckCircleOutlined className="stat-icon" style={{ color: '#52c41a' }} /><span className="stat-label">已解决</span></Space>
            <div className="stat-value" style={{ color: '#52c41a' }}>{resolvedCount}</div>
          </div>
        </Col>
      </Row>

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
                {/* Enhanced Filters */}
                <div style={{ marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <FilterOutlined style={{ color: 'var(--lm-text-tertiary)' }} />
                  <Select
                    allowClear placeholder="状态" value={statusFilter} onChange={(v) => { setStatusFilter(v); setPage(1); }}
                    style={{ width: 120 }}
                    options={[
                      { value: 'fired', label: '触发中' },
                      { value: 'acknowledged', label: '已确认' },
                      { value: 'resolved', label: '已解决' },
                    ]}
                  />
                  <Select
                    allowClear placeholder="优先级" value={priorityFilter} onChange={(v) => { setPriorityFilter(v); setPage(1); }}
                    style={{ width: 100 }}
                    options={[
                      { value: 'P0', label: '🔴 P0' },
                      { value: 'P1', label: '🟡 P1' },
                      { value: 'P2', label: '🟢 P2' },
                    ]}
                  />
                </div>
                <Table dataSource={alerts} columns={alertColumns} rowKey="id" size="small" loading={loading}
                  pagination={{ current: page, total, pageSize: 15, onChange: setPage, showTotal: (t) => `共 ${t} 条` }} />
              </>
            ),
          },
          {
            key: 'rules',
            label: '告警规则',
            children: (
              <>
                <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'flex-end' }}>
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setRuleModalOpen(true)}>创建规则</Button>
                </div>
                <Table dataSource={rules} columns={ruleColumns} rowKey="id" size="small" pagination={false} />
              </>
            ),
          },
        ]} />
      </Card>

      {/* Smart Alert Card Drawer */}
      <Drawer
        title={
          <Space>
            <AlertOutlined />
            <span>智能告警卡片</span>
            {detailAlert && (
              <Tag color={priorityColors[detailAlert.priority]} style={{ borderRadius: 4, fontWeight: 600 }}>
                {detailAlert.priority}
              </Tag>
            )}
          </Space>
        }
        open={drawerOpen}
        onClose={() => { setDrawerOpen(false); setAlertContext(null); }}
        width={560}
        styles={{ body: { background: 'var(--lm-bg-layout)', padding: 16 } }}
      >
        {detailAlert && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* Basic Info */}
            <Card size="small" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}>
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="优先级">
                  <Tag color={priorityColors[detailAlert.priority]} style={{ fontWeight: 600 }}>{detailAlert.priority}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="严重度">
                  <Tag color={detailAlert.severity === 'critical' ? '#ff4d4f' : '#faad14'}>{detailAlert.severity}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="告警类型">
                  {detailAlert.alert_type === 'predictive' ? (
                    <Tag color="#722ed1" style={{ borderRadius: 4 }}>预测告警</Tag>
                  ) : (
                    <Tag color="#1677ff" style={{ borderRadius: 4 }}>实时告警</Tag>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag color={statusLabels[detailAlert.status]?.color || '#8c8c8c'}>
                    {statusLabels[detailAlert.status]?.label || detailAlert.status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="触发时间">
                  {detailAlert.fired_at ? dayjs(detailAlert.fired_at).format('YYYY-MM-DD HH:mm:ss') : '-'}
                </Descriptions.Item>
                {detailAlert.acked_at && (
                  <Descriptions.Item label="确认时间">
                    {dayjs(detailAlert.acked_at).format('MM-DD HH:mm')} ({detailAlert.acked_by})
                  </Descriptions.Item>
                )}
                {detailAlert.resolved_at && (
                  <Descriptions.Item label="解决时间">
                    {dayjs(detailAlert.resolved_at).format('MM-DD HH:mm')} ({detailAlert.resolved_by})
                  </Descriptions.Item>
                )}
              </Descriptions>
            </Card>

            {/* Alert Message */}
            <Card size="small" title="告警信息" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
              <Paragraph style={{ color: 'var(--lm-text-secondary)', whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.8, margin: 0 }}>
                {detailAlert.message}
              </Paragraph>
            </Card>

            {/* Smart Context Section */}
            {contextLoading ? (
              <div style={{ textAlign: 'center', padding: 24 }}><Spin size="small" /> 加载上下文...</div>
            ) : alertContext && (
              <>
                {/* Frequency Trend */}
                {alertContext.frequency_trend?.length > 0 && (
                  <Card size="small"
                    title={<Space size={4}><FireOutlined style={{ color: '#fa8c16' }} /><span>7 天触发频率</span></Space>}
                    style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
                    styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 60 }}>
                      {alertContext.frequency_trend.map((p: any, i: number) => {
                        const maxFreq = Math.max(...alertContext.frequency_trend.map((t: any) => t.count), 1);
                        const h = Math.max((p.count / maxFreq) * 50, 2);
                        return (
                          <Tooltip key={i} title={`${p.date}: ${p.count} 次`}>
                            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                              <div style={{
                                width: '100%', height: h, borderRadius: 3,
                                background: p.count > maxFreq * 0.7 ? '#ff4d4f' : p.count > 0 ? '#fa8c16' : 'rgba(255,255,255,0.06)',
                                transition: 'height 0.3s',
                              }} />
                              <span style={{ fontSize: 9, color: 'var(--lm-text-tertiary)' }}>{p.date}</span>
                            </div>
                          </Tooltip>
                        );
                      })}
                    </div>
                  </Card>
                )}

                {/* Similar Alerts */}
                {alertContext.similar_alerts?.length > 0 && (
                  <Card size="small"
                    title={<Space size={4}><ExclamationCircleOutlined style={{ color: '#722ed1' }} /><span>相似告警 ({alertContext.total_similar})</span></Space>}
                    style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
                    styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
                  >
                    <div style={{ maxHeight: 160, overflow: 'auto' }}>
                      {alertContext.similar_alerts.map((sa: any, i: number) => (
                        <div key={i} style={{
                          padding: '6px 0',
                          borderBottom: i < alertContext.similar_alerts.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          fontSize: 12,
                        }}>
                          <div style={{ flex: 1, overflow: 'hidden' }}>
                            <Tag color={sa.severity === 'critical' ? '#ff4d4f' : '#faad14'} style={{ borderRadius: 3, fontSize: 10, marginRight: 6 }}>
                              {sa.severity}
                            </Tag>
                            <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 11 }}>
                              {sa.message?.slice(0, 60)}
                            </Text>
                          </div>
                          <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 10, flexShrink: 0, marginLeft: 8 }}>
                            {sa.fired_at ? dayjs(sa.fired_at).fromNow() : ''}
                          </Text>
                        </div>
                      ))}
                    </div>
                  </Card>
                )}

                {/* AI Suggestion */}
                {alertContext.ai_suggestion && (
                  <Card size="small"
                    title={<Space size={4}><BulbOutlined style={{ color: '#52c41a' }} /><span>AI 处置建议</span></Space>}
                    style={{ background: 'var(--lm-bg-card)', border: '1px solid rgba(82,196,26,0.15)', borderRadius: 10 }}
                    styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
                  >
                    <div className="lm-markdown-content" style={{ fontSize: 13, lineHeight: 1.7 }}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{alertContext.ai_suggestion}</ReactMarkdown>
                    </div>
                  </Card>
                )}
              </>
            )}

            {/* Actions */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {detailAlert.status === 'fired' && (
                <Button icon={<CheckOutlined />} onClick={() => { handleAck(detailAlert.id); setDetailAlert({ ...detailAlert, status: 'acknowledged' }); }}>
                  确认
                </Button>
              )}
              {detailAlert.status !== 'resolved' && (
                <Button type="primary" icon={<CheckCircleOutlined />} onClick={() => { handleResolve(detailAlert.id); setDetailAlert({ ...detailAlert, status: 'resolved' }); }}>
                  解决
                </Button>
              )}
              {detailAlert.analysis_task_id && (
                <Button type="link" onClick={() => setDrawerOpen(false)}>
                  查看关联分析 →
                </Button>
              )}
            </div>
          </div>
        )}
      </Drawer>

      {/* Rule Create Modal */}
      <Modal title="创建告警规则" open={ruleModalOpen} onCancel={() => setRuleModalOpen(false)} footer={null} destroyOnClose>
        <Form form={ruleForm} layout="vertical" onFinish={handleCreateRule}>
          <Form.Item name="business_line_id" label="业务线" rules={[{ required: true }]}>
            <Select placeholder="选择业务线" showSearch optionFilterProp="label"
              options={bizLines.map(b => ({ value: b.id, label: b.name }))} />
          </Form.Item>
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'keyword', label: '关键词匹配' },
              { value: 'pattern', label: '模式匹配' },
              { value: 'ai_anomaly', label: 'AI 异常' },
              { value: 'threshold', label: '阈值触发' },
            ]} />
          </Form.Item>
          <Space style={{ width: '100%' }} size={16}>
            <Form.Item name="severity" label="严重度" initialValue="warning" style={{ flex: 1 }}>
              <Select options={[
                { value: 'critical', label: 'Critical' },
                { value: 'warning', label: 'Warning' },
                { value: 'info', label: 'Info' },
              ]} />
            </Form.Item>
            <Form.Item name="cron_expression" label="Cron 表达式" initialValue="*/30 * * * *" style={{ flex: 1 }}>
              <Input placeholder="*/30 * * * *" />
            </Form.Item>
          </Space>
          <Form.Item name="description" label="描述"><Input.TextArea rows={2} /></Form.Item>
          <Form.Item><Button type="primary" htmlType="submit" block>创建</Button></Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default AlertList;
