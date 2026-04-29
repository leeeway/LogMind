import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Typography, Button, Space, Tag, Input, Select, Card, Timeline, message,
  Tooltip, Popconfirm, Divider, Dropdown,
} from 'antd';
import {
  ArrowLeftOutlined, ClockCircleOutlined, CheckCircleOutlined,
  SendOutlined, AlertOutlined, ThunderboltOutlined,
  UserOutlined, RobotOutlined, FireOutlined,
  ExclamationCircleOutlined, EyeOutlined, EditOutlined,
  CopyOutlined, FieldTimeOutlined,
} from '@ant-design/icons';
import { incidentApi } from '@/api/incidents';
import { useQuickDiagnose } from '@/components/QuickDiagnose';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const { Title, Text, Paragraph } = Typography;

const severityConfig: Record<string, { color: string; label: string }> = {
  P0: { color: '#ff4d4f', label: 'P0 紧急' },
  P1: { color: '#fa8c16', label: 'P1 严重' },
  P2: { color: '#faad14', label: 'P2 一般' },
  P3: { color: '#8c8c8c', label: 'P3 轻微' },
};

const statusConfig: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  investigating: { color: '#ff4d4f', icon: <FireOutlined />, label: '调查中' },
  identified: { color: '#fa8c16', icon: <ExclamationCircleOutlined />, label: '已定位' },
  monitoring: { color: '#1677ff', icon: <EyeOutlined />, label: '观察中' },
  resolved: { color: '#52c41a', icon: <CheckCircleOutlined />, label: '已解决' },
};

const eventTypeIcon: Record<string, React.ReactNode> = {
  alert: <AlertOutlined style={{ color: '#ff4d4f' }} />,
  action: <ThunderboltOutlined style={{ color: '#1677ff' }} />,
  message: <UserOutlined style={{ color: 'var(--lm-text-secondary)' }} />,
  ai: <RobotOutlined style={{ color: '#722ed1' }} />,
  status_change: <FieldTimeOutlined style={{ color: '#faad14' }} />,
};

