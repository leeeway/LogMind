import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, Tag, Typography, Space, Button, Spin, Descriptions, List, Rate, message, Tooltip, Steps, Timeline } from 'antd';
import { ArrowLeftOutlined, LikeOutlined, DislikeOutlined, NodeIndexOutlined, ToolOutlined } from '@ant-design/icons';
import { analysisApi } from '@/api/analysis';
import dayjs from 'dayjs';

const { Title, Text, Paragraph } = Typography;

const severityColors: Record<string, string> = { critical: '#ff4d4f', warning: '#faad14', info: '#1677ff' };
const statusColors: Record<string, string> = { completed: '#52c41a', running: '#1677ff', failed: '#ff4d4f', pending: '#8c8c8c' };

const TaskDetail: React.FC = () => {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [task, setTask] = useState<any>(null);
  const [trace, setTrace] = useState<any>(null);

  useEffect(() => {
    if (!taskId) return;
    const load = async () => {
      setLoading(true);
      try {
        const [taskRes, traceRes] = await Promise.all([
          analysisApi.getTask(taskId),
          analysisApi.getTrace(taskId),
        ]);
        setTask(taskRes.data);
        setTrace(traceRes.data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    load();
    // Poll if running
    const timer = setInterval(() => {
      if (task?.status === 'running') load();
    }, 5000);
    return () => clearInterval(timer);
  }, [taskId]);

  const handleFeedback = async (resultId: string, score: number) => {
    try {
      await analysisApi.submitFeedback(resultId, score);
      message.success(score > 0 ? '感谢反馈 👍' : '感谢反馈，将改进分析质量');
    } catch { message.error('反馈失败'); }
  };

  if (loading) return <div style={{ textAlign: 'center', paddingTop: 120 }}><Spin size="large" /></div>;
  if (!task) return <div style={{ textAlign: 'center', paddingTop: 120 }}><Text>任务不存在</Text></div>;

  return (
    <div className="lm-animate-in">
      <Space style={{ marginBottom: 20 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/analysis')}>返回</Button>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>分析详情</Title>
        <Tag color={statusColors[task.status]}>{task.status}</Tag>
      </Space>

      {/* Task Info */}
      <Card size="small" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}>
        <Descriptions size="small" column={4}>
          <Descriptions.Item label="任务类型">{task.task_type}</Descriptions.Item>
          <Descriptions.Item label="日志数">{task.log_count?.toLocaleString()}</Descriptions.Item>
          <Descriptions.Item label="Token">{task.token_usage?.toLocaleString()}</Descriptions.Item>
          <Descriptions.Item label="成本">${task.cost_usd?.toFixed(4)}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{dayjs(task.created_at).format('YYYY-MM-DD HH:mm:ss')}</Descriptions.Item>
          <Descriptions.Item label="完成时间">{task.completed_at ? dayjs(task.completed_at).format('YYYY-MM-DD HH:mm:ss') : '-'}</Descriptions.Item>
          <Descriptions.Item label="错误信息" span={2}>
            {task.error_message ? <Text type="danger">{task.error_message}</Text> : <Text type="secondary">无</Text>}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Pipeline Trace */}
      {trace && (
        <Card
          title={<Space><NodeIndexOutlined /> Pipeline 执行追踪 <Tag>{trace.total_duration_ms}ms</Tag></Space>}
          size="small"
          style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
          styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {trace.stages?.map((stage: any, i: number) => {
              const pct = trace.total_duration_ms > 0 ? Math.max((stage.duration_ms / trace.total_duration_ms) * 100, 2) : 0;
              const icon = stage.status === 'ok' ? '✅' : stage.status === 'skipped' ? '⏭️' : '❌';
              return (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  <span style={{ width: 24 }}>{icon}</span>
                  <span style={{ width: 160, color: 'var(--lm-text-secondary)' }}>{stage.stage}</span>
                  <div style={{ flex: 1, background: 'rgba(255,255,255,0.04)', borderRadius: 4, height: 16, overflow: 'hidden' }}>
                    <div style={{
                      width: `${pct}%`,
                      height: '100%',
                      background: stage.status === 'error' ? '#ff4d4f' : 'linear-gradient(90deg, #1677ff, #4096ff)',
                      borderRadius: 4,
                      transition: 'width 0.5s ease',
                    }} />
                  </div>
                  <span style={{ width: 70, textAlign: 'right', color: 'var(--lm-text-tertiary)', fontSize: 12 }}>
                    {stage.duration_ms}ms
                  </span>
                </div>
              );
            })}
          </div>

          {/* Tool calls */}
          {trace.tool_calls?.length > 0 && (
            <div style={{ marginTop: 16, padding: '12px 0', borderTop: '1px solid var(--lm-border-light)' }}>
              <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 12 }}>
                <ToolOutlined /> Agent 工具调用链
              </Text>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
                {trace.tool_calls.map((tc: any, i: number) => (
                  <Tooltip key={i} title={`${tc.tool_name} (${tc.duration_ms}ms) - ${tc.success ? '成功' : '失败'}`}>
                    <Tag color={tc.success ? 'processing' : 'error'} style={{ borderRadius: 4 }}>
                      🔧 {tc.tool_name} ({tc.duration_ms}ms)
                    </Tag>
                  </Tooltip>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Analysis Results */}
      <Card
        title="分析结果"
        size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
      >
        <List
          dataSource={task.results || []}
          renderItem={(result: any) => (
            <List.Item
              style={{ padding: '16px 0', borderBottom: '1px solid var(--lm-border-light)' }}
              actions={[
                <Tooltip title="分析准确"><Button size="small" icon={<LikeOutlined />} onClick={() => handleFeedback(result.id, 1)} /></Tooltip>,
                <Tooltip title="分析不准"><Button size="small" icon={<DislikeOutlined />} onClick={() => handleFeedback(result.id, -1)} /></Tooltip>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <Tag color={severityColors[result.severity]} style={{ borderRadius: 4 }}>{result.severity}</Tag>
                    <Tag style={{ borderRadius: 4 }}>{result.result_type}</Tag>
                    <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                      置信度: {(result.confidence_score * 100).toFixed(0)}%
                    </Text>
                  </Space>
                }
                description={
                  <Paragraph style={{ color: 'var(--lm-text-secondary)', margin: '8px 0 0', whiteSpace: 'pre-wrap' }}>
                    {result.content}
                  </Paragraph>
                }
              />
            </List.Item>
          )}
          locale={{ emptyText: '暂无分析结果' }}
        />
      </Card>
    </div>
  );
};

export default TaskDetail;
