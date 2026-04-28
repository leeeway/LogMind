import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Popover, List, Tag, Typography, Space, Button, Empty } from 'antd';
import { BellOutlined, AlertOutlined, CheckOutlined } from '@ant-design/icons';
import { alertsApi } from '@/api/alerts';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

const { Text } = Typography;

const priorityColors: Record<string, string> = { P0: '#ff4d4f', P1: '#fa8c16', P2: '#fadb14', P3: '#8c8c8c' };

const NotificationBell: React.FC = () => {
  const navigate = useNavigate();
  const [alerts, setAlerts] = useState<any[]>([]);
  const [firedCount, setFiredCount] = useState(0);
  const [open, setOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAlerts = async () => {
    try {
      const { data } = await alertsApi.listHistory({ page: 1, page_size: 8 });
      const items = data.items || [];
      setAlerts(items);
      setFiredCount(items.filter((a: any) => a.status === 'fired').length);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    fetchAlerts();
    timerRef.current = setInterval(fetchAlerts, 60000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, []);

  const handleAck = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await alertsApi.ackAlert(id);
      fetchAlerts();
    } catch { /* ignore */ }
  };

  const content = (
    <div style={{ width: 360 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 0 10px', borderBottom: '1px solid var(--lm-border-light)' }}>
        <Text strong style={{ color: 'var(--lm-text)', fontSize: 14 }}>
          <AlertOutlined style={{ marginRight: 6 }} />通知中心
        </Text>
        <Button type="link" size="small" onClick={() => { setOpen(false); navigate('/alerts'); }}>
          查看全部
        </Button>
      </div>
      {alerts.length > 0 ? (
        <List
          dataSource={alerts.slice(0, 6)}
          renderItem={(item: any) => (
            <div
              onClick={() => { setOpen(false); navigate('/alerts'); }}
              style={{
                padding: '10px 4px',
                borderBottom: '1px solid var(--lm-border-light)',
                cursor: 'pointer',
                transition: 'background 0.2s',
                display: 'flex',
                gap: 10,
                alignItems: 'flex-start',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'rgba(22,119,255,0.04)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <Tag color={priorityColors[item.priority] || '#8c8c8c'} style={{ borderRadius: 4, fontWeight: 600, flexShrink: 0, marginTop: 2 }}>
                {item.priority}
              </Tag>
              <div style={{ flex: 1, minWidth: 0 }}>
                <Text ellipsis style={{ color: 'var(--lm-text)', fontSize: 13, display: 'block' }}>
                  {item.message}
                </Text>
                <Space size={8} style={{ marginTop: 4 }}>
                  <Tag
                    color={item.status === 'fired' ? '#ff4d4f' : item.status === 'acknowledged' ? '#fa8c16' : '#52c41a'}
                    style={{ borderRadius: 4, fontSize: 11 }}
                  >
                    {item.status === 'fired' && <span className="lm-running-dot" style={{ background: '#ff4d4f' }} />}
                    {item.status === 'fired' ? '触发中' : item.status === 'acknowledged' ? '已确认' : '已解决'}
                  </Tag>
                  <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                    {item.fired_at ? dayjs(item.fired_at).fromNow() : ''}
                  </Text>
                </Space>
              </div>
              {item.status === 'fired' && (
                <Button
                  size="small" type="text"
                  icon={<CheckOutlined style={{ fontSize: 12 }} />}
                  onClick={(e) => handleAck(item.id, e)}
                  style={{ color: 'var(--lm-text-tertiary)', flexShrink: 0 }}
                />
              )}
            </div>
          )}
        />
      ) : (
        <Empty description="暂无告警" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: '20px 0' }} />
      )}
    </div>
  );

  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="bottomRight"
      overlayStyle={{ width: 380 }}
      overlayInnerStyle={{
        background: 'var(--lm-bg-elevated)',
        border: '1px solid var(--lm-border-light)',
        borderRadius: 12,
        boxShadow: '0 12px 40px rgba(0,0,0,0.4)',
      }}
    >
      <Badge count={firedCount} size="small" offset={[-2, 4]}>
        <BellOutlined
          style={{
            fontSize: 16,
            color: firedCount > 0 ? '#fa8c16' : 'var(--lm-text-tertiary)',
            cursor: 'pointer',
            transition: 'color 0.3s',
          }}
        />
      </Badge>
    </Popover>
  );
};

export default NotificationBell;
