import React, { useEffect, useState, useRef } from 'react';
import { Typography, Space, Tag, Button, Select, Spin, Empty, Card } from 'antd';
import {
  FileTextOutlined, LeftOutlined, RightOutlined, CopyOutlined,
  AlertOutlined, CheckCircleOutlined, RiseOutlined, FallOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import client from '@/api/client';

const { Title, Text } = Typography;

const WeeklyReport: React.FC = () => {
  const [weekOffset, setWeekOffset] = useState(0);
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const trendCanvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const res = await client.get('/dashboard/weekly-report', { params: { week_offset: weekOffset } });
        setReport(res.data);
      } catch { /* ignore */ }
      setLoading(false);
    };
    load();
  }, [weekOffset]);

  // Draw trend chart
  useEffect(() => {
    const canvas = trendCanvasRef.current;
    if (!canvas || !report?.daily_trends?.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const parent = canvas.parentElement;
    canvas.width = parent?.clientWidth || 400;
    canvas.height = 120;
    const W = canvas.width;
    const H = canvas.height;
    const data = report.daily_trends;
    const maxVal = Math.max(...data.map((d: any) => d.error_count + d.warning_count), 1);
    const barW = Math.min((W - 40) / data.length - 4, 40);

    ctx.clearRect(0, 0, W, H);

    data.forEach((d: any, i: number) => {
      const x = 20 + i * ((W - 40) / data.length) + ((W - 40) / data.length - barW) / 2;
      const errH = (d.error_count / maxVal) * (H - 30);
      const warnH = (d.warning_count / maxVal) * (H - 30);

      // Warning bar
      ctx.fillStyle = 'rgba(250,173,20,0.5)';
      ctx.beginPath();
      ctx.roundRect(x, H - 20 - warnH - errH, barW, warnH, [3, 3, 0, 0]);
      ctx.fill();

      // Error bar
      ctx.fillStyle = 'rgba(255,77,79,0.7)';
      ctx.beginPath();
      ctx.roundRect(x, H - 20 - errH, barW, errH, [3, 3, 0, 0]);
      ctx.fill();

      // Date label
      ctx.font = '10px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(255,255,255,0.4)';
      ctx.fillText(d.date, x + barW / 2, H - 5);
    });
  }, [report]);

  const copyMarkdown = () => {
    if (!report) return;
    const md = `# LogMind 运维周报 (${report.week_start} ~ ${report.week_end})\n\n${report.ai_summary}\n\n## 建议行动\n${report.action_items.map((a: string) => `- ${a}`).join('\n')}`;
    navigator.clipboard.writeText(md);
  };

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>;
  if (!report) return <Empty description="暂无报告数据" />;

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <FileTextOutlined style={{ fontSize: 20, color: '#1677ff' }} />
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>智能巡检周报</Title>
        </Space>
        <Space>
          <Button icon={<LeftOutlined />} size="small" onClick={() => setWeekOffset(Math.max(weekOffset - 1, -12))} />
          <Tag color="blue" style={{ margin: 0 }}>{report.week_start} ~ {report.week_end}</Tag>
          <Button icon={<RightOutlined />} size="small" onClick={() => setWeekOffset(Math.min(weekOffset + 1, 0))} disabled={weekOffset >= 0} />
          <Button icon={<CopyOutlined />} size="small" onClick={copyMarkdown}>复制 Markdown</Button>
        </Space>
      </div>

      {/* KPI Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 16 }}>
        {[
          { label: '分析任务', value: report.total_tasks, color: '#1677ff', icon: <CheckCircleOutlined /> },
          { label: '严重错误', value: report.total_errors, color: '#ff4d4f', icon: <AlertOutlined /> },
          { label: '告警', value: report.total_alerts, color: '#faad14', icon: <AlertOutlined /> },
          { label: 'P0 告警', value: report.p0_alerts, color: report.p0_alerts > 0 ? '#ff4d4f' : '#52c41a', icon: <AlertOutlined /> },
          { label: '成功率', value: `${report.success_rate}%`, color: '#52c41a', icon: <CheckCircleOutlined /> },
        ].map((kpi, i) => (
          <div key={i} style={{
            background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
            borderRadius: 10, padding: '12px 16px', textAlign: 'center',
            animation: `lm-fadeSlideIn 0.3s ease-out ${i * 0.05}s both`,
          }}>
            <div style={{ fontSize: 28, fontWeight: 700, fontFamily: 'monospace', color: kpi.color, lineHeight: 1 }}>
              {kpi.value}
            </div>
            <div style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', marginTop: 4 }}>{kpi.label}</div>
          </div>
        ))}
      </div>

      {/* Main Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 16 }}>
        {/* Left Column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Trend Chart */}
          <Card size="small" title="错误趋势" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            <div style={{ position: 'relative' }}>
              <canvas ref={trendCanvasRef} style={{ display: 'block', width: '100%', height: 120, borderRadius: 6 }} />
            </div>
            <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
              <Space size={4}><div style={{ width: 12, height: 8, borderRadius: 2, background: 'rgba(255,77,79,0.7)' }} /><Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>Error</Text></Space>
              <Space size={4}><div style={{ width: 12, height: 8, borderRadius: 2, background: 'rgba(250,173,20,0.5)' }} /><Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>Warning</Text></Space>
            </div>
          </Card>

          {/* AI Summary */}
          <Card size="small" title="📝 AI 摘要" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            <div className="lm-markdown-content" style={{ fontSize: 13, lineHeight: 1.8 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{report.ai_summary}</ReactMarkdown>
            </div>
          </Card>
        </div>

        {/* Right Column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Top Services */}
          <Card size="small" title="🏆 Top 5 问题服务" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            {report.top_services?.length ? report.top_services.map((s: any, i: number) => (
              <div key={i} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '8px 0',
                borderBottom: i < report.top_services.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                animation: `lm-fadeSlideIn 0.3s ease-out ${i * 0.06}s both`,
              }}>
                <Space size={8}>
                  <Tag style={{
                    width: 20, height: 20, borderRadius: '50%', textAlign: 'center',
                    lineHeight: '20px', padding: 0, fontSize: 11, fontWeight: 700,
                    background: i === 0 ? '#ff4d4f' : i === 1 ? '#fa8c16' : i === 2 ? '#faad14' : 'var(--lm-bg-elevated)',
                    border: 'none', color: '#fff',
                  }}>{i + 1}</Tag>
                  <Text style={{ color: 'var(--lm-text)', fontSize: 13 }}>{s.service_name}</Text>
                </Space>
                <Space size={8}>
                  <Text style={{ fontFamily: 'monospace', fontWeight: 600, color: s.error_count > 0 ? '#ff4d4f' : 'var(--lm-text-tertiary)' }}>
                    {s.error_count}
                  </Text>
                  <Tag color={s.change_pct > 0 ? '#ff4d4f' : s.change_pct < 0 ? '#52c41a' : '#8c8c8c'} style={{ borderRadius: 3, fontSize: 10 }}>
                    {s.change_pct > 0 ? <RiseOutlined /> : s.change_pct < 0 ? <FallOutlined /> : null}
                    {Math.abs(s.change_pct)}%
                  </Tag>
                </Space>
              </div>
            )) : <Text style={{ color: 'var(--lm-text-tertiary)' }}>无数据</Text>}
          </Card>

          {/* Action Items */}
          <Card size="small" title="🎯 建议行动" style={{
            background: 'var(--lm-bg-card)', border: '1px solid rgba(82,196,26,0.15)', borderRadius: 10, flex: 1,
          }} styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}>
            {report.action_items?.map((item: string, i: number) => (
              <div key={i} style={{
                padding: '6px 0', fontSize: 13, color: 'var(--lm-text-secondary)', lineHeight: 1.6,
                borderBottom: i < report.action_items.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                display: 'flex', gap: 8, alignItems: 'flex-start',
              }}>
                <span style={{ color: '#52c41a', flexShrink: 0 }}>•</span>
                {item}
              </div>
            ))}
          </Card>
        </div>
      </div>
    </div>
  );
};

export default WeeklyReport;
