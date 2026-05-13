import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { Button, Input, Typography, Space, Tag, message, Tooltip, Select } from 'antd';
import {
  SendOutlined, PlusOutlined, DeleteOutlined, RobotOutlined,
  UserOutlined, ThunderboltOutlined, CopyOutlined,
  LoadingOutlined, BranchesOutlined, ExportOutlined,
  QuestionCircleOutlined, ClockCircleOutlined, RadarChartOutlined,
  AlertOutlined, SearchOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import { chatApi } from '@/api/chat';
import { businessLineApi } from '@/api/services';
import { useAuthStore } from '@/stores/authStore';
import AgentStepCard, { ToolStep } from '@/components/AgentStepCard';

const { Text, Title } = Typography;

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  isStreaming?: boolean;
  metadata?: {
    suggested_actions?: SuggestedAction[];
  };
}

interface SessionSummary {
  id: string;
  title: string;
  message_count: number;
}

interface BusinessLineOption {
  id: string;
  name: string;
}

interface SuggestedAction {
  label: string;
  prompt: string;
  kind?: 'follow_up' | 'diagnose' | 'task';
}

interface DynamicQuestionTemplate {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  accent: string;
  defaults: Record<string, string>;
  buildPrompt: (values: Record<string, string>) => string;
}

interface LiveRecommendation {
  id: string;
  title: string;
  prompt: string;
  reason: string;
  priority: 'critical' | 'warning' | 'info';
  kind: string;
  tone: string;
  metric?: string;
}

interface ChatRouteState {
  prefill?: string;
  source?: string;
}

interface RawChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  metadata?: {
    suggested_actions?: SuggestedAction[];
  };
}

const WELCOME_SUGGESTIONS = [
  '最近1小时有哪些关键错误？',
  '帮我分析 auth-service 的超时问题',
  '最近有什么告警需要关注？',
  '系统整体健康状况如何？',
  '对比今天和昨天的错误分布',
  '帮我追踪 NPE 的调用链',
];

const priorityMeta: Record<LiveRecommendation['priority'], { color: string; label: string }> = {
  critical: { color: '#ff4d4f', label: '现在最该看' },
  warning: { color: '#faad14', label: '值得关注' },
  info: { color: '#1677ff', label: '建议先问' },
};

