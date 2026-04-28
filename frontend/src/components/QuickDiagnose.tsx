import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Modal, Input, Typography, Space, Tag } from 'antd';
import { ThunderboltOutlined, SendOutlined, SearchOutlined, AlertOutlined, BugOutlined } from '@ant-design/icons';

const { Text, Title } = Typography;

interface DiagnoseContext {
  context?: string; // Pre-filled context
  source?: string;  // Where it was triggered from
}

interface QuickDiagnoseContextType {
  open: (ctx?: DiagnoseContext) => void;
}

const QuickDiagnoseCtx = createContext<QuickDiagnoseContextType>({ open: () => {} });

export const useQuickDiagnose = () => useContext(QuickDiagnoseCtx);

const QUICK_COMMANDS = [
  { icon: <AlertOutlined />, label: '最近有什么告警？', color: '#faad14' },
  { icon: <SearchOutlined />, label: '最近1小时有哪些关键错误？', color: '#1677ff' },
  { icon: <BugOutlined />, label: '系统整体健康状况如何？', color: '#52c41a' },
  { icon: <ThunderboltOutlined />, label: '帮我排查最近的超时问题', color: '#722ed1' },
];

export const QuickDiagnoseProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [visible, setVisible] = useState(false);
  const [input, setInput] = useState('');
  const [context, setContext] = useState<DiagnoseContext | undefined>();
  const navigate = useNavigate();

  const open = useCallback((ctx?: DiagnoseContext) => {
    setContext(ctx);
    setInput(ctx?.context || '');
    setVisible(true);
  }, []);

  // Keyboard shortcut: Ctrl+Shift+D
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'D') {
        e.preventDefault();
        open();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open]);

  const submit = (text?: string) => {
    const msg = text || input.trim();
    if (!msg) return;
    setVisible(false);
    setInput('');
    // Navigate to chat with pre-filled message
    navigate('/chat', { state: { prefill: msg, source: context?.source } });
  };

  return (
    <QuickDiagnoseCtx.Provider value={{ open }}>
      {children}
      <Modal
        open={visible}
        onCancel={() => setVisible(false)}
        footer={null}
        closable={false}
        centered
        width={520}
        styles={{
          body: {
            background: 'var(--lm-bg-card)',
            border: '1px solid var(--lm-border-light)',
            borderRadius: 16,
            padding: 0,
            overflow: 'hidden',
          },
          mask: { backdropFilter: 'blur(8px)' },
        }}
      >
        {/* Header */}
        <div style={{
          padding: '20px 24px 12px',
          background: 'linear-gradient(135deg, rgba(22,119,255,0.06), rgba(114,46,209,0.06))',
          borderBottom: '1px solid var(--lm-border-light)',
        }}>
          <Space>
            <div style={{
              width: 36, height: 36, borderRadius: 10,
              background: 'linear-gradient(135deg, rgba(22,119,255,0.2), rgba(114,46,209,0.2))',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              border: '1px solid rgba(22,119,255,0.15)',
            }}>
              <ThunderboltOutlined style={{ fontSize: 18, color: '#1677ff' }} />
            </div>
            <div>
              <Title level={5} style={{ margin: 0, color: 'var(--lm-text)' }}>AI 快速诊断</Title>
              <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                Ctrl+Shift+D · 描述问题，AI 自动排查
              </Text>
            </div>
          </Space>
          {context?.source && (
            <Tag color="blue" style={{ borderRadius: 4, marginTop: 8, fontSize: 11 }}>
              来源: {context.source}
            </Tag>
          )}
        </div>

        {/* Input */}
        <div style={{ padding: '16px 24px' }}>
          <div style={{
            display: 'flex', gap: 8,
            background: 'var(--lm-bg-elevated)', borderRadius: 10,
            border: '1px solid var(--lm-border-light)', padding: '8px 12px',
          }}>
            <Input.TextArea
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="描述你遇到的问题..."
              autoSize={{ minRows: 1, maxRows: 3 }}
              autoFocus
              style={{ border: 'none', background: 'transparent', boxShadow: 'none', fontSize: 14, resize: 'none', padding: '4px 0' }}
              onPressEnter={e => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); submit(); } }}
            />
            <SendOutlined
              style={{
                fontSize: 16, color: input.trim() ? '#1677ff' : 'var(--lm-text-tertiary)',
                cursor: input.trim() ? 'pointer' : 'default', alignSelf: 'flex-end', padding: '4px 0',
              }}
              onClick={() => submit()}
            />
          </div>
        </div>

        {/* Quick Commands */}
        <div style={{ padding: '0 24px 20px', display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {QUICK_COMMANDS.map((cmd, i) => (
            <div
              key={i}
              onClick={() => submit(cmd.label)}
              style={{
                padding: '6px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 12,
                background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
                color: 'var(--lm-text-secondary)', transition: 'all 0.2s',
                display: 'flex', alignItems: 'center', gap: 6,
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = `${cmd.color}40`; e.currentTarget.style.color = 'var(--lm-text)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--lm-border-light)'; e.currentTarget.style.color = 'var(--lm-text-secondary)'; }}
            >
              <span style={{ color: cmd.color }}>{cmd.icon}</span>
              {cmd.label}
            </div>
          ))}
        </div>
      </Modal>
    </QuickDiagnoseCtx.Provider>
  );
};
