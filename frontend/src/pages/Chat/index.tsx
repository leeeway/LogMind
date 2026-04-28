import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { Button, Input, Typography, Space, Spin, Tag, message, Tooltip } from 'antd';
import {
  SendOutlined, PlusOutlined, DeleteOutlined, RobotOutlined,
  UserOutlined, ThunderboltOutlined, CopyOutlined,
  LoadingOutlined, BranchesOutlined, ExportOutlined,
  HistoryOutlined, QuestionCircleOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { chatApi } from '@/api/chat';
import { useAuthStore } from '@/stores/authStore';
import AgentStepCard, { ToolStep } from '@/components/AgentStepCard';

const { Text, Title } = Typography;

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  isStreaming?: boolean;
}

const WELCOME_SUGGESTIONS = [
  '最近1小时有哪些关键错误？',
  '帮我分析 auth-service 的超时问题',
  '最近有什么告警需要关注？',
  '系统整体健康状况如何？',
  '对比今天和昨天的错误分布',
  '帮我追踪 NPE 的调用链',
];

const ChatPage: React.FC = () => {
  const [sessions, setSessions] = useState<any[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [toolSteps, setToolSteps] = useState<ToolStep[]>([]);
  const [thinkingRound, setThinkingRound] = useState(0);
  const [thinkingText, setThinkingText] = useState('');
  const [followUps, setFollowUps] = useState<string[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);
  const token = useAuthStore((s) => s.token);
  const location = useLocation();

  // Handle prefill from QuickDiagnose
  useEffect(() => {
    const state = location.state as any;
    if (state?.prefill) {
      setInput(state.prefill);
      // Clear the state
      window.history.replaceState({}, document.title);
      // Auto-send after a short delay
      setTimeout(() => {
        sendMessage(state.prefill);
      }, 300);
    }
  }, [location.state]);

  // Load sessions
  const loadSessions = useCallback(async () => {
    try {
      const { data } = await chatApi.listSessions();
      setSessions(data?.sessions || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

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
      setMessages((data?.messages || []).map((m: any) => ({
        role: m.role,
        content: m.content,
        timestamp: m.timestamp,
      })));
    } catch { setMessages([]); }
  };

  // Delete session
  const deleteSession = async (sessionId: string) => {
    try {
      await chatApi.deleteSession(sessionId);
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setMessages([]);
      }
      loadSessions();
    } catch { /* ignore */ }
  };

  // Send message with SSE streaming + multi-round ReAct
  const sendMessage = async (content?: string) => {
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

              } else if (event.type === 'done') {
                setMessages(prev => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = { ...last, isStreaming: false };
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
    } catch (err: any) {
      message.error('发送失败: ' + (err.message || '网络错误'));
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
  };

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
          {sessions.map((s: any) => (
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
                  {WELCOME_SUGGESTIONS.map((s, i) => (
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
                        <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
                          <Tooltip title="复制">
                            <CopyOutlined
                              style={{ fontSize: 12, color: 'var(--lm-text-tertiary)', cursor: 'pointer' }}
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
                    <div style={{ fontSize: 14, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{msg.content}</div>
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
