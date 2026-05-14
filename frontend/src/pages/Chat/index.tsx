import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { Button, Input, Typography, Space, Tag, message, Tooltip, Select } from 'antd';
import {
  SendOutlined, PlusOutlined, DeleteOutlined, RobotOutlined,
  UserOutlined, ThunderboltOutlined, CopyOutlined,
  LoadingOutlined, BranchesOutlined, ExportOutlined,
  QuestionCircleOutlined, ClockCircleOutlined, RadarChartOutlined,
  AlertOutlined, CheckCircleOutlined, ExclamationCircleOutlined,
  HistoryOutlined, FireOutlined, AppstoreOutlined,
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

interface StreamEvent {
  type?: string;
  [key: string]: unknown;
}

const WELCOME_SUGGESTIONS = [
  '最近1小时有哪些关键错误？',
  '帮我分析 auth-service 的超时问题',
  '最近有什么告警需要关注？',
  '系统整体健康状况如何？',
  '对比今天和昨天的错误分布',
  '帮我追踪 NPE 的调用链',
];

const COMPOSER_PRESETS = [
  {
    key: 'blast-radius',
    label: '影响范围',
    prompt: '请分析当前异常的影响范围、受影响服务和优先处理顺序。',
  },
  {
    key: 'timeline',
    label: '追时间线',
    prompt: '请按时间线梳理这次故障，从最早异常到当前影响逐步说明。',
  },
  {
    key: 'compare',
    label: '对比窗口',
    prompt: '请对比最近1小时与昨天同时间段的错误趋势和差异点。',
  },
  {
    key: 'next-step',
    label: '下一步',
    prompt: '请把下一步排查动作拆成 3-5 个明确步骤，并说明每步要验证什么。',
  },
];

const priorityMeta: Record<LiveRecommendation['priority'], { color: string; label: string }> = {
  critical: { color: '#ff4d4f', label: '马上处理' },
  warning: { color: '#faad14', label: '值得关注' },
  info: { color: '#1677ff', label: '建议先问' },
};

const panelStyle: React.CSSProperties = {
  borderRadius: 18,
  border: '1px solid var(--lm-border-light)',
  background: 'linear-gradient(180deg, var(--lm-bg-container), var(--lm-bg-card))',
  boxShadow: 'var(--lm-shadow-elevated)',
  backdropFilter: 'blur(18px)',
  WebkitBackdropFilter: 'blur(18px)',
  overflow: 'hidden',
};

const sectionStyle: React.CSSProperties = {
  padding: 16,
  borderRadius: 16,
  border: '1px solid var(--lm-border-light)',
  background: 'var(--lm-bg-elevated)',
};

const formatRelativeTime = (iso?: string) => {
  if (!iso) return '刚刚';
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso;
  const diffMinutes = Math.max(0, Math.round((Date.now() - ts) / 60000));
  if (diffMinutes < 1) return '刚刚';
  if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours} 小时前`;
  const diffDays = Math.round(diffHours / 24);
  return `${diffDays} 天前`;
};

const getSessionLabel = (session: SessionSummary) =>
  session.title?.trim() || `新会话 ${session.id.slice(0, 6)}`;

const getSessionDescription = (session: SessionSummary) =>
  session.message_count > 0 ? `${session.message_count} 条消息` : '还没有消息';

const buildFollowUpsFromAssistant = (assistantContent: string) => {
  const dividerIdx = assistantContent.lastIndexOf('---');
  if (dividerIdx <= 0) return [];
  const afterDivider = assistantContent.slice(dividerIdx + 3);
  return afterDivider
    .split('\n')
    .map((line) => line.replace(/^[\s\-\d.*]+/, '').trim())
    .filter((line) => line.length > 4 && line.length < 60 && !line.startsWith('#'))
    .slice(0, 3);
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
  const [multiAgentFindings, setMultiAgentFindings] = useState<{ name: string; displayName: string; status: string; summary: string }[]>([]);
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
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<TextAreaRef>(null);
  const token = useAuthStore((s) => s.token);
  const location = useLocation();

  const dynamicTemplates = useMemo<DynamicQuestionTemplate[]>(() => ([
    {
      id: 'account-activity',
      title: '账号操作追踪',
      description: '输入账号或订单号，回放最近关键操作轨迹。',
      icon: <ClockCircleOutlined />,
      accent: '#1677ff',
      defaults: { account: '', hours: '1', serviceName: '', serviceNames: [], serviceScope: 'core', keyword: '' },
      buildPrompt: (values) => (
        `请帮我查询账号 ${values.account} 在最近 ${values.hours || '1'} 小时做了哪些操作，` +
        `${values.keyword ? `同时关注关键词 ${values.keyword}，` : ''}` +
        `${values.serviceScope === 'all'
          ? '覆盖全部业务线，'
          : values.serviceScope === 'core'
            ? '覆盖核心业务线，'
            : values.serviceScope === 'selected' && values.serviceNames.length
              ? `重点查看 ${values.serviceNames.join('、')}，`
              : values.serviceName
                ? `重点查看 ${values.serviceName}，`
                : ''}` +
        '按时间线列出关键动作、异常和影响服务。'
      ),
    },
    {
      id: 'service-errors',
      title: '服务错误诊断',
      description: '按服务和时间范围定位错误峰值、异常模式和影响范围。',
      icon: <RadarChartOutlined />,
      accent: '#52c41a',
      defaults: { account: '', hours: '1', serviceName: '', serviceNames: [], serviceScope: 'single', keyword: '' },
      buildPrompt: (values) => (
        `请分析 ${values.serviceName || '目标服务'} 最近 ${values.hours || '1'} 小时的关键错误、异常趋势和影响范围，` +
        `${values.keyword ? `重点关注关键词 ${values.keyword}，` : ''}` +
        '并给出下一步排查建议。'
      ),
    },
    {
      id: 'alert-check',
      title: '告警关联排查',
      description: '查看重要告警并判断与服务健康、账号行为的关联。',
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
  const isMobile = viewportWidth < 1180;
  const showRightRail = viewportWidth >= 1580;

  const resetDiagnosticState = useCallback(() => {
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
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      const { data } = await chatApi.listSessions();
      setSessions(data?.sessions || []);
    } catch {
      // ignore
    }
  }, []);

  const loadBusinessLines = useCallback(async () => {
    try {
      const { data } = await businessLineApi.list({ page_size: 100 });
      const items = Array.isArray(data?.items) ? data.items as BusinessLineOption[] : [];
      setBusinessLines(items.map((item) => ({
        id: item.id,
        name: item.name,
      })));
    } catch {
      // ignore
    }
  }, []);

  const loadRecommendations = useCallback(async () => {
    try {
      const { data } = await chatApi.getRecommendations({ window_minutes: 60, limit: 6 });
      setLiveRecommendations(Array.isArray(data?.items) ? data.items as LiveRecommendation[] : []);
    } catch {
      setLiveRecommendations([]);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadBusinessLines();
  }, [loadBusinessLines]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadRecommendations();
    const timer = window.setInterval(() => {
      loadRecommendations();
    }, 60000);
    return () => window.clearInterval(timer);
  }, [loadRecommendations]);

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, toolSteps, thinkingRound, followUps, suggestedActions]);

  const createSession = async () => {
    try {
      const { data } = await chatApi.createSession();
      setActiveSessionId(data.id);
      setMessages([]);
      setInput('');
      resetDiagnosticState();
      loadSessions();
      window.setTimeout(() => inputRef.current?.focus(), 80);
    } catch {
      message.error('创建会话失败');
    }
  };

  const loadSession = async (sessionId: string) => {
    setActiveSessionId(sessionId);
    resetDiagnosticState();
    try {
      const { data } = await chatApi.getSession(sessionId);
      const rawMessages = Array.isArray(data?.messages) ? data.messages as RawChatMessage[] : [];
      setMessages(rawMessages.map((item) => ({
        role: item.role,
        content: item.content,
        timestamp: item.timestamp,
        metadata: item.metadata,
      })));
      const assistantMessages = rawMessages.filter((item) => item.role === 'assistant');
      const lastAssistant = assistantMessages[assistantMessages.length - 1];
      setSuggestedActions(lastAssistant?.metadata?.suggested_actions || []);
    } catch {
      setMessages([]);
    }
  };

  const deleteSession = async (sessionId: string) => {
    try {
      await chatApi.deleteSession(sessionId);
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setMessages([]);
        resetDiagnosticState();
      }
      loadSessions();
    } catch {
      // ignore
    }
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

  const copyContent = (text: string) => {
    navigator.clipboard.writeText(text).then(() => message.success('已复制'));
  };

  const exportSession = () => {
    if (!messages.length) return;
    const md = messages
      .map((item) => item.role === 'user' ? `## 用户\n${item.content}` : `## AI 助手\n${item.content}`)
      .join('\n\n---\n\n');
    const header = `# LogMind 诊断报告\n\n> 时间: ${new Date().toLocaleString()}\n> 工具调用: ${toolSteps.length} 次\n\n---\n\n`;
    navigator.clipboard.writeText(header + md).then(() => message.success('诊断报告已复制到剪贴板'));
  };

  const sendMessage = useCallback(async (content?: string) => {
    const text = (content ?? input).trim();
    if (!text || sending) return;

    setInput('');
    let sessionId = activeSessionId;
    if (!sessionId) {
      try {
        const { data } = await chatApi.createSession();
        sessionId = data.id;
        setActiveSessionId(sessionId);
      } catch {
        message.error('创建会话失败');
        return;
      }
    }

    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text, timestamp: new Date().toISOString() },
      { role: 'assistant', content: '', isStreaming: true, timestamp: new Date().toISOString() },
    ]);
    setSending(true);
    resetDiagnosticState();

    try {
      const isDev = window.location.port === '3000';
      const baseUrl = isDev ? 'http://localhost:8000' : '';
      const response = await fetch(`${baseUrl}/api/v1/chat/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ content: text }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let assistantContent = '';
      let latestSuggestedActions: SuggestedAction[] = [];
      let buffer = '';

      const applyAssistantContent = (nextContent: string, isStreaming = true, metadata?: ChatMessage['metadata']) => {
        setMessages((prev) => {
          const updated = [...prev];
          const lastIndex = updated.length - 1;
          const last = updated[lastIndex];
          if (last?.role === 'assistant') {
            updated[lastIndex] = { ...last, content: nextContent, isStreaming, metadata };
          }
          return updated;
        });
      };

      const handleEvent = (event: StreamEvent) => {
        if (event.type === 'thinking') {
          setThinkingRound((event.round as number) || 0);
          setThinkingText((event.content as string) || '');
          return;
        }

        if (event.type === 'tool_call') {
          setToolSteps((prev) => [...prev, {
            name: (event.name as string) || 'unknown_tool',
            args: event.args,
            round: (event.round as number) || 0,
            status: 'running',
            startTime: Date.now(),
          }]);
          return;
        }

        if (event.type === 'tool_result') {
          setToolSteps((prev) => prev.map((step) =>
            step.name === event.name && step.status === 'running'
              ? {
                ...step,
                result: event.result as string | undefined,
                summary: event.summary as string | undefined,
                status: 'done' as const,
                endTime: Date.now(),
              }
              : step
          ));

          if (event.name === 'trace_linked_operations') {
            try {
              const parsed = JSON.parse((event.result as string) || '{}');
              const segs = parsed.trace_segments || [];
              if (Array.isArray(segs) && segs.length > 0) {
                setTraceSegments(segs);
                setTraceUncorrelated(parsed.uncorrelated_entries || []);
                setTraceTopology(parsed.service_topology || {});
                setTraceSummary(parsed.summary || '');
                const errSvcs = segs
                  .filter((segment: TraceSegment) => segment.has_error)
                  .flatMap((segment: TraceSegment) => (
                    segment.nodes
                      .filter((node: TraceNode) => node.level === 'error')
                      .map((node: TraceNode) => node.service)
                  ));
                setTraceErrorServices([...new Set(errSvcs)]);
              }
            } catch {
              // ignore
            }
          } else if (event.name === 'query_operation_timeline' || event.name === 'query_account_activity') {
            try {
              const parsed = JSON.parse((event.result as string) || '{}');
              const nextTimeline = parsed.timeline || parsed.activities || [];
              if (Array.isArray(nextTimeline) && nextTimeline.length > 0) {
                setTimelineEntries(nextTimeline.slice(0, 30));
                setTimelineSummary(parsed.summary || '');
              }
            } catch {
              // ignore
            }
          }
          return;
        }

        if (event.type === 'multi_agent_start') {
          setThinkingRound(0);
          setThinkingText('多 Agent 协作诊断中...');
          const agents = ((event.agents as Array<{ name: string; display_name: string }>) || []).map((item) => ({
            name: item.name,
            displayName: item.display_name,
            status: 'running',
            summary: '',
          }));
          setMultiAgentFindings(agents);
          return;
        }

        if (event.type === 'agent_done') {
          setMultiAgentFindings((prev) => prev.map((finding) =>
            finding.name === event.agent
              ? { ...finding, status: (event.status as string) || 'done', summary: (event.summary as string) || '' }
              : finding
          ));
          return;
        }

        if (event.type === 'search_clues') {
          setDiagnosticClues((event.clues as DiagnosticClue[]) || []);
          setSearchSummary((event.summary as string) || '');
          setThinkingText('搜索完成，正在整理诊断...');
          return;
        }

        if (event.type === 'step_done') {
          setThinkingText(`第 ${String(event.round || 0)}/${String(event.total_rounds || 0)} 轮完成，继续分析中...`);
          return;
        }

        if (event.type === 'token') {
          assistantContent += (event.content as string) || '';
          setThinkingRound(0);
          applyAssistantContent(assistantContent, true);
          return;
        }

        if (event.type === 'suggested_actions') {
          latestSuggestedActions = (event.actions as SuggestedAction[]) || [];
          setSuggestedActions(latestSuggestedActions);
          return;
        }

        if (event.type === 'done') {
          applyAssistantContent(assistantContent, false, { suggested_actions: latestSuggestedActions });
          setFollowUps(buildFollowUpsFromAssistant(assistantContent));
          return;
        }

        if (event.type === 'error') {
          message.error((event.message as string) || '流式响应出错');
        }
      };

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));
              handleEvent(event);
            } catch {
              // skip malformed event frames
            }
          }
        }

        const finalLine = buffer.trim();
        if (finalLine.startsWith('data: ')) {
          try {
            const event = JSON.parse(finalLine.slice(6));
            handleEvent(event);
          } catch {
            // ignore trailing fragment
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
      message.error(`发送失败: ${errorMessage}`);
      setMessages((prev) => {
        const updated = [...prev];
        const lastIndex = updated.length - 1;
        if (updated[lastIndex]?.role === 'assistant') {
          updated[lastIndex] = {
            ...updated[lastIndex],
            content: '⚠️ 连接失败，请稍后重试或重新发起问题。',
            isStreaming: false,
          };
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
  }, [activeSessionId, input, loadSessions, resetDiagnosticState, sending, token]);

  const sendTemplatePrompt = useCallback(() => {
    if (activeTemplateId === 'account-activity' && !templateValues.account.trim()) {
      message.warning('请输入账号或查询对象');
      return;
    }
    if (templateValues.serviceScope === 'selected' && templateValues.serviceNames.length === 0) {
      message.warning('多业务线模式下请至少选择一个业务线');
      return;
    }
    if ((activeTemplateId === 'service-errors' || activeTemplateId === 'alert-check') && !templateValues.serviceName.trim()) {
      message.warning('请选择服务后再发起查询');
      return;
    }
    const builtPrompt = activeTemplate.buildPrompt(templateValues).trim();
    sendMessage(builtPrompt);
  }, [activeTemplate, activeTemplateId, sendMessage, templateValues]);

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

  const sessionStats = useMemo(() => ({
    totalSessions: sessions.length,
    totalMessages: sessions.reduce((acc, session) => acc + session.message_count, 0),
    toolCount: toolSteps.length,
    clueCount: diagnosticClues.length + traceSegments.length + timelineEntries.length,
  }), [diagnosticClues.length, sessions, timelineEntries.length, toolSteps.length, traceSegments.length]);

  const smartRecommendations = useMemo(() => {
    if (suggestedActions.length > 0) {
      return suggestedActions.map((item, index) => ({
        id: `${item.label}-${index}`,
        label: item.label,
        prompt: item.prompt,
      }));
    }
    if (followUps.length > 0) {
      return followUps.map((item, index) => ({
        id: `follow-up-${index}`,
        label: item,
        prompt: item,
      }));
    }
    return COMPOSER_PRESETS.map((item) => ({
      id: item.key,
      label: item.label,
      prompt: item.prompt,
    }));
  }, [followUps, suggestedActions]);

  const assistantMessageCount = messages.filter((item) => item.role === 'assistant').length;
  const showComposerSummary = !showRightRail && messages.length === 0 && (
    sessionStats.toolCount > 0 ||
    timelineEntries.length > 0 ||
    traceSegments.length > 0 ||
    diagnosticClues.length > 0
  );

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: isMobile ? 'column' : 'row',
        gap: 18,
        height: isMobile ? 'auto' : 'calc(100vh - 56px)',
        minHeight: 'calc(100vh - 56px)',
        margin: '-24px',
        padding: 18,
        overflowX: 'hidden',
        overflowY: isMobile ? 'auto' : 'hidden',
        background: 'radial-gradient(circle at top left, rgba(22,119,255,0.16), transparent 28%), radial-gradient(circle at bottom right, rgba(114,46,209,0.12), transparent 24%), var(--lm-bg-layout)',
      }}
    >
      <div
        style={{
          ...panelStyle,
          width: isMobile ? '100%' : 286,
          minWidth: isMobile ? undefined : 286,
          display: 'flex',
          flexDirection: 'column',
          maxHeight: isMobile ? 320 : '100%',
        }}
      >
        <div style={{ padding: 18, borderBottom: '1px solid var(--lm-border-light)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <div
              style={{
                width: 42,
                height: 42,
                borderRadius: 14,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: 'linear-gradient(135deg, #1677ff, #69b1ff)',
                color: '#fff',
              }}
            >
              <RobotOutlined style={{ fontSize: 18 }} />
            </div>
            <div>
              <Text style={{ display: 'block', fontSize: 16, fontWeight: 700, color: 'var(--lm-text)' }}>
                LogMind Chat
              </Text>
              <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                会话诊断工作台
              </Text>
            </div>
          </div>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            style={{ width: '100%', height: 42, borderRadius: 12 }}
            onClick={createSession}
          >
            新建会话
          </Button>
        </div>

        <div style={{ padding: '14px 16px 10px' }}>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
              gap: 10,
            }}
          >
            {[
              { label: '会话数', value: sessionStats.totalSessions, icon: <HistoryOutlined />, color: '#1677ff' },
              { label: '消息数', value: sessionStats.totalMessages, icon: <AppstoreOutlined />, color: '#722ed1' },
            ].map((item) => (
              <div
                key={item.label}
                style={{
                  padding: 12,
                  borderRadius: 14,
                  background: 'var(--lm-bg-elevated)',
                  border: '1px solid var(--lm-border-light)',
                }}
              >
                <div style={{ color: item.color, marginBottom: 8 }}>{item.icon}</div>
                <Text style={{ display: 'block', fontSize: 20, fontWeight: 700, color: 'var(--lm-text)' }}>
                  {item.value}
                </Text>
                <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                  {item.label}
                </Text>
              </div>
            ))}
          </div>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: '0 10px 12px' }}>
          {sessions.length === 0 && (
            <div
              style={{
                padding: 18,
                borderRadius: 16,
                border: '1px dashed var(--lm-border-light)',
                background: 'var(--lm-bg-elevated)',
                textAlign: 'center',
              }}
            >
              <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                还没有历史会话，直接开始一个新的诊断问题吧。
              </Text>
            </div>
          )}

          {sessions.map((session) => (
            <div
              key={session.id}
              onClick={() => loadSession(session.id)}
              style={{
                padding: 14,
                borderRadius: 16,
                marginBottom: 10,
                cursor: 'pointer',
                border: activeSessionId === session.id
                  ? '1px solid rgba(22,119,255,0.28)'
                  : '1px solid var(--lm-border-light)',
                background: activeSessionId === session.id
                  ? 'linear-gradient(180deg, rgba(22,119,255,0.10), rgba(22,119,255,0.04))'
                  : 'var(--lm-bg-elevated)',
              }}
            >
              <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                <div
                  style={{
                    width: 34,
                    height: 34,
                    borderRadius: 12,
                    background: activeSessionId === session.id
                      ? 'linear-gradient(135deg, #1677ff, #69b1ff)'
                      : 'rgba(22,119,255,0.10)',
                    color: activeSessionId === session.id ? '#fff' : 'var(--lm-text-secondary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  <HistoryOutlined />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Text
                    style={{
                      display: 'block',
                      fontSize: 13,
                      fontWeight: 600,
                      color: 'var(--lm-text)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {getSessionLabel(session)}
                  </Text>
                  <Text style={{ display: 'block', fontSize: 11, color: 'var(--lm-text-tertiary)', marginTop: 4 }}>
                    {getSessionDescription(session)}
                  </Text>
                </div>
                <DeleteOutlined
                  style={{ color: 'var(--lm-text-tertiary)', marginTop: 2 }}
                  onClick={(event) => {
                    event.stopPropagation();
                    deleteSession(session.id);
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div
        style={{
          ...panelStyle,
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          maxHeight: isMobile ? 'none' : '100%',
        }}
      >
        <div style={{ padding: messages.length > 0 ? '16px 20px' : 20, borderBottom: '1px solid var(--lm-border-light)' }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: isMobile ? 'flex-start' : 'center',
              gap: 16,
              flexWrap: 'wrap',
            }}
          >
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                <Tag color="blue" style={{ borderRadius: 999, margin: 0 }}>
                  多轮诊断
                </Tag>
                <Tag color="purple" style={{ borderRadius: 999, margin: 0 }}>
                  流式响应
                </Tag>
                {sending && (
                  <Tag color="processing" style={{ borderRadius: 999, margin: 0 }}>
                    AI 正在分析
                  </Tag>
                )}
              </div>
              <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
                {activeSessionId
                  ? (sessions.find((session) => session.id === activeSessionId)?.title || '当前对话')
                  : '更强的 Chat 诊断页'}
              </Title>
              {messages.length === 0 && (
                <Text style={{ fontSize: 13, color: 'var(--lm-text-secondary)' }}>
                  支持诊断线索、工具推理链、链路追踪和快捷深挖动作，底部输入区改成持续可用的工作区。
                </Text>
              )}
            </div>

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'stretch' }}>
              <div style={{ ...sectionStyle, minWidth: 140, padding: '12px 14px' }}>
                <Text style={{ display: 'block', fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                  AI 回复
                </Text>
                <Text style={{ fontSize: 22, fontWeight: 700, color: 'var(--lm-text)' }}>
                  {assistantMessageCount}
                </Text>
              </div>
              <div style={{ ...sectionStyle, minWidth: 140, padding: '12px 14px' }}>
                <Text style={{ display: 'block', fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                  当前线索
                </Text>
                <Text style={{ fontSize: 22, fontWeight: 700, color: 'var(--lm-text)' }}>
                  {sessionStats.clueCount}
                </Text>
              </div>
              {messages.length > 0 && (
                <div style={{ ...sectionStyle, minWidth: 140, padding: '12px 14px' }}>
                  <Text style={{ display: 'block', fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                    对话消息
                  </Text>
                  <Text style={{ fontSize: 22, fontWeight: 700, color: 'var(--lm-text)' }}>
                    {messages.length}
                  </Text>
                </div>
              )}
              {messages.length > 0 && (
                <Tooltip title="复制当前会话为诊断报告">
                  <Button
                    icon={<ExportOutlined />}
                    style={{ height: 44, borderRadius: 12 }}
                    onClick={exportSession}
                  >
                    导出报告
                  </Button>
                </Tooltip>
              )}
            </div>
          </div>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: messages.length > 0 ? '16px 20px 20px' : 20 }}>
          {messages.length === 0 && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: showRightRail ? 'minmax(0, 1.4fr) minmax(320px, 0.8fr)' : '1fr',
                gap: 18,
                alignItems: 'start',
              }}
            >
              <div style={{ ...sectionStyle, padding: 24 }}>
                <div
                  style={{
                    width: 72,
                    height: 72,
                    borderRadius: 24,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: 'linear-gradient(135deg, rgba(22,119,255,0.14), rgba(114,46,209,0.16))',
                    border: '1px solid rgba(22,119,255,0.14)',
                    marginBottom: 18,
                  }}
                >
                  <ThunderboltOutlined style={{ fontSize: 32, color: '#1677ff' }} />
                </div>
                <Title level={3} style={{ marginBottom: 8, color: 'var(--lm-text)' }}>
                  一页完成搜索、诊断、追踪和追问
                </Title>
                <Text style={{ display: 'block', fontSize: 14, lineHeight: 1.8, color: 'var(--lm-text-secondary)' }}>
                  你可以直接输入账号、订单号、traceId、报错关键词或者服务名。页面会在同一工作台里展示回复、推理链、时间线、链路和推荐动作，不需要来回切换。
                </Text>

                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, minmax(0, 1fr))',
                    gap: 12,
                    marginTop: 22,
                  }}
                >
                  {[
                    {
                      title: '更强输入区',
                      desc: '支持 Enter 发送、Shift+Enter 换行，底部始终保留快捷动作。',
                      icon: <CheckCircleOutlined style={{ color: '#1677ff' }} />,
                    },
                    {
                      title: '诊断副驾驶',
                      desc: '右侧面板集中放推荐问题、模板和上下文摘要。',
                      icon: <BranchesOutlined style={{ color: '#722ed1' }} />,
                    },
                    {
                      title: '上下文深挖',
                      desc: '自动接住工具链、时间线、链路图和线索卡片。',
                      icon: <FireOutlined style={{ color: '#fa541c' }} />,
                    },
                  ].map((item) => (
                    <div
                      key={item.title}
                      style={{
                        padding: 16,
                        borderRadius: 16,
                        background: 'linear-gradient(180deg, var(--lm-bg-container), var(--lm-bg-elevated))',
                        border: '1px solid var(--lm-border-light)',
                      }}
                    >
                      <div style={{ marginBottom: 10 }}>{item.icon}</div>
                      <Text style={{ display: 'block', fontSize: 14, fontWeight: 700, color: 'var(--lm-text)' }}>
                        {item.title}
                      </Text>
                      <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.7 }}>
                        {item.desc}
                      </Text>
                    </div>
                  ))}
                </div>

                <div style={{ marginTop: 24 }}>
                  <Text style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
                    开场问题
                  </Text>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 10 }}>
                    {(liveRecommendations.length > 0
                      ? liveRecommendations.slice(0, 4).map((item) => item.prompt)
                      : WELCOME_SUGGESTIONS.slice(0, 4)
                    ).map((prompt, index) => (
                      <div
                        key={`${prompt}-${index}`}
                        onClick={() => sendMessage(prompt)}
                        style={{
                          padding: '10px 14px',
                          borderRadius: 999,
                          border: '1px solid rgba(22,119,255,0.16)',
                          background: 'rgba(22,119,255,0.10)',
                          color: '#1677ff',
                          cursor: 'pointer',
                          fontSize: 12,
                          fontWeight: 600,
                        }}
                      >
                        {prompt}
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div style={{ ...sectionStyle, padding: 20 }}>
                <Text style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--lm-text)', marginBottom: 12 }}>
                  快速诊断模板
                </Text>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {dynamicTemplates.map((template) => (
                    <div
                      key={template.id}
                      onClick={() => applyTemplate(template.id)}
                      style={{
                        padding: 14,
                        borderRadius: 14,
                        border: activeTemplateId === template.id
                          ? `1px solid ${template.accent}44`
                          : '1px solid var(--lm-border-light)',
                        background: activeTemplateId === template.id
                          ? `${template.accent}12`
                          : 'rgba(255,255,255,0.72)',
                        cursor: 'pointer',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ color: template.accent, fontSize: 16 }}>{template.icon}</span>
                        <div>
                          <Text style={{ display: 'block', fontSize: 13, fontWeight: 700, color: 'var(--lm-text)' }}>
                            {template.title}
                          </Text>
                          <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)' }}>
                            {template.description}
                          </Text>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {messages.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18, minHeight: '100%' }}>
              {messages.map((msg, idx) => (
                <div
                  key={`${msg.role}-${idx}`}
                  style={{
                    display: 'flex',
                    gap: 12,
                    justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                    width: '100%',
                  }}
                >
                  {msg.role !== 'user' && (
                    <div
                      style={{
                        width: 38,
                        height: 38,
                        borderRadius: 14,
                        background: 'linear-gradient(135deg, rgba(114,46,209,0.18), rgba(22,119,255,0.18))',
                        border: '1px solid rgba(114,46,209,0.16)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                      }}
                    >
                      <RobotOutlined style={{ color: '#722ed1' }} />
                    </div>
                  )}

                  <div
                    style={{
                      width: msg.role === 'assistant' ? 'min(100%, 980px)' : 'fit-content',
                      maxWidth: msg.role === 'assistant' ? 'calc(100% - 50px)' : 'min(920px, 82%)',
                      minWidth: isMobile ? 0 : (msg.role === 'assistant' ? 320 : 180),
                      padding: msg.role === 'user' ? '14px 16px' : '16px 18px',
                      borderRadius: 20,
                      background: msg.role === 'user'
                        ? 'linear-gradient(135deg, #1677ff, #4096ff)'
                        : 'var(--lm-bg-elevated)',
                      color: msg.role === 'user' ? '#fff' : 'var(--lm-text)',
                      border: msg.role === 'user' ? 'none' : '1px solid var(--lm-border-light)',
                      boxShadow: msg.role === 'user'
                        ? '0 18px 40px rgba(22,119,255,0.18)'
                        : 'var(--lm-shadow-card)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      {msg.role === 'user' ? (
                        <UserOutlined style={{ color: 'rgba(255,255,255,0.85)' }} />
                      ) : (
                        <RobotOutlined style={{ color: '#722ed1' }} />
                      )}
                      <Text style={{ fontSize: 12, color: msg.role === 'user' ? 'rgba(255,255,255,0.72)' : 'var(--lm-text-tertiary)' }}>
                        {msg.role === 'user' ? '你' : 'LogMind AI'}
                      </Text>
                      {msg.timestamp && (
                        <Text style={{ fontSize: 12, color: msg.role === 'user' ? 'rgba(255,255,255,0.58)' : 'var(--lm-text-tertiary)' }}>
                          {formatRelativeTime(msg.timestamp)}
                        </Text>
                      )}
                    </div>

                    {msg.role === 'assistant' ? (
                      <>
                        {msg.content ? (
                          <div className="lm-markdown-content" style={{ fontSize: 14, lineHeight: 1.8 }}>
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                          </div>
                        ) : msg.isStreaming ? (
                          <Space>
                            <LoadingOutlined style={{ color: '#722ed1' }} />
                            <Text style={{ color: 'var(--lm-text-tertiary)' }}>正在生成诊断结论...</Text>
                          </Space>
                        ) : null}

                        {msg.isStreaming && msg.content && (
                          <span
                            className="lm-cursor-blink"
                            style={{
                              display: 'inline-block',
                              width: 2,
                              height: 16,
                              background: '#722ed1',
                              marginLeft: 2,
                              verticalAlign: 'text-bottom',
                            }}
                          />
                        )}

                        {msg.content && !msg.isStreaming && (
                          <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                            <Button
                              type="text"
                              size="small"
                              icon={<CopyOutlined />}
                              style={{ color: 'var(--lm-text-tertiary)' }}
                              onClick={() => copyContent(msg.content)}
                            >
                              复制回答
                            </Button>
                          </div>
                        )}
                      </>
                    ) : (
                      <>
                        <div style={{ fontSize: 14, lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>
                          {msg.content}
                        </div>
                        {!sending && (
                          <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
                            <Button
                              type="text"
                              size="small"
                              icon={<CopyOutlined />}
                              style={{ color: 'rgba(255,255,255,0.82)' }}
                              onClick={() => copyContent(msg.content)}
                            >
                              复制
                            </Button>
                            <Button
                              type="text"
                              size="small"
                              icon={<SendOutlined />}
                              style={{ color: 'rgba(255,255,255,0.82)' }}
                              onClick={() => sendMessage(msg.content)}
                            >
                              再问一次
                            </Button>
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  {msg.role === 'user' && (
                    <div
                      style={{
                        width: 38,
                        height: 38,
                        borderRadius: 14,
                        background: 'linear-gradient(135deg, #1677ff, #69b1ff)',
                        color: '#fff',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                      }}
                    >
                      <UserOutlined />
                    </div>
                  )}
                </div>
              ))}

              {diagnosticClues.length > 0 && !sending && (
                <DiagnosticClues clues={diagnosticClues} summary={searchSummary} onAction={(prompt) => sendMessage(prompt)} />
              )}

              {multiAgentFindings.length > 0 && (
                <div style={sectionStyle}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <BranchesOutlined style={{ color: '#722ed1' }} />
                    <Text style={{ fontSize: 13, fontWeight: 700, color: 'var(--lm-text)' }}>
                      多 Agent 协作诊断
                    </Text>
                    <Tag color="purple" style={{ margin: 0, borderRadius: 999 }}>
                      {multiAgentFindings.filter((item) => item.status === 'done').length}/{multiAgentFindings.length}
                    </Tag>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
                    {multiAgentFindings.map((finding) => (
                      <div
                        key={finding.name}
                        style={{
                          padding: 12,
                          borderRadius: 14,
                          border: `1px solid ${
                            finding.status === 'done'
                              ? 'rgba(82,196,26,0.22)'
                              : finding.status === 'error'
                                ? 'rgba(255,77,79,0.22)'
                                : 'var(--lm-border-light)'
                          }`,
                          background: 'var(--lm-bg-elevated)',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          {finding.status === 'running' && <LoadingOutlined style={{ color: '#722ed1' }} />}
                          {finding.status === 'done' && <CheckCircleOutlined style={{ color: '#52c41a' }} />}
                          {finding.status === 'error' && <ExclamationCircleOutlined style={{ color: '#ff4d4f' }} />}
                          <Text style={{ fontSize: 12, fontWeight: 700, color: 'var(--lm-text)' }}>
                            {finding.displayName}
                          </Text>
                        </div>
                        <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
                          {finding.summary || '正在整理当前分支诊断结果...'}
                        </Text>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {(toolSteps.length > 0 || thinkingRound > 0) && (
                <div
                  style={{
                    ...sectionStyle,
                    background: 'linear-gradient(180deg, rgba(22,119,255,0.05), rgba(114,46,209,0.03))',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <BranchesOutlined style={{ color: '#722ed1' }} />
                    <Text style={{ fontSize: 13, fontWeight: 700, color: 'var(--lm-text)' }}>
                      Agent 推理链
                    </Text>
                    {thinkingRound > 0 && (
                      <Tag color="processing" style={{ borderRadius: 999, margin: 0 }}>
                        第 {thinkingRound} 轮
                      </Tag>
                    )}
                    {toolSteps.length > 0 && (
                      <Text style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                        {toolSteps.filter((step) => step.status === 'done').length}/{toolSteps.length} 完成
                      </Text>
                    )}
                  </div>

                  {toolSteps.map((step, index) => (
                    <AgentStepCard key={`${step.name}-${index}`} step={step} isLast={index === toolSteps.length - 1 && !thinkingText} />
                  ))}

                  {thinkingText && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingLeft: 24, paddingTop: 4 }}>
                      <LoadingOutlined style={{ color: '#722ed1' }} />
                      <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>{thinkingText}</Text>
                    </div>
                  )}
                </div>
              )}

              {traceSegments.length > 0 && !sending && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
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
                <div style={sectionStyle}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                    <ClockCircleOutlined style={{ color: '#1677ff' }} />
                    <Text style={{ fontSize: 13, fontWeight: 700, color: 'var(--lm-text)' }}>
                      操作时间线
                    </Text>
                    {timelineSummary && (
                      <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
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
                          gridTemplateColumns: isMobile ? '1fr' : '140px 160px 1fr',
                          gap: 12,
                          padding: 14,
                          borderRadius: 14,
                          background: 'var(--lm-bg-elevated)',
                          border: '1px solid var(--lm-border-light)',
                        }}
                      >
                        <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)', fontFamily: 'monospace' }}>
                          {entry.time}
                        </Text>
                        <div>
                          <Text style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--lm-text)' }}>
                            {entry.service}
                          </Text>
                          <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                            {entry.filetype || entry.domain}
                          </Text>
                        </div>
                        <div>
                          <Text style={{ display: 'block', fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.7 }}>
                            {entry.action}
                          </Text>
                          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                            {entry.identity && <Tag style={{ margin: 0, borderRadius: 999 }}>{entry.identity}</Tag>}
                            {entry.host && <Tag style={{ margin: 0, borderRadius: 999 }}>{entry.host}</Tag>}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {followUps.length > 0 && !sending && (
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
                  <QuestionCircleOutlined style={{ color: 'var(--lm-text-tertiary)', marginTop: 8 }} />
                  {followUps.map((item, index) => (
                    <div
                      key={`${item}-${index}`}
                      onClick={() => sendMessage(item)}
                      style={{
                        padding: '9px 14px',
                        borderRadius: 999,
                        border: '1px solid var(--lm-border-light)',
                        background: 'var(--lm-bg-elevated)',
                        color: 'var(--lm-text-secondary)',
                        cursor: 'pointer',
                        fontSize: 12,
                        fontWeight: 600,
                      }}
                    >
                      {item}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div
          style={{
            borderTop: '1px solid var(--lm-border-light)',
            padding: 18,
            background: 'linear-gradient(180deg, rgba(13,18,32,0.02), var(--lm-bg-container))',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {smartRecommendations.slice(0, 6).map((item) => (
                <div
                  key={item.id}
                  onClick={() => sendMessage(item.prompt)}
                  style={{
                    padding: '8px 12px',
                    borderRadius: 999,
                    border: '1px solid rgba(22,119,255,0.14)',
                    background: 'rgba(22,119,255,0.10)',
                    color: '#1677ff',
                    cursor: 'pointer',
                    fontSize: 12,
                    fontWeight: 600,
                  }}
                >
                  {item.label}
                </div>
              ))}
            </div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr',
                gap: 14,
              }}
            >
              <div
                style={{
                  borderRadius: 18,
                  border: `1px solid ${input.length > 7500 ? 'rgba(255,77,79,0.45)' : 'var(--lm-border-light)'}`,
                  background: 'linear-gradient(180deg, var(--lm-bg-container), var(--lm-bg-elevated))',
                  boxShadow: 'var(--lm-shadow-card)',
                  padding: 12,
                }}
              >
                <Input.TextArea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="输入账号、traceId、服务名或你的诊断问题。Enter 发送，Shift+Enter 换行。"
                  autoSize={{ minRows: 3, maxRows: 7 }}
                  maxLength={8000}
                  disabled={sending}
                  style={{
                    border: 'none',
                    background: 'transparent',
                    boxShadow: 'none',
                    fontSize: 15,
                    lineHeight: 1.8,
                    resize: 'none',
                    padding: 0,
                  }}
                  onPressEnter={(event) => {
                    if (event.shiftKey) return;
                    event.preventDefault();
                    sendMessage();
                  }}
                />

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10, gap: 12, flexWrap: 'wrap' }}>
                  <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                    Enter 发送，Shift+Enter 换行，支持连续追问和流式诊断。
                  </Text>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    {input.length > 0 && (
                      <Text style={{ fontSize: 12, color: input.length > 7500 ? '#ff4d4f' : 'var(--lm-text-tertiary)' }}>
                        {input.length} / 8000
                      </Text>
                    )}
                    <Button
                      type="primary"
                      icon={sending ? <LoadingOutlined /> : <SendOutlined />}
                      onClick={() => sendMessage()}
                      disabled={!input.trim() || sending || input.length > 8000}
                      style={{ height: 42, borderRadius: 12, paddingInline: 18 }}
                    >
                      发送
                    </Button>
                  </div>
                </div>
              </div>

              {showComposerSummary && (
                <div
                  style={{
                    borderRadius: 18,
                    border: '1px solid var(--lm-border-light)',
                    background: 'var(--lm-bg-elevated)',
                    padding: 14,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 12,
                  }}
                >
                  <Text style={{ fontSize: 12, fontWeight: 700, color: 'var(--lm-text)' }}>
                    诊断摘要
                  </Text>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
                    {[
                      { label: '工具步骤', value: sessionStats.toolCount },
                      { label: '时间线', value: timelineEntries.length },
                      { label: '链路段', value: traceSegments.length },
                      { label: '线索卡', value: diagnosticClues.length },
                    ].map((item) => (
                      <div
                        key={item.label}
                        style={{
                          padding: 10,
                          borderRadius: 12,
                          background: 'var(--lm-bg-elevated)',
                          border: '1px solid var(--lm-border-light)',
                        }}
                      >
                        <Text style={{ display: 'block', fontSize: 18, fontWeight: 700, color: 'var(--lm-text)' }}>
                          {item.value}
                        </Text>
                        <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                          {item.label}
                        </Text>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {showRightRail && (
        <div
          style={{
            ...panelStyle,
            width: 320,
            minWidth: 320,
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
            padding: 16,
            overflow: 'auto',
          }}
        >
          <div style={sectionStyle}>
            <Text style={{ display: 'block', fontSize: 13, fontWeight: 700, color: 'var(--lm-text)', marginBottom: 12 }}>
              智能推荐
            </Text>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(liveRecommendations.length > 0 ? liveRecommendations : []).slice(0, 5).map((item) => (
                <div
                  key={item.id}
                  onClick={() => sendMessage(item.prompt)}
                  style={{
                    padding: 12,
                    borderRadius: 14,
                    background: 'var(--lm-bg-elevated)',
                    border: '1px solid var(--lm-border-light)',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <Tag color={priorityMeta[item.priority].color} style={{ margin: 0, borderRadius: 999 }}>
                      {priorityMeta[item.priority].label}
                    </Tag>
                    <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                      {item.kind}
                    </Text>
                  </div>
                  <Text style={{ display: 'block', fontSize: 13, fontWeight: 700, color: 'var(--lm-text)' }}>
                    {item.title}
                  </Text>
                  <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
                    {item.reason}
                  </Text>
                </div>
              ))}
              {liveRecommendations.length === 0 && (
                <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                  暂时没有实时推荐，下面的模板仍然可以直接发起诊断。
                </Text>
              )}
            </div>
          </div>

          <div style={sectionStyle}>
            <Text style={{ display: 'block', fontSize: 13, fontWeight: 700, color: 'var(--lm-text)', marginBottom: 12 }}>
              快速模板
            </Text>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
              {dynamicTemplates.map((template) => (
                <div
                  key={template.id}
                  onClick={() => applyTemplate(template.id)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 999,
                    border: activeTemplateId === template.id
                      ? `1px solid ${template.accent}55`
                      : '1px solid var(--lm-border-light)',
                    background: activeTemplateId === template.id ? `${template.accent}12` : 'transparent',
                    color: activeTemplateId === template.id ? template.accent : 'var(--lm-text-secondary)',
                    cursor: 'pointer',
                    fontSize: 12,
                    fontWeight: 600,
                  }}
                >
                  {template.title}
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <Input
                value={templateValues.account}
                onChange={(e) => updateTemplateValue('account', e.target.value)}
                placeholder="账号 / 订单号 / traceId"
              />
              <Input
                value={templateValues.keyword}
                onChange={(e) => updateTemplateValue('keyword', e.target.value)}
                placeholder="错误关键词（可选）"
              />
              <Select
                value={templateValues.hours}
                onChange={(value) => updateTemplateValue('hours', value)}
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
                options={[
                  { value: 'single', label: '单服务' },
                  { value: 'selected', label: '指定业务线' },
                  { value: 'core', label: '核心业务线' },
                  { value: 'all', label: '全部业务线' },
                ]}
              />
              {(activeTemplateId === 'service-errors' || activeTemplateId === 'alert-check' || templateValues.serviceScope === 'single') && (
                <Input
                  value={templateValues.serviceName}
                  onChange={(e) => updateTemplateValue('serviceName', e.target.value)}
                  placeholder="服务名称，例如 auth-service"
                />
              )}
              {templateValues.serviceScope === 'selected' && (
                <Select
                  mode="multiple"
                  value={templateValues.serviceNames}
                  onChange={(value) => updateTemplateValue('serviceNames', value)}
                  placeholder="选择业务线"
                  options={businessLines.map((item) => ({ value: item.name, label: item.name }))}
                />
              )}
              <Button type="primary" icon={<SendOutlined />} style={{ borderRadius: 12 }} onClick={sendTemplatePrompt}>
                发起模板诊断
              </Button>
            </div>
          </div>

          <div style={sectionStyle}>
            <Text style={{ display: 'block', fontSize: 13, fontWeight: 700, color: 'var(--lm-text)', marginBottom: 12 }}>
              当前上下文
            </Text>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div
                style={{
                  padding: 12,
                  borderRadius: 14,
                  background: 'rgba(22,119,255,0.05)',
                  border: '1px solid rgba(22,119,255,0.12)',
                }}
              >
                <Text style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--lm-text)' }}>
                  当前模板
                </Text>
                <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
                  {activeTemplate.title} · {activeTemplate.description}
                </Text>
              </div>

              {messages.length > 0 && (
                <div
                  style={{
                    padding: 12,
                    borderRadius: 14,
                    background: 'var(--lm-bg-elevated)',
                    border: '1px solid var(--lm-border-light)',
                  }}
                >
                  <Text style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--lm-text)' }}>
                    最近活跃
                  </Text>
                  <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
                    {messages.length} 条对话，最后一次更新 {formatRelativeTime(messages[messages.length - 1]?.timestamp)}。
                  </Text>
                </div>
              )}

              {suggestedActions.length > 0 && (
                <div
                  style={{
                    padding: 12,
                    borderRadius: 14,
                    background: 'rgba(114,46,209,0.05)',
                    border: '1px solid rgba(114,46,209,0.12)',
                  }}
                >
                  <Text style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--lm-text)' }}>
                    AI 建议动作
                  </Text>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                    {suggestedActions.slice(0, 4).map((action, index) => (
                      <Tag
                        key={`${action.label}-${index}`}
                        color="purple"
                        style={{ cursor: 'pointer', borderRadius: 999, margin: 0 }}
                        onClick={() => sendMessage(action.prompt)}
                      >
                        {action.label}
                      </Tag>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ChatPage;
