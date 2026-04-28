import React, { useEffect, useState } from 'react';
import { Spin, Tag, Typography, Space, Empty } from 'antd';
import {
  AlertOutlined, ThunderboltOutlined, RiseOutlined,
  ClockCircleOutlined, BugOutlined, CheckCircleOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import { timelineApi } from '@/api/chat';
import dayjs from 'dayjs';

const { Text } = Typography;

interface TimelineEvent {
  timestamp: string;
  event_type: string;
  severity: string;
  title: string;
  description: string;
  source: string;
  metadata: Record<string, any>;
}

const typeIcons: Record<string, React.ReactNode> = {
  alert: <AlertOutlined />,
  error_spike: <RiseOutlined />,
  change_point: <ThunderboltOutlined />,
  ai_finding: <RobotOutlined />,
  stage: <ClockCircleOutlined />,
  correlation: <BugOutlined />,
};

const severityColors: Record<string, { bg: string; border: string; dot: string }> = {
  critical: { bg: 'rgba(255,77,79,0.06)', border: '#ff4d4f', dot: '#ff4d4f' },
  warning: { bg: 'rgba(250,173,20,0.06)', border: '#faad14', dot: '#faad14' },
  info: { bg: 'rgba(22,119,255,0.06)', border: '#1677ff', dot: '#1677ff' },
};

const typeLabels: Record<string, string> = {
  alert: '告警', ai_finding: 'AI 发现', stage: 'Pipeline',
  error_spike: '错误峰值', change_point: '变更点', correlation: '跨服务关联',
};

interface Props {
  taskId: string;
}

const IncidentTimeline: React.FC<Props> = ({ taskId }) => {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const { data } = await timelineApi.getTimeline(taskId);
        setEvents(data?.events || []);
      } catch { /* ignore */ }
      setLoading(false);
    };
    load();
  }, [taskId]);

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>;
  if (!events.length) return <Empty description="暂无时间线事件" style={{ padding: 40 }} />;

  return (
    <div style={{ padding: '16px 0' }}>
      <div style={{ position: 'relative', paddingLeft: 40 }}>
        {/* Vertical line */}
        <div style={{
          position: 'absolute', left: 15, top: 8, bottom: 8, width: 2,
          background: 'linear-gradient(180deg, var(--lm-border-light) 0%, rgba(22,119,255,0.3) 50%, var(--lm-border-light) 100%)',
        }} />

        {events.map((event, idx) => {
          const colors = severityColors[event.severity] || severityColors.info;
          const isLast = idx === events.length - 1;

          return (
            <div
              key={idx}
              style={{
                position: 'relative', marginBottom: isLast ? 0 : 16,
                animation: `lm-fadeSlideIn 0.4s ease-out ${idx * 0.06}s both`,
              }}
            >
              {/* Dot */}
              <div style={{
                position: 'absolute', left: -33, top: 6,
                width: 14, height: 14, borderRadius: '50%',
                background: colors.dot, border: '3px solid var(--lm-bg-container)',
                boxShadow: `0 0 8px ${colors.dot}40`,
                zIndex: 2,
              }} />

              {/* Connecting arrow for cascade */}
              {idx > 0 && events[idx - 1].severity === 'critical' && event.severity === 'critical' && (
                <div style={{
                  position: 'absolute', left: -29, top: -8,
                  width: 6, height: 8,
                  borderLeft: '3px solid transparent',
                  borderRight: '3px solid transparent',
                  borderTop: `6px solid ${colors.dot}`,
                }} />
              )}

              {/* Card */}
              <div style={{
                padding: '10px 14px', borderRadius: 10,
                background: colors.bg,
                borderLeft: `3px solid ${colors.border}`,
                transition: 'all 0.2s',
                cursor: 'default',
              }}
                onMouseEnter={e => { e.currentTarget.style.background = `${colors.border}12`; }}
                onMouseLeave={e => { e.currentTarget.style.background = colors.bg; }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <Space size={6}>
                    <span style={{ color: colors.border, fontSize: 14 }}>
                      {typeIcons[event.event_type] || <ClockCircleOutlined />}
                    </span>
                    <Text style={{ fontWeight: 600, fontSize: 13, color: 'var(--lm-text)' }}>
                      {event.title}
                    </Text>
                    <Tag
                      color={colors.border}
                      style={{ borderRadius: 4, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}
                    >
                      {typeLabels[event.event_type] || event.event_type}
                    </Tag>
                  </Space>
                  <Text style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--lm-text-tertiary)' }}>
                    {event.timestamp ? dayjs(event.timestamp).format('HH:mm:ss') : ''}
                  </Text>
                </div>
                <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', lineHeight: 1.6 }}>
                  {event.description}
                </Text>

                {/* Metadata */}
                {event.metadata && Object.keys(event.metadata).length > 0 && (
                  <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {event.metadata.duration_ms != null && (
                      <Tag style={{ borderRadius: 4, fontSize: 10 }}>⏱ {event.metadata.duration_ms}ms</Tag>
                    )}
                    {event.metadata.confidence != null && (
                      <Tag style={{ borderRadius: 4, fontSize: 10 }}>🎯 {(event.metadata.confidence * 100).toFixed(0)}%</Tag>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default IncidentTimeline;
