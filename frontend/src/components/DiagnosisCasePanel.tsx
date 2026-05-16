import React from 'react';
import { Button, Progress, Space, Tag, Typography } from 'antd';
import {
  CheckCircleOutlined,
  CopyOutlined,
  ExperimentOutlined,
  ForkOutlined,
  PlayCircleOutlined,
  RocketOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

export type DiagnosisStage = '侦察' | '聚焦' | '关联' | '验证' | '结论';

export interface EvidenceSummary {
  label?: string;
  summary: string;
}

export interface HypothesisUpdate {
  hypothesis: string;
  confidence: number;
  supporting_evidence?: string[];
  counter_evidence?: string[];
  evidence_summaries?: EvidenceSummary[];
  impact_scope?: string;
  missing_confirmations?: string[];
}

export interface DecisionAction {
  id: string;
  label: string;
  prompt?: string;
  kind: 'follow_up' | 'diagnose' | 'task' | 'incident' | 'copy';
  description?: string;
}

export interface DiagnosisCaseState {
  question?: string;
  path?: string;
  stage: DiagnosisStage;
  stage_index?: number;
  total_stages?: number;
  status?: string;
  stage_summary?: string;
  hypothesis?: string;
  confidence?: number;
  supporting_evidence?: string[];
  counter_evidence?: string[];
  evidence_summaries?: EvidenceSummary[];
  impact_scope?: string;
  missing_confirmations?: string[];
  actions?: DecisionAction[];
}

interface Props {
  caseState: DiagnosisCaseState;
  actions?: DecisionAction[];
  onAction: (action: DecisionAction) => void;
  compact?: boolean;
}

const stages: DiagnosisStage[] = ['侦察', '聚焦', '关联', '验证', '结论'];

const actionIcon: Record<DecisionAction['kind'], React.ReactNode> = {
  follow_up: <PlayCircleOutlined />,
  diagnose: <ForkOutlined />,
  task: <ExperimentOutlined />,
  incident: <RocketOutlined />,
  copy: <CopyOutlined />,
};

const pathLabel: Record<string, string> = {
  service_error: '服务错误诊断',
  account_replay: '账号回放',
  trace: '链路追踪',
  smart_search: '智能搜索',
  multi_agent: '多 Agent 会诊',
  general: '通用诊断',
};

const DiagnosisCasePanel: React.FC<Props> = ({ caseState, actions = [], onAction, compact = false }) => {
  const currentIndex = caseState.stage_index ?? stages.indexOf(caseState.stage);
  const confidence = Math.max(0, Math.min(100, caseState.confidence ?? 0));
  const visibleActions = actions.length ? actions : caseState.actions || [];
  const confidenceStatus = confidence >= 70 ? 'success' : confidence >= 40 ? 'normal' : 'exception';

  return (
    <div
      style={{
        padding: compact ? 12 : 16,
        borderRadius: 12,
        border: '1px solid rgba(22,119,255,0.16)',
        background: 'linear-gradient(180deg, rgba(22,119,255,0.06), rgba(19,194,194,0.03))',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <CheckCircleOutlined style={{ color: '#1677ff' }} />
        <Text style={{ fontSize: 13, fontWeight: 800, color: 'var(--lm-text)' }}>
          专家办案面板
        </Text>
        <Tag color="blue" style={{ margin: 0, borderRadius: 999 }}>
          {pathLabel[caseState.path || 'general'] || caseState.path || '诊断路径'}
        </Tag>
      </div>

      <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
        {stages.map((stage, index) => (
          <div
            key={stage}
            style={{
              flex: compact ? '0 0 auto' : 1,
              minWidth: compact ? 48 : 72,
              padding: '7px 8px',
              borderRadius: 8,
              textAlign: 'center',
              fontSize: 12,
              fontWeight: 700,
              color: index <= currentIndex ? '#1677ff' : 'var(--lm-text-tertiary)',
              background: index <= currentIndex ? 'rgba(22,119,255,0.1)' : 'var(--lm-bg-elevated)',
              border: index === currentIndex ? '1px solid rgba(22,119,255,0.35)' : '1px solid var(--lm-border-light)',
            }}
          >
            {stage}
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'minmax(0, 1fr) 160px', gap: 14 }}>
        <div>
          <Text style={{ display: 'block', fontSize: 12, color: 'var(--lm-text-tertiary)', marginBottom: 4 }}>
            当前假设
          </Text>
          <Text style={{ display: 'block', fontSize: 13, fontWeight: 700, color: 'var(--lm-text)', lineHeight: 1.65 }}>
            {caseState.hypothesis || '正在等待第一批证据。'}
          </Text>
          {caseState.impact_scope && (
            <Text style={{ display: 'block', marginTop: 6, fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
              影响范围：{caseState.impact_scope}
            </Text>
          )}
        </div>
        <div>
          <Text style={{ display: 'block', fontSize: 12, color: 'var(--lm-text-tertiary)', marginBottom: 8 }}>
            置信度
          </Text>
          <Progress percent={confidence} size="small" status={confidenceStatus} />
        </div>
      </div>

      {(caseState.supporting_evidence?.length || caseState.counter_evidence?.length) ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          {(caseState.supporting_evidence || []).map((label) => (
            <Tag key={`support-${label}`} color="green" style={{ margin: 0, borderRadius: 999 }}>
              {label} 支持
            </Tag>
          ))}
          {(caseState.counter_evidence || []).map((label) => (
            <Tag key={`counter-${label}`} color="orange" style={{ margin: 0, borderRadius: 999 }}>
              {label} 待确认
            </Tag>
          ))}
        </div>
      ) : null}

      {!compact && (caseState.evidence_summaries || []).length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
          {(caseState.evidence_summaries || []).slice(-4).map((item, index) => (
            <div
              key={`${item.label || 'evidence'}-${index}`}
              style={{
                padding: '9px 10px',
                borderRadius: 8,
                background: 'var(--lm-bg-elevated)',
                border: '1px solid var(--lm-border-light)',
              }}
            >
              {item.label && (
                <Tag color="geekblue" style={{ margin: '0 6px 0 0', borderRadius: 6 }}>
                  {item.label}
                </Tag>
              )}
              <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
                {item.summary}
              </Text>
            </div>
          ))}
        </div>
      )}

      {!compact && (caseState.missing_confirmations || []).length > 0 && (
        <div
          style={{
            marginTop: 12,
            padding: '10px 12px',
            borderRadius: 8,
            background: 'rgba(250,140,22,0.08)',
            border: '1px solid rgba(250,140,22,0.18)',
          }}
        >
          <Text style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--lm-text)', marginBottom: 6 }}>
            还需要确认
          </Text>
          {(caseState.missing_confirmations || []).slice(0, 3).map((item) => (
            <Text key={item} style={{ display: 'block', fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.7 }}>
              {item}
            </Text>
          ))}
        </div>
      )}

      {visibleActions.length > 0 && (
        <Space size={8} wrap style={{ marginTop: 14 }}>
          {visibleActions.map((action) => (
            <Button
              key={action.id || action.label}
              size="small"
              icon={actionIcon[action.kind] || <PlayCircleOutlined />}
              onClick={() => onAction(action)}
              style={{ borderRadius: 8 }}
            >
              {action.label}
            </Button>
          ))}
        </Space>
      )}
    </div>
  );
};

export default DiagnosisCasePanel;
