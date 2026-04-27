import React, { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { Card, Tag, Typography, Space, Button, Spin, Empty, Divider, Input, message } from 'antd';
import { ArrowLeftOutlined, SwapOutlined, PlusCircleOutlined, CheckCircleOutlined, WarningOutlined, RiseOutlined } from '@ant-design/icons';
import { analysisApi } from '@/api/analysis';

const { Title, Text, Paragraph } = Typography;

const severityColors: Record<string, string> = { critical: '#ff4d4f', warning: '#faad14', info: '#1677ff', error: '#ff4d4f' };

const TaskCompare: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [taskA, setTaskA] = useState(searchParams.get('a') || '');
  const [taskB, setTaskB] = useState(searchParams.get('b') || '');

  const doCompare = async () => {
    if (!taskA || !taskB) { message.warning('请输入两个任务 ID'); return; }
    setLoading(true);
    try {
      const { data } = await analysisApi.compare(taskA, taskB);
      setResult(data);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '对比失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (taskA && taskB) doCompare();
  }, []);

  const renderErrorList = (errors: any[], color: string, icon: React.ReactNode) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {errors.map((e: any, i: number) => (
        <div key={i} style={{
          padding: '10px 14px',
          background: 'var(--lm-bg-elevated)',
          borderRadius: 8,
          borderLeft: `3px solid ${color}`,
        }}>
          <Space style={{ marginBottom: 4 }}>
            {icon}
            <Tag color={severityColors[e.severity]} style={{ borderRadius: 4 }}>{e.severity}</Tag>
            <Tag style={{ borderRadius: 4 }}>{e.result_type}</Tag>
            {e.previous_severity && <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>← {e.previous_severity}</Text>}
            {e.change && <Tag color="orange" style={{ borderRadius: 4, fontSize: 11 }}>{e.change}</Tag>}
          </Space>
          <Paragraph style={{ color: 'var(--lm-text-secondary)', margin: '4px 0 0', fontSize: 13 }} ellipsis={{ rows: 3, expandable: true }}>
            {e.content}
          </Paragraph>
        </div>
      ))}
    </div>
  );

  return (
    <div className="lm-animate-in">
      <Space style={{ marginBottom: 20 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/analysis')}>返回</Button>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>分析对比</Title>
      </Space>

      {/* Input Bar */}
      <Card
        size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
      >
        <Space>
          <Input placeholder="Task A (基线)" value={taskA} onChange={e => setTaskA(e.target.value)} style={{ width: 300 }} />
          <SwapOutlined style={{ color: 'var(--lm-text-tertiary)', fontSize: 18 }} />
          <Input placeholder="Task B (当前)" value={taskB} onChange={e => setTaskB(e.target.value)} style={{ width: 300 }} />
          <Button type="primary" onClick={doCompare} loading={loading} icon={<SwapOutlined />}>对比</Button>
        </Space>
      </Card>

      {loading && <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>}

      {result && !loading && (
        <>
          {/* Summary */}
          <Card
            size="small"
            style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
          >
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Text style={{ fontSize: 15, color: 'var(--lm-text)' }}>{result.summary}</Text>
              <div style={{ marginTop: 8, display: 'flex', justifyContent: 'center', gap: 24 }}>
                <Text style={{ color: '#ff4d4f' }}>🔴 新增 {result.new_errors?.length || 0}</Text>
                <Text style={{ color: '#52c41a' }}>🟢 修复 {result.resolved_errors?.length || 0}</Text>
                <Text style={{ color: '#faad14' }}>🟡 恶化 {result.worsened?.length || 0}</Text>
                <Text style={{ color: '#1677ff' }}>🔵 改善 {result.improved?.length || 0}</Text>
                <Text style={{ color: 'var(--lm-text-tertiary)' }}>⚪ 未变化 {result.unchanged || 0}</Text>
              </div>
            </div>
          </Card>

          {/* Detail Sections */}
          {result.new_errors?.length > 0 && (
            <Card
              title={<Space><PlusCircleOutlined style={{ color: '#ff4d4f' }} /> 新增错误 ({result.new_errors.length})</Space>}
              size="small"
              style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
            >
              {renderErrorList(result.new_errors, '#ff4d4f', <PlusCircleOutlined style={{ color: '#ff4d4f' }} />)}
            </Card>
          )}

          {result.resolved_errors?.length > 0 && (
            <Card
              title={<Space><CheckCircleOutlined style={{ color: '#52c41a' }} /> 已修复 ({result.resolved_errors.length})</Space>}
              size="small"
              style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
            >
              {renderErrorList(result.resolved_errors, '#52c41a', <CheckCircleOutlined style={{ color: '#52c41a' }} />)}
            </Card>
          )}

          {result.worsened?.length > 0 && (
            <Card
              title={<Space><RiseOutlined style={{ color: '#faad14' }} /> 恶化 ({result.worsened.length})</Space>}
              size="small"
              style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
            >
              {renderErrorList(result.worsened, '#faad14', <WarningOutlined style={{ color: '#faad14' }} />)}
            </Card>
          )}

          {result.improved?.length > 0 && (
            <Card
              title={<Space><CheckCircleOutlined style={{ color: '#1677ff' }} /> 改善 ({result.improved.length})</Space>}
              size="small"
              style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
              styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
            >
              {renderErrorList(result.improved, '#1677ff', <CheckCircleOutlined style={{ color: '#1677ff' }} />)}
            </Card>
          )}
        </>
      )}

      {!result && !loading && <Empty description="输入两个任务 ID 开始对比" />}
    </div>
  );
};

export default TaskCompare;
