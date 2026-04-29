import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Typography, Button, Space, Tag, Card, Modal, Input, Select, message, Tooltip, Badge } from 'antd';
import {
  PlusOutlined, AlertOutlined, ClockCircleOutlined,
  CheckCircleOutlined, EyeOutlined, ThunderboltOutlined,
  FireOutlined, ExclamationCircleOutlined, SearchOutlined,
} from '@ant-design/icons';
import { incidentApi } from '@/api/incidents';

const { Title, Text } = Typography;

const severityConfig: Record<string, { color: string; label: string }> = {
  P0: { color: '#ff4d4f', label: 'P0 - 紧急' },
  P1: { color: '#fa8c16', label: 'P1 - 严重' },
  P2: { color: '#faad14', label: 'P2 - 一般' },
  P3: { color: '#8c8c8c', label: 'P3 - 轻微' },
};

const statusConfig: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  investigating: { color: '#ff4d4f', icon: <FireOutlined />, label: '调查中' },
  identified: { color: '#fa8c16', icon: <ExclamationCircleOutlined />, label: '已定位' },
  monitoring: { color: '#1677ff', icon: <EyeOutlined />, label: '观察中' },
  resolved: { color: '#52c41a', icon: <CheckCircleOutlined />, label: '已解决' },
};

const formatDuration = (seconds: number) => {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
};

const IncidentList: React.FC = () => {
  const [incidents, setIncidents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newSeverity, setNewSeverity] = useState('P2');
  const [newDesc, setNewDesc] = useState('');
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const { data } = await incidentApi.list();
      setIncidents(data?.incidents || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Live timer update
  useEffect(() => {
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [load]);

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    try {
      const { data } = await incidentApi.create({ title: newTitle, description: newDesc, severity: newSeverity });
      message.success('故障已创建');
      setCreateOpen(false);
      setNewTitle('');
      setNewDesc('');
      load();
      if (data?.id) navigate(`/incidents/${data.id}`);
    } catch { message.error('创建失败'); }
  };

  const activeCount = incidents.filter(i => i.status !== 'resolved').length;

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Space>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
            <AlertOutlined style={{ marginRight: 8 }} />故障作战室
          </Title>
          {activeCount > 0 && (
            <Badge count={activeCount} style={{ backgroundColor: '#ff4d4f' }}>
              <Tag color="red" style={{ borderRadius: 4 }}>进行中</Tag>
            </Badge>
          )}
        </Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          创建故障
        </Button>
      </div>

      {/* Incident Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 12 }}>
        {incidents.map(inc => {
          const sev = severityConfig[inc.severity] || severityConfig.P2;
          const st = statusConfig[inc.status] || statusConfig.investigating;

          return (
            <Card
              key={inc.id}
              hoverable
              onClick={() => navigate(`/incidents/${inc.id}`)}
              style={{
                background: 'var(--lm-bg-card)',
                border: `1px solid ${inc.status !== 'resolved' ? sev.color + '25' : 'var(--lm-border-light)'}`,
                borderRadius: 12,
                cursor: 'pointer',
                transition: 'all 0.2s',
              }}
              styles={{ body: { padding: '14px 18px' } }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                <div style={{ flex: 1 }}>
                  <Tag color={sev.color} style={{ borderRadius: 4, fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
                    {inc.severity}
                  </Tag>
                  <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--lm-text)', lineHeight: 1.3 }}>
                    {inc.title}
                  </div>
                </div>
                <Tag
                  icon={st.icon}
                  color={st.color}
                  style={{ borderRadius: 4, fontSize: 11, flexShrink: 0 }}
                >
                  {st.label}
                </Tag>
              </div>

              {inc.description && (
                <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)', display: 'block', marginBottom: 8 }}>
                  {inc.description.slice(0, 80)}
                </Text>
              )}

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                <Space size={12}>
                  <span>
                    <ClockCircleOutlined style={{ marginRight: 4 }} />
                    {formatDuration(inc.duration_seconds)}
                  </span>
                  {inc.assignee && <span>👤 {inc.assignee}</span>}
                </Space>
                <span>{new Date(inc.created_at).toLocaleString()}</span>
              </div>
            </Card>
          );
        })}

        {!loading && incidents.length === 0 && (
          <div style={{
            gridColumn: '1 / -1', textAlign: 'center', padding: 60,
            color: 'var(--lm-text-tertiary)',
          }}>
            <CheckCircleOutlined style={{ fontSize: 48, marginBottom: 16, opacity: 0.3 }} />
            <div style={{ fontSize: 16 }}>暂无故障 — 一切运行正常 ✅</div>
          </div>
        )}
      </div>

      {/* Create Modal */}
      <Modal
        title="创建故障工单"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={handleCreate}
        okText="创建"
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Input
            placeholder="故障标题"
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            onPressEnter={handleCreate}
          />
          <Select
            value={newSeverity}
            onChange={setNewSeverity}
            style={{ width: '100%' }}
            options={Object.entries(severityConfig).map(([k, v]) => ({ value: k, label: v.label }))}
          />
          <Input.TextArea
            placeholder="问题描述（可选）"
            value={newDesc}
            onChange={e => setNewDesc(e.target.value)}
            autoSize={{ minRows: 2, maxRows: 4 }}
          />
        </Space>
      </Modal>
    </div>
  );
};

export default IncidentList;
