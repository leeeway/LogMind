import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Typography, Button, Space, Card, Input, Select, message, Modal,
  Tag, Tooltip, Drawer, Empty, Popconfirm,
} from 'antd';
import {
  PlusOutlined, EditOutlined, SaveOutlined, EyeOutlined,
  DeleteOutlined, NumberOutlined, LineChartOutlined,
  BarChartOutlined, FileSearchOutlined, AppstoreOutlined,
  AlertOutlined, FileTextOutlined, DragOutlined, CloseOutlined,
} from '@ant-design/icons';
import { dashboardBuilderApi } from '@/api/dashboardBuilder';

const { Title, Text } = Typography;

// ── Widget Type Registry ────────────────────────────────
const WIDGET_TYPES = [
  { type: 'number', label: '数字卡片', icon: <NumberOutlined />, defaultW: 3, defaultH: 2 },
  { type: 'line_chart', label: '折线图', icon: <LineChartOutlined />, defaultW: 6, defaultH: 4 },
  { type: 'bar_chart', label: '柱状图', icon: <BarChartOutlined />, defaultW: 6, defaultH: 4 },
  { type: 'log_list', label: '日志列表', icon: <FileSearchOutlined />, defaultW: 6, defaultH: 4 },
  { type: 'status_matrix', label: '服务状态', icon: <AppstoreOutlined />, defaultW: 4, defaultH: 3 },
  { type: 'alert_list', label: '告警列表', icon: <AlertOutlined />, defaultW: 6, defaultH: 4 },
  { type: 'markdown', label: '文本/链接', icon: <FileTextOutlined />, defaultW: 4, defaultH: 3 },
];

const GRID_COLS = 12;
const CELL_SIZE = 80;

interface Widget {
  id: string;
  type: string;
  title: string;
  x: number;
  y: number;
  w: number;
  h: number;
  config: Record<string, any>;
}

const defaultWidgetTitle: Record<string, string> = {
  number: '错误数',
  line_chart: '错误趋势',
  bar_chart: '服务对比',
  log_list: '最新日志',
  status_matrix: '服务健康',
  alert_list: '最新告警',
  markdown: '自定义文本',
};

