import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { Button, Input, Typography, Space, Tag, message, Tooltip, Select } from 'antd';
import {
  SendOutlined, PlusOutlined, DeleteOutlined, RobotOutlined,
  UserOutlined, ThunderboltOutlined, CopyOutlined,
  LoadingOutlined, BranchesOutlined, ExportOutlined,
  QuestionCircleOutlined, ClockCircleOutlined, RadarChartOutlined,
  AlertOutlined, SearchOutlined, CheckCircleOutlined, ExclamationCircleOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import { chatApi } from '@/api/chat';
import { businessLineApi } from '@/api/services';
import { useAuthStore } from '@/stores/authStore';
import AgentStepCard, { ToolStep } from '@/components/AgentStepCard';
import TraceTimeline, { TraceSegment, TraceNode } from '@/components/TraceTimeline';
import ServiceFlowDiagram, { ServiceTopology } from '@/components/ServiceFlowDiagram';
import DiagnosticClues, { DiagnosticClue } from '@/components/DiagnosticClues';

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
  defaults: TemplateValues;
  buildPrompt: (values: TemplateValues) => string;
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

interface TimelineEntry {
  time: string;
  service: string;
  domain: string;
  filetype: string;
  host: string;
  identity: string;
  action: string;
}

interface TemplateValues {
  account: string;
  hours: string;
  serviceName: string;
  serviceNames: string[];
  serviceScope: 'single' | 'selected' | 'core' | 'all';
  keyword: string;
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
  const [timelineEntries, setTimelineEntries] = useState<TimelineEntry[]>([]);
  const [timelineSummary, setTimelineSummary] = useState('');
  const [traceSegments, setTraceSegments] = useState<TraceSegment[]>([]);
  const [traceUncorrelated, setTraceUncorrelated] = useState<TraceNode[]>([]);
  const [traceTopology, setTraceTopology] = useState<ServiceTopology>({});
  const [traceSummary, setTraceSummary] = useState('');
  const [traceErrorServices, setTraceErrorServices] = useState<string[]>([]);
  const [multiAgentFindings, setMultiAgentFindings] = useState<{name: string; displayName: string; status: string; summary: string}[]>([]);
  const [diagnosticClues, setDiagnosticClues] = useState<DiagnosticClue[]>([]);
  const [searchSummary, setSearchSummary] = useState('');
  const [activeTemplateId, setActiveTemplateId] = useState('account-activity');
  const [templateValues, setTemplateValues] = useState<TemplateValues>({
    account: '',
    hours: '1',
    serviceName: '',
    serviceNames: [],
    serviceScope: 'core',
    keyword: '',
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
      defaults: { account: '', hours: '1', serviceName: '', serviceNames: [], serviceScope: 'core', keyword: '' },
      buildPrompt: (values) => (
        `请帮我查询账号 ${values.account} 在最近 ${values.hours || '1'} 小时做了哪些操作，` +
        `${values.keyword ? `同时关注关键词 ${values.keyword}，` : ''}` +
        `${values.serviceScope === 'all' ? '覆盖全部业务线，' : values.serviceScope === 'core' ? '覆盖核心业务线，' : values.serviceScope === 'selected' && values.serviceNames.length ? `重点查看 ${values.serviceNames.join('、')}，` : values.serviceName ? `重点查看 ${values.serviceName}，` : ''}` +
        '按时间线列出关键动作、异常和影响服务。'
      ),
    },
    {
      id: 'service-errors',
      title: '服务错误诊断',
      description: '按服务和时间范围快速定位关键错误与异常趋势',
      icon: <RadarChartOutlined />,
      accent: '#52c41a',
      defaults: { account: '', hours: '1', serviceName: '', serviceNames: [], serviceScope: 'single', keyword: '' },
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
      defaults: { account: '', hours: '1', serviceName: '', serviceNames: [], serviceScope: 'single', keyword: '' },
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
      setTimelineEntries([]);
      setTimelineSummary('');
      setTraceSegments([]);
      setTraceUncorrelated([]);
      setTraceTopology({});
      setTraceSummary('');
      setTraceErrorServices([]);
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
        setTimelineEntries([]);
        setTimelineSummary('');
        setTraceSegments([]);
        setTraceUncorrelated([]);
        setTraceTopology({});
        setTraceSummary('');
        setTraceErrorServices([]);
      }
      loadSessions();
    } catch { /* ignore */ }
  };

  const updateTemplateValue = <K extends keyof TemplateValues>(key: K, value: TemplateValues[K]) => {
    setTemplateValues((prev) => ({ ...prev, [key]: value }));
  };

  const applyTemplate = (templateId: string) => {
    const template = dynamicTemplates.find((item) => item.id === templateId);
    if (!template) return;
    setActiveTemplateId(templateId);
    setTemplateValues((prev) => ({ ...template.defaults, ...prev } as TemplateValues));
  };

  const sendTemplatePrompt = () => {
    if (activeTemplateId === 'account-activity' && !templateValues.account.trim()) {
      message.warning('请输入账号后再发起查询');
      return;
    }
    if (templateValues.serviceScope === 'selected' && templateValues.serviceNames.length === 0) {
      message.warning('多业务线模式下请至少选择一个业务线');
      return;
    }
    if (
      (activeTemplateId === 'service-errors' || activeTemplateId === 'alert-check') &&
      !templateValues.serviceName.trim()
    ) {
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
    setTimelineEntries([]);
    setTimelineSummary('');
    setTraceSegments([]);
    setTraceUncorrelated([]);
    setTraceTopology({});
    setTraceSummary('');
    setTraceErrorServices([]);
    setMultiAgentFindings([]);
    setDiagnosticClues([]);
    setSearchSummary('');

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
                if (event.name === 'trace_linked_operations') {
                  try {
                    const parsed = JSON.parse(event.result || '{}');
                    const segs = parsed.trace_segments || [];
                    if (Array.isArray(segs) && segs.length > 0) {
                      setTraceSegments(segs);
                      setTraceUncorrelated(parsed.uncorrelated_entries || []);
                      setTraceTopology(parsed.service_topology || {});
                      setTraceSummary(parsed.summary || '');
                      const errSvcs = segs
                        .filter((s: TraceSegment) => s.has_error)
                        .flatMap((s: TraceSegment) => s.nodes.filter((n: TraceNode) => n.level === 'error').map((n: TraceNode) => n.service));
                      setTraceErrorServices([...new Set(errSvcs)]);
                    }
                  } catch { /* ignore */ }
                } else if (event.name === 'query_operation_timeline' || event.name === 'query_account_activity') {
                  try {
                    const parsed = JSON.parse(event.result || '{}');
                    const nextTimeline = parsed.timeline || parsed.activities || [];
                    if (Array.isArray(nextTimeline) && nextTimeline.length > 0) {
                      setTimelineEntries(nextTimeline.slice(0, 30));
                      setTimelineSummary(parsed.summary || '');
                    }
                  } catch { /* ignore */ }
                }

              } else if (event.type === 'multi_agent_start') {
                setThinkingRound(0);
                setThinkingText('多Agent协作诊断中...');
                const agents = (event.agents || []).map((a: {name: string; display_name: string}) => ({
                  name: a.name, displayName: a.display_name, status: 'running', summary: '',
                }));
                setMultiAgentFindings(agents);

              } else if (event.type === 'agent_done') {
                setMultiAgentFindings(prev => prev.map(f =>
                  f.name === event.agent
                    ? { ...f, status: event.status, summary: event.summary || '' }
                    : f
                ));

              } else if (event.type === 'search_clues') {
                setDiagnosticClues(event.clues || []);
                setSearchSummary(event.summary || '');
                setThinkingText('搜索完成，正在分析...');

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
      let errorMessage = '网络错误';
      if (err instanceof Error) {
        if (err.message.includes('422')) {
          errorMessage = '消息内容过长或格式无效，请精简后重试';
        } else if (err.message.includes('401')) {
          errorMessage = '登录已过期，请重新登录';
        } else {
          errorMessage = err.message;
        }
      }
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
              <div style={{ textAlign: 'center', paddingTop: 80 }}>
                <div style={{
                  width: 64, height: 64, borderRadius: 18, margin: '0 auto 16px',
                  background: 'linear-gradient(135deg, rgba(22,119,255,0.15), rgba(114,46,209,0.15))',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  border: '1px solid rgba(22,119,255,0.1)',
                }}>
                  <ThunderboltOutlined style={{ fontSize: 28, color: '#1677ff' }} />
                </div>
                <Title level={4} style={{ color: 'var(--lm-text)', marginBottom: 6 }}>
                  LogMind AI 诊断
                </Title>
                <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 13 }}>
                  输入账号、订单号、traceId 或关键词，自动搜索全部业务线并给出诊断线索
                </Text>

                {/* Smart Search Bar */}
                <div style={{
                  maxWidth: 560, margin: '28px auto 0', textAlign: 'left',
                }}>
                  <div style={{
                    display: 'flex', gap: 8, alignItems: 'center',
                    background: 'var(--lm-bg-elevated)', borderRadius: 14,
                    border: '1px solid var(--lm-border-light)', padding: '10px 14px',
                  }}>
                    <SearchOutlined style={{ color: 'var(--lm-text-tertiary)', fontSize: 16 }} />
                    <Input
                      value={templateValues.account}
                      onChange={(e) => updateTemplateValue('account', e.target.value)}
                      placeholder="输入账号、订单号、traceId 或错误关键词..."
                      style={{ border: 'none', background: 'transparent', boxShadow: 'none', fontSize: 14, flex: 1 }}
                      onPressEnter={() => {
                        if (templateValues.account.trim()) {
                          sendMessage(templateValues.account.trim());
                        }
                      }}
                    />
                    <Button
                      type="primary"
                      icon={<SearchOutlined />}
                      onClick={() => {
                        if (templateValues.account.trim()) {
                          sendMessage(templateValues.account.trim());
                        }
                      }}
                      disabled={!templateValues.account.trim() || sending}
                      style={{ borderRadius: 10 }}
                    >
                      搜索
                    </Button>
                  </div>

                  <div style={{ display: 'flex', gap: 8, marginTop: 10, justifyContent: 'center' }}>
                    <Select
                      value={templateValues.hours}
                      onChange={(value) => updateTemplateValue('hours', value)}
                      style={{ width: 120 }}
                      size="small"
                      options={[
                        { value: '1', label: '最近1小时' },
                        { value: '3', label: '最近3小时' },
                        { value: '6', label: '最近6小时' },
                        { value: '24', label: '最近24小时' },
                      ]}
                    />
                    <Select
                      value={templateValues.serviceScope}
                      onChange={(value) => updateTemplateValue('serviceScope', value)}
                      style={{ width: 130 }}
                      size="small"
                      options={[
                        { value: 'all', label: '全部业务线' },
                        { value: 'core', label: '核心业务线' },
                      ]}
                    />
                  </div>
                </div>

                {/* Quick Actions */}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginTop: 24 }}>
                  {(liveRecommendations.length > 0
                    ? liveRecommendations.slice(0, 4).map((item) => item.prompt)
                    : WELCOME_SUGGESTIONS.slice(0, 4)
                  ).map((s, i) => (
                    <div
                      key={i}
                      onClick={() => sendMessage(s)}
                      style={{
                        padding: '7px 12px', borderRadius: 10, cursor: 'pointer', fontSize: 12,
                        background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
                        color: 'var(--lm-text-secondary)', transition: 'all 0.2s',
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(22,119,255,0.3)'; e.currentTarget.style.color = 'var(--lm-text)'; }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--lm-border-light)'; e.currentTarget.style.color = 'var(--lm-text-secondary)'; }}
                    >
                      <ThunderboltOutlined style={{ marginRight: 5, color: '#1677ff' }} />{s}
                    </div>
                  ))}
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

            {/* Diagnostic Clues from Smart Search */}
            {diagnosticClues.length > 0 && !sending && (
              <div style={{ marginBottom: 16, marginLeft: 46, animation: 'lm-fadeSlideIn 0.3s ease-out' }}>
                <DiagnosticClues
                  clues={diagnosticClues}
                  summary={searchSummary}
                  onAction={(prompt) => sendMessage(prompt)}
                />
              </div>
            )}

            {/* Multi-Agent Collaboration */}
            {multiAgentFindings.length > 0 && (
              <div style={{
                marginBottom: 16, marginLeft: 46,
                padding: '14px 16px', borderRadius: 14,
                background: 'linear-gradient(135deg, rgba(114,46,209,0.03), rgba(22,119,255,0.03))',
                border: '1px solid rgba(114,46,209,0.12)',
                animation: 'lm-fadeSlideIn 0.3s ease-out',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                  <BranchesOutlined style={{ color: '#722ed1', fontSize: 14 }} />
                  <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
                    多Agent协作诊断
                  </Text>
                  <Tag color="purple" style={{ margin: 0, borderRadius: 999, fontSize: 10 }}>
                    {multiAgentFindings.filter(f => f.status === 'done').length}/{multiAgentFindings.length} 完成
                  </Tag>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
                  {multiAgentFindings.map((finding) => (
                    <div key={finding.name} style={{
                      padding: '10px 12px', borderRadius: 10,
                      background: 'var(--lm-bg-card)',
                      border: `1px solid ${finding.status === 'done' ? 'rgba(82,196,26,0.2)' : finding.status === 'error' ? 'rgba(255,77,79,0.2)' : 'var(--lm-border-light)'}`,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                        {finding.status === 'running' && <LoadingOutlined style={{ fontSize: 11, color: '#722ed1' }} />}
                        {finding.status === 'done' && <CheckCircleOutlined style={{ fontSize: 11, color: '#52c41a' }} />}
                        {finding.status === 'error' && <ExclamationCircleOutlined style={{ fontSize: 11, color: '#ff4d4f' }} />}
                        <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
                          {finding.displayName}
                        </Text>
                      </div>
                      {finding.summary && (
                        <Text style={{ fontSize: 11, color: 'var(--lm-text-secondary)', lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                          {finding.summary}
                        </Text>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

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

            {/* Trace Linked Operations Visualization */}
            {traceSegments.length > 0 && !sending && (
              <div style={{ marginBottom: 16, marginLeft: 46, animation: 'lm-fadeSlideIn 0.3s ease-out' }}>
                {Object.keys(traceTopology).length > 0 && (
                  <ServiceFlowDiagram topology={traceTopology} errorServices={traceErrorServices} />
                )}
                <TraceTimeline
                  segments={traceSegments}
                  uncorrelatedEntries={traceUncorrelated}
                  summary={traceSummary}
                />
              </div>
            )}

            {timelineEntries.length > 0 && !sending && (
              <div style={{
                marginBottom: 16, marginLeft: 46,
                padding: '14px 16px',
                borderRadius: 14,
                background: 'var(--lm-bg-card)',
                border: '1px solid var(--lm-border-light)',
                animation: 'lm-fadeSlideIn 0.3s ease-out',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <ClockCircleOutlined style={{ color: '#1677ff', fontSize: 14 }} />
                  <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
                    操作时间线
                  </Text>
                  {timelineSummary && (
                    <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                      {timelineSummary}
                    </Text>
                  )}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {timelineEntries.map((entry, index) => (
                    <div
                      key={`${entry.time}-${entry.service}-${index}`}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '120px 120px 1fr',
                        gap: 10,
                        alignItems: 'start',
                        padding: '10px 12px',
                        borderRadius: 10,
                        background: 'var(--lm-bg-elevated)',
                        border: '1px solid var(--lm-border-light)',
                      }}
                    >
                      <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)', fontFamily: 'monospace' }}>
                        {entry.time}
                      </Text>
                      <div>
                        <Text style={{ display: 'block', fontSize: 12, color: 'var(--lm-text)', fontWeight: 600 }}>
                          {entry.service}
                        </Text>
                        <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                          {entry.filetype || entry.domain}
                        </Text>
                      </div>
                      <div>
                        <Text style={{ display: 'block', fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
                          {entry.action}
                        </Text>
                        <div style={{ marginTop: 4, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          {entry.identity && <Tag style={{ margin: 0, borderRadius: 999 }}>{entry.identity}</Tag>}
                          {entry.host && <Tag style={{ margin: 0, borderRadius: 999 }}>{entry.host}</Tag>}
                        </div>
                      </div>
                    </div>
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
              display: 'flex', gap: 12, alignItems: 'flex-end',
              background: 'var(--lm-bg-elevated)', borderRadius: 14,
              border: `1px solid ${input.length > 7500 ? 'rgba(255,77,79,0.4)' : 'var(--lm-border-light)'}`, padding: '8px 12px',
              transition: 'border-color 0.2s',
            }}>
              <Input.TextArea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                placeholder="描述你的问题... (Ctrl+Enter 发送)"
                autoSize={{ minRows: 1, maxRows: 4 }}
                maxLength={8000}
                style={{ border: 'none', background: 'transparent', boxShadow: 'none', fontSize: 14, resize: 'none', padding: '4px 0' }}
                onPressEnter={(e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); sendMessage(); } }}
                disabled={sending}
              />
              <Button
                type="primary"
                shape="circle"
                icon={sending ? <LoadingOutlined /> : <SendOutlined />}
                onClick={() => sendMessage()}
                disabled={!input.trim() || sending || input.length > 8000}
                style={{ flexShrink: 0 }}
              />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, padding: '0 4px' }}>
              <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                Ctrl+Enter 发送 · 多轮推理 · 多Agent协作
              </Text>
              {input.length > 0 && (
                <Text style={{ fontSize: 11, color: input.length > 7500 ? '#ff4d4f' : 'var(--lm-text-tertiary)' }}>
                  {input.length} / 8000
                </Text>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatPage;
