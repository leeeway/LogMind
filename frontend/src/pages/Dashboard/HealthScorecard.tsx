import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Space, Button, Tag, Row, Col, Progress, Empty, Spin, Drawer,
  Descriptions, Badge,
} from 'antd';
import {
  SafetyCertificateOutlined, ReloadOutlined, RiseOutlined, FallOutlined,
  TrophyOutlined,
} from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const gradeColors: Record<string, string> = {
  A: '#52c41a', B: '#1677ff', C: '#faad14', D: '#fa8c16', F: '#ff4d4f',
};

const HealthScorecard: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<any>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data: res } = await client.get('/dashboard/health-scores', { params: { days: 7 } });
      setData(res);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) return <Spin style={{ display: 'block', textAlign: 'center', marginTop: 80 }} />;
  if (!data) return <Empty description="暂无服务数据" style={{ marginTop: 80 }} />;

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <SafetyCertificateOutlined style={{ marginRight: 8 }} />服务健康评分卡
        </Title>
        <Space>
          <Tag color="#52c41a">健康 {data.healthy_count}</Tag>
          <Tag color="#faad14">注意 {data.warning_count}</Tag>
          <Tag color="#ff4d4f">异常 {data.critical_count}</Tag>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </Space>
      </div>

      {/* Average Score */}
      <Card size="small" style={{
        background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12,
        textAlign: 'center', marginBottom: 16, padding: '8px 0',
      }}>
        <Space size={24}>
          <div>
            <div style={{ fontSize: 36, fontWeight: 800, color: data.avg_score >= 75 ? '#52c41a' : data.avg_score >= 50 ? '#faad14' : '#ff4d4f' }}>
              {data.avg_score}
            </div>
            <Text type="secondary">平均健康分</Text>
          </div>
          <div style={{ fontSize: 13, color: 'var(--lm-text-secondary)', textAlign: 'left' }}>
            <div>{data.services.length} 个服务</div>
            <div>健康 {data.healthy_count} / 注意 {data.warning_count} / 异常 {data.critical_count}</div>
          </div>
        </Space>
      </Card>

      {/* Service Cards */}
      <Row gutter={[12, 12]}>
        {data.services.map((svc: any) => (
          <Col span={6} key={svc.service_id}>
            <Card
              size="small"
              hoverable
              onClick={() => setDetail(svc)}
              style={{
                background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
                borderRadius: 12, cursor: 'pointer', transition: 'transform 0.2s',
              }}
              styles={{ body: { padding: '16px' } }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                <Text strong style={{ color: 'var(--lm-text)', fontSize: 13 }}>{svc.service_name}</Text>
                <Tag
                  color={gradeColors[svc.grade]}
                  style={{ borderRadius: 4, fontSize: 14, fontWeight: 700, padding: '0 8px' }}
                >
                  {svc.grade}
                </Tag>
              </div>
              <div style={{ fontSize: 32, fontWeight: 800, color: gradeColors[svc.grade], lineHeight: 1.1 }}>
                {svc.health_score}
              </div>
              <div style={{ display: 'flex', gap: 4, marginTop: 8 }}>
                {svc.weekly_trend?.map((w: any, i: number) => {
                  const h = Math.max(4, w.score / 3);
                  return (
                    <div key={i} style={{
                      flex: 1, height: h, borderRadius: 2,
                      background: w.score >= 75 ? '#52c41a' : w.score >= 50 ? '#faad14' : '#ff4d4f',
                      opacity: 0.6 + i * 0.1,
                    }} />
                  );
                })}
              </div>
              <Text type="secondary" style={{ fontSize: 11, marginTop: 6, display: 'block' }}>
                {svc.top_issue}
              </Text>
            </Card>
          </Col>
        ))}
      </Row>

      {/* Detail Drawer */}
      <Drawer
        title={<Space><TrophyOutlined /> {detail?.service_name} 健康详情</Space>}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={480}
      >
        {detail && (
          <div>
            <div style={{ textAlign: 'center', marginBottom: 20 }}>
              <div style={{ fontSize: 48, fontWeight: 800, color: gradeColors[detail.grade] }}>
                {detail.health_score}
              </div>
              <Tag color={gradeColors[detail.grade]} style={{ fontSize: 16, padding: '2px 16px' }}>
                {detail.grade} 级
              </Tag>
            </div>

            {/* Radar dimensions */}
            <Text strong style={{ display: 'block', marginBottom: 8 }}>五维评分</Text>
            {detail.dimensions?.map((d: any, i: number) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                  <Text style={{ fontSize: 12 }}>{d.name} ({(d.weight * 100).toFixed(0)}%)</Text>
                  <Text strong style={{ fontSize: 12 }}>{d.score}</Text>
                </div>
                <Progress
                  percent={d.score}
                  size="small"
                  showInfo={false}
                  strokeColor={d.score >= 75 ? '#52c41a' : d.score >= 50 ? '#faad14' : '#ff4d4f'}
                />
                <Text type="secondary" style={{ fontSize: 11 }}>{d.detail}</Text>
              </div>
            ))}

            <Text strong style={{ display: 'block', margin: '16px 0 8px' }}>改善建议</Text>
            <Card size="small" style={{ background: 'rgba(22,119,255,0.05)', borderRadius: 8 }}>
              <Text style={{ fontSize: 13 }}>{detail.suggestion}</Text>
            </Card>
          </div>
        )}
      </Drawer>
    </div>
  );
};

export default HealthScorecard;
