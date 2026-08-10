import { useEffect, useState } from 'react';
import { Button, Card, Input, message, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { httpAccessApi, type HttpAccessSite } from '@/api/httpAccess';

const roleLabels: Record<string, string> = { app: 'APP', account: '账号', payment: '支付', front: '前台', general: '通用', cdn_download: 'CDN/下载' };
const modeLabels: Record<string, string> = { observe: '仅观测', enabled: '已启用', disabled: '已禁用' };

export default function HttpAccessSites() {
  const [items, setItems] = useState<HttpAccessSite[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [mode, setMode] = useState('');
  const load = async (discoverIfEmpty = false) => {
    setLoading(true);
    try {
      let { data } = await httpAccessApi.listSites({ ...(search ? { search } : {}), ...(mode ? { monitoring_mode: mode } : {}) });
      if (discoverIfEmpty && !search && !mode && !data.items.length) {
        await httpAccessApi.discoverSites();
        ({ data } = await httpAccessApi.listSites());
      }
      setItems(data.items);
    }
    catch { message.error('加载访问站点失败'); } finally { setLoading(false); }
  };
  useEffect(() => { void load(true); }, []);
  const update = async (row: HttpAccessSite, patch: Record<string, unknown>) => {
    try { await httpAccessApi.updateSite(row.id, patch); message.success('已保存'); void load(); }
    catch (error: any) { message.error(error?.response?.data?.detail || '保存失败'); }
  };
  const columns: ColumnsType<HttpAccessSite> = [
    { title: '站点', dataIndex: 'site', width: 280, render: (v: string, r) => <><Typography.Text strong>{v}</Typography.Text><br />{r.sources.map(s => <Tag key={s}>{s === 'ingress' ? 'Ingress/Java' : 'Nginx/C#'}</Tag>)}</> },
    { title: '环境', dataIndex: 'environment', width: 100, render: (v, r) => <Select value={v} style={{ width: 88 }} options={[{value:'production',label:'生产'},{value:'test',label:'测试'}]} onChange={environment => update(r, { environment })} /> },
    { title: '角色', dataIndex: 'role', width: 120, render: (v, r) => <Select value={v} style={{ width: 105 }} options={Object.entries(roleLabels).map(([value,label]) => ({value,label}))} onChange={role => update(r, { role })} /> },
    { title: '检测状态', dataIndex: 'monitoring_mode', width: 130, render: (v, r) => <Select value={v} style={{ width: 110 }} options={Object.entries(modeLabels).map(([value,label]) => ({value,label}))} onChange={monitoring_mode => update(r, { monitoring_mode })} /> },
    { title: '告警项', width: 240, render: (_, r) => <Space direction="vertical" size={1}><span>4xx <Switch size="small" checked={r.enable_4xx} onChange={enable_4xx => update(r, { enable_4xx })} /></span><span>延迟 <Switch size="small" checked={r.enable_latency} onChange={enable_latency => update(r, { enable_latency })} /></span><span>流量 <Switch size="small" disabled={!['app','account','payment','front'].includes(r.role)} checked={r.enable_traffic_drop} onChange={enable_traffic_drop => update(r, { enable_traffic_drop })} /></span></Space> },
    { title: '最后活跃', dataIndex: 'last_seen_at', width: 180, render: v => v ? new Date(v).toLocaleString() : '-' },
  ];
  return <Card title="HTTP 访问站点治理" extra={<Space><Button onClick={() => httpAccessApi.discoverSites().then(() => load()).catch(() => message.error('站点发现失败'))}>同步发现</Button><Button onClick={() => load()}>刷新</Button></Space>}>
    <Typography.Paragraph type="secondary">站点从 Nginx / Ingress 访问日志自动发现。新站点默认“仅观测”；测试或禁用站点不会发送企微告警。</Typography.Paragraph>
    <Space style={{ marginBottom: 16 }} wrap><Input placeholder="搜索站点" value={search} onChange={e => setSearch(e.target.value)} onPressEnter={load} style={{ width: 240 }} /><Select placeholder="检测状态" allowClear value={mode || undefined} onChange={v => setMode(v || '')} options={Object.entries(modeLabels).map(([value,label]) => ({value,label}))} style={{ width: 130 }} /><Button type="primary" onClick={load}>筛选</Button></Space>
    <Table rowKey="id" loading={loading} columns={columns} dataSource={items} pagination={{ pageSize: 20 }} scroll={{ x: 1100 }} />
  </Card>;
}
