import { useEffect, useState } from 'react';
import {
  Alert, Button, Card, Form, Input, message, Modal, Select, Space, Statistic,
  Switch, Table, Tabs, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { httpAccessApi, type GitRepository, type HttpAccessSite } from '@/api/httpAccess';
import { businessLineApi } from '@/api/services';

const roleLabels: Record<string, string> = { app: 'APP', account: '账号', payment: '支付', front: '前台', general: '通用', cdn_download: 'CDN/下载' };
const modeLabels: Record<string, string> = { observe: '仅观测', enabled: '已启用', disabled: '已禁用' };
type Option = { value: string; label: string };

export default function HttpAccessSites() {
  const [items, setItems] = useState<HttpAccessSite[]>([]);
  const [repos, setRepos] = useState<GitRepository[]>([]);
  const [pending, setPending] = useState<any[]>([]);
  const [learningRules, setLearningRules] = useState<any[]>([]);
  const [status, setStatus] = useState<any>();
  const [businessLines, setBusinessLines] = useState<Option[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [mode, setMode] = useState('');
  const [source, setSource] = useState('');
  const [environment, setEnvironment] = useState('');
  const [role, setRole] = useState('');
  const [selectedSiteIds, setSelectedSiteIds] = useState<string[]>([]);
  const [repoOpen, setRepoOpen] = useState(false);
  const [repoForm] = Form.useForm();

  const load = async (discoverIfEmpty = false) => {
    setLoading(true);
    try {
      const filters = {
        ...(search ? { search } : {}), ...(mode ? { monitoring_mode: mode } : {}),
        ...(source ? { source } : {}), ...(environment ? { environment } : {}),
        ...(role ? { role } : {}),
      };
      let { data } = await httpAccessApi.listSites(filters);
      if (discoverIfEmpty && !Object.keys(filters).length && !data.items.length) {
        await httpAccessApi.discoverSites();
        ({ data } = await httpAccessApi.listSites());
      }
      setItems(data.items);
      setSelectedSiteIds([]);
      const [statusRes, pendingRes, learningRes, repoRes, bizRes] = await Promise.all([
        httpAccessApi.governanceStatus(), httpAccessApi.pending(),
        httpAccessApi.learningRules(), httpAccessApi.repositories(), businessLineApi.listAll(),
      ]);
      setStatus(statusRes.data);
      setPending(pendingRes.data.items || []);
      setLearningRules(learningRes.data.items || []);
      setRepos(repoRes.data.items || []);
      setBusinessLines((bizRes.data.items || []).map((item: any) => ({ value: item.id, label: `${item.name} · ${item.language}` })));
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载HTTP访问治理数据失败');
    } finally { setLoading(false); }
  };
  useEffect(() => { void load(true); }, []);

  const update = async (row: HttpAccessSite, patch: Record<string, unknown>) => {
    try { await httpAccessApi.updateSite(row.id, patch); message.success('已保存'); void load(); }
    catch (error: any) { message.error(error?.response?.data?.detail || '保存失败'); }
  };

  const siteColumns: ColumnsType<HttpAccessSite> = [
    { title: '站点', dataIndex: 'site', width: 270, render: (v: string, r) => <><Typography.Text strong>{v}</Typography.Text><br />{r.sources.map(s => <Tag key={s}>{s === 'ingress' ? 'Ingress/Java' : 'Nginx/C#'}</Tag>)}</> },
    { title: '环境', dataIndex: 'environment', width: 100, render: (v, r) => <Select value={v} style={{ width: 88 }} options={[{value:'production',label:'生产'},{value:'test',label:'测试'}]} onChange={environment => update(r, { environment })} /> },
    { title: '角色', dataIndex: 'role', width: 120, render: (v, r) => <Select value={v} style={{ width: 105 }} options={Object.entries(roleLabels).map(([value,label]) => ({value,label}))} onChange={role => update(r, { role })} /> },
    { title: '检测状态', dataIndex: 'monitoring_mode', width: 130, render: (v, r) => <Select value={v} style={{ width: 110 }} options={Object.entries(modeLabels).map(([value,label]) => ({value,label}))} onChange={monitoring_mode => update(r, { monitoring_mode })} /> },
    { title: '应用日志', dataIndex: 'diagnostic_business_line_id', width: 190, render: (v, r) => <Select allowClear showSearch optionFilterProp="label" value={v || undefined} placeholder="关联业务线" style={{ width: 175 }} options={businessLines} onChange={diagnostic_business_line_id => update(r, { diagnostic_business_line_id: diagnostic_business_line_id || null })} /> },
    { title: '代码仓库', dataIndex: 'repository_id', width: 170, render: (v, r) => <Select allowClear value={v || undefined} placeholder="关联仓库" style={{ width: 155 }} options={repos.map(repo => ({ value: repo.id, label: repo.name }))} onChange={repository_id => update(r, { repository_id: repository_id || null })} /> },
    { title: '部署服务名', dataIndex: 'deployment_service_name', width: 170, render: (v, r) => <Input defaultValue={v} placeholder="CI service名称" onBlur={event => { const next = event.currentTarget.value.trim(); if (next !== (v || '')) void update(r, { deployment_service_name: next }); }} /> },
    { title: '告警项', width: 220, render: (_, r) => <Space direction="vertical" size={1}><span>4xx <Switch size="small" checked={r.enable_4xx} onChange={enable_4xx => update(r, { enable_4xx })} /></span><span>延迟 <Switch size="small" checked={r.enable_latency} onChange={enable_latency => update(r, { enable_latency })} /></span><span>流量 <Switch size="small" disabled={!['app','account','payment','front'].includes(r.role)} checked={r.enable_traffic_drop} onChange={enable_traffic_drop => update(r, { enable_traffic_drop })} /></span></Space> },
    { title: '最后活跃', dataIndex: 'last_seen_at', width: 180, render: v => v ? new Date(v).toLocaleString() : '-' },
  ];

  const incidentColumns: ColumnsType<any> = [
    { title: '级别', dataIndex: 'priority', width: 70, render: v => <Tag color={v === 'P0' ? 'red' : 'orange'}>{v}</Tag> },
    { title: '站点', dataIndex: 'site', width: 240 },
    { title: '问题', width: 390, render: (_, r) => <><div>{r.kind} · {r.route_key || '站点级'}</div><Typography.Text type="secondary">最近：{new Date(r.last_seen_at).toLocaleString()} · 通知{r.notification_count}次</Typography.Text>{r.diagnosis?.knowledge_sources?.length ? <div><Tag color="purple">经验：{r.diagnosis.knowledge_sources.join('、')}</Tag></div> : null}{r.diagnosis?.code_findings?.[0]?.matched_files?.[0] ? <div><Tag color="geekblue">代码：{r.diagnosis.code_findings[0].matched_files[0]}</Tag></div> : null}</> },
    { title: '状态', dataIndex: 'status', width: 110, render: (v, r) => <Tag color={r.notification_pending ? 'red' : 'blue'}>{r.notification_pending ? '待补发' : v}</Tag> },
    { title: '操作', width: 340, render: (_, r) => <Space wrap>
      <Button size="small" onClick={() => openFeedback(r.id, 'valid')}>确认有效</Button>
      <Button size="small" onClick={() => openFeedback(r.id, 'expected')}>预期行为</Button>
      <Button size="small" danger onClick={() => openFeedback(r.id, 'false_positive')}>误报</Button>
      <Button size="small" type="primary" onClick={() => openFeedback(r.id, 'resolved')}>已解决</Button>
    </Space> },
  ];

  const repoColumns: ColumnsType<GitRepository> = [
    { title: '仓库', width: 260, render: (_, r) => <><Typography.Text strong>{r.name}</Typography.Text><br /><Typography.Text type="secondary" ellipsis>{r.clone_url}</Typography.Text></> },
    { title: '分支', dataIndex: 'default_branch', width: 90 },
    { title: '同步', width: 180, render: (_, r) => <><Tag color={r.last_sync_status === 'success' ? 'green' : r.last_sync_status === 'failed' ? 'red' : 'blue'}>{r.last_sync_status}</Tag><br />{r.last_commit_sha ? r.last_commit_sha.slice(0, 12) : '-'}</> },
    { title: '上次同步', dataIndex: 'last_synced_at', width: 180, render: v => v ? new Date(v).toLocaleString() : '-' },
    { title: '操作', width: 180, render: (_, r) => <Space><Button onClick={() => httpAccessApi.testRepository(r.id).then(() => { message.success('连接成功'); void load(); }).catch((e) => message.error(e?.response?.data?.detail || '连接失败'))}>测试</Button><Button onClick={() => httpAccessApi.syncRepository(r.id).then(() => { message.success('已提交同步'); void load(); }).catch((e) => message.error(e?.response?.data?.detail || '提交同步失败'))}>同步</Button></Space> },
  ];

  const learningColumns: ColumnsType<any> = [
    { title: '站点', dataIndex: 'site', width: 250 },
    { title: '类型', dataIndex: 'kind', width: 110 },
    { title: '来源', dataIndex: 'source', width: 100, render: v => <Tag color={v === 'auto' ? 'blue' : 'purple'}>{v === 'auto' ? '自动学习' : '人工反馈'}</Tag> },
    { title: '结论', dataIndex: 'disposition', width: 110 },
    { title: '抑制原因', dataIndex: 'reason' },
    { title: '命中', dataIndex: 'hit_count', width: 80 },
    { title: '复核时间', dataIndex: 'expires_at', width: 180, render: v => v ? new Date(v).toLocaleString() : '-' },
  ];

  const feedback = async (id: string, action: 'valid' | 'false_positive' | 'expected' | 'resolved', comment: string) => {
    try { await httpAccessApi.feedback(id, action, comment); message.success('反馈已记录并用于后续学习'); void load(); }
    catch (error: any) { message.error(error?.response?.data?.detail || '反馈失败'); }
  };

  const openFeedback = (id: string, action: 'valid' | 'false_positive' | 'expected' | 'resolved') => {
    let comment = '';
    const titles = { valid: '确认有效风险', false_positive: '标记误报', expected: '标记预期行为', resolved: '记录解决经验' };
    Modal.confirm({
      title: titles[action],
      content: <Input.TextArea rows={4} maxLength={2000} placeholder={action === 'resolved' ? '请填写处理原因和修复方式，后续相似风险会引用这条经验' : '请填写判断依据，便于30天后复核'} onChange={event => { comment = event.target.value; }} />,
      okText: '提交', cancelText: '取消',
      onOk: () => feedback(id, action, comment.trim()),
    });
  };

  const bulkUpdate = async (patch: Record<string, unknown>) => {
    if (!selectedSiteIds.length) { message.warning('请先勾选站点'); return; }
    try {
      await httpAccessApi.bulkUpdate(selectedSiteIds, patch);
      message.success(`已更新${selectedSiteIds.length}个站点`);
      void load();
    } catch (error: any) { message.error(error?.response?.data?.detail || '批量更新失败'); }
  };

  const createRepo = async () => {
    const values = await repoForm.validateFields();
    try { await httpAccessApi.createRepository(values); message.success('仓库已创建'); setRepoOpen(false); repoForm.resetFields(); void load(); }
    catch (error: any) { message.error(error?.response?.data?.detail || '创建失败'); }
  };

  return <Space direction="vertical" size={16} style={{ width: '100%' }}>
    {status && !status.healthy && <Alert type="error" showIcon message="HTTP巡检当前不可正常告警" description={`巡检开关：${status.patrol_enabled ? '开' : '关'}；通知开关：${status.notification_enabled ? '开' : '关'}；启用站点：${status.site_modes?.enabled || 0}；心跳：${status.heartbeat_stale ? '已超时' : '正常'}`} />}
    {status && <Card><Space size={40} wrap><Statistic title="启用站点" value={status.site_modes?.enabled || 0} /><Statistic title="仅观测" value={status.site_modes?.observe || 0} /><Statistic title="禁用" value={status.site_modes?.disabled || 0} /><Statistic title="候选风险" value={status.last_run?.candidate_incident_count || 0} /><Statistic title="站点策略抑制" value={status.last_run?.suppressed_site_mode_count || 0} /><Statistic title="学习抑制" value={status.last_run?.learned_suppressed_count || 0} /><Statistic title="待补发" value={status.pending_notification_count || 0} valueStyle={{ color: status.pending_notification_count ? '#cf1322' : undefined }} /><Statistic title="待处理风险" value={status.unresolved_incident_count || 0} /></Space></Card>}
    <Card title="HTTP访问监控治理" extra={<Space><Button onClick={() => httpAccessApi.discoverSites().then(() => load()).catch(() => message.error('站点发现失败'))}>同步发现</Button><Button onClick={() => load()}>刷新</Button></Space>}>
      <Tabs items={[
        { key: 'sites', label: `站点配置 (${items.length})`, children: <><Typography.Paragraph type="secondary">现有生产站点保持启用，新发现站点默认仅观测；深度诊断需关联应用业务线和只读Git仓库。</Typography.Paragraph><Space style={{ marginBottom: 12 }} wrap><Input placeholder="搜索站点" value={search} onChange={e => setSearch(e.target.value)} onPressEnter={() => load()} style={{ width: 220 }} /><Select placeholder="来源" allowClear value={source || undefined} onChange={v => setSource(v || '')} options={[{value:'nginx',label:'Nginx/C#'},{value:'ingress',label:'Ingress/Java'}]} style={{ width: 130 }} /><Select placeholder="环境" allowClear value={environment || undefined} onChange={v => setEnvironment(v || '')} options={[{value:'production',label:'生产'},{value:'test',label:'测试'}]} style={{ width: 110 }} /><Select placeholder="角色" allowClear value={role || undefined} onChange={v => setRole(v || '')} options={Object.entries(roleLabels).map(([value,label]) => ({value,label}))} style={{ width: 120 }} /><Select placeholder="检测状态" allowClear value={mode || undefined} onChange={v => setMode(v || '')} options={Object.entries(modeLabels).map(([value,label]) => ({value,label}))} style={{ width: 130 }} /><Button type="primary" onClick={() => load()}>筛选</Button></Space><Space style={{ marginBottom: 16 }} wrap><Typography.Text type="secondary">已选 {selectedSiteIds.length} 个</Typography.Text><Button onClick={() => bulkUpdate({ monitoring_mode: 'enabled' })}>批量启用</Button><Button onClick={() => bulkUpdate({ monitoring_mode: 'observe' })}>批量仅观测</Button><Button danger onClick={() => bulkUpdate({ monitoring_mode: 'disabled' })}>批量禁用</Button><Button onClick={() => bulkUpdate({ environment: 'test', monitoring_mode: 'disabled' })}>设为测试并禁用</Button></Space><Table rowKey="id" rowSelection={{ selectedRowKeys: selectedSiteIds, onChange: keys => setSelectedSiteIds(keys.map(String)) }} loading={loading} columns={siteColumns} dataSource={items} pagination={{ pageSize: 20 }} scroll={{ x: 1450 }} /></> },
        { key: 'incidents', label: `待处理 (${pending.length})`, children: <Table rowKey="id" loading={loading} columns={incidentColumns} dataSource={pending} pagination={{ pageSize: 20 }} scroll={{ x: 1050 }} /> },
        { key: 'learning', label: `学习规则 (${learningRules.length})`, children: <Table rowKey="id" loading={loading} columns={learningColumns} dataSource={learningRules} pagination={{ pageSize: 20 }} scroll={{ x: 1050 }} /> },
        { key: 'repos', label: `代码仓库 (${repos.length})`, children: <><Space style={{ marginBottom: 16 }}><Button type="primary" onClick={() => setRepoOpen(true)}>新增只读仓库</Button><Typography.Text type="secondary">凭证只填写Secret环境变量名，数据库不保存Token。</Typography.Text></Space><Table rowKey="id" loading={loading} columns={repoColumns} dataSource={repos} pagination={false} /></> },
      ]} />
    </Card>
    <Modal title="新增GitLab只读仓库" open={repoOpen} onCancel={() => setRepoOpen(false)} onOk={createRepo} destroyOnClose>
      <Form form={repoForm} layout="vertical" initialValues={{ default_branch: 'main' }}>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="clone_url" label="HTTPS Clone URL" rules={[{ required: true }, { type: 'url' }]}><Input placeholder="https://gitlab.example/group/project.git" /></Form.Item>
        <Form.Item name="default_branch" label="默认分支" rules={[{ required: true }]}><Select options={[{ value: 'main', label: 'main' }, { value: 'master', label: 'master' }]} /></Form.Item>
        <Form.Item name="credential_ref" label="凭证环境变量" tooltip="变量值格式 username:deploy-token"><Input placeholder="LOGMIND_GIT_TOKEN_PROJECT" /></Form.Item>
      </Form>
    </Modal>
  </Space>;
}
