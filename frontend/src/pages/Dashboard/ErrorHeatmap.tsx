import React, { useEffect, useState, useMemo } from 'react';
import { Typography, Space, Tag, Select, Spin, Tooltip, Card, Empty } from 'antd';
import { HeatMapOutlined, ArrowRightOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import client from '@/api/client';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

// ── Color Scale (5 levels) ─────────────────────
const colorScale = (value: number, max: number): string => {
  if (max === 0 || value === 0) return 'rgba(255,255,255,0.03)';
  const ratio = Math.min(value / max, 1);
  if (ratio < 0.2) return 'rgba(22,119,255,0.15)';
  if (ratio < 0.4) return 'rgba(22,119,255,0.35)';
  if (ratio < 0.6) return 'rgba(114,46,209,0.5)';
  if (ratio < 0.8) return 'rgba(255,77,79,0.55)';
  return 'rgba(255,77,79,0.85)';
};

const borderColor = (value: number, max: number): string => {
  if (max === 0 || value === 0) return 'rgba(255,255,255,0.03)';
  const ratio = Math.min(value / max, 1);
  if (ratio < 0.4) return 'rgba(22,119,255,0.3)';
  if (ratio < 0.7) return 'rgba(114,46,209,0.4)';
  return 'rgba(255,77,79,0.5)';
};

const ErrorHeatmap: React.FC = () => {
  const [days, setDays] = useState(1);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [hoveredCell, setHoveredCell] = useState<{ service: string; bucket: string; x: number; y: number } | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const res = await client.get('/dashboard/heatmap', { params: { days } });
        setData(res.data);
      } catch { /* ignore */ }
      setLoading(false);
    };
    load();
  }, [days]);

  // Build matrix lookup
  const cellMap = useMemo(() => {
    if (!data?.cells) return {};
    const map: Record<string, any> = {};
    for (const c of data.cells) {
      map[`${c.service_id}__${c.bucket}`] = c;
    }
    return map;
  }, [data]);

  const maxCount = useMemo(() => {
    if (!data?.cells) return 1;
    return Math.max(...data.cells.map((c: any) => c.error_count + c.warning_count), 1);
  }, [data]);

  const formatBucket = (bucket: string) => {
    if (!bucket) return '';
    if (data?.granularity === 'hour') return dayjs(bucket).format('HH:00');
    return dayjs(bucket).format('MM/DD');
  };

  const handleCellClick = (serviceId: string, bucket: string) => {
    const params = new URLSearchParams();
    if (bucket) {
      const start = dayjs(bucket);
      const end = data?.granularity === 'hour' ? start.add(1, 'hour') : start.add(1, 'day');
      params.set('time_from', start.toISOString());
      params.set('time_to', end.toISOString());
    }
    navigate(`/logs?${params.toString()}`);
  };

  return (
    <div className="lm-animate-in" style={{ padding: 0 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <HeatMapOutlined style={{ fontSize: 20, color: '#1677ff' }} />
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>错误热力图</Title>
          <Tag color="blue">{data?.granularity === 'hour' ? '按小时' : '按天'}</Tag>
        </Space>
        <Select
          value={days}
          onChange={setDays}
          style={{ width: 120 }}
          options={[
            { value: 1, label: '最近 1 天' },
            { value: 7, label: '最近 7 天' },
            { value: 14, label: '最近 14 天' },
            { value: 30, label: '最近 30 天' },
          ]}
        />
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
      ) : !data?.services?.length ? (
        <Empty description="暂无数据" />
      ) : (
        <Card
          style={{
            background: 'var(--lm-bg-card)',
            border: '1px solid var(--lm-border-light)',
            borderRadius: 12,
            overflow: 'hidden',
          }}
          styles={{ body: { padding: 0 } }}
        >
          <div style={{ overflowX: 'auto', padding: '20px 20px 16px' }}>
            {/* Time labels row */}
            <div style={{ display: 'flex', paddingLeft: 140, marginBottom: 4 }}>
              {(data.time_buckets || []).map((b: string, i: number) => (
                <div
                  key={i}
                  style={{
                    width: 28, minWidth: 28, textAlign: 'center',
                    fontSize: 9, color: 'var(--lm-text-tertiary)',
                    fontFamily: 'monospace',
                  }}
                >
                  {i % (data.granularity === 'hour' ? 2 : 1) === 0 ? formatBucket(b) : ''}
                </div>
              ))}
            </div>

            {/* Service rows */}
            {(data.services || []).map((service: any, si: number) => (
              <div
                key={service.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  marginBottom: 3,
                  animation: `lm-fadeSlideIn 0.3s ease-out ${si * 0.04}s both`,
                }}
              >
                {/* Service name */}
                <div style={{
                  width: 136, minWidth: 136, paddingRight: 8,
                  fontSize: 12, color: 'var(--lm-text-secondary)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <div style={{
                    width: 4, height: 16, borderRadius: 2,
                    background: service.total_errors > maxCount * 0.5
                      ? '#ff4d4f'
                      : service.total_errors > 0 ? '#faad14' : '#52c41a',
                  }} />
                  {service.name}
                </div>

                {/* Cells */}
                {(data.time_buckets || []).map((bucket: string, bi: number) => {
                  const cell = cellMap[`${service.id}__${bucket}`];
                  const count = cell ? cell.error_count + cell.warning_count : 0;

                  return (
                    <Tooltip
                      key={bi}
                      title={
                        <div style={{ fontSize: 12 }}>
                          <div>{service.name}</div>
                          <div>{formatBucket(bucket)}</div>
                          <div style={{ marginTop: 4 }}>
                            🔴 错误: {cell?.error_count || 0} · ⚠️ 告警: {cell?.warning_count || 0}
                          </div>
                          {count > 0 && <div style={{ marginTop: 4, color: '#4096ff' }}>点击查看日志 →</div>}
                        </div>
                      }
                    >
                      <div
                        onClick={() => count > 0 && handleCellClick(service.id, bucket)}
                        style={{
                          width: 24, height: 24, minWidth: 24, margin: '0 2px',
                          borderRadius: 3,
                          background: colorScale(count, maxCount),
                          border: `1px solid ${borderColor(count, maxCount)}`,
                          cursor: count > 0 ? 'pointer' : 'default',
                          transition: 'all 0.15s',
                        }}
                        onMouseEnter={e => {
                          if (count > 0) {
                            e.currentTarget.style.transform = 'scale(1.3)';
                            e.currentTarget.style.zIndex = '10';
                            e.currentTarget.style.position = 'relative';
                          }
                        }}
                        onMouseLeave={e => {
                          e.currentTarget.style.transform = 'scale(1)';
                          e.currentTarget.style.zIndex = '0';
                        }}
                      />
                    </Tooltip>
                  );
                })}

                {/* Total */}
                <div style={{
                  minWidth: 50, paddingLeft: 8,
                  fontSize: 11, fontFamily: 'monospace',
                  color: service.total_errors > 0 ? '#ff4d4f' : 'var(--lm-text-tertiary)',
                  fontWeight: service.total_errors > 0 ? 600 : 400,
                }}>
                  {service.total_errors > 0 ? service.total_errors : '—'}
                </div>
              </div>
            ))}
          </div>

          {/* Legend */}
          <div style={{
            padding: '12px 20px',
            borderTop: '1px solid var(--lm-border-light)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}>
            <Space size={4}>
              <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>少</Text>
              {[0, 0.2, 0.4, 0.6, 0.8, 1].map((r, i) => (
                <div
                  key={i}
                  style={{
                    width: 16, height: 16, borderRadius: 3,
                    background: colorScale(r * 10, 10),
                    border: `1px solid ${borderColor(r * 10, 10)}`,
                  }}
                />
              ))}
              <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>多</Text>
            </Space>
            <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
              点击色块钻取日志 <ArrowRightOutlined />
            </Text>
          </div>
        </Card>
      )}
    </div>
  );
};

export default ErrorHeatmap;
