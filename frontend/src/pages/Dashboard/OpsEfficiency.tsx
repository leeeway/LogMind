import React, { useState, useEffect, useCallback } from 'react';
import {
  Alert, Card, Typography, Space, Button, Tag, Row, Col, Progress, Empty, Spin,
} from 'antd';
import {
  RadarChartOutlined, ReloadOutlined, RiseOutlined, FallOutlined,
  CheckCircleOutlined, WarningOutlined, BulbOutlined, RocketOutlined,
} from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const gradeColors: Record<string, string> = {
  S: '#722ed1', A: '#52c41a', B: '#1677ff', C: '#faad14', D: '#ff4d4f',
};

interface EfficiencyDimension {
  name: string;
  score: number;
  previous_score: number;
  change: number;
  detail: string;
  icon: string;
}

interface WeeklyEfficiency {
  week_label: string;
  score: number;
}

interface EfficiencyReport {
  highlights: string[];
  improvements_needed: string[];
  suggestions: string[];
  action_items?: string[];
  risk_flags?: string[];
  north_star?: string;
}

interface OpsEfficiencyData {
  overall_score: number;
  overall_previous: number;
  overall_change: number;
  grade: string;
  dimensions: EfficiencyDimension[];
  weekly_trend: WeeklyEfficiency[];
  report: EfficiencyReport;
}

