import React, { useState } from 'react';
import { Typography, Tag, Tooltip } from 'antd';
import {
  ClockCircleOutlined, ExclamationCircleOutlined,
  CheckCircleOutlined, DownOutlined, RightOutlined,
  ApiOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

export interface TraceNode {
  time: string;
  service: string;
  action: string;
  level: 'info' | 'warning' | 'error';
  host: string;
  domain: string;
  identity: string;
  call_targets?: string[];
}

export interface TraceSegment {
  segment_id: string;
  correlation_ids: string[];
  start_time: string;
  end_time: string;
  duration_ms: number;
  has_error: boolean;
  error_summary: string;
  node_count: number;
  nodes: TraceNode[];
}

interface TraceTimelineProps {
  segments: TraceSegment[];
  uncorrelatedEntries?: TraceNode[];
  summary?: string;
}

/* --- PLACEHOLDER_TRACE_TIMELINE_REST --- */

const levelConfig = {
  error: { color: '#ff4d4f', bg: 'rgba(255,77,79,0.08)', icon: <ExclamationCircleOutlined /> },
  warning: { color: '#faad14', bg: 'rgba(250,173,20,0.08)', icon: <ExclamationCircleOutlined /> },
  info: { color: '#1677ff', bg: 'rgba(22,119,255,0.05)', icon: <CheckCircleOutlined /> },
};

const serviceColors = [
  '#1677ff', '#722ed1', '#13c2c2', '#52c41a', '#fa8c16',
  '#eb2f96', '#2f54eb', '#fadb14', '#a0d911', '#f5222d',
];

function getServiceColor(service: string, allServices: string[]): string {
  const idx = allServices.indexOf(service);
  return serviceColors[idx % serviceColors.length];
}

const SegmentCard: React.FC<{
  segment: TraceSegment;
  allServices: string[];
}> = ({ segment, allServices }) => {
  const [expanded, setExpanded] = useState(segment.has_error || segment.nodes.length <= 5);

  return (
    <div style={{
      marginBottom: 14,
      borderRadius: 14,
      border: `1px solid ${segment.has_error ? 'rgba(255,77,79,0.25)' : 'var(--lm-border-light)'}`,
      background: segment.has_error ? 'rgba(255,77,79,0.02)' : 'var(--lm-bg-card)',
      overflow: 'hidden',
    }}>
      {/* Segment Header */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          padding: '10px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          cursor: 'pointer',
          background: segment.has_error ? 'rgba(255,77,79,0.04)' : 'var(--lm-bg-elevated)',
          borderBottom: expanded ? '1px solid var(--lm-border-light)' : 'none',
        }}
      >
        {expanded ? <DownOutlined style={{ fontSize: 10 }} /> : <RightOutlined style={{ fontSize: 10 }} />}

        {segment.has_error ? (
          <ExclamationCircleOutlined style={{ color: '#ff4d4f', fontSize: 14 }} />
        ) : (
          <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 14 }} />
        )}

        <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
          {segment.nodes[0]?.service}
          {segment.nodes.length > 1 && ` → ${segment.nodes[segment.nodes.length - 1]?.service}`}
        </Text>

        <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
          {segment.node_count} 步
        </Text>

        {segment.duration_ms > 0 && (
          <Tag style={{ margin: 0, borderRadius: 999, fontSize: 10 }}>
            {segment.duration_ms >= 1000
              ? `${(segment.duration_ms / 1000).toFixed(1)}s`
              : `${segment.duration_ms}ms`}
          </Tag>
        )}

        {segment.correlation_ids.length > 0 && (
          <Tooltip title={segment.correlation_ids.join('\n')}>
            <Tag color="blue" style={{ margin: 0, borderRadius: 999, fontSize: 10 }}>
              <ApiOutlined /> {segment.correlation_ids.length} ID
            </Tag>
          </Tooltip>
        )}

        {segment.has_error && segment.error_summary && (
          <Text style={{ fontSize: 11, color: '#ff4d4f', marginLeft: 'auto', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {segment.error_summary}
          </Text>
        )}
      </div>

      {/* Segment Nodes */}
      {expanded && (
        <div style={{ padding: '10px 14px' }}>
          {segment.nodes.map((node, idx) => {
            const config = levelConfig[node.level] || levelConfig.info;
            const svcColor = getServiceColor(node.service, allServices);
            const isLast = idx === segment.nodes.length - 1;

            return (
              <div key={`${node.time}-${idx}`} style={{ display: 'flex', gap: 10, position: 'relative' }}>
                {/* Vertical connector line */}
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 16 }}>
                  <div style={{
                    width: 10, height: 10, borderRadius: '50%',
                    background: config.color, border: `2px solid ${config.color}33`,
                    flexShrink: 0, marginTop: 4,
                  }} />
                  {!isLast && (
                    <div style={{ width: 2, flex: 1, background: 'var(--lm-border-light)', minHeight: 20 }} />
                  )}
                </div>

                {/* Node content */}
                <div style={{
                  flex: 1, paddingBottom: isLast ? 0 : 12,
                  background: config.bg, borderRadius: 10, padding: '8px 12px', marginBottom: isLast ? 0 : 6,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', fontFamily: 'monospace' }}>
                      {node.time.split(' ')[1] || node.time}
                    </Text>
                    <Tag style={{ margin: 0, borderRadius: 999, fontSize: 10, color: svcColor, borderColor: `${svcColor}44`, background: `${svcColor}10` }}>
                      {node.service}
                    </Tag>
                    {node.host && (
                      <Text style={{ fontSize: 10, color: 'var(--lm-text-tertiary)' }}>{node.host}</Text>
                    )}
                  </div>
                  <Text style={{ fontSize: 12, color: node.level === 'error' ? '#ff4d4f' : 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
                    {node.action}
                  </Text>
                  {node.call_targets && node.call_targets.length > 0 && (
                    <div style={{ marginTop: 4 }}>
                      {node.call_targets.map((target, i) => (
                        <Tag key={i} style={{ margin: '0 4px 0 0', borderRadius: 999, fontSize: 10 }}>
                          → {target}
                        </Tag>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

const TraceTimeline: React.FC<TraceTimelineProps> = ({ segments, uncorrelatedEntries, summary }) => {
  const allServices = Array.from(new Set(
    segments.flatMap(s => s.nodes.map(n => n.service))
  ));

  if (segments.length === 0 && (!uncorrelatedEntries || uncorrelatedEntries.length === 0)) {
    return null;
  }

  return (
    <div style={{
      padding: '14px 16px',
      borderRadius: 14,
      background: 'var(--lm-bg-card)',
      border: '1px solid var(--lm-border-light)',
      animation: 'lm-fadeSlideIn 0.3s ease-out',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <ClockCircleOutlined style={{ color: '#722ed1', fontSize: 14 }} />
        <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
          链路追踪
        </Text>
        <Tag color="purple" style={{ margin: 0, borderRadius: 999, fontSize: 10 }}>
          {segments.length} 条链路
        </Tag>
        {summary && (
          <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', marginLeft: 'auto' }}>
            {summary}
          </Text>
        )}
      </div>

      {/* Segments */}
      {segments.map((segment) => (
        <SegmentCard key={segment.segment_id} segment={segment} allServices={allServices} />
      ))}

      {/* Uncorrelated entries */}
      {uncorrelatedEntries && uncorrelatedEntries.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', display: 'block', marginBottom: 6 }}>
            未关联记录 ({uncorrelatedEntries.length})
          </Text>
          {uncorrelatedEntries.slice(0, 5).map((entry, idx) => (
            <div key={idx} style={{
              display: 'flex', gap: 8, padding: '6px 10px', borderRadius: 8,
              background: 'var(--lm-bg-elevated)', marginBottom: 4, fontSize: 12,
            }}>
              <Text style={{ color: 'var(--lm-text-tertiary)', fontFamily: 'monospace', fontSize: 11 }}>
                {entry.time.split(' ')[1] || entry.time}
              </Text>
              <Tag style={{ margin: 0, borderRadius: 999, fontSize: 10 }}>{entry.service}</Tag>
              <Text style={{ color: 'var(--lm-text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {entry.action}
              </Text>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default TraceTimeline;
