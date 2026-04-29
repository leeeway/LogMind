import React, { useState, useEffect, useCallback } from 'react';
import { Card, Typography, Space, Button, Tag, Row, Col, Spin, DatePicker, message, Empty, Tooltip } from 'antd';
import {
  CalendarOutlined, RocketOutlined, ReloadOutlined, ShareAltOutlined,
  AlertOutlined, CheckCircleOutlined, ClockCircleOutlined, ArrowUpOutlined,
  ArrowDownOutlined, MinusOutlined, CopyOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import client from '@/api/client';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const DailyStandup: React.FC = () => {
  const [date, setDate] = useState<string>('');
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [sharing, setSharing] = useState(false);

  const load = useCallback(async (targetDate?: string) => {
    setLoading(true);
    try {
      const params: any = {};
      if (targetDate) params.date = targetDate;
      const { data: res } = await client.get('/dashboard/standup', { params });
      setData(res);
      if (!targetDate && res.date) setDate(res.date);
    } catch { message.error('加载站会摘要失败'); }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleGenerate = async () => {
    setLoading(true);
    try {
      const params: any = {};
      if (date) params.date = date;
      const { data: res } = await client.post('/dashboard/standup/generate', null, { params });
      setData(res);
      message.success('站会摘要已生成');
    } catch { message.error('生成失败'); }
    setLoading(false);
  };

  const handleShare = async () => {
    setSharing(true);
    try {
      const params: any = {};
      if (date) params.date = date;
      const { data: res } = await client.post('/dashboard/standup/share', null, { params });
      message.success(`已分享到 ${res.sent_count} 个通道`);
    } catch { message.error('分享失败'); }
    setSharing(false);
  };

  const copyToClipboard = () => {
    if (!data?.ai_summary) return;
    const text = `📋 LogMind 每日站会 — ${data.date}\n\n${data.ai_summary}`;
    navigator.clipboard.writeText(text).then(() => message.success('已复制到剪贴板'));
  };

  const alerts = data?.data?.alerts || {};
  const analysis = data?.data?.analysis || {};
  const trendIcon = (v: number) =>
    v > 0 ? <ArrowUpOutlined style={{ color: '#ff4d4f', fontSize: 12 }} />
    : v < 0 ? <ArrowDownOutlined style={{ color: '#52c41a', fontSize: 12 }} />
    : <MinusOutlined style={{ color: '#8c8c8c', fontSize: 12 }} />;

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Space>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
            <CalendarOutlined style={{ marginRight: 8 }} />每日站会
          </Title>
          {data?.date && <Tag color="#722ed1">{data.date}</Tag>}
          {data?.generated_at && (
            <Text type="secondary" style={{ fontSize: 11 }}>
              生成于 {dayjs(data.generated_at).format('HH:mm:ss')}
            </Text>
          )}
        </Space>
        <Space>
          <DatePicker
            value={date ? dayjs(date) : undefined}
            onChange={(d) => { const ds = d?.format('YYYY-MM-DD') || ''; setDate(ds); if (ds) load(ds); }}
            disabledDate={(d) => d.isAfter(dayjs())}
            allowClear
            placeholder="选择日期"
            style={{ width: 140 }}
          />
          <Button icon={<RocketOutlined />} type="primary" onClick={handleGenerate} loading={loading}>
            重新生成
          </Button>
          <Button icon={<ShareAltOutlined />} onClick={handleShare} loading={sharing}>
            分享
          </Button>
          <Tooltip title="复制摘要">
            <Button icon={<CopyOutlined />} onClick={copyToClipboard} />
          </Tooltip>
        </Space>
      </div>

      {loading && !data ? (
        <div style={{ textAlign: 'center', paddingTop: 100 }}><Spin size="large" /></div>
      ) : !data ? (
        <Empty description="暂无站会数据" />
      ) : (
        <>
          {/* KPI Cards */}
          <Row gutter={12} style={{ marginBottom: 16 }}>
            {[
              { label: '告警总数', value: alerts.total || 0, icon: <AlertOutlined />,
                color: alerts.total > 0 ? '#ff4d4f' : '#52c41a',
                sub: <Space size={4}>{trendIcon(alerts.vs_prev_day)}<span>较前日 {alerts.vs_prev_day > 0 ? '+' : ''}{alerts.vs_prev_day || 0}</span></Space>,
              },
              { label: 'P0 严重', value: alerts.p0 || 0, icon: <ThunderboltOutlined />,
                color: alerts.p0 > 0 ? '#ff4d4f' : '#52c41a',
                sub: `P1: ${alerts.p1 || 0} · P2: ${alerts.p2 || 0}`,
              },
              { label: '确认率', value: `${alerts.ack_rate_pct || 0}%`, icon: <CheckCircleOutlined />,
                color: (alerts.ack_rate_pct || 0) >= 80 ? '#52c41a' : '#faad14',
                sub: `解决率: ${alerts.resolve_rate_pct || 0}%`,
              },
              { label: '分析任务', value: analysis.total_tasks || 0, icon: <ClockCircleOutlined />,
                color: '#1677ff',
                sub: `完成: ${analysis.completed || 0} · 严重: ${analysis.critical_findings || 0}`,
              },
            ].map((kpi, i) => (
              <Col span={6} key={i}>
                <div className="lm-stat-card" style={{ borderLeft: `3px solid ${kpi.color}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span style={{ color: kpi.color, fontSize: 14 }}>{kpi.icon}</span>
                    <span className="stat-label">{kpi.label}</span>
                  </div>
                  <div className="stat-value" style={{ color: kpi.color, fontSize: 28 }}>{kpi.value}</div>
                  <div style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', marginTop: 2 }}>{kpi.sub}</div>
                </div>
              </Col>
            ))}
          </Row>

          {/* Top Services */}
          {data.data?.top_services?.length > 0 && (
            <Card
              size="small"
              title={<Space>🏢 受影响服务 Top 5</Space>}
              style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
            >
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {data.data.top_services.map((s: any, i: number) => (
                  <div key={i} style={{
                    padding: '8px 14px', background: 'var(--lm-bg-elevated)', borderRadius: 8,
                    border: '1px solid var(--lm-border-light)',
                    display: 'flex', alignItems: 'center', gap: 8,
                  }}>
                    <Tag color={i === 0 ? '#ff4d4f' : i === 1 ? '#faad14' : '#1677ff'} style={{ borderRadius: 4, margin: 0 }}>
                      #{i + 1}
                    </Tag>
                    <span style={{ fontWeight: 600, color: 'var(--lm-text)' }}>{s.name}</span>
                    <span style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>{s.tasks} 任务</span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* AI Summary */}
          <Card
            title={<Space>🤖 AI 站会摘要</Space>}
            size="small"
            extra={
              <Space>
                <Tag color="purple">AI Generated</Tag>
                <Space size={4}>
                  {trendIcon(alerts.vs_week_avg)}
                  <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
                    较周均 {alerts.vs_week_avg > 0 ? '+' : ''}{(alerts.vs_week_avg || 0).toFixed(0)}
                  </Text>
                </Space>
              </Space>
            }
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
          >
            <div className="lm-markdown-content" style={{ lineHeight: 1.8, fontSize: 14 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.ai_summary || '暂无摘要'}</ReactMarkdown>
            </div>
          </Card>
        </>
      )}
    </div>
  );
};

export default DailyStandup;
