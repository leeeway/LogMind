import React, { useState } from 'react';
import { Typography, Tag, Button, Tooltip } from 'antd';
import {
  ExclamationCircleOutlined, WarningOutlined, InfoCircleOutlined,
  SearchOutlined, BranchesOutlined, SwapOutlined,
  DownOutlined, RightOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

export interface DiagnosticClue {
  severity: 'critical' | 'warning' | 'info';
  title: string;
  detail: string;
  affected_services: string[];
  time_range: string;
  suggestion: string;
}

interface DiagnosticCluesProps {
  clues: DiagnosticClue[];
  summary?: string;
  onAction?: (prompt: string) => void;
}

const severityConfig = {
  critical: { color: '#ff4d4f', bg: 'rgba(255,77,79,0.06)', border: 'rgba(255,77,79,0.2)', icon: <ExclamationCircleOutlined />, label: '严重' },
  warning: { color: '#faad14', bg: 'rgba(250,173,20,0.06)', border: 'rgba(250,173,20,0.2)', icon: <WarningOutlined />, label: '警告' },
  info: { color: '#1677ff', bg: 'rgba(22,119,255,0.04)', border: 'rgba(22,119,255,0.15)', icon: <InfoCircleOutlined />, label: '信息' },
};

const ClueCard: React.FC<{ clue: DiagnosticClue; onAction?: (prompt: string) => void }> = ({ clue, onAction }) => {
  const [expanded, setExpanded] = useState(clue.severity === 'critical');
  const config = severityConfig[clue.severity] || severityConfig.info;

  return (
    <div style={{
      padding: '12px 14px',
      borderRadius: 12,
      border: `1px solid ${config.border}`,
      background: config.bg,
      marginBottom: 10,
    }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
      >
        {expanded ? <DownOutlined style={{ fontSize: 10 }} /> : <RightOutlined style={{ fontSize: 10 }} />}
        <span style={{ color: config.color, fontSize: 13 }}>{config.icon}</span>
        <Tag color={config.color} style={{ margin: 0, borderRadius: 999, fontSize: 10 }}>{config.label}</Tag>
        <Text style={{ fontSize: 13, fontWeight: 600, color: 'var(--lm-text)', flex: 1 }}>
          {clue.title}
        </Text>
        {clue.affected_services.length > 0 && (
          <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
            {clue.affected_services.length} 个服务
          </Text>
        )}
      </div>

      {expanded && (
        <div style={{ marginTop: 10, paddingLeft: 26 }}>
          <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.7, display: 'block' }}>
            {clue.detail}
          </Text>

          {clue.suggestion && (
            <div style={{
              marginTop: 8, padding: '6px 10px', borderRadius: 8,
              background: 'rgba(22,119,255,0.05)', border: '1px dashed rgba(22,119,255,0.15)',
            }}>
              <Text style={{ fontSize: 11, color: '#1677ff' }}>
                💡 {clue.suggestion}
              </Text>
            </div>
          )}

          {onAction && (
            <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {clue.affected_services.length > 0 && (
                <Button
                  size="small"
                  icon={<SearchOutlined />}
                  style={{ borderRadius: 8, fontSize: 11 }}
                  onClick={() => onAction(`请详细分析 ${clue.affected_services[0]} 最近的错误日志`)}
                >
                  查看详细日志
                </Button>
              )}
              {clue.affected_services.length > 0 && (
                <Button
                  size="small"
                  icon={<BranchesOutlined />}
                  style={{ borderRadius: 8, fontSize: 11 }}
                  onClick={() => onAction(`请追踪 ${clue.affected_services[0]} 的完整调用链路`)}
                >
                  追踪链路
                </Button>
              )}
              <Button
                size="small"
                icon={<SwapOutlined />}
                style={{ borderRadius: 8, fontSize: 11 }}
                onClick={() => onAction(`请对比 ${clue.affected_services[0] || '该服务'} 今天和昨天的错误分布`)}
              >
                对比昨天
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const DiagnosticClues: React.FC<DiagnosticCluesProps> = ({ clues, summary, onAction }) => {
  if (!clues || clues.length === 0) return null;

  return (
    <div style={{
      padding: '14px 16px',
      borderRadius: 14,
      background: 'var(--lm-bg-card)',
      border: '1px solid var(--lm-border-light)',
      animation: 'lm-fadeSlideIn 0.3s ease-out',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <SearchOutlined style={{ color: '#722ed1', fontSize: 14 }} />
        <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
          诊断线索
        </Text>
        <Tag color="purple" style={{ margin: 0, borderRadius: 999, fontSize: 10 }}>
          {clues.length} 条
        </Tag>
        {summary && (
          <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', marginLeft: 'auto' }}>
            {summary}
          </Text>
        )}
      </div>

      {clues.map((clue, idx) => (
        <ClueCard key={idx} clue={clue} onAction={onAction} />
      ))}
    </div>
  );
};

export default DiagnosticClues;
