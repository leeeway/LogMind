import React, { useState } from 'react';
import { Tag, Space, Typography, Tooltip } from 'antd';
import {
  SearchOutlined, AlertOutlined, BugOutlined, ThunderboltOutlined,
  LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined,
  DownOutlined, RightOutlined, ClockCircleOutlined,
  DatabaseOutlined, ApartmentOutlined, FileSearchOutlined,
  ExperimentOutlined, BookOutlined, RadarChartOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

const toolMeta: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  search_logs:             { icon: <SearchOutlined />,      label: '搜索日志',     color: '#1677ff' },
  get_log_context:         { icon: <ClockCircleOutlined />, label: '日志上下文',   color: '#1677ff' },
  count_error_patterns:    { icon: <DatabaseOutlined />,    label: '错误统计',     color: '#722ed1' },
  list_available_indices:  { icon: <DatabaseOutlined />,    label: '索引列表',     color: '#8c8c8c' },
  search_knowledge_base:   { icon: <BookOutlined />,        label: '知识库检索',   color: '#13c2c2' },
  search_similar_incidents:{ icon: <ExperimentOutlined />,  label: '历史相似故障', color: '#fa8c16' },
  search_cross_service_logs:{ icon: <ApartmentOutlined />, label: '跨服务关联',   color: '#eb2f96' },
  get_alerts:              { icon: <AlertOutlined />,       label: '告警查询',     color: '#faad14' },
  get_service_health:      { icon: <RadarChartOutlined />,  label: '服务健康',     color: '#52c41a' },
  compare_time_windows:    { icon: <FileSearchOutlined />,  label: '时间对比',     color: '#2f54eb' },
  trace_error_chain:       { icon: <ApartmentOutlined />,   label: '错误链追踪',   color: '#f5222d' },
  create_analysis_task:    { icon: <ExperimentOutlined />,  label: '创建分析任务', color: '#fa541c' },
};

export interface ToolStep {
  name: string;
  args: any;
  result?: string;
  summary?: string;
  evidence_label?: string;
  round: number;
  status: 'running' | 'done' | 'error';
  startTime: number;
  endTime?: number;
}

interface Props {
  step: ToolStep;
  isLast?: boolean;
}

const AgentStepCard: React.FC<Props> = ({ step, isLast }) => {
  const [expanded, setExpanded] = useState(false);
  const meta = toolMeta[step.name] || { icon: <ThunderboltOutlined />, label: step.name, color: '#8c8c8c' };
  const elapsed = step.endTime ? ((step.endTime - step.startTime) / 1000).toFixed(1) : null;

  // Parse result for smart preview
  const parsedResult = (() => {
    if (!step.result) return null;
    try {
      return JSON.parse(step.result);
    } catch {
      return null;
    }
  })();

  // Generate compact args summary
  const argsSummary = Object.entries(step.args || {})
    .filter(([_, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}="${String(v).slice(0, 30)}"`)
    .join(', ');

  return (
    <div style={{ position: 'relative', paddingLeft: 24 }}>
      {/* Connector line */}
      {!isLast && (
        <div style={{
          position: 'absolute', left: 10, top: 28, bottom: -8,
          width: 1, background: `${meta.color}22`,
        }} />
      )}

      {/* Step dot */}
      <div style={{
        position: 'absolute', left: 4, top: 10,
        width: 14, height: 14, borderRadius: '50%',
        background: step.status === 'running' ? `${meta.color}33` : `${meta.color}22`,
        border: `2px solid ${meta.color}`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        animation: step.status === 'running' ? 'lm-breathe 1.5s ease-in-out infinite' : 'none',
      }}>
        {step.status === 'running' && (
          <div style={{ width: 4, height: 4, borderRadius: '50%', background: meta.color }} />
        )}
      </div>

      {/* Card */}
      <div
        style={{
          padding: '8px 12px', borderRadius: 8, marginBottom: 6,
          background: step.status === 'running'
            ? `linear-gradient(135deg, ${meta.color}08, ${meta.color}04)`
            : 'rgba(255,255,255,0.02)',
          border: `1px solid ${meta.color}${step.status === 'running' ? '25' : '12'}`,
          cursor: step.result ? 'pointer' : 'default',
          transition: 'all 0.2s',
        }}
        onClick={() => step.result && setExpanded(!expanded)}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: meta.color, fontSize: 13 }}>{meta.icon}</span>
          <Tag color={meta.color} style={{ borderRadius: 4, fontSize: 11, margin: 0, fontWeight: 500 }}>
            {meta.label}
          </Tag>
          <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {argsSummary}
          </Text>
          <Space size={4}>
            {step.evidence_label && (
              <Tooltip title="证据编号">
                <Tag style={{
                  borderRadius: 4, fontSize: 10, margin: 0,
                  background: 'rgba(114,46,209,0.1)', border: '1px solid rgba(114,46,209,0.25)',
                  color: '#722ed1', fontFamily: 'monospace', fontWeight: 600,
                }}>
                  {step.evidence_label}
                </Tag>
              </Tooltip>
            )}
            {elapsed && (
              <Text style={{ fontSize: 10, color: 'var(--lm-text-tertiary)', fontFamily: 'monospace' }}>
                {elapsed}s
              </Text>
            )}
            {step.status === 'running' && <LoadingOutlined style={{ color: meta.color, fontSize: 12 }} />}
            {step.status === 'done' && <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 12 }} />}
            {step.status === 'error' && <CloseCircleOutlined style={{ color: '#ff4d4f', fontSize: 12 }} />}
            {step.result && (
              expanded ? <DownOutlined style={{ fontSize: 10, color: 'var(--lm-text-tertiary)' }} />
                       : <RightOutlined style={{ fontSize: 10, color: 'var(--lm-text-tertiary)' }} />
            )}
          </Space>
        </div>

        {/* Summary (always visible when done) */}
        {step.status === 'done' && step.summary && !expanded && (
          <div style={{
            marginTop: 4, fontSize: 11, color: 'var(--lm-text-secondary)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            paddingLeft: 21,
          }}>
            {step.summary.slice(0, 100)}
          </div>
        )}

        {/* Expanded result */}
        {expanded && step.result && (
          <div style={{
            marginTop: 8, padding: '8px 10px', borderRadius: 6,
            background: 'rgba(0,0,0,0.15)', fontSize: 11, lineHeight: 1.6,
            fontFamily: 'monospace', maxHeight: 200, overflow: 'auto',
            color: 'var(--lm-text-secondary)', wordBreak: 'break-all',
            animation: 'lm-fadeSlideIn 0.2s ease-out',
          }}>
            {parsedResult ? (
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 11 }}>
                {JSON.stringify(parsedResult, null, 2)}
              </pre>
            ) : (
              step.result
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default AgentStepCard;