const ChatPage: React.FC = () => {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [businessLines, setBusinessLines] = useState<BusinessLineOption[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [toolSteps, setToolSteps] = useState<ToolStep[]>([]);
  const [thinkingRound, setThinkingRound] = useState(0);
  const [thinkingText, setThinkingText] = useState('');
  const [followUps, setFollowUps] = useState<string[]>([]);
  const [suggestedActions, setSuggestedActions] = useState<SuggestedAction[]>([]);
  const [liveRecommendations, setLiveRecommendations] = useState<LiveRecommendation[]>([]);
  const [activeTemplateId, setActiveTemplateId] = useState('account-activity');
  const [templateValues, setTemplateValues] = useState<Record<string, string>>({
    account: '',
    hours: '1',
    serviceName: '',
  });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<TextAreaRef>(null);
  const token = useAuthStore((s) => s.token);
  const location = useLocation();

  const dynamicTemplates = useMemo<DynamicQuestionTemplate[]>(() => ([
    {
      id: 'account-activity',
      title: '账号操作追踪',
      description: '输入账号，查询最近一段时间的关键操作轨迹',
      icon: <ClockCircleOutlined />,
      accent: '#1677ff',
      defaults: { account: '', hours: '1', serviceName: '' },
      buildPrompt: (values) => (
        `请帮我查询账号 ${values.account} 在最近 ${values.hours || '1'} 小时做了哪些操作，` +
        `${values.serviceName ? `重点查看 ${values.serviceName}，` : ''}` +
        '按时间线列出关键动作、异常和影响服务。'
      ),
    },
    {
      id: 'service-errors',
      title: '服务错误诊断',
      description: '按服务和时间范围快速定位关键错误与异常趋势',
      icon: <RadarChartOutlined />,
      accent: '#52c41a',
      defaults: { account: '', hours: '1', serviceName: '' },
      buildPrompt: (values) => (
        `请分析 ${values.serviceName || '目标服务'} 最近 ${values.hours || '1'} 小时的关键错误、异常趋势和影响范围，` +
        '并给出下一步排查建议。'
      ),
    },
    {
      id: 'alert-check',
      title: '告警与关联排查',
      description: '检查同时间段的重要告警，并关联服务健康情况',
      icon: <AlertOutlined />,
      accent: '#fa8c16',
      defaults: { account: '', hours: '1', serviceName: '' },
      buildPrompt: (values) => (
        `请查询最近 ${values.hours || '1'} 小时的重要告警，` +
        `${values.serviceName ? `重点关注 ${values.serviceName}，` : ''}` +
        `${values.account ? `并判断是否与账号 ${values.account} 的操作有关。` : '并总结对应服务的影响范围。'}`
      ),
    },
  ]), []);

  const activeTemplate = dynamicTemplates.find((item) => item.id === activeTemplateId) || dynamicTemplates[0];

  // Load sessions
  const loadSessions = useCallback(async () => {
    try {
      const { data } = await chatApi.listSessions();
      setSessions(data?.sessions || []);
    } catch { /* ignore */ }
  }, []);

  const loadBusinessLines = useCallback(async () => {
    try {
      const { data } = await businessLineApi.list({ page_size: 100 });
      const items = Array.isArray(data?.items) ? data.items as BusinessLineOption[] : [];
      setBusinessLines(items.map((item) => ({
        id: item.id,
        name: item.name,
      })));
    } catch { /* ignore */ }
  }, []);

  const loadRecommendations = useCallback(async () => {
    try {
      const { data } = await chatApi.getRecommendations({ window_minutes: 60, limit: 6 });
      setLiveRecommendations(Array.isArray(data?.items) ? data.items as LiveRecommendation[] : []);
    } catch {
      setLiveRecommendations([]);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadSessions(); }, [loadSessions]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadBusinessLines(); }, [loadBusinessLines]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadRecommendations();
    const timer = window.setInterval(() => {
      loadRecommendations();
    }, 60000);
    return () => window.clearInterval(timer);
  }, [loadRecommendations]);

  // Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, toolSteps, thinkingRound]);

  // Create new session
  const createSession = async () => {
    try {
      const { data } = await chatApi.createSession();
      setActiveSessionId(data.id);
      setMessages([]);
      setToolSteps([]);
      setThinkingRound(0);
      setThinkingText('');
      setFollowUps([]);
      setSuggestedActions([]);
      loadSessions();
    } catch { message.error('创建会话失败'); }
  };

  // Load session messages
  const loadSession = async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setToolSteps([]);
    setThinkingRound(0);
    try {
      const { data } = await chatApi.getSession(sessionId);
      const rawMessages = Array.isArray(data?.messages) ? data.messages as RawChatMessage[] : [];
      setMessages(rawMessages.map((m) => ({
        role: m.role,
        content: m.content,
        timestamp: m.timestamp,
        metadata: m.metadata,
      })));
      const assistantMessages = rawMessages.filter((m) => m.role === 'assistant');
      const lastAssistant = assistantMessages[assistantMessages.length - 1];
      setSuggestedActions(lastAssistant?.metadata?.suggested_actions || []);
    } catch { setMessages([]); }
  };

  // Delete session
  const deleteSession = async (sessionId: string) => {
    try {
      await chatApi.deleteSession(sessionId);
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setMessages([]);
        setSuggestedActions([]);
        setFollowUps([]);
      }
      loadSessions();
    } catch { /* ignore */ }
  };

  const updateTemplateValue = (key: string, value: string) => {
    setTemplateValues((prev) => ({ ...prev, [key]: value }));
  };

  const applyTemplate = (templateId: string) => {
    const template = dynamicTemplates.find((item) => item.id === templateId);
    if (!template) return;
    setActiveTemplateId(templateId);
    setTemplateValues((prev) => ({ ...template.defaults, ...prev }));
  };

  const sendTemplatePrompt = () => {
    if (activeTemplateId === 'account-activity' && !templateValues.account.trim()) {
      message.warning('请输入账号后再发起查询');
      return;
    }
    if ((activeTemplateId === 'service-errors' || activeTemplateId === 'alert-check') && !templateValues.serviceName.trim()) {
      message.warning('请选择服务后再发起查询');
      return;
    }
    const builtPrompt = activeTemplate.buildPrompt(templateValues).trim();
    sendMessage(builtPrompt);
  };

  // Send message with SSE streaming + multi-round ReAct
  const sendMessage = useCallback(async (content?: string) => {
    const text = content || input.trim();
    if (!text || sending) return;
    setInput('');

    // Auto-create session if needed
    let sessionId = activeSessionId;
    if (!sessionId) {
      try {
        const { data } = await chatApi.createSession();
        sessionId = data.id;
        setActiveSessionId(sessionId);
      } catch { message.error('创建会话失败'); return; }
    }

    // Add user message
    setMessages(prev => [...prev, { role: 'user', content: text, timestamp: new Date().toISOString() }]);
    setSending(true);
    setToolSteps([]);
    setThinkingRound(0);
    setThinkingText('');
    setFollowUps([]);
    setSuggestedActions([]);

    // Add placeholder for assistant
    setMessages(prev => [...prev, { role: 'assistant', content: '', isStreaming: true }]);

    try {
      const isDev = window.location.port === '3000';
      const baseUrl = isDev ? 'http://localhost:8000' : '';
      const response = await fetch(`${baseUrl}/api/v1/chat/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ content: text }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let assistantContent = '';
      let latestSuggestedActions: SuggestedAction[] = [];

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          const lines = chunk.split('\n');

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));

              if (event.type === 'thinking') {
                setThinkingRound(event.round);
                setThinkingText(event.content);

              } else if (event.type === 'tool_call') {
                setToolSteps(prev => [...prev, {
                  name: event.name,
                  args: event.args,
                  round: event.round,
                  status: 'running',
                  startTime: Date.now(),
                }]);

              } else if (event.type === 'tool_result') {
                setToolSteps(prev => prev.map(s =>
                  s.name === event.name && s.status === 'running'
                    ? { ...s, result: event.result, summary: event.summary, status: 'done' as const, endTime: Date.now() }
                    : s
                ));

              } else if (event.type === 'step_done') {
                // Round complete, waiting for next
                setThinkingText(`第 ${event.round}/${event.total_rounds} 轮完成，继续分析...`);

              } else if (event.type === 'token') {
                assistantContent += event.content;
                setThinkingRound(0); // Hide thinking indicator
                setMessages(prev => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = { ...last, content: assistantContent, isStreaming: true };
                  }
                  return updated;
                });

              } else if (event.type === 'suggested_actions') {
                latestSuggestedActions = event.actions || [];
                setSuggestedActions(latestSuggestedActions);

              } else if (event.type === 'done') {
                setMessages(prev => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = {
                      ...last,
                      isStreaming: false,
                      metadata: { suggested_actions: latestSuggestedActions },
                    };
                  }
                  return updated;
                });

                // Extract follow-up suggestions from response
                const dividerIdx = assistantContent.lastIndexOf('---');
                if (dividerIdx > 0) {
                  const afterDivider = assistantContent.slice(dividerIdx + 3);
                  const suggestions = afterDivider
                    .split('\n')
                    .map(l => l.replace(/^[\s\-\d.*]+/, '').trim())
                    .filter(l => l.length > 4 && l.length < 60 && !l.startsWith('#'));
                  setFollowUps(suggestions.slice(0, 3));
                }

              } else if (event.type === 'error') {
                message.error(event.message);
              }
            } catch { /* skip invalid JSON lines */ }
          }
        }
      }
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : '网络错误';
      message.error('发送失败: ' + errorMessage);
      setMessages(prev => {
        const updated = [...prev];
        if (updated[updated.length - 1]?.role === 'assistant') {
          updated[updated.length - 1] = { ...updated[updated.length - 1], content: '⚠️ 连接失败', isStreaming: false };
        }
        return updated;
      });
    } finally {
      setSending(false);
      setThinkingRound(0);
      setThinkingText('');
      loadSessions();
      inputRef.current?.focus();
    }
  }, [activeSessionId, input, loadSessions, sending, token]);

  useEffect(() => {
    const state = (location.state || {}) as ChatRouteState;
    if (!state.prefill) return;

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setInput(state.prefill);
    window.history.replaceState({}, document.title);
    const timer = window.setTimeout(() => {
      sendMessage(state.prefill);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [location.state, sendMessage]);

  const copyContent = (text: string) => {
    navigator.clipboard.writeText(text).then(() => message.success('已复制'));
  };

  const exportSession = () => {
    if (!messages.length) return;
    const md = messages
      .map(m => m.role === 'user' ? `## 🧑 用户\n${m.content}` : `## 🤖 AI 助手\n${m.content}`)
      .join('\n\n---\n\n');
    const header = `# LogMind 诊断报告\n\n> 时间: ${new Date().toLocaleString()}\n> 工具调用: ${toolSteps.length} 次\n\n---\n\n`;
    navigator.clipboard.writeText(header + md).then(() => message.success('诊断报告已复制到剪贴板'));
  };

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 56px)', margin: '-24px', overflow: 'hidden' }}>
      {/* Left Sidebar — Sessions */}
      <div style={{
        width: 260, borderRight: '1px solid var(--lm-border-light)', display: 'flex', flexDirection: 'column',
        background: 'var(--lm-bg-container)',
      }}>
        <div style={{ padding: 16, display: 'flex', gap: 8 }}>
          <Button type="primary" icon={<PlusOutlined />} style={{ borderRadius: 8, height: 40, flex: 1 }} onClick={createSession}>
            新对话
          </Button>
          {messages.length > 0 && (
            <Tooltip title="导出诊断报告">
              <Button icon={<ExportOutlined />} style={{ borderRadius: 8, height: 40 }} onClick={exportSession} />
            </Tooltip>
          )}
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: '0 8px' }}>
          {sessions.map((s) => (
            <div
              key={s.id}
              onClick={() => loadSession(s.id)}
              style={{
                padding: '10px 12px', borderRadius: 8, marginBottom: 4, cursor: 'pointer',
                background: activeSessionId === s.id ? 'var(--lm-primary-bg)' : 'transparent',
                border: activeSessionId === s.id ? '1px solid rgba(22,119,255,0.2)' : '1px solid transparent',
                transition: 'all 0.2s',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}
              onMouseEnter={e => { if (activeSessionId !== s.id) (e.currentTarget.style.background = 'var(--lm-bg-elevated)'); }}
              onMouseLeave={e => { if (activeSessionId !== s.id) (e.currentTarget.style.background = 'transparent'); }}
            >
              <div style={{ flex: 1, overflow: 'hidden' }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--lm-text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {s.title}
                </div>
                <div style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', marginTop: 2 }}>
                  {s.message_count} 条消息
                </div>
              </div>
              <DeleteOutlined
                style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }}
                onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }}
              />
            </div>
          ))}
        </div>
      </div>

      {/* Main Chat Area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: 'var(--lm-bg-layout)' }}>
        {/* Messages */}
        <div style={{ flex: 1, overflow: 'auto', padding: '24px 0' }}>
          <div style={{ maxWidth: 800, margin: '0 auto', padding: '0 24px' }}>
            {messages.length === 0 && (
              <div style={{ textAlign: 'center', paddingTop: 60 }}>
                <div style={{
                  width: 72, height: 72, borderRadius: 20, margin: '0 auto 20px',
                  background: 'linear-gradient(135deg, rgba(22,119,255,0.15), rgba(114,46,209,0.15))',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  border: '1px solid rgba(22,119,255,0.1)',
                }}>
                  <ThunderboltOutlined style={{ fontSize: 32, color: '#1677ff' }} />
                </div>
                <Title level={3} style={{ color: 'var(--lm-text)', marginBottom: 8 }}>
                  LogMind AI 诊断助手
                </Title>
                <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 14 }}>
                  自主排查 Agent · 多轮推理 · 真实 ES 日志查询 · 12 种诊断工具
                </Text>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginTop: 28 }}>
                  {(liveRecommendations.length > 0
                    ? liveRecommendations.map((item) => item.prompt)
                    : WELCOME_SUGGESTIONS
                  ).map((s, i) => (
                    <div
                      key={i}
                      onClick={() => sendMessage(s)}
                      style={{
                        padding: '8px 14px', borderRadius: 10, cursor: 'pointer', fontSize: 13,
                        background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
                        color: 'var(--lm-text-secondary)', transition: 'all 0.2s',
                        maxWidth: 240,
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(22,119,255,0.3)'; e.currentTarget.style.color = 'var(--lm-text)'; }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--lm-border-light)'; e.currentTarget.style.color = 'var(--lm-text-secondary)'; }}
                    >
                      <ThunderboltOutlined style={{ marginRight: 6, color: '#1677ff' }} />{s}
                    </div>
                  ))}
                </div>
                {liveRecommendations.length > 0 && (
                  <div style={{
                    marginTop: 30,
                    textAlign: 'left',
                    background: 'linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01))',
                    border: '1px solid var(--lm-border-light)',
                    borderRadius: 18,
                    padding: 18,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                      <RadarChartOutlined style={{ color: '#1677ff', fontSize: 16 }} />
                      <Text style={{ color: 'var(--lm-text)', fontWeight: 600, fontSize: 14 }}>
                        实时诊断推荐
                      </Text>
                      <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }}>
                        每分钟刷新一次，把眼前最值得问的问题放前面
                      </Text>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
                      {liveRecommendations.map((item) => {
                        const meta = priorityMeta[item.priority] || priorityMeta.info;
                        return (
                          <div
                            key={item.id}
                            onClick={() => sendMessage(item.prompt)}
                            style={{
                              padding: '14px 15px',
                              borderRadius: 14,
                              cursor: 'pointer',
                              border: `1px solid ${meta.color}33`,
                              background: `linear-gradient(180deg, ${meta.color}10, rgba(255,255,255,0.02))`,
                              transition: 'transform 0.2s ease, border-color 0.2s ease',
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.transform = 'translateY(-2px)';
                              e.currentTarget.style.borderColor = `${meta.color}66`;
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.transform = 'translateY(0)';
                              e.currentTarget.style.borderColor = `${meta.color}33`;
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                              <Tag color={meta.color} style={{ borderRadius: 999, margin: 0, fontSize: 10 }}>
                                {meta.label}
                              </Tag>
                              {item.metric && (
                                <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 11 }}>
                                  {item.metric}
                                </Text>
                              )}
                            </div>
                            <Text style={{ display: 'block', color: 'var(--lm-text)', fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
                              {item.title}
                            </Text>
                            <Text style={{ display: 'block', color: 'var(--lm-text-secondary)', fontSize: 12, lineHeight: 1.6, minHeight: 38 }}>
                              {item.reason}
                            </Text>
                            <div style={{
                              marginTop: 10,
                              fontSize: 11,
                              color: meta.color,
                              fontWeight: 600,
                            }}>
                              点击直接发起诊断
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                <div style={{
                  marginTop: 28,
                  textAlign: 'left',
                  background: 'var(--lm-bg-card)',
                  border: '1px solid var(--lm-border-light)',
                  borderRadius: 16,
                  padding: 18,
                }}>
                  <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 14 }}>
                    <div>
                      <Text style={{ display: 'block', color: 'var(--lm-text)', fontSize: 14, fontWeight: 600 }}>
                        动态问题发起
                      </Text>
                      <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }}>
                        先选场景，再补参数，直接发起严谨诊断
                      </Text>
                    </div>
                    <Button type="primary" icon={<SendOutlined />} onClick={sendTemplatePrompt} disabled={sending}>
                      发起查询
                    </Button>
                  </Space>

                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
                    {dynamicTemplates.map((template) => {
                      const active = template.id === activeTemplateId;
                      return (
                        <div
                          key={template.id}
                          onClick={() => applyTemplate(template.id)}
                          style={{
                            width: 'calc(33.333% - 7px)',
                            minWidth: 180,
                            padding: '12px 14px',
                            borderRadius: 12,
                            cursor: 'pointer',
                            border: `1px solid ${active ? `${template.accent}55` : 'var(--lm-border-light)'}`,
                            background: active ? `${template.accent}10` : 'var(--lm-bg-elevated)',
                            transition: 'all 0.2s',
                          }}
                        >
                          <Space align="start">
                            <span style={{ color: template.accent, fontSize: 16 }}>{template.icon}</span>
                            <div>
                              <Text style={{ display: 'block', color: 'var(--lm-text)', fontWeight: 600, fontSize: 13 }}>
                                {template.title}
                              </Text>
                              <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 11 }}>
                                {template.description}
                              </Text>
                            </div>
                          </Space>
                        </div>
                      );
                    })}
                  </div>

                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    <Input
                      value={templateValues.account}
                      onChange={(e) => updateTemplateValue('account', e.target.value)}
                      placeholder="账号 / userId / 手机号"
                      style={{ flex: 1, minWidth: 180, borderRadius: 10 }}
                    />
                    <Select
                      value={templateValues.hours}
                      onChange={(value) => updateTemplateValue('hours', value)}
                      style={{ width: 120 }}
                      options={[
                        { value: '1', label: '最近1小时' },
                        { value: '3', label: '最近3小时' },
                        { value: '6', label: '最近6小时' },
                        { value: '24', label: '最近24小时' },
                      ]}
                    />
                    <Select
                      allowClear
                      placeholder="选择服务（可选）"
                      value={templateValues.serviceName || undefined}
                      onChange={(value) => updateTemplateValue('serviceName', value || '')}
                      style={{ flex: 1, minWidth: 180 }}
                      options={businessLines.map((biz) => ({ value: biz.name, label: biz.name }))}
                    />
                  </div>

                  <div style={{
                    marginTop: 12,
                    padding: '10px 12px',
                    borderRadius: 10,
                    background: 'var(--lm-bg-elevated)',
                    border: '1px dashed var(--lm-border-light)',
                    color: 'var(--lm-text-secondary)',
                    fontSize: 12,
                    lineHeight: 1.6,
                  }}>
                    <SearchOutlined style={{ marginRight: 6, color: '#1677ff' }} />
                    {activeTemplate.buildPrompt(templateValues)}
                  </div>
                </div>
              </div>
            )}

            {messages.map((msg, idx) => (
              <div key={idx} style={{
                display: 'flex', gap: 12, marginBottom: 20,
                flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
              }}>
                {/* Avatar */}
                <div style={{
                  width: 34, height: 34, borderRadius: 10, flexShrink: 0,
                  background: msg.role === 'user'
                    ? 'linear-gradient(135deg, #1677ff, #4096ff)'
                    : 'linear-gradient(135deg, rgba(114,46,209,0.2), rgba(22,119,255,0.2))',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  border: msg.role === 'user' ? 'none' : '1px solid rgba(114,46,209,0.15)',
                }}>
                  {msg.role === 'user'
                    ? <UserOutlined style={{ color: '#fff', fontSize: 14 }} />
                    : <RobotOutlined style={{ color: '#722ed1', fontSize: 14 }} />
                  }
                </div>

                {/* Bubble */}
                <div style={{
                  maxWidth: '78%', padding: '10px 14px', borderRadius: 14,
                  background: msg.role === 'user' ? 'var(--lm-primary)' : 'var(--lm-bg-card)',
                  border: msg.role === 'user' ? 'none' : '1px solid var(--lm-border-light)',
                  color: msg.role === 'user' ? '#fff' : 'var(--lm-text)',
                  position: 'relative',
                }}>
                  {msg.role === 'assistant' ? (
                    <>
                      {msg.content ? (
                        <div className="lm-markdown-content" style={{ fontSize: 14, lineHeight: 1.7 }}>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                        </div>
                      ) : msg.isStreaming ? (
                        <Space><LoadingOutlined style={{ color: '#722ed1' }} /><Text style={{ color: 'var(--lm-text-tertiary)' }}>思考中...</Text></Space>
                      ) : null}
                      {msg.content && !msg.isStreaming && (
                        <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
                          <Tooltip title="复制回答">
                            <Button
                              type="text" size="small"
                              icon={<CopyOutlined />}
                              style={{ fontSize: 12, color: 'var(--lm-text-tertiary)', padding: '0 4px', height: 22 }}
                              onClick={() => copyContent(msg.content)}
                            />
                          </Tooltip>
                        </div>
                      )}
                      {msg.isStreaming && msg.content && (
                        <span className="lm-cursor-blink" style={{ display: 'inline-block', width: 2, height: 16, background: '#722ed1', marginLeft: 2, verticalAlign: 'text-bottom' }} />
                      )}
                    </>
                  ) : (
                    <>
                      <div style={{ fontSize: 14, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{msg.content}</div>
                      {!sending && (
                        <div style={{ marginTop: 6, display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                          <Tooltip title="复制">
                            <Button
                              type="text" size="small"
                              icon={<CopyOutlined />}
                              style={{ fontSize: 11, color: 'rgba(255,255,255,0.6)', padding: '0 4px', height: 20 }}
                              onClick={() => copyContent(msg.content)}
                            />
                          </Tooltip>
                          <Tooltip title="重新提问">
                            <Button
                              type="text" size="small"
                              icon={<SendOutlined />}
                              style={{ fontSize: 11, color: 'rgba(255,255,255,0.6)', padding: '0 4px', height: 20 }}
                              onClick={() => sendMessage(msg.content)}
                            />
                          </Tooltip>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}

            {/* Agent Thinking Chain */}
            {(toolSteps.length > 0 || thinkingRound > 0) && (
              <div style={{
                marginBottom: 16, marginLeft: 46,
                padding: '12px 14px', borderRadius: 12,
                background: 'rgba(22,119,255,0.03)',
                border: '1px solid rgba(22,119,255,0.08)',
                animation: 'lm-fadeSlideIn 0.3s ease-out',
              }}>
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <BranchesOutlined style={{ color: '#722ed1', fontSize: 14 }} />
                  <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
                    Agent 推理链
                  </Text>
                  {thinkingRound > 0 && (
                    <Tag color="processing" style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>
                      第 {thinkingRound} 轮
                    </Tag>
                  )}
                  {toolSteps.length > 0 && (
                    <Text style={{ fontSize: 10, color: 'var(--lm-text-tertiary)', marginLeft: 'auto' }}>
                      {toolSteps.filter(s => s.status === 'done').length}/{toolSteps.length} 步完成
                    </Text>
                  )}
                </div>

                {/* Steps */}
                {toolSteps.map((step, i) => (
                  <AgentStepCard key={`${step.name}-${i}`} step={step} isLast={i === toolSteps.length - 1 && !thinkingText} />
                ))}

                {/* Current thinking */}
                {thinkingText && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    paddingLeft: 24, paddingTop: 4, fontSize: 11, color: 'var(--lm-text-tertiary)',
                  }}>
                    <LoadingOutlined style={{ color: '#722ed1' }} />
                    {thinkingText}
                  </div>
                )}
              </div>
            )}

            {/* Follow-up Suggestions */}
            {followUps.length > 0 && !sending && (
              <div style={{
                marginBottom: 16, marginLeft: 46,
                display: 'flex', flexWrap: 'wrap', gap: 6,
                animation: 'lm-fadeSlideIn 0.3s ease-out',
              }}>
                <QuestionCircleOutlined style={{ color: 'var(--lm-text-tertiary)', fontSize: 12, marginTop: 5 }} />
                {followUps.map((q, i) => (
                  <div
                    key={i}
                    onClick={() => sendMessage(q)}
                    style={{
                      padding: '5px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 12,
                      background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
                      color: 'var(--lm-text-secondary)', transition: 'all 0.2s',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(114,46,209,0.3)'; e.currentTarget.style.color = 'var(--lm-text)'; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--lm-border-light)'; e.currentTarget.style.color = 'var(--lm-text-secondary)'; }}
                  >
                    {q}
                  </div>
                ))}
              </div>
            )}

            {suggestedActions.length > 0 && !sending && (
              <div style={{
                marginBottom: 16, marginLeft: 46,
                padding: '14px 16px',
                borderRadius: 14,
                background: 'var(--lm-bg-card)',
                border: '1px solid var(--lm-border-light)',
                animation: 'lm-fadeSlideIn 0.3s ease-out',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <ThunderboltOutlined style={{ color: '#1677ff', fontSize: 14 }} />
                  <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
                    相关操作
                  </Text>
                  <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                    继续深挖当前上下文
                  </Text>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {suggestedActions.map((action, index) => (
                    <Button
                      key={`${action.label}-${index}`}
                      onClick={() => sendMessage(action.prompt)}
                      style={{ borderRadius: 10 }}
                    >
                      {action.label}
                    </Button>
                  ))}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input Area */}
        <div style={{
          padding: '12px 24px 20px', borderTop: '1px solid var(--lm-border-light)',
          background: 'var(--lm-bg-container)',
        }}>
          <div style={{ maxWidth: 800, margin: '0 auto' }}>
            <div style={{
              display: 'flex',
              gap: 8,
              flexWrap: 'wrap',
              marginBottom: 10,
            }}>
              {dynamicTemplates.map((template) => {
                const active = template.id === activeTemplateId;
                return (
                  <div
                    key={template.id}
                    onClick={() => applyTemplate(template.id)}
                    style={{
                      padding: '6px 10px',
                      borderRadius: 999,
                      cursor: 'pointer',
                      border: `1px solid ${active ? `${template.accent}55` : 'var(--lm-border-light)'}`,
                      background: active ? `${template.accent}10` : 'var(--lm-bg-elevated)',
                      color: active ? 'var(--lm-text)' : 'var(--lm-text-secondary)',
                      fontSize: 12,
                    }}
                  >
                    <span style={{ color: template.accent, marginRight: 6 }}>{template.icon}</span>
                    {template.title}
                  </div>
                );
              })}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <Input
                value={templateValues.account}
                onChange={(e) => updateTemplateValue('account', e.target.value)}
                placeholder="账号 / userId"
                style={{ width: 180, borderRadius: 10 }}
                disabled={sending}
              />
              <Select
                value={templateValues.hours}
                onChange={(value) => updateTemplateValue('hours', value)}
                style={{ width: 130 }}
                disabled={sending}
                options={[
                  { value: '1', label: '最近1小时' },
                  { value: '3', label: '最近3小时' },
                  { value: '6', label: '最近6小时' },
                  { value: '24', label: '最近24小时' },
                ]}
              />
              <Select
                allowClear
                placeholder="服务（可选）"
                value={templateValues.serviceName || undefined}
                onChange={(value) => updateTemplateValue('serviceName', value || '')}
                style={{ minWidth: 200, flex: 1 }}
                disabled={sending}
                options={businessLines.map((biz) => ({ value: biz.name, label: biz.name }))}
              />
              <Button onClick={sendTemplatePrompt} disabled={sending} icon={<ThunderboltOutlined />}>
                套用模板提问
              </Button>
            </div>
            <div style={{
              display: 'flex', gap: 12, alignItems: 'flex-end',
              background: 'var(--lm-bg-elevated)', borderRadius: 14,
              border: '1px solid var(--lm-border-light)', padding: '8px 12px',
              transition: 'border-color 0.2s',
            }}>
              <Input.TextArea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                placeholder="描述你的问题... (Ctrl+Enter 发送，Ctrl+Shift+D 快捷诊断)"
                autoSize={{ minRows: 1, maxRows: 4 }}
                style={{ border: 'none', background: 'transparent', boxShadow: 'none', fontSize: 14, resize: 'none', padding: '4px 0' }}
                onPressEnter={(e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); sendMessage(); } }}
                disabled={sending}
              />
              <Button
                type="primary"
                shape="circle"
                icon={sending ? <LoadingOutlined /> : <SendOutlined />}
                onClick={() => sendMessage()}
                disabled={!input.trim() || sending}
                style={{ flexShrink: 0 }}
              />
            </div>
            <div style={{ textAlign: 'center', marginTop: 6 }}>
              <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                LogMind AI v4.0 · 12 种诊断工具 · ReAct 多轮推理 · Ctrl+Enter 发送
              </Text>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatPage;
