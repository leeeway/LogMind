import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Button, Input, Typography, Space, Spin, Tag, message, Tooltip } from 'antd';
import {
  SendOutlined, PlusOutlined, DeleteOutlined, RobotOutlined,
  UserOutlined, SearchOutlined, AlertOutlined, BugOutlined,
  LoadingOutlined, ThunderboltOutlined, CopyOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { chatApi } from '@/api/chat';
import { useAuthStore } from '@/stores/authStore';

const { Text, Title } = Typography;

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  tool_calls?: { name: string; args: any; result?: string }[];
  isStreaming?: boolean;
}

const toolIcons: Record<string, React.ReactNode> = {
  search_logs: <SearchOutlined />,
  get_alerts: <AlertOutlined />,
  get_known_issues: <BugOutlined />,
};

const WELCOME_SUGGESTIONS = [
  '最近1小时有哪些关键错误？',
  '帮我分析 auth-service 的超时问题',
  '最近有什么告警需要关注？',
  '系统整体健康状况如何？',
];

const ChatPage: React.FC = () => {
  const [sessions, setSessions] = useState<any[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [toolCalls, setToolCalls] = useState<{ name: string; args: any; result?: string }[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);
  const token = useAuthStore((s) => s.token);

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
  }, [messages, toolCalls]);

  // Create new session
  const createSession = async () => {
    try {
      const { data } = await chatApi.createSession();
      setActiveSessionId(data.id);
      setMessages([]);
      setToolCalls([]);
      loadSessions();
    } catch { message.error('创建会话失败'); }
  };

  // Load session messages
  const loadSession = async (sessionId: string) => {
    setActiveSessionId(sessionId);
    setToolCalls([]);
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

  // Send message with SSE streaming
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
    setToolCalls([]);

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

              if (event.type === 'token') {
                assistantContent += event.content;
                setMessages(prev => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = { ...last, content: assistantContent, isStreaming: true };
                  }
                  return updated;
                });
              } else if (event.type === 'tool_call') {
                setToolCalls(prev => [...prev, { name: event.name, args: event.args }]);
              } else if (event.type === 'tool_result') {
                setToolCalls(prev =>
                  prev.map(tc => tc.name === event.name ? { ...tc, result: event.result } : tc)
                );
              } else if (event.type === 'done') {
                setMessages(prev => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = { ...last, isStreaming: false };
                  }
                  return updated;
                });
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
      loadSessions();
      inputRef.current?.focus();
    }
  };

  const copyContent = (text: string) => {
    navigator.clipboard.writeText(text).then(() => message.success('已复制'));
  };

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 56px)', margin: '-24px', overflow: 'hidden' }}>
      {/* Left Sidebar — Sessions */}
      <div style={{
        width: 260, borderRight: '1px solid var(--lm-border-light)', display: 'flex', flexDirection: 'column',
        background: 'var(--lm-bg-container)',
      }}>
        <div style={{ padding: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} block onClick={createSession}
            style={{ borderRadius: 8, height: 40 }}>
            新对话
          </Button>
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
              <div style={{ textAlign: 'center', paddingTop: 80 }}>
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
                  用自然语言描述你的问题，AI 会自动搜索日志、分析根因、给出建议
                </Text>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center', marginTop: 32 }}>
                  {WELCOME_SUGGESTIONS.map((s, i) => (
                    <div
                      key={i}
                      onClick={() => sendMessage(s)}
                      style={{
                        padding: '10px 16px', borderRadius: 10, cursor: 'pointer', fontSize: 13,
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
                display: 'flex', gap: 12, marginBottom: 24,
                flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
              }}>
                {/* Avatar */}
                <div style={{
                  width: 36, height: 36, borderRadius: 10, flexShrink: 0,
                  background: msg.role === 'user'
                    ? 'linear-gradient(135deg, #1677ff, #4096ff)'
                    : 'linear-gradient(135deg, rgba(114,46,209,0.2), rgba(22,119,255,0.2))',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  border: msg.role === 'user' ? 'none' : '1px solid rgba(114,46,209,0.15)',
                }}>
                  {msg.role === 'user'
                    ? <UserOutlined style={{ color: '#fff', fontSize: 16 }} />
                    : <RobotOutlined style={{ color: '#722ed1', fontSize: 16 }} />
                  }
                </div>

                {/* Bubble */}
                <div style={{
                  maxWidth: '75%', padding: '12px 16px', borderRadius: 14,
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

            {/* Tool Calls */}
            {toolCalls.length > 0 && (
              <div style={{ marginBottom: 16, marginLeft: 48 }}>
                {toolCalls.map((tc, i) => (
                  <div key={i} style={{
                    padding: '8px 12px', borderRadius: 8, marginBottom: 6,
                    background: 'rgba(22,119,255,0.04)', border: '1px solid rgba(22,119,255,0.1)',
                    fontSize: 12,
                  }}>
                    <Space>
                      {toolIcons[tc.name] || <ThunderboltOutlined />}
                      <Tag color="blue" style={{ borderRadius: 4 }}>{tc.name}</Tag>
                      <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 11 }}>
                        {JSON.stringify(tc.args)}
                      </Text>
                      {tc.result ? (
                        <Tag color="green" style={{ borderRadius: 4 }}>✓ 完成</Tag>
                      ) : (
                        <LoadingOutlined style={{ color: '#1677ff' }} />
                      )}
                    </Space>
                  </div>
                ))}
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input Area */}
        <div style={{
          padding: '16px 24px 24px', borderTop: '1px solid var(--lm-border-light)',
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
                placeholder="描述你的问题... (Ctrl+Enter 发送)"
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
            <div style={{ textAlign: 'center', marginTop: 8 }}>
              <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                LogMind AI · Ctrl+Enter 发送 · AI 可能产生不准确的信息
              </Text>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatPage;