const formatDuration = (seconds: number) => {
  if (seconds < 60) return `${seconds}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h > 0 ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
};

const WarRoom: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const quickDiagnose = useQuickDiagnose();
  const [incident, setIncident] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [msgInput, setMsgInput] = useState('');
  const [editing, setEditing] = useState(false);
  const [postmortem, setPostmortem] = useState('');
  const timelineEndRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const { data } = await incidentApi.get(id);
      setIncident(data);
      setPostmortem(data?.postmortem || '');
    } catch { message.error('加载失败'); }
    setLoading(false);
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Live reload every 10s for active incidents
  useEffect(() => {
    if (incident?.status === 'resolved') return;
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [incident?.status, load]);

  // Auto-scroll timeline
  useEffect(() => {
    timelineEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [incident?.timeline?.length]);

  const sendMessage = async () => {
    if (!msgInput.trim() || !id) return;
    try {
      await incidentApi.addEvent(id, { event_type: 'message', content: msgInput });
      setMsgInput('');
      load();
    } catch { message.error('发送失败'); }
  };

  const updateStatus = async (status: string) => {
    if (!id) return;
    await incidentApi.update(id, { status });
    load();
    if (status === 'resolved') message.success('故障已解决 ✅');
  };

  const updateSeverity = async (severity: string) => {
    if (!id) return;
    await incidentApi.update(id, { severity });
    load();
  };

  const savePostmortem = async () => {
    if (!id) return;
    await incidentApi.update(id, { postmortem });
    message.success('复盘报告已保存');
    setEditing(false);
    load();
  };

  const exportReport = () => {
    if (!incident) return;
    const timeline = (incident.timeline || [])
      .map((e: any) => `- **${new Date(e.created_at).toLocaleTimeString()}** [${e.type}] ${e.content}`)
      .join('\n');
    const md = `# 故障报告: ${incident.title}\n\n` +
      `> 严重度: ${incident.severity} | 状态: ${incident.status}\n` +
      `> 创建: ${incident.created_at} | 持续: ${formatDuration(incident.duration_seconds)}\n\n` +
      `## 时间线\n${timeline}\n\n` +
      `## 复盘\n${incident.postmortem || '暂无'}\n`;
    navigator.clipboard.writeText(md).then(() => message.success('报告已复制'));
  };

  if (!incident && !loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--lm-text-tertiary)' }}>故障不存在</div>;

  const sev = severityConfig[incident?.severity] || severityConfig.P2;
  const st = statusConfig[incident?.status] || statusConfig.investigating;

  return (
    <div className="lm-animate-in" style={{ height: 'calc(100vh - 80px)', display: 'flex', flexDirection: 'column', margin: '-24px' }}>
      {/* Header */}
      <div style={{
        padding: '12px 24px', flexShrink: 0,
        background: 'var(--lm-bg-container)', borderBottom: '1px solid var(--lm-border-light)',
        display: 'flex', alignItems: 'center', gap: 12,
      }}>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/incidents')} />

        <Dropdown menu={{
          items: Object.entries(severityConfig).map(([k, v]) => ({ key: k, label: v.label })),
          onClick: ({ key }) => updateSeverity(key),
        }}>
          <Tag color={sev.color} style={{ cursor: 'pointer', borderRadius: 4, fontWeight: 700, fontSize: 13 }}>
            {incident?.severity}
          </Tag>
        </Dropdown>

        <Title level={5} style={{ margin: 0, color: 'var(--lm-text)', flex: 1 }}>
          {incident?.title}
        </Title>

        <Space size={4}>
          <Tag icon={<ClockCircleOutlined />} style={{ borderRadius: 4, fontFamily: 'monospace' }}>
            {formatDuration(incident?.duration_seconds || 0)}
          </Tag>

          {incident?.status !== 'resolved' && (
            <>
              <Tooltip title="AI 诊断">
                <Button size="small" icon={<ThunderboltOutlined />}
                  style={{ color: '#722ed1', borderColor: '#722ed133' }}
                  onClick={() => quickDiagnose.open({
                    context: `帮我排查这个故障: ${incident?.title}\n描述: ${incident?.description}\n严重度: ${incident?.severity}`,
                    source: '故障作战室',
                  })}
                />
              </Tooltip>
              <Select
                value={incident?.status}
                onChange={updateStatus}
                size="small"
                style={{ width: 120 }}
                options={Object.entries(statusConfig).map(([k, v]) => ({
                  value: k, label: <Space size={4}>{v.icon}{v.label}</Space>,
                }))}
              />
            </>
          )}
          <Tooltip title="导出报告">
            <Button size="small" icon={<CopyOutlined />} onClick={exportReport} />
          </Tooltip>
        </Space>
      </div>

      {/* Main Content — 2 columns */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 340px', overflow: 'hidden' }}>
        {/* Left: Timeline + Chat */}
        <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', borderRight: '1px solid var(--lm-border-light)' }}>
          {/* Timeline */}
          <div style={{ flex: 1, overflow: 'auto', padding: '16px 24px' }}>
            <Timeline
              items={(incident?.timeline || []).map((evt: any) => ({
                dot: eventTypeIcon[evt.type] || eventTypeIcon.message,
                children: (
                  <div style={{ fontSize: 13 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
                      <Text strong style={{ color: 'var(--lm-text)', fontSize: 13 }}>{evt.user}</Text>
                      <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                        {new Date(evt.created_at).toLocaleTimeString()}
                      </Text>
                    </div>
                    <div style={{ color: 'var(--lm-text-secondary)', lineHeight: 1.5 }}>
                      {evt.content}
                    </div>
                  </div>
                ),
              }))}
            />
            <div ref={timelineEndRef} />
          </div>

          {/* Message Input */}
          {incident?.status !== 'resolved' && (
            <div style={{
              padding: '10px 24px', borderTop: '1px solid var(--lm-border-light)',
              background: 'var(--lm-bg-container)', flexShrink: 0,
            }}>
              <div style={{ display: 'flex', gap: 8 }}>
                <Input
                  value={msgInput}
                  onChange={e => setMsgInput(e.target.value)}
                  placeholder="发送消息到时间线..."
                  onPressEnter={sendMessage}
                  style={{ borderRadius: 8 }}
                />
                <Button type="primary" icon={<SendOutlined />} onClick={sendMessage} disabled={!msgInput.trim()}>
                  发送
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* Right: Info + Postmortem */}
        <div style={{ overflow: 'auto', padding: 16, background: 'var(--lm-bg-layout)' }}>
          {/* Status Card */}
          <Card size="small" title="故障信息"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10, marginBottom: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 }, body: { fontSize: 12 } }}
          >
            <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr', gap: '8px 8px' }}>
              <Text style={{ color: 'var(--lm-text-tertiary)' }}>状态</Text>
              <Tag icon={st.icon} color={st.color} style={{ borderRadius: 4 }}>{st.label}</Tag>

              <Text style={{ color: 'var(--lm-text-tertiary)' }}>严重度</Text>
              <Tag color={sev.color} style={{ borderRadius: 4 }}>{sev.label}</Tag>

              <Text style={{ color: 'var(--lm-text-tertiary)' }}>持续时间</Text>
              <Text style={{ fontFamily: 'monospace', color: 'var(--lm-text)' }}>
                {formatDuration(incident?.duration_seconds || 0)}
              </Text>

              <Text style={{ color: 'var(--lm-text-tertiary)' }}>创建时间</Text>
              <Text style={{ color: 'var(--lm-text)' }}>
                {incident?.created_at ? new Date(incident.created_at).toLocaleString() : '-'}
              </Text>

              <Text style={{ color: 'var(--lm-text-tertiary)' }}>负责人</Text>
              <Text style={{ color: 'var(--lm-text)' }}>{incident?.assignee || '未指派'}</Text>
            </div>
          </Card>

          {/* Description */}
          {incident?.description && (
            <Card size="small" title="问题描述"
              style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10, marginBottom: 12 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 }, body: { fontSize: 12 } }}
            >
              <Text style={{ color: 'var(--lm-text-secondary)' }}>{incident.description}</Text>
            </Card>
          )}

          {/* Postmortem */}
          <Card size="small"
            title={<Space><span>复盘报告</span>{incident?.status === 'resolved' && !editing && <Button size="small" type="text" icon={<EditOutlined />} onClick={() => setEditing(true)} />}</Space>}
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 }, body: { fontSize: 12 } }}
          >
            {editing ? (
              <Space direction="vertical" style={{ width: '100%' }}>
                <Input.TextArea
                  value={postmortem}
                  onChange={e => setPostmortem(e.target.value)}
                  placeholder="## 根因分析&#10;&#10;## 影响范围&#10;&#10;## 改进措施"
                  autoSize={{ minRows: 6, maxRows: 20 }}
                  style={{ fontSize: 12, fontFamily: 'monospace' }}
                />
                <Space>
                  <Button type="primary" size="small" onClick={savePostmortem}>保存</Button>
                  <Button size="small" onClick={() => setEditing(false)}>取消</Button>
                </Space>
              </Space>
            ) : (
              postmortem ? (
                <div style={{ fontSize: 13, color: 'var(--lm-text-secondary)' }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{postmortem}</ReactMarkdown>
                </div>
              ) : (
                <Text style={{ color: 'var(--lm-text-tertiary)' }}>
                  {incident?.status === 'resolved' ? '点击编辑按钮撰写复盘报告' : '故障解决后可撰写复盘报告'}
                </Text>
              )
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};

export default WarRoom;
