import React, { useState, useEffect, useCallback } from 'react';
import { Card, Typography, Select, DatePicker, Button, Input, Table, Tag, Space, Statistic, Row, Col, message, Tooltip, Segmented } from 'antd';
import { SearchOutlined, BarChartOutlined, CopyOutlined, DownloadOutlined, ClockCircleOutlined, FileSearchOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { logsApi, businessLineApi } from '@/api/services';
import { useQuickDiagnose } from '@/components/QuickDiagnose';
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const severityColors: Record<string, string> = {
  CRITICAL: '#ff4d4f', ERROR: '#ff7875', WARNING: '#faad14', INFO: '#1677ff', DEBUG: '#8c8c8c',
  critical: '#ff4d4f', error: '#ff7875', warning: '#faad14', info: '#1677ff', debug: '#8c8c8c',
};

// Quick time range presets
const timePresets = [
  { label: '15分钟', value: 15, unit: 'minute' as const },
  { label: '1小时', value: 1, unit: 'hour' as const },
  { label: '6小时', value: 6, unit: 'hour' as const },
  { label: '24小时', value: 24, unit: 'hour' as const },
  { label: '3天', value: 3, unit: 'day' as const },
  { label: '7天', value: 7, unit: 'day' as const },
];

const LogSearch: React.FC = () => {
  const quickDiagnose = useQuickDiagnose();
  const [bizLines, setBizLines] = useState<any[]>([]);
  const [bizId, setBizId] = useState<string>('');
  const [query, setQuery] = useState('');
  const [severity, setSeverity] = useState('error');
  const [timeRange, setTimeRange] = useState<[dayjs.Dayjs, dayjs.Dayjs]>([dayjs().subtract(1, 'hour'), dayjs()]);
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<any>(null);
  const [searchCount, setSearchCount] = useState(0);
  const [searchDuration, setSearchDuration] = useState(0);
  // AI NL Query state
  const [searchMode, setSearchMode] = useState<'standard' | 'ai'>('standard');
  const [nlQuestion, setNlQuestion] = useState('');
  const [nlParsing, setNlParsing] = useState(false);
  const [nlParsed, setNlParsed] = useState<any>(null);

  useEffect(() => {
    businessLineApi.listAll().then(res => {
      const items = res.data?.items || [];
      setBizLines(items);
      if (items.length > 0) setBizId(items[0].id);
    });
  }, []);

  const doSearch = useCallback(async () => {
    if (!bizId) return;
    setLoading(true);
    const start = Date.now();
    try {
      const { data } = await logsApi.search({
        business_line_id: bizId,
        time_from: timeRange[0].toISOString(),
        time_to: timeRange[1].toISOString(),
        query,
        severity,
        size: 200,
      });
      setLogs(data.hits || data.logs || []);
      setSearchDuration(Date.now() - start);
      setSearchCount(prev => prev + 1);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      if (detail) message.error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      else console.error(err);
    } finally {
      setLoading(false);
    }
  }, [bizId, timeRange, query, severity]);

  const loadStats = async () => {
    if (!bizId) return;
    try {
      const { data } = await logsApi.getStats(bizId);
      setStats(data);
    } catch { /* ignore */ }
  };

  useEffect(() => { if (bizId) loadStats(); }, [bizId]);

  // AI Natural Language Query
  const doNLQuery = async () => {
    if (!nlQuestion.trim()) return;
    setNlParsing(true);
    setNlParsed(null);
    try {
      const { data } = await logsApi.naturalQuery(nlQuestion);
      setNlParsed(data);
      // Auto-fill parsed params into standard search
      if (data.query) setQuery(data.query);
      if (data.severity) setSeverity(data.severity);
      if (data.time_from && data.time_to) {
        setTimeRange([dayjs(data.time_from), dayjs(data.time_to)]);
      }
      message.success(`AI 解析完成: ${data.explanation || '已填充搜索参数'}`);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : 'AI 解析失败，请尝试更明确的描述');
    }
    setNlParsing(false);
  };

  // Keyboard shortcut: Enter to search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        doSearch();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [doSearch]);

  const setQuickTime = (value: number, unit: 'minute' | 'hour' | 'day') => {
    setTimeRange([dayjs().subtract(value, unit), dayjs()]);
  };

  const copyLogContent = (record: any) => {
    const text = record.message || JSON.stringify(record, null, 2);
    navigator.clipboard.writeText(text).then(() => message.success('已复制'));
  };

  const exportLogs = () => {
    if (!logs.length) return;
    const text = logs.map((l, i) => {
      const ts = l['@timestamp'] || l.timestamp || '';
      return `[${ts}] [${l.level || '-'}] ${l.message || JSON.stringify(l)}`;
    }).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `logmind-logs-${dayjs().format('YYYYMMDD-HHmmss')}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    message.success('已导出');
  };

  // Highlight query keywords in message
  const highlightMessage = (text: string) => {
    if (!query || !text) return text;
    try {
      const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
      const parts = text.split(regex);
      return parts.map((part, i) =>
        regex.test(part) ? <mark key={i} style={{ background: 'rgba(22,119,255,0.3)', color: '#fff', padding: '0 2px', borderRadius: 2 }}>{part}</mark> : part
      );
    } catch {
      return text;
    }
  };

  const logColumns = [
    {
      title: '时间', dataIndex: '@timestamp', width: 170,
      render: (v: string) => v ? (
        <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
          {dayjs(v).format('MM-DD HH:mm:ss.SSS')}
        </span>
      ) : '-',
    },
    {
      title: '级别', dataIndex: 'level', width: 80,
      render: (v: string) => <Tag color={severityColors[v] || '#8c8c8c'} style={{ borderRadius: 4 }}>{v}</Tag>,
    },
    {
      title: '消息', dataIndex: 'message', ellipsis: true,
      render: (v: string) => <span style={{ fontSize: 13 }}>{highlightMessage(v)}</span>,
    },
    { title: '来源', dataIndex: 'source', width: 160, ellipsis: true },
    {
      title: '', width: 80,
      render: (_: any, record: any) => (
        <Space size={4}>
          <Tooltip title="复制"><CopyOutlined style={{ color: 'var(--lm-text-tertiary)', cursor: 'pointer' }} onClick={() => copyLogContent(record)} /></Tooltip>
          <Tooltip title="AI 排查">
            <ThunderboltOutlined
              style={{ color: '#722ed1', cursor: 'pointer', fontSize: 13 }}
              onClick={() => quickDiagnose.open({
                context: `帮我分析这条日志:\n级别: ${record.level}\n时间: ${record['@timestamp']}\n消息: ${(record.message || '').slice(0, 200)}\n来源: ${record.source || ''}`,
                source: '日志搜索',
              })}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <FileSearchOutlined style={{ marginRight: 8 }} />日志搜索
        </Title>
        <Space>
          <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
            ⌘+Enter 快速搜索
          </Text>
          <Segmented
            value={searchMode}
            onChange={(v) => setSearchMode(v as any)}
            options={[
              { value: 'standard', label: '标准搜索' },
              { value: 'ai', label: <><RobotOutlined /> AI 智能搜索</> },
            ]}
            size="small"
          />
          <Button icon={<DownloadOutlined />} onClick={exportLogs} disabled={!logs.length}>导出</Button>
        </Space>
      </div>

      {/* AI Search Mode */}
      {searchMode === 'ai' && (
        <Card
          size="small"
          style={{ background: 'linear-gradient(135deg, rgba(22,119,255,0.06), rgba(114,46,209,0.06))', border: '1px solid rgba(22,119,255,0.15)', borderRadius: 12, marginBottom: 16 }}
        >
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            <div style={{ flex: 1 }}>
              <Input.TextArea
                value={nlQuestion}
                onChange={e => setNlQuestion(e.target.value)}
                placeholder="用自然语言描述你想搜索的日志...例如: 最近1小时xxx服务的超时错误"
                autoSize={{ minRows: 2, maxRows: 4 }}
                onPressEnter={(e) => { if (e.ctrlKey || e.metaKey) doNLQuery(); }}
                style={{ fontSize: 14 }}
              />
              <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)', marginTop: 4, display: 'block' }}>
                AI 将解析你的意图并自动填充搜索参数 · ⌘+Enter 发送
              </Text>
            </div>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              onClick={doNLQuery}
              loading={nlParsing}
              style={{ height: 54 }}
            >
              AI 解析
            </Button>
          </div>
          {nlParsed && (
            <div style={{ marginTop: 12, padding: 12, background: 'var(--lm-bg-card)', borderRadius: 8, border: '1px solid var(--lm-border-light)' }}>
              <Space size={8} wrap>
                <Tag color="blue">关键词: {nlParsed.query || '无'}</Tag>
                <Tag color="orange">级别: {nlParsed.severity}</Tag>
                {nlParsed.domain && <Tag color="cyan">域名: {nlParsed.domain}</Tag>}
                <Tag>返回: {nlParsed.size} 条</Tag>
              </Space>
              {nlParsed.explanation && (
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--lm-text-secondary)' }}>
                  💡 {nlParsed.explanation}
                </div>
              )}
              <div style={{ marginTop: 8, textAlign: 'right' }}>
                <Button size="small" type="primary" icon={<SearchOutlined />} onClick={doSearch}>使用这些参数搜索</Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Search Bar */}
      <Card
        size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
      >
        <Space wrap size={12} style={{ marginBottom: 10 }}>
          <Select
            value={bizId}
            onChange={setBizId}
            options={bizLines.map(b => ({ value: b.id, label: b.name }))}
            placeholder="业务线"
            showSearch
            filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
            style={{ width: 180 }}
          />
          <Select value={severity} onChange={setSeverity} style={{ width: 120 }} options={[
            { value: 'critical', label: 'Critical' }, { value: 'error', label: 'Error' },
            { value: 'warning', label: 'Warning' }, { value: 'info', label: 'Info' },
          ]} />
          <RangePicker showTime value={timeRange} onChange={(v: any) => v && setTimeRange(v)} />
          <Input
            placeholder="关键词搜索..."
            value={query}
            onChange={e => setQuery(e.target.value)}
            style={{ width: 220 }}
            onPressEnter={doSearch}
            allowClear
            prefix={<SearchOutlined style={{ color: 'var(--lm-text-tertiary)' }} />}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={doSearch} loading={loading}>搜索</Button>
        </Space>

        {/* Quick time presets */}
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <ClockCircleOutlined style={{ color: 'var(--lm-text-tertiary)', fontSize: 12 }} />
          {timePresets.map(p => (
            <Tag
              key={p.label}
              style={{ cursor: 'pointer', borderRadius: 4, fontSize: 11 }}
              onClick={() => setQuickTime(p.value, p.unit)}
            >
              {p.label}
            </Tag>
          ))}
          {searchCount > 0 && (
            <Text style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
              查询耗时 {searchDuration}ms · {logs.length} 条结果
            </Text>
          )}
        </div>
      </Card>

      {/* Stats */}
      {stats && (
        <Card
          title={<Space><BarChartOutlined /> 统计概览</Space>}
          size="small"
          style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, marginBottom: 16 }}
          styles={{ header: { borderBottom: '1px solid var(--lm-border-light)' } }}
        >
          <Row gutter={16}>
            <Col span={6}><Statistic title="总日志数" value={stats.total_logs || 0} /></Col>
            <Col span={6}><Statistic title="错误日志" value={stats.error_count || 0} valueStyle={{ color: '#ff4d4f' }} /></Col>
            <Col span={6}><Statistic title="警告日志" value={stats.warning_count || 0} valueStyle={{ color: '#faad14' }} /></Col>
            <Col span={6}><Statistic title="索引大小" value={stats.index_size || '-'} /></Col>
          </Row>
        </Card>
      )}

      {/* Results */}
      <Card
        title={
          <Space>
            搜索结果
            {logs.length > 0 && <Tag color="blue">{logs.length}</Tag>}
          </Space>
        }
        size="small"
        style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }}
        styles={{ body: { padding: 0 }, header: { borderBottom: '1px solid var(--lm-border-light)' } }}
      >
        <Table
          dataSource={logs}
          columns={logColumns}
          rowKey={(r: any, i) => r._id || `${i}`}
          size="small"
          loading={loading}
          pagination={{ pageSize: 50, showTotal: (t) => `共 ${t} 条`, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
          expandable={{
            expandedRowRender: (record: any) => (
              <pre style={{
                fontSize: 12, color: 'var(--lm-text-secondary)', whiteSpace: 'pre-wrap',
                maxHeight: 300, overflow: 'auto', padding: 12, borderRadius: 8,
                background: 'var(--lm-bg-layout)', fontFamily: 'Menlo, Consolas, monospace',
              }}>
                {JSON.stringify(record, null, 2)}
              </pre>
            ),
          }}
          locale={{ emptyText: searchCount === 0 ? '请选择条件后点击搜索' : '未找到匹配的日志' }}
        />
      </Card>
    </div>
  );
};

export default LogSearch;
