import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, Tag, Typography, Space, Button, Spin, Descriptions, List, message, Tooltip, Tabs } from 'antd';
import { ArrowLeftOutlined, LikeOutlined, DislikeOutlined, NodeIndexOutlined, ToolOutlined, CopyOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
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
  const [activeResultTab, setActiveResultTab] = useState('all');

  const load = useCallback(async () => {
    if (!taskId) return;
    setLoading(true);
    try {
      const [taskRes, traceRes] = await Promise.all([
        analysisApi.getTask(taskId),
        analysisApi.getTrace(taskId).catch(() => ({ data: null })),
      ]);
      setTask(taskRes.data);
      setTrace(traceRes.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    load();
  }, [load]);

  // Auto poll when running
  useEffect(() => {
    if (task?.status !== 'running') return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [task?.status, load]);

  const handleFeedback = async (resultId: string, score: number) => {
    try {
      await analysisApi.submitFeedback(resultId, score);
      message.success(score > 0 ? '感谢反馈 👍' : '感谢反馈，将改进分析质量');
    } catch { message.error('反馈失败'); }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).then(() => message.success('已复制'));
  };

  const exportResults = () => {
    if (!task?.results?.length) return;
    const text = task.results.map((r: any, i: number) =>
      `[${i + 1}] [${r.severity}] (置信度: ${(r.confidence_score * 100).toFixed(0)}%)\n${r.content}\n`
    ).join('\n---\n\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `logmind-analysis-${taskId?.slice(0, 8)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    message.success('已导出');
  };

  if (loading && !task) return <div style={{ textAlign: 'center', paddingTop: 120 }}><Spin size="large" /></div>;
  if (!task) return <div style={{ textAlign: 'center', paddingTop: 120 }}><Text>任务不存在</Text></div>;

  const duration = task.completed_at && task.created_at
    ? dayjs(task.completed_at).diff(dayjs(task.created_at), 'second')
    : null;

  const filteredResults = activeResultTab === 'all'
    ? (task.results || [])
    : (task.results || []).filter((r: any) => r.severity === activeResultTab);

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/analysis')}>返回</Button>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>分析详情</Title>
          <Tag color={statusColors[task.status]}>
            {task.status === 'running' && <span className="lm-running-dot" />}
            {task.status}
          </Tag>
          {duration != null && <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }}>耗时 {duration}s</Text>}
        </Space>
        <Space>
          {task.status === 'running' && <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>}
          <Button icon={<DownloadOutlined />} onClick={exportResults} disabled={!task.results?.length}>导出</Button>
          <Button icon={<CopyOutlined />} onClick={() => copyToClipboard(taskId || '')}>复制 ID</Button>
        </Space>
      </div>

      {/* Task Info */}
      <Card size="small" style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}>
        <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }}>
          <Descriptions.Item label="任务类型">{task.task_type}</Descriptions.Item>
          <Descriptions.Item label="日志数"><span style={{ fontWeight: 600 }}>{task.log_count?.toLocaleString()}</span></Descriptions.Item>
          <Descriptions.Item label="Token">{task.token_usage?.toLocaleString()}</Descriptions.Item>
          <Descriptions.Item label="成本"><span style={{ color: '#52c41a' }}>${task.cost_usd?.toFixed(4)}</span></Descriptions.Item>
          <Descriptions.Item label="创建时间">{dayjs(task.created_at).format('YYYY-MM-DD HH:mm:ss')}</Descriptions.Item>
          <Descriptions.Item label="完成时间">{task.completed_at ? dayjs(task.completed_at).format('YYYY-MM-DD HH:mm:ss') : <span className="lm-running-dot" />}</Descriptions.Item>
          <Descriptions.Item label="错误信息" span={2}>
            {task.error_message ? <Text type="danger">{task.error_message}</Text> : <Text type="secondary">无</Text>}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Pipeline Trace */}
      {trace && (
        <Card
          title={<Space><NodeIndexOutlined /> Pipeline 执行追踪 <Tag color="blue">{trace.total_duration_ms}ms</Tag></Space>}
          size="small"
          style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
          styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {trace.stages?.map((stage: any, i: number) => {
              const pct = trace.total_duration_ms > 0 ? Math.max((stage.duration_ms / trace.total_duration_ms) * 100, 2) : 0;
              const icon = stage.status === 'ok' ? '✅' : stage.status === 'skipped' ? '⏭️' : '❌';
              return (
                <Tooltip key={i} title={`${stage.stage}: ${stage.duration_ms}ms — ${stage.status}`}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                    <span style={{ width: 24 }}>{icon}</span>
                    <span style={{ width: 160, color: 'var(--lm-text-secondary)', fontSize: 12 }}>{stage.stage}</span>
                    <div style={{ flex: 1, background: 'rgba(255,255,255,0.04)', borderRadius: 4, height: 18, overflow: 'hidden' }}>
                      <div style={{
                        width: `${pct}%`,
                        height: '100%',
                        background: stage.status === 'error' ? '#ff4d4f' : 'linear-gradient(90deg, #1677ff, #4096ff)',
                        borderRadius: 4,
                        transition: 'width 0.8s ease',
                        display: 'flex',
                        alignItems: 'center',
                        paddingLeft: 6,
                      }}>
                        {pct > 15 && <span style={{ fontSize: 10, color: '#fff', whiteSpace: 'nowrap' }}>{stage.duration_ms}ms</span>}
                      </div>
                    </div>
                    <span style={{ width: 60, textAlign: 'right', color: 'var(--lm-text-tertiary)', fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
                      {stage.duration_ms}ms
                    </span>
                  </div>
                </Tooltip>
              );
            })}
          </div>

          {/* Tool calls */}
          {trace.tool_calls?.length > 0 && (
            <div style={{ marginTop: 16, padding: '12px 0', borderTop: '1px solid var(--lm-border-light)' }}>
              <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 12 }}>
                <ToolOutlined /> Agent 工具调用链 ({trace.tool_calls.length} 次)
              </Text>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                {trace.tool_calls.map((tc: any, i: number) => (
                  <Tooltip key={i} title={
                    <div style={{ fontSize: 12 }}>
                      <div>{tc.tool_name}</div>
                      <div>耗时: {tc.duration_ms}ms</div>
                      <div>状态: {tc.success ? '✅ 成功' : '❌ 失败'}</div>
                      {tc.result_preview && <div style={{ marginTop: 4, maxWidth: 300, wordBreak: 'break-all' }}>{tc.result_preview.slice(0, 200)}</div>}
                    </div>
                  }>
                    <Tag color={tc.success ? 'processing' : 'error'} style={{ borderRadius: 4, cursor: 'help' }}>
                      🔧 {tc.tool_name} <span style={{ opacity: 0.7 }}>({tc.duration_ms}ms)</span>
                    </Tag>
                  </Tooltip>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Analysis Results with severity filter tabs */}
      <Card
        title={
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>分析结果 ({task.results?.length || 0})</span>
            <Space size={4}>
              {['all', 'critical', 'warning', 'info'].map(sev => {
                const count = sev === 'all' ? (task.results?.length || 0) : (task.results || []).filter((r: any) => r.severity === sev).length;
                if (count === 0 && sev !== 'all') return null;
                return (
                  <Tag
                    key={sev}
                    color={activeResultTab === sev ? (severityColors[sev] || '#1677ff') : undefined}
                    onClick={() => setActiveResultTab(sev)}
                    style={{ cursor: 'pointer', borderRadius: 4 }}
                  >
                    {sev === 'all' ? '全部' : sev} ({count})
                  </Tag>
                );
              })}
            </Space>
          </div>
        }
        size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
      >
        <List
          dataSource={filteredResults}
          renderItem={(result: any, idx: number) => (
            <List.Item
              style={{ padding: '16px 0', borderBottom: '1px solid var(--lm-border-light)' }}
              actions={[
                <Tooltip title="复制内容">
                  <Button size="small" icon={<CopyOutlined />} onClick={() => copyToClipboard(result.content)} />
                </Tooltip>,
                <Tooltip title="分析准确">
                  <Button size="small" icon={<LikeOutlined />} onClick={() => handleFeedback(result.id, 1)} />
                </Tooltip>,
                <Tooltip title="分析不准">
                  <Button size="small" icon={<DislikeOutlined />} onClick={() => handleFeedback(result.id, -1)} />
                </Tooltip>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }}>#{idx + 1}</Text>
                    <Tag color={severityColors[result.severity]} style={{ borderRadius: 4 }}>{result.severity}</Tag>
                    <Tag style={{ borderRadius: 4 }}>{result.result_type}</Tag>
                    <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                      置信度: <span style={{ color: result.confidence_score >= 0.8 ? '#52c41a' : result.confidence_score >= 0.5 ? '#faad14' : '#ff4d4f' }}>
                        {(result.confidence_score * 100).toFixed(0)}%
                      </span>
                    </Text>
                  </Space>
                }
                description={
                  <>
                    <Paragraph style={{ color: 'var(--lm-text-secondary)', margin: '8px 0 0', whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7 }}>
                      {result.content}
                    </Paragraph>
                    {result.structured_data && result.structured_data !== '{}' && (
                      <details style={{ marginTop: 8 }}>
                        <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--lm-text-tertiary)' }}>结构化数据</summary>
                        <pre style={{
                          marginTop: 6, padding: 10, fontSize: 11,
                          background: 'var(--lm-bg-layout)', borderRadius: 6,
                          color: 'var(--lm-text-secondary)', overflow: 'auto', maxHeight: 200,
                        }}>
                          {typeof result.structured_data === 'string'
                            ? JSON.stringify(JSON.parse(result.structured_data), null, 2)
                            : JSON.stringify(result.structured_data, null, 2)}
                        </pre>
                      </details>
                    )}
                  </>
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