const OpsEfficiency: React.FC = () => {
  const [data, setData] = useState<OpsEfficiencyData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data: res } = await client.get('/dashboard/ops-efficiency', { params: { days: 30 } });
      setData(res as OpsEfficiencyData);
    } catch {
      setError('效能雷达加载失败，可能是后端指标聚合暂时不可用。');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !data) return <Spin style={{ display: 'block', textAlign: 'center', marginTop: 80 }} />;
  if (!data) {
    return (
      <div className="lm-animate-in">
        {error && (
          <Alert
            type="warning"
            showIcon
            message={error}
            action={<Button size="small" onClick={load}>重试</Button>}
            style={{ marginBottom: 16 }}
          />
        )}
        <Empty description="暂无效能数据" style={{ marginTop: 80 }} />
      </div>
    );
  }

  const improving = data.overall_change > 0;

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <RadarChartOutlined style={{ marginRight: 8 }} />运维效能雷达
        </Title>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
      </div>

      {error && (
        <Alert
          type="warning"
          showIcon
          message={error}
          action={<Button size="small" onClick={load}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {data.report?.north_star && (
        <Alert
          type="info"
          showIcon
          icon={<RocketOutlined />}
          message="本期北极星"
          description={data.report.north_star}
          style={{ marginBottom: 16, borderRadius: 12 }}
        />
      )}

      {/* Overall Score */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card style={{
            background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
            borderRadius: 12, textAlign: 'center',
          }}>
            <div style={{ fontSize: 56, fontWeight: 800, color: gradeColors[data.grade] }}>
              {data.overall_score}
            </div>
            <Tag
              color={gradeColors[data.grade]}
              style={{ fontSize: 16, padding: '2px 16px', borderRadius: 6 }}
            >
              {data.grade} 级
            </Tag>
            <div style={{ marginTop: 8 }}>
              {improving ? (
                <Tag color="#52c41a"><RiseOutlined /> +{data.overall_change} vs 上期</Tag>
              ) : (
                <Tag color="#ff4d4f"><FallOutlined /> {data.overall_change} vs 上期</Tag>
              )}
            </div>
            <Progress
              percent={data.overall_score}
              strokeColor={gradeColors[data.grade]}
              trailColor="rgba(255,255,255,0.06)"
              showInfo={false}
              style={{ marginTop: 8 }}
            />
          </Card>
        </Col>

        {/* Weekly Trend */}
        <Col span={16}>
          <Card
            title="周效能趋势"
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, height: '100%' }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', height: 130, padding: '0 16px' }}>
              {data.weekly_trend?.map((w: WeeklyEfficiency) => {
                const maxS = Math.max(...data.weekly_trend.map((t: WeeklyEfficiency) => t.score || 1), 1);
                const barH = Math.max(12, (w.score / maxS) * 120);
                return (
                  <div key={w.week_label} style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--lm-text)', marginBottom: 4 }}>
                      {w.score}
                    </div>
                    <div style={{
                      height: barH,
                      background: w.score >= 70 ? '#52c41a' : w.score >= 50 ? '#faad14' : '#ff4d4f',
                      borderRadius: '4px 4px 0 0', transition: 'height 0.3s',
                    }} />
                    <div style={{ fontSize: 11, color: 'var(--lm-text-secondary)', marginTop: 4 }}>{w.week_label}</div>
                  </div>
                );
              })}
            </div>
          </Card>
        </Col>
      </Row>

      {/* 6 Dimensions */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        {data.dimensions?.map((d: EfficiencyDimension, i: number) => (
          <Col span={8} key={i}>
            <Card size="small" style={{
              background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 10,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <Space size={4}>
                  <span style={{ fontSize: 16 }}>{d.icon}</span>
                  <Text strong style={{ color: 'var(--lm-text)', fontSize: 13 }}>{d.name}</Text>
                </Space>
                <div>
                  <span style={{ fontSize: 22, fontWeight: 800, color: d.score >= 70 ? '#52c41a' : d.score >= 50 ? '#faad14' : '#ff4d4f' }}>
                    {d.score}
                  </span>
                </div>
              </div>
              <Progress
                percent={d.score}
                size="small"
                showInfo={false}
                strokeColor={d.score >= 70 ? '#52c41a' : d.score >= 50 ? '#faad14' : '#ff4d4f'}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                <Text style={{ fontSize: 11, color: 'var(--lm-text-secondary)' }}>{d.detail}</Text>
                {d.change !== 0 && (
                  <Tag
                    color={d.change > 0 ? '#52c41a' : '#ff4d4f'}
                    style={{ fontSize: 10, borderRadius: 3 }}
                  >
                    {d.change > 0 ? '+' : ''}{d.change}
                  </Tag>
                )}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card
            title={<Space><WarningOutlined style={{ color: '#fa8c16' }} /> 风险旗帜</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data.report?.risk_flags?.length ? data.report.risk_flags.map((item: string, i: number) => (
              <div key={i} style={{ padding: '5px 0', fontSize: 12, color: 'var(--lm-text)' }}>{item}</div>
            )) : (
              <Text type="secondary" style={{ fontSize: 12 }}>暂无明显风险，继续保持当前节奏。</Text>
            )}
          </Card>
        </Col>
        <Col span={12}>
          <Card
            title={<Space><RocketOutlined style={{ color: '#1677ff' }} /> 下一步动作</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data.report?.action_items?.map((item: string, i: number) => (
              <div key={i} style={{ padding: '5px 0', fontSize: 12, color: 'var(--lm-text)' }}>{item}</div>
            ))}
          </Card>
        </Col>
      </Row>

      {/* Report */}
      <Row gutter={16}>
        <Col span={8}>
          <Card
            title={<Space><CheckCircleOutlined style={{ color: '#52c41a' }} /> 亮点</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data.report?.highlights?.map((h: string, i: number) => (
              <div key={i} style={{ padding: '4px 0', fontSize: 12, color: 'var(--lm-text)' }}>{h}</div>
            ))}
          </Card>
        </Col>
        <Col span={8}>
          <Card
            title={<Space><WarningOutlined style={{ color: '#faad14' }} /> 需改进</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data.report?.improvements_needed?.length ? data.report.improvements_needed.map((h: string, i: number) => (
              <div key={i} style={{ padding: '4px 0', fontSize: 12, color: 'var(--lm-text)' }}>{h}</div>
            )) : (
              <Text type="secondary" style={{ fontSize: 12 }}>所有维度表现良好 🎉</Text>
            )}
          </Card>
        </Col>
        <Col span={8}>
          <Card
            title={<Space><BulbOutlined style={{ color: '#1677ff' }} /> 建议</Space>}
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
            styles={{ header: { borderBottom: '1px solid var(--lm-border-light)', fontSize: 13 } }}
          >
            {data.report?.suggestions?.map((h: string, i: number) => (
              <div key={i} style={{ padding: '4px 0', fontSize: 12, color: 'var(--lm-text)' }}>{h}</div>
            ))}
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default OpsEfficiency;
