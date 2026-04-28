import React, { useEffect, useState } from 'react';
import { Card, Typography, Row, Col, Table, Tag, Space, Progress, Statistic, Button, Select } from 'antd';
import { DashboardOutlined, ReloadOutlined, CheckCircleOutlined, ClockCircleOutlined, WarningOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { dashboardApi } from '@/api/dashboard';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const SLADashboard: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState(7);

  const load = async () => {
    setLoading(true);
    try {
      const { data: sla } = await dashboardApi.getSLA(days);
      setData(sla);
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [days]);

  const getAvailabilityColor = (pct: number) => {
    if (pct >= 99.9) return '#52c41a';
    if (pct >= 99.5) return '#faad14';
    return '#ff4d4f';
  };

  const getBudgetColor = (pct: number) => {
    if (pct <= 50) return '#52c41a';
    if (pct <= 80) return '#faad14';
    return '#ff4d4f';
  };

  const columns = [
    {
      title: '服务', dataIndex: 'business_line_name', key: 'name',
      render: (v: string) => <span style={{ fontWeight: 600, color: 'var(--lm-text)' }}>{v}</span>,
    },
    {
      title: '可用性', dataIndex: 'availability_pct', key: 'availability',
      render: (v: number) => (
        <span style={{ fontWeight: 700, fontSize: 16, color: getAvailabilityColor(v) }}>
          {v.toFixed(3)}%
        </span>
      ),
      sorter: (a: any, b: any) => a.availability_pct - b.availability_pct,
    },
    {
      title: 'SLA 目标', dataIndex: 'sla_target', key: 'target',
      render: (v: number) => <Tag color="#1677ff" style={{ borderRadius: 4 }}>{v}%</Tag>,
    },
    {
      title: 'MTTR', dataIndex: 'mttr_minutes', key: 'mttr',
      render: (v: number) => (
        <span style={{ fontWeight: 600, color: v > 30 ? '#ff4d4f' : v > 10 ? '#faad14' : '#52c41a' }}>
          {v.toFixed(1)} min
        </span>
      ),
      sorter: (a: any, b: any) => a.mttr_minutes - b.mttr_minutes,
    },
    {
      title: '事件数', dataIndex: 'total_incidents', key: 'incidents',
      render: (v: number, r: any) => (
        <Space>
          <span>{v}</span>
          <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
            ({r.resolved_incidents} 已解决)
          </Text>
        </Space>
      ),
    },
    {
      title: '错误预算消耗', dataIndex: 'error_budget_consumed_pct', key: 'budget',
      width: 200,
      render: (v: number) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Progress
            percent={Math.min(v, 100)}
            strokeColor={getBudgetColor(v)}
            trailColor="rgba(255,255,255,0.06)"
            size="small"
            showInfo={false}
            style={{ flex: 1, marginBottom: 0 }}
          />
          <span style={{ fontSize: 12, fontWeight: 600, color: getBudgetColor(v), minWidth: 40 }}>
            {v.toFixed(1)}%
          </span>
        </div>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <SafetyCertificateOutlined style={{ marginRight: 8 }} />SLA 监控
        </Title>
        <Space>
          <Select value={days} onChange={setDays} style={{ width: 120 }} options={[
            { value: 1, label: '近 1 天' },
            { value: 7, label: '近 7 天' },
            { value: 14, label: '近 14 天' },
            { value: 30, label: '近 30 天' },
          ]} />
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </Space>
      </div>

      {/* Overview Cards */}
      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col span={6}>
          <div className="lm-stat-card">
            <div className="stat-label"><CheckCircleOutlined style={{ marginRight: 4 }} />总体可用性</div>
            <div className="stat-value" style={{ color: data ? getAvailabilityColor(data.overall_availability) : '#52c41a' }}>
              {data?.overall_availability?.toFixed(3) || '—'}%
            </div>
          </div>
        </Col>
        <Col span={6}>
          <div className="lm-stat-card">
            <div className="stat-label"><ClockCircleOutlined style={{ marginRight: 4 }} />平均 MTTR</div>
            <div className="stat-value">
              {data?.overall_mttr_minutes?.toFixed(1) || '—'} <span style={{ fontSize: 14, opacity: 0.6 }}>min</span>
            </div>
          </div>
        </Col>
        <Col span={6}>
          <div className="lm-stat-card">
            <div className="stat-label"><WarningOutlined style={{ marginRight: 4 }} />总事件数</div>
            <div className="stat-value" style={{ color: '#ff7875' }}>
              {data?.total_incidents?.toLocaleString() || '0'}
            </div>
          </div>
        </Col>
        <Col span={6}>
          <div className="lm-stat-card">
            <div className="stat-label"><CheckCircleOutlined style={{ marginRight: 4 }} />已解决</div>
            <div className="stat-value" style={{ color: '#52c41a' }}>
              {data?.total_resolved?.toLocaleString() || '0'}
            </div>
          </div>
        </Col>
      </Row>

      {/* Availability Ring (simplified with Progress) */}
      {data && (
        <Card
          size="small"
          style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 20, textAlign: 'center', padding: '20px 0' }}
        >
          <div style={{ display: 'flex', justifyContent: 'center', gap: 60 }}>
            <div>
              <Progress
                type="dashboard"
                percent={Math.min(data.overall_availability, 100)}
                strokeColor={getAvailabilityColor(data.overall_availability)}
                trailColor="rgba(255,255,255,0.06)"
                format={() => `${data.overall_availability.toFixed(2)}%`}
                size={160}
              />
              <div style={{ textAlign: 'center', marginTop: 8, color: 'var(--lm-text-secondary)', fontSize: 13 }}>
                总体可用性 (近{days}天)
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 16 }}>
              <Statistic title="平均修复时间" value={data.overall_mttr_minutes} suffix="分钟" precision={1} />
              <Statistic title="事件解决率" value={data.total_incidents > 0 ? (data.total_resolved / data.total_incidents * 100) : 100} suffix="%" precision={1} valueStyle={{ color: '#52c41a' }} />
            </div>
          </div>
        </Card>
      )}

      {/* Per-service SLA Table */}
      <Card
        title="服务级 SLA 指标"
        size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' }, body: { padding: 0 } }}
      >
        <Table
          dataSource={data?.by_business_line || []}
          columns={columns}
          rowKey="business_line_id"
          loading={loading}
          size="small"
          pagination={false}
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>
    </div>
  );
};

export default SLADashboard;
