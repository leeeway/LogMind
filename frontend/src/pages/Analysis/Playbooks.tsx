import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Typography, Space, Button, Tag, Row, Col, Empty, Spin, Input, Drawer,
  Steps, Descriptions, Badge,
} from 'antd';
import {
  FileProtectOutlined, ReloadOutlined, SearchOutlined, CheckCircleOutlined,
  ClockCircleOutlined, BookOutlined,
} from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const Playbooks: React.FC = () => {
  const [playbooks, setPlaybooks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [suggestion, setSuggestion] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/playbooks', { params: { days: 30 } });
      setPlaybooks(data.playbooks || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSearch = async () => {
    if (search.length < 5) return;
    try {
      const { data } = await client.get('/playbooks/suggest', { params: { alert_message: search } });
      setSuggestion(data);
    } catch { /* ignore */ }
  };

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <FileProtectOutlined style={{ marginRight: 8 }} />智能诊断剧本
        </Title>
        <Space>
          <Tag color="#722ed1">{playbooks.length} 个剧本</Tag>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        </Space>
      </div>

      {/* Search bar */}
      <Card size="small" style={{
        background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
        borderRadius: 12, marginBottom: 16,
      }}>
        <Text strong style={{ display: 'block', marginBottom: 8, color: 'var(--lm-text)' }}>
          🔍 输入告警消息，自动匹配诊断剧本
        </Text>
        <Input.Search
          placeholder="例如: java.lang.NullPointerException at com.xxx.Service.method..."
          enterButton="匹配剧本"
          size="large"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onSearch={handleSearch}
        />
        {suggestion && (
          <div style={{
            marginTop: 12, padding: 12, borderRadius: 8,
            background: suggestion.similarity === 'exact'
              ? 'rgba(82,196,26,0.06)' : suggestion.similarity === 'partial'
              ? 'rgba(250,173,20,0.06)' : 'rgba(255,255,255,0.03)',
            border: `1px solid ${
              suggestion.similarity === 'exact' ? 'rgba(82,196,26,0.2)' :
              suggestion.similarity === 'partial' ? 'rgba(250,173,20,0.2)' : 'var(--lm-border-light)'
            }`,
          }}>
            <Text style={{ fontSize: 13 }}>{suggestion.message}</Text>
            {suggestion.matched_playbook && (
              <Button
                type="link" size="small" style={{ marginLeft: 8 }}
                onClick={() => setDetail(suggestion.matched_playbook)}
              >
                查看剧本 →
              </Button>
            )}
          </div>
        )}
      </Card>

      {/* Playbook cards */}
      <Row gutter={[12, 12]}>
        {playbooks.map((pb: any) => (
          <Col span={8} key={pb.pattern_id}>
            <Card
              size="small"
              hoverable
              onClick={() => setDetail(pb)}
              style={{
                background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)',
                borderRadius: 12, cursor: 'pointer',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <Text strong style={{ color: 'var(--lm-text)', fontSize: 12, fontFamily: 'monospace' }}>
                  {pb.error_class.length > 40 ? pb.error_class.slice(0, 40) + '…' : pb.error_class}
                </Text>
                <Badge count={pb.usage_count} style={{ background: '#1677ff' }} />
              </div>
              <Row gutter={8}>
                <Col span={8}>
                  <div style={{ fontSize: 10, color: 'var(--lm-text-secondary)' }}>步骤</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#722ed1' }}>{pb.step_count}</div>
                </Col>
                <Col span={8}>
                  <div style={{ fontSize: 10, color: 'var(--lm-text-secondary)' }}>成功率</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: pb.success_rate > 60 ? '#52c41a' : '#faad14' }}>
                    {pb.success_rate}%
                  </div>
                </Col>
                <Col span={8}>
                  <div style={{ fontSize: 10, color: 'var(--lm-text-secondary)' }}>平均</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#1677ff' }}>
                    {pb.avg_resolution_min > 60 ? `${(pb.avg_resolution_min / 60).toFixed(1)}h` : `${pb.avg_resolution_min}m`}
                  </div>
                </Col>
              </Row>
            </Card>
          </Col>
        ))}
      </Row>

      {playbooks.length === 0 && !loading && (
        <Empty description="随着更多告警的处理，系统将自动学习并生成诊断剧本" style={{ marginTop: 60 }} />
      )}

      {/* Detail Drawer */}
      <Drawer
        title={<Space><BookOutlined /> 诊断剧本详情</Space>}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={560}
      >
        {detail && (
          <div>
            <Descriptions size="small" column={1} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="错误类型">
                <Text code style={{ fontSize: 12 }}>{detail.error_class}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="历史出现">{detail.usage_count} 次</Descriptions.Item>
              <Descriptions.Item label="成功率">
                <Tag color={detail.success_rate > 60 ? '#52c41a' : '#faad14'}>{detail.success_rate}%</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="平均解决">
                {detail.avg_resolution_min > 60 ? `${(detail.avg_resolution_min / 60).toFixed(1)} 小时` : `${detail.avg_resolution_min} 分钟`}
              </Descriptions.Item>
            </Descriptions>

            <Text strong style={{ display: 'block', marginBottom: 12 }}>诊断步骤</Text>
            <Steps
              direction="vertical"
              size="small"
              current={-1}
              items={detail.steps?.map((s: any) => ({
                title: <Text style={{ fontSize: 13 }}>{s.action}</Text>,
                description: <Tag style={{ fontSize: 10 }}>{s.source}</Tag>,
              }))}
            />

            {detail.recent_cases?.length > 0 && (
              <>
                <Text strong style={{ display: 'block', margin: '20px 0 8px' }}>历史案例</Text>
                {detail.recent_cases.map((c: any, i: number) => (
                  <div key={i} style={{
                    padding: '6px 12px', marginBottom: 6, borderRadius: 6,
                    background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  }}>
                    <Space size={8}>
                      <Tag color={c.severity === 'critical' ? '#ff4d4f' : '#faad14'} style={{ fontSize: 10 }}>{c.severity}</Tag>
                      <Text style={{ fontSize: 11 }}>{c.date?.slice(0, 10)}</Text>
                    </Space>
                    <Space size={8}>
                      {c.resolution_time_min !== null && (
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          <ClockCircleOutlined /> {c.resolution_time_min}m
                        </Text>
                      )}
                      {c.feedback_score !== null && (
                        <Tag color={c.feedback_score > 0 ? '#52c41a' : '#ff4d4f'} style={{ fontSize: 10 }}>
                          {c.feedback_score > 0 ? '👍' : '👎'}
                        </Tag>
                      )}
                    </Space>
                  </div>
                ))}
              </>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
};

export default Playbooks;
