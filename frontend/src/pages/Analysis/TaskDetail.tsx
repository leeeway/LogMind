import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, Tag, Typography, Space, Button, Spin, Descriptions, List, message, Tooltip, Dropdown } from 'antd';
import { ArrowLeftOutlined, LikeOutlined, DislikeOutlined, NodeIndexOutlined, ToolOutlined, CopyOutlined, DownloadOutlined, ReloadOutlined, LoadingOutlined, FileTextOutlined, FieldTimeOutlined, ApartmentOutlined, AimOutlined, LinkOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { analysisApi } from '@/api/analysis';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import IncidentTimeline from '@/components/IncidentTimeline';
import RootCauseGraph from '@/components/RootCauseGraph';
import dayjs from 'dayjs';

const { Title, Text } = Typography;

const severityColors: Record<string, string> = { critical: '#ff4d4f', warning: '#faad14', info: '#1677ff' };
const statusColors: Record<string, string> = { completed: '#52c41a', running: '#1677ff', failed: '#ff4d4f', pending: '#8c8c8c' };
const evidenceKindLabels: Record<string, string> = {
  log_sample: '日志证据',
  change_point: '变点',
  cross_service: '跨服务',
  history_match: '历史命中',
  knowledge_match: '知识库命中',
  regression: '回归',
  ai_finding: 'AI 发现',
};
const pipelineStageLabels: Record<string, string> = {
  knowledge_retrieval: '知识库预检索',
  semantic_dedup: '历史经验匹配',
  prompt_build: '分析上下文组装',
  ai_inference: 'AI 分析推理',
  result_parse: '结论校验清洗',
};

const parseJsonList = (value: any): any[] => {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== 'string') return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const TaskDetail: React.FC = () => {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [task, setTask] = useState<any>(null);
  const [trace, setTrace] = useState<any>(null);
  const [rootCause, setRootCause] = useState<any>(null);
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
      const rootCauseRes = await analysisApi.getRootcauseChain(taskId).catch(() => ({ data: null }));
      setRootCause(rootCauseRes.data);
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
      message.success(
        score > 0
          ? '已验证，并加入自学习知识库 👍'
          : '已标记为不准确，后续分析将排除该经验',
      );
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

  const exportReport = async (format: 'html' | 'markdown') => {
    if (!taskId) return;
    try {
      const { data } = await analysisApi.generateReport(taskId, format);
      const mimeType = format === 'html' ? 'text/html' : 'text/markdown';
      const blob = new Blob([data.content], { type: mimeType });
      const url = URL.createObjectURL(blob);
      if (format === 'html') {
        // Open HTML report in new tab for preview
        window.open(url, '_blank');
        message.success('报告已在新标签页打开');
      } else {
        const a = document.createElement('a');
        a.href = url;
        a.download = data.filename || `report.${format === 'markdown' ? 'md' : 'html'}`;
        a.click();
        URL.revokeObjectURL(url);
        message.success('报告已导出');
      }
    } catch {
      message.error('报告生成失败');
    }
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
          <Dropdown menu={{ items: [
            { key: 'html', label: '📄 导出 HTML 报告', icon: <FileTextOutlined />, onClick: () => exportReport('html') },
            { key: 'md', label: '📝 导出 Markdown', icon: <FileTextOutlined />, onClick: () => exportReport('markdown') },
            { type: 'divider' as const },
            { key: 'txt', label: '导出纯文本', icon: <DownloadOutlined />, onClick: exportResults, disabled: !task.results?.length },
          ] }}>
            <Button icon={<DownloadOutlined />}>导出报告 ▾</Button>
          </Dropdown>
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
                <Tooltip key={i} title={`${pipelineStageLabels[stage.stage] || stage.stage}: ${stage.duration_ms}ms — ${stage.status}`}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                    <span style={{ width: 24 }}>{icon}</span>
                    <span style={{ width: 160, color: 'var(--lm-text-secondary)', fontSize: 12 }}>
                      {pipelineStageLabels[stage.stage] || stage.stage}
                    </span>
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
          renderItem={(result: any, idx: number) => {
            const sourceRefs = parseJsonList(result.source_log_refs);
            const evidence = result.evidence_summary || [];
            const candidates = result.root_cause_candidates || [];
            const verifications = result.next_verifications || [];
            return (
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
                    <Space wrap>
                      <Text style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }}>#{idx + 1}</Text>
                      <Tag color={severityColors[result.severity]} style={{ borderRadius: 4 }}>{result.severity}</Tag>
                      <Tag style={{ borderRadius: 4 }}>{result.result_type}</Tag>
                      <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                        置信度: <span style={{ color: result.confidence_score >= 0.8 ? '#52c41a' : result.confidence_score >= 0.5 ? '#faad14' : '#ff4d4f' }}>
                          {(result.confidence_score * 100).toFixed(0)}%
                        </span>
                      </Text>
                      {sourceRefs.length > 0 && <Tag icon={<LinkOutlined />} color="processing" style={{ borderRadius: 4 }}>日志 {sourceRefs.length}</Tag>}
                    </Space>
                  }
                  description={
                    <>
                      <div className="lm-markdown-content" style={{ margin: '8px 0 0', fontSize: 13, lineHeight: 1.7 }}>
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.content}</ReactMarkdown>
                      </div>

                      {(candidates.length > 0 || evidence.length > 0 || verifications.length > 0) && (
                        <div style={{
                          marginTop: 10, padding: 10, borderRadius: 8,
                          background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
                        }}>
                          {candidates.length > 0 && (
                            <div style={{ marginBottom: 8 }}>
                              <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', fontWeight: 600 }}>
                                <AimOutlined /> 根因候选
                              </Text>
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                                {candidates.slice(0, 3).map((item: any) => (
                                  <Tooltip key={item.id || item.title} title={item.reason}>
                                    <Tag color={severityColors[item.severity] || 'blue'} style={{ borderRadius: 4 }}>
                                      {item.service || item.title} · {(item.score * 100).toFixed(0)}%
                                    </Tag>
                                  </Tooltip>
                                ))}
                              </div>
                            </div>
                          )}
                          {evidence.length > 0 && (
                            <div style={{ marginBottom: 8 }}>
                              <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', fontWeight: 600 }}>
                                <LinkOutlined /> 证据摘要
                              </Text>
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                                {evidence.slice(0, 5).map((item: any) => (
                                  <Tooltip key={item.id || item.title} title={item.detail}>
                                    <Tag style={{ borderRadius: 4 }}>
                                      {evidenceKindLabels[item.kind] || item.kind}
                                      {item.service ? ` · ${item.service}` : ''}
                                    </Tag>
                                  </Tooltip>
                                ))}
                              </div>
                            </div>
                          )}
                          {verifications.length > 0 && (
                            <div>
                              <Text style={{ fontSize: 12, color: 'var(--lm-text-secondary)', fontWeight: 600 }}>
                                <CheckCircleOutlined /> 下一步验证
                              </Text>
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                                {verifications.slice(0, 4).map((item: string) => (
                                  <Tag key={item} color="green" style={{ borderRadius: 4 }}>{item}</Tag>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      )}

                      {sourceRefs.length > 0 && (
                        <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                          {sourceRefs.slice(0, 8).map((ref: string) => (
                            <Tag key={ref} style={{ borderRadius: 4, fontFamily: 'monospace', fontSize: 11 }}>{ref}</Tag>
                          ))}
                        </div>
                      )}

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
            );
          }}
          locale={{ emptyText: '暂无分析结果' }}
        />
      </Card>

      {task.status === 'completed' && rootCause && (rootCause.candidates?.length > 0 || rootCause.evidence?.length > 0) && (
        <Card
          title={<Space><AimOutlined /> 定位证据</Space>}
          size="small"
          style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginTop: 16 }}
          styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.1fr) minmax(0, 0.9fr)', gap: 16 }}>
            <div>
              <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 12, fontWeight: 600 }}>候选根因</Text>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
                {rootCause.candidates?.slice(0, 5).map((candidate: any) => (
                  <div key={candidate.id} style={{
                    padding: '10px 12px', borderRadius: 8,
                    background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
                  }}>
                    <Space wrap style={{ marginBottom: 4 }}>
                      <Tag color={severityColors[candidate.severity] || 'blue'} style={{ borderRadius: 4 }}>{candidate.severity}</Tag>
                      <Text strong style={{ color: 'var(--lm-text)', fontSize: 13 }}>{candidate.title}</Text>
                      <Tag style={{ borderRadius: 4 }}>评分 {(candidate.score * 100).toFixed(0)}%</Tag>
                      <Tag style={{ borderRadius: 4 }}>证据 {candidate.evidence_refs?.length || 0}</Tag>
                    </Space>
                    <div style={{ color: 'var(--lm-text-secondary)', fontSize: 12, lineHeight: 1.6 }}>{candidate.reason}</div>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 12, fontWeight: 600 }}>证据与验证</Text>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                {rootCause.evidence?.slice(0, 10).map((item: any) => (
                  <Tooltip key={item.id} title={item.detail}>
                    <Tag color={item.kind === 'change_point' ? 'orange' : item.kind === 'cross_service' ? 'purple' : undefined} style={{ borderRadius: 4 }}>
                      {evidenceKindLabels[item.kind] || item.kind}{item.service ? ` · ${item.service}` : ''}
                    </Tag>
                  </Tooltip>
                ))}
              </div>
              {rootCause.next_verifications?.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 12 }}>
                  {rootCause.next_verifications.slice(0, 5).map((item: string) => (
                    <div key={item} style={{ color: 'var(--lm-text-secondary)', fontSize: 12, lineHeight: 1.5 }}>
                      <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 6 }} />
                      {item}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* Running State — Agent Reasoning Animation */}
      {task.status === 'running' && (
        <Card
          title={
            <Space>
              <LoadingOutlined spin style={{ color: '#1677ff' }} />
              <span>AI 正在分析中...</span>
              {trace?.tool_calls?.length > 0 && (
                <Tag color="blue" style={{ borderRadius: 4 }}>步骤 {trace.tool_calls.length}</Tag>
              )}
            </Space>
          }
          size="small"
          style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginTop: 16 }}
          styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {trace?.tool_calls?.map((tc: any, i: number) => (
              <div
                key={i}
                className="lm-animate-in"
                style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                  background: 'var(--lm-bg-elevated)', borderRadius: 8,
                  border: '1px solid var(--lm-border-light)',
                  animationDelay: `${i * 0.1}s`,
                }}
              >
                <span style={{ fontWeight: 600, color: 'var(--lm-text-tertiary)', fontSize: 12, width: 24, textAlign: 'center' }}>{i + 1}</span>
                <Tag color={tc.success ? 'processing' : 'error'} style={{ borderRadius: 4 }}>🔧 {tc.tool_name}</Tag>
                <span style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>{tc.duration_ms}ms</span>
                <Tag color={tc.success ? '#52c41a' : '#ff4d4f'} style={{ borderRadius: 4, fontSize: 11 }}>
                  {tc.success ? '✓' : '✗'}
                </Tag>
              </div>
            ))}
            {(!trace?.tool_calls || trace.tool_calls.length === 0) && (
              <div style={{ textAlign: 'center', padding: 20, color: 'var(--lm-text-tertiary)' }}>
                <div className="lm-pulse" style={{ display: 'inline-block' }}>正在初始化分析 Pipeline...</div>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Incident Timeline */}
      {task.status === 'completed' && taskId && (
        <Card
          title={<Space><FieldTimeOutlined /> 事件时间线</Space>}
          size="small"
          style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginTop: 16 }}
          styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
        >
          <IncidentTimeline taskId={taskId} />
        </Card>
      )}

      {/* Root Cause Chain */}
      {task.status === 'completed' && taskId && (
        <Card
          title={<Space><ApartmentOutlined /> 根因链图谱</Space>}
          size="small"
          style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginTop: 16 }}
          styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
        >
          <RootCauseGraph taskId={taskId} />
        </Card>
      )}
    </div>
  );
};

export default TaskDetail;
