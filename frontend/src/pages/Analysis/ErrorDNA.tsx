import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Space, Button, Tag, Table, Spin, Empty, Tooltip,
  Row, Col, Badge, Drawer, Descriptions, Progress,
} from 'antd';
import {
  ExperimentOutlined, ReloadOutlined, RiseOutlined, FallOutlined,
  MinusOutlined, BugOutlined, ClockCircleOutlined, ApartmentOutlined,
} from '@ant-design/icons';
import client from '@/api/client';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const trendIcons: Record<string, React.ReactNode> = {
  rising: <RiseOutlined style={{ color: '#ff4d4f' }} />,
  falling: <FallOutlined style={{ color: '#52c41a' }} />,
  stable: <MinusOutlined style={{ color: '#8c8c8c' }} />,
};

const ErrorDNA: React.FC = () => {
  const [patterns, setPatterns] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState({ patterns: 0, occurrences: 0 });
  const [detailDrawer, setDetailDrawer] = useState<any>(null);
  const [timeline, setTimeline] = useState<any>(null);
  const [mutations, setMutations] = useState<any[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [pRes, mRes] = await Promise.all([
        client.get('/error-dna/patterns', { params: { days: 7, min_count: 2 } }).catch(() => ({ data: { patterns: [], total_patterns: 0, total_occurrences: 0 } })),
        client.get('/error-dna/mutations').catch(() => ({ data: { mutations: [] } })),
      ]);
      setPatterns(pRes?.data?.patterns || []);
      setTotal({
        patterns: pRes?.data?.total_patterns || 0,
        occurrences: pRes?.data?.total_occurrences || 0,
      });
      setMutations(mRes?.data?.mutations || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const showDetail = async (pattern: any) => {
    setDetailDrawer(pattern);
    try {
      const { data } = await client.get(`/error-dna/patterns/${pattern.pattern_id}/timeline`);
      setTimeline(data);
    } catch { /* ignore */ }
  };

  const columns = [
    {
      title: '错误签名', dataIndex: 'signature', key: 'sig', ellipsis: true,
      render: (v: string, r: any) => (
        <Space>
          <BugOutlined style={{ color: r.severity === 'critical' ? '#ff4d4f' : '#faad14' }} />
          <Text strong style={{ color: 'var(--lm-text)', fontFamily: 'monospace', fontSize: 12 }}>
            {v}
          </Text>
        </Space>
      ),
    },
    {
      title: '出现', dataIndex: 'occurrence_count', key: 'cnt', width: 80,
      sorter: (a: any, b: any) => a.occurrence_count - b.occurrence_count,
      render: (v: number) => <Badge count={v} style={{ background: v > 10 ? '#ff4d4f' : '#1677ff' }} />,
    },
    {
      title: '趋势', dataIndex: 'trend', key: 'trend', width: 70,
      render: (v: string) => <Tooltip title={v}>{trendIcons[v]}</Tooltip>,
    },
    {
      title: '受影响服务', dataIndex: 'affected_services', key: 'svc',
      render: (v: string[]) => v.slice(0, 3).map((s, i) => (
        <Tag key={i} style={{ borderRadius: 3, fontSize: 11 }}>{s}</Tag>
      )),
    },
    {
      title: '首次', dataIndex: 'first_seen', key: 'first', width: 100,
      render: (v: string) => <Text style={{ fontSize: 11, color: 'var(--lm-text-secondary)' }}>{dayjs(v).format('MM-DD HH:mm')}</Text>,
    },
    {
      title: '最近', dataIndex: 'last_seen', key: 'last', width: 100,
      render: (v: string) => <Text style={{ fontSize: 11, color: 'var(--lm-text-secondary)' }}>{dayjs(v).format('MM-DD HH:mm')}</Text>,
    },
    {
      title: '', key: 'action', width: 80,
      render: (_: any, r: any) => (
        <Button size="small" type="link" onClick={() => showDetail(r)}>详情</Button>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <ExperimentOutlined style={{ marginRight: 8 }} />错误 DNA 指纹图谱
        </Title>
        <Space>
          <Tag color="#1677ff">{total.patterns} 个 Pattern</Tag>
          <Tag color="#722ed1">{total.occurrences} 次出现</Tag>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </Space>
      </div>

      {/* Mutation Alert */}
      {mutations.length > 0 && (
        <Card size="small" style={{
          marginBottom: 16, background: 'rgba(250,173,20,0.06)',
          border: '1px solid rgba(250,173,20,0.2)', borderRadius: 12,
        }}>
          <Space>
            <ExperimentOutlined style={{ color: '#faad14', fontSize: 16 }} />
            <Text strong style={{ color: '#faad14' }}>
              检测到 {mutations.length} 个错误变种 — 与已知 Pattern 相似但不完全匹配
            </Text>
          </Space>
          <div style={{ marginTop: 8 }}>
            {mutations.slice(0, 3).map((m: any, i: number) => (
              <div key={i} style={{ fontSize: 12, color: 'var(--lm-text-secondary)', padding: '2px 0' }}>
                <Tag color="orange" style={{ fontSize: 10 }}>相似度 {(m.similarity * 100).toFixed(0)}%</Tag>
                <Text code style={{ fontSize: 11 }}>{m.mutated_signature}</Text>
                <Text type="secondary" style={{ fontSize: 11 }}> ← 变种自 {m.original_signature}</Text>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Pattern Table */}
      <Card size="small" style={{
        background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
      }} styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={patterns}
          columns={columns}
          rowKey="pattern_id"
          loading={loading}
          size="small"
          pagination={false}
          locale={{ emptyText: <Empty description="暂无错误模式数据" /> }}
        />
      </Card>

      {/* Detail Drawer */}
      <Drawer
        title={<Space><BugOutlined /> Pattern 生命周期</Space>}
        open={!!detailDrawer}
        onClose={() => { setDetailDrawer(null); setTimeline(null); }}
        width={520}
      >
        {detailDrawer && (
          <div>
            <Descriptions size="small" column={1} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="签名">
                <Text code style={{ fontSize: 12 }}>{detailDrawer.signature}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="总出现">{detailDrawer.occurrence_count} 次</Descriptions.Item>
              <Descriptions.Item label="趋势">
                {trendIcons[detailDrawer.trend]} {detailDrawer.trend}
              </Descriptions.Item>
            </Descriptions>

            {timeline && (
              <>
                <Text strong style={{ display: 'block', marginBottom: 8 }}>每日趋势</Text>
                {timeline.daily_counts?.map((d: any) => (
                  <div key={d.date} style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0',
                  }}>
                    <Text style={{ fontSize: 12, minWidth: 70 }}>{d.date}</Text>
                    <Progress
                      percent={Math.min(100, d.count * 10)}
                      size="small"
                      format={() => d.count}
                      strokeColor={d.count > 5 ? '#ff4d4f' : '#1677ff'}
                    />
                  </div>
                ))}

                <Text strong style={{ display: 'block', margin: '16px 0 8px' }}>服务分布</Text>
                {timeline.service_distribution?.map((s: any, i: number) => (
                  <Tag key={i} style={{ marginBottom: 4 }}>
                    {s.service}: {s.count}
                  </Tag>
                ))}
              </>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
};

export default ErrorDNA;