const DashboardBuilder: React.FC = () => {
  const [dashboards, setDashboards] = useState<any[]>([]);
  const [activeDashId, setActiveDashId] = useState<string | null>(null);
  const [dashName, setDashName] = useState('我的看板');
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [editMode, setEditMode] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const [resizing, setResizing] = useState<string | null>(null);
  const [dragStart, setDragStart] = useState({ mx: 0, my: 0, wx: 0, wy: 0 });
  const [resizeStart, setResizeStart] = useState({ mx: 0, my: 0, ww: 0, wh: 0 });
  const [addDrawer, setAddDrawer] = useState(false);
  const [configWidget, setConfigWidget] = useState<Widget | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  // Load dashboards list
  const loadList = useCallback(async () => {
    try {
      const { data } = await dashboardBuilderApi.list();
      setDashboards(data?.dashboards || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadList(); }, [loadList]);

  // Load specific dashboard
  const loadDashboard = useCallback(async (id: string) => {
    try {
      const { data } = await dashboardBuilderApi.get(id);
      setActiveDashId(id);
      setDashName(data.name);
      setWidgets(data.layout || []);
      setEditMode(false);
    } catch { message.error('加载失败'); }
  }, []);

  // Save dashboard
  const saveDashboard = async () => {
    const payload = { name: dashName, layout: widgets };
    try {
      if (activeDashId) {
        await dashboardBuilderApi.update(activeDashId, payload);
      } else {
        const { data } = await dashboardBuilderApi.create(payload);
        setActiveDashId(data.id);
      }
      message.success('看板已保存');
      setEditMode(false);
      loadList();
    } catch { message.error('保存失败'); }
  };

  // Delete dashboard
  const deleteDashboard = async () => {
    if (!activeDashId) return;
    try {
      await dashboardBuilderApi.delete(activeDashId);
      setActiveDashId(null);
      setWidgets([]);
      setDashName('我的看板');
      message.success('已删除');
      loadList();
    } catch { message.error('删除失败'); }
  };

  // Add widget
  const addWidget = (type: string) => {
    const wt = WIDGET_TYPES.find(t => t.type === type);
    const id = `w_${Date.now()}`;
    // Find free spot
    const maxY = widgets.reduce((max, w) => Math.max(max, w.y + w.h), 0);
    setWidgets(prev => [...prev, {
      id, type,
      title: defaultWidgetTitle[type] || 'Widget',
      x: 0, y: maxY,
      w: wt?.defaultW || 4,
      h: wt?.defaultH || 3,
      config: {},
    }]);
    setAddDrawer(false);
  };

  // Remove widget
  const removeWidget = (id: string) => {
    setWidgets(prev => prev.filter(w => w.id !== id));
  };

  // Drag handling
  const handleDragStart = (e: React.MouseEvent, widget: Widget) => {
    if (!editMode) return;
    e.preventDefault();
    setDragging(widget.id);
    setDragStart({ mx: e.clientX, my: e.clientY, wx: widget.x, wy: widget.y });
  };

  const handleResizeStart = (e: React.MouseEvent, widget: Widget) => {
    if (!editMode) return;
    e.preventDefault();
    e.stopPropagation();
    setResizing(widget.id);
    setResizeStart({ mx: e.clientX, my: e.clientY, ww: widget.w, wh: widget.h });
  };

  useEffect(() => {
    if (!dragging && !resizing) return;

    const handleMove = (e: MouseEvent) => {
      if (dragging) {
        const dx = Math.round((e.clientX - dragStart.mx) / CELL_SIZE);
        const dy = Math.round((e.clientY - dragStart.my) / CELL_SIZE);
        setWidgets(prev => prev.map(w =>
          w.id === dragging
            ? { ...w, x: Math.max(0, Math.min(GRID_COLS - w.w, dragStart.wx + dx)), y: Math.max(0, dragStart.wy + dy) }
            : w
        ));
      }
      if (resizing) {
        const dw = Math.round((e.clientX - resizeStart.mx) / CELL_SIZE);
        const dh = Math.round((e.clientY - resizeStart.my) / CELL_SIZE);
        setWidgets(prev => prev.map(w =>
          w.id === resizing
            ? { ...w, w: Math.max(2, Math.min(GRID_COLS - w.x, resizeStart.ww + dw)), h: Math.max(2, resizeStart.wh + dh) }
            : w
        ));
      }
    };

    const handleUp = () => {
      setDragging(null);
      setResizing(null);
    };

    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleUp);
    return () => {
      window.removeEventListener('mousemove', handleMove);
      window.removeEventListener('mouseup', handleUp);
    };
  }, [dragging, resizing, dragStart, resizeStart]);

  // Grid height
  const gridHeight = Math.max(6, widgets.reduce((max, w) => Math.max(max, w.y + w.h), 0) + 2);

  // Render widget content (preview mode)
  const renderWidgetContent = (w: Widget) => {
    const wt = WIDGET_TYPES.find(t => t.type === w.type);
    return (
      <div style={{
        height: '100%', display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
        color: 'var(--lm-text-tertiary)', fontSize: 12,
      }}>
        <div style={{ fontSize: 28, marginBottom: 8, opacity: 0.4 }}>{wt?.icon}</div>
        {w.type === 'number' && (
          <div style={{ fontSize: 32, fontWeight: 700, fontFamily: 'monospace', color: 'var(--lm-text)' }}>
            {w.config.value || '—'}
          </div>
        )}
        {w.type === 'markdown' && w.config.content && (
          <div style={{ fontSize: 13, color: 'var(--lm-text-secondary)', textAlign: 'center', padding: '0 8px' }}>
            {w.config.content.slice(0, 100)}
          </div>
        )}
        {!['number', 'markdown'].includes(w.type) && (
          <div style={{ opacity: 0.5 }}>{wt?.label}</div>
        )}
      </div>
    );
  };

  return (
    <div className="lm-animate-in">
      {/* Toolbar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
            <AppstoreOutlined style={{ marginRight: 8 }} />
            {editMode ? (
              <Input
                value={dashName}
                onChange={e => setDashName(e.target.value)}
                style={{ width: 200, fontSize: 16, fontWeight: 600 }}
                size="small"
              />
            ) : (
              dashName
            )}
          </Title>
        </Space>

        <Space>
          {/* Dashboard selector */}
          {dashboards.length > 0 && !editMode && (
            <Select
              value={activeDashId || undefined}
              onChange={loadDashboard}
              placeholder="选择看板"
              style={{ width: 160 }}
              size="small"
              allowClear
              options={dashboards.map(d => ({ value: d.id, label: d.name }))}
            />
          )}

          {editMode ? (
            <>
              <Button icon={<PlusOutlined />} onClick={() => setAddDrawer(true)}>
                添加组件
              </Button>
              <Button type="primary" icon={<SaveOutlined />} onClick={saveDashboard}>
                保存
              </Button>
              <Button onClick={() => setEditMode(false)}>取消</Button>
            </>
          ) : (
            <>
              <Button icon={<EditOutlined />} onClick={() => setEditMode(true)}>编辑</Button>
              <Button icon={<PlusOutlined />} onClick={() => {
                setActiveDashId(null);
                setDashName('新看板');
                setWidgets([]);
                setEditMode(true);
              }}>新建</Button>
              {activeDashId && (
                <Popconfirm title="确认删除此看板？" onConfirm={deleteDashboard}>
                  <Button danger icon={<DeleteOutlined />} />
                </Popconfirm>
              )}
            </>
          )}
        </Space>
      </div>

      {/* Grid */}
      <div
        ref={gridRef}
        style={{
          position: 'relative',
          width: GRID_COLS * CELL_SIZE,
          minHeight: gridHeight * CELL_SIZE,
          maxWidth: '100%',
          background: editMode
            ? `repeating-linear-gradient(
                0deg, var(--lm-border-light) 0, var(--lm-border-light) 1px, transparent 1px, transparent ${CELL_SIZE}px
              ),
              repeating-linear-gradient(
                90deg, var(--lm-border-light) 0, var(--lm-border-light) 1px, transparent 1px, transparent ${CELL_SIZE}px
              )`
            : 'transparent',
          borderRadius: 12,
          transition: 'background 0.3s',
        }}
      >
        {widgets.map(w => (
          <div
            key={w.id}
            style={{
              position: 'absolute',
              left: w.x * CELL_SIZE,
              top: w.y * CELL_SIZE,
              width: w.w * CELL_SIZE - 8,
              height: w.h * CELL_SIZE - 8,
              background: 'var(--lm-bg-card)',
              border: `1px solid ${editMode ? 'var(--lm-border)' : 'var(--lm-border-light)'}`,
              borderRadius: 10,
              overflow: 'hidden',
              transition: dragging === w.id || resizing === w.id ? 'none' : 'all 0.2s',
              boxShadow: dragging === w.id ? 'var(--lm-shadow-elevated)' : 'var(--lm-shadow-card)',
              cursor: editMode ? 'move' : 'default',
              zIndex: dragging === w.id ? 10 : 1,
              userSelect: 'none',
            }}
            onMouseDown={e => handleDragStart(e, w)}
          >
            {/* Widget Header */}
            <div style={{
              padding: '6px 10px',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              borderBottom: '1px solid var(--lm-border-light)',
              fontSize: 12, fontWeight: 600, color: 'var(--lm-text-secondary)',
            }}>
              <span>{w.title}</span>
              {editMode && (
                <Space size={2}>
                  <Tooltip title="配置">
                    <EditOutlined
                      style={{ cursor: 'pointer', fontSize: 11 }}
                      onClick={e => { e.stopPropagation(); setConfigWidget(w); }}
                    />
                  </Tooltip>
                  <Tooltip title="删除">
                    <CloseOutlined
                      style={{ cursor: 'pointer', fontSize: 11, color: '#ff4d4f' }}
                      onClick={e => { e.stopPropagation(); removeWidget(w.id); }}
                    />
                  </Tooltip>
                </Space>
              )}
            </div>

            {/* Widget Content */}
            <div style={{ height: 'calc(100% - 32px)', padding: 4 }}>
              {renderWidgetContent(w)}
            </div>

            {/* Resize handle */}
            {editMode && (
              <div
                style={{
                  position: 'absolute', right: 0, bottom: 0,
                  width: 14, height: 14, cursor: 'se-resize',
                  background: 'linear-gradient(135deg, transparent 50%, var(--lm-text-tertiary) 50%)',
                  borderRadius: '0 0 10px 0', opacity: 0.4,
                }}
                onMouseDown={e => handleResizeStart(e, w)}
              />
            )}
          </div>
        ))}

        {widgets.length === 0 && (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', color: 'var(--lm-text-tertiary)',
          }}>
            <AppstoreOutlined style={{ fontSize: 48, marginBottom: 16, opacity: 0.3 }} />
            <div style={{ fontSize: 16, marginBottom: 8 }}>
              {editMode ? '点击「添加组件」开始构建看板' : '选择或创建一个看板'}
            </div>
          </div>
        )}
      </div>

      {/* Add Widget Drawer */}
      <Drawer
        title="添加组件"
        open={addDrawer}
        onClose={() => setAddDrawer(false)}
        width={320}
      >
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {WIDGET_TYPES.map(wt => (
            <div
              key={wt.type}
              onClick={() => addWidget(wt.type)}
              style={{
                padding: '16px 12px', textAlign: 'center', cursor: 'pointer',
                background: 'var(--lm-bg-elevated)', border: '1px solid var(--lm-border-light)',
                borderRadius: 10, transition: 'all 0.2s',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = '#1677ff'; e.currentTarget.style.transform = 'scale(1.02)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--lm-border-light)'; e.currentTarget.style.transform = 'scale(1)'; }}
            >
              <div style={{ fontSize: 24, marginBottom: 6, color: '#1677ff' }}>{wt.icon}</div>
              <div style={{ fontSize: 12, color: 'var(--lm-text-secondary)' }}>{wt.label}</div>
            </div>
          ))}
        </div>
      </Drawer>

      {/* Config Modal */}
      <Modal
        title={`配置: ${configWidget?.title}`}
        open={!!configWidget}
        onCancel={() => setConfigWidget(null)}
        onOk={() => {
          if (configWidget) {
            setWidgets(prev => prev.map(w => w.id === configWidget.id ? configWidget : w));
            setConfigWidget(null);
          }
        }}
        okText="确认"
      >
        {configWidget && (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <div>
              <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>标题</Text>
              <Input
                value={configWidget.title}
                onChange={e => setConfigWidget({ ...configWidget, title: e.target.value })}
              />
            </div>
            {configWidget.type === 'number' && (
              <div>
                <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>演示值</Text>
                <Input
                  value={configWidget.config.value || ''}
                  onChange={e => setConfigWidget({
                    ...configWidget,
                    config: { ...configWidget.config, value: e.target.value },
                  })}
                  placeholder="1,234"
                />
              </div>
            )}
            {configWidget.type === 'markdown' && (
              <div>
                <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>内容</Text>
                <Input.TextArea
                  value={configWidget.config.content || ''}
                  onChange={e => setConfigWidget({
                    ...configWidget,
                    config: { ...configWidget.config, content: e.target.value },
                  })}
                  placeholder="Markdown 内容..."
                  autoSize={{ minRows: 3, maxRows: 8 }}
                />
              </div>
            )}
            <div>
              <Text style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>数据源 (索引模式)</Text>
              <Input
                value={configWidget.config.index_pattern || ''}
                onChange={e => setConfigWidget({
                  ...configWidget,
                  config: { ...configWidget.config, index_pattern: e.target.value },
                })}
                placeholder="*"
              />
            </div>
          </Space>
        )}
      </Modal>
    </div>
  );
};

export default DashboardBuilder;
