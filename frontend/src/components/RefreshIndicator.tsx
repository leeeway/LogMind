import React from 'react';
import { Space, Typography, Tooltip } from 'antd';
import { SyncOutlined, ClockCircleOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

const { Text } = Typography;

interface RefreshIndicatorProps {
  lastUpdated: Date | null;
  secondsUntilRefresh: number;
  loading: boolean;
  onRefresh: () => void;
}

const RefreshIndicator: React.FC<RefreshIndicatorProps> = ({
  lastUpdated,
  secondsUntilRefresh,
  loading,
  onRefresh,
}) => {
  return (
    <Tooltip title="点击手动刷新">
      <Space
        onClick={onRefresh}
        style={{
          cursor: 'pointer',
          padding: '4px 10px',
          borderRadius: 6,
          background: 'var(--lm-bg-elevated)',
          border: '1px solid var(--lm-border-light)',
          fontSize: 12,
          transition: 'all 0.2s',
        }}
        className="lm-refresh-indicator"
      >
        <SyncOutlined spin={loading} style={{ color: loading ? 'var(--lm-primary)' : 'var(--lm-text-tertiary)' }} />
        <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }}>
          {loading ? '刷新中...' : (
            <>
              {lastUpdated ? dayjs(lastUpdated).format('HH:mm:ss') : '-'}
              <span style={{ marginLeft: 6, color: 'var(--lm-text-tertiary)', opacity: 0.6 }}>
                {secondsUntilRefresh}s
              </span>
            </>
          )}
        </Text>
      </Space>
    </Tooltip>
  );
};

export default RefreshIndicator;
