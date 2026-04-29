import React, { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Avatar, Dropdown, Typography, Space, Badge, Tooltip, Breadcrumb } from 'antd';
import {
  DashboardOutlined,
  ExperimentOutlined,
  AlertOutlined,
  FileSearchOutlined,
  ClusterOutlined,
  SettingOutlined,
  BookOutlined,
  LogoutOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ThunderboltOutlined,
  BugOutlined,
  SafetyCertificateOutlined,
  BulbOutlined,
  MessageOutlined,
  ApartmentOutlined,
  RocketOutlined,
  HeatMapOutlined,
  HistoryOutlined,
  FileTextOutlined,
  TableOutlined,
  RadarChartOutlined,
  WifiOutlined,
  AppstoreOutlined,
  WarningOutlined,
  DeploymentUnitOutlined,
  CalendarOutlined,
  TeamOutlined,
  SoundOutlined,
  MedicineBoxOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import { alertsApi } from '@/api/alerts';
import ErrorBoundary from '@/components/ErrorBoundary';
import NotificationBell from '@/components/NotificationBell';
import { useTheme } from '@/hooks/useTheme';

const { Sider, Header, Content } = Layout;
const { Text } = Typography;

const menuItems = [
  { key: '/command-center', icon: <RocketOutlined />, label: '指挥中心' },
  { key: '/', icon: <DashboardOutlined />, label: '总览' },
  { key: '/chat', icon: <MessageOutlined />, label: 'AI 诊断' },
  { key: '/incidents', icon: <WarningOutlined />, label: '故障作战室' },
  {
    key: 'grp-monitor',
    icon: <RadarChartOutlined />,
    label: '态势感知',
    children: [
      { key: '/patrol', icon: <RadarChartOutlined />, label: '异常巡逻' },
      { key: '/heatmap', icon: <HeatMapOutlined />, label: '错误热力图' },
      { key: '/topology', icon: <ApartmentOutlined />, label: '服务拓扑' },
      { key: '/sla', icon: <SafetyCertificateOutlined />, label: 'SLA 监控' },
      { key: '/correlation', icon: <ApartmentOutlined />, label: '故障关联' },
      { key: '/alert-fatigue', icon: <SoundOutlined />, label: '告警疲劳' },
      { key: '/mttr', icon: <MedicineBoxOutlined />, label: 'MTTR 健康' },
    ],
  },
  {
    key: 'grp-analysis',
    icon: <ExperimentOutlined />,
    label: '分析排查',
    children: [
      { key: '/analysis', icon: <ExperimentOutlined />, label: '分析中心' },
      { key: '/alerts', icon: <AlertOutlined />, label: '告警管理' },
      { key: '/logs', icon: <FileSearchOutlined />, label: '日志搜索' },
      { key: '/live-tail', icon: <WifiOutlined />, label: '实时日志流' },
      { key: '/time-travel', icon: <HistoryOutlined />, label: '时光回溯' },
      { key: '/known-issues', icon: <BugOutlined />, label: '已知问题' },
      { key: '/error-dna', icon: <ExperimentOutlined />, label: '错误 DNA' },
    ],
  },
  {
    key: 'grp-ops',
    icon: <ClusterOutlined />,
    label: '运维管理',
    children: [
      { key: '/business-lines', icon: <ClusterOutlined />, label: '服务管理' },
      { key: '/weekly-report', icon: <FileTextOutlined />, label: '巡检周报' },
      { key: '/pivot', icon: <TableOutlined />, label: '透视分析' },
      { key: '/dashboard-builder', icon: <AppstoreOutlined />, label: '看板编辑器' },
      { key: '/ai-insights', icon: <ThunderboltOutlined />, label: 'AI 洞察' },
      { key: '/daily-standup', icon: <CalendarOutlined />, label: '每日站会' },
      { key: '/changes', icon: <DeploymentUnitOutlined />, label: '变更追踪' },
      { key: '/oncall', icon: <TeamOutlined />, label: '值班排班' },
    ],
  },
  {
    key: 'grp-system',
    icon: <SettingOutlined />,
    label: '系统',
    children: [
      { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
      { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
    ],
  },
];

const breadcrumbMap: Record<string, string> = {
  '': '总览',
  analysis: '分析中心',
  alerts: '告警管理',
  logs: '日志搜索',
  'business-lines': '服务管理',
  'known-issues': '已知问题',
  sla: 'SLA 监控',
  chat: 'AI 诊断',
  topology: '服务拓扑',
  'command-center': '指挥中心',
  heatmap: '错误热力图',
  'time-travel': '时光回溯',
  'weekly-report': '巡检周报',
  pivot: '透视分析',
  patrol: '异常巡逻',
  'live-tail': '实时日志流',
  'ai-insights': 'AI 洞察',
  incidents: '故障作战室',
  'dashboard-builder': '看板编辑器',
  knowledge: '知识库',
  settings: '系统设置',
  compare: '对比分析',
  'error-dna': '错误 DNA',
  'alert-fatigue': '告警疲劳',
  correlation: '故障关联',
  mttr: 'MTTR 健康',
};

const AppLayout: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [alertCount, setAlertCount] = useState(0);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const { theme, toggleTheme, isDark } = useTheme();

  const selectedKey = '/' + (location.pathname.split('/')[1] || '');

  // Determine which sub-menu should be open based on current path
  const getOpenKeys = (): string[] => {
    const flatMap: Record<string, string> = {
      '/patrol': 'grp-monitor', '/heatmap': 'grp-monitor', '/topology': 'grp-monitor', '/sla': 'grp-monitor', '/correlation': 'grp-monitor', '/alert-fatigue': 'grp-monitor', '/mttr': 'grp-monitor',
      '/analysis': 'grp-analysis', '/alerts': 'grp-analysis', '/logs': 'grp-analysis', '/live-tail': 'grp-analysis', '/time-travel': 'grp-analysis', '/known-issues': 'grp-analysis', '/error-dna': 'grp-analysis',
      '/business-lines': 'grp-ops', '/weekly-report': 'grp-ops', '/pivot': 'grp-ops', '/dashboard-builder': 'grp-ops', '/ai-insights': 'grp-ops',
      '/knowledge': 'grp-system', '/settings': 'grp-system',
    };
    const grp = flatMap[selectedKey];
    return grp ? [grp] : [];
  };

  const [openKeys, setOpenKeys] = useState<string[]>(getOpenKeys());

  // Sync open keys on route change
  useEffect(() => {
    const newKeys = getOpenKeys();
    setOpenKeys(prev => {
      const merged = new Set([...prev, ...newKeys]);
      return Array.from(merged);
    });
  }, [selectedKey]);

  // Fetch unresolved alert count
  useEffect(() => {
    const fetchAlertCount = async () => {
      try {
        const { data } = await alertsApi.listHistory({ page: 1, page_size: 1 });
        setAlertCount(data.total || 0);
      } catch { /* ignore */ }
    };
    fetchAlertCount();
    const timer = setInterval(fetchAlertCount, 120000);
    return () => clearInterval(timer);
  }, []);

  // Build breadcrumb from path
  const pathParts = location.pathname.split('/').filter(Boolean);
  const breadcrumbItems = [
    { title: <a onClick={() => navigate('/')}>LogMind</a> },
    ...pathParts.map((part, index) => {
      const label = breadcrumbMap[part] || part;
      const path = '/' + pathParts.slice(0, index + 1).join('/');
      const isLast = index === pathParts.length - 1;
      return {
        title: isLast ? label : <a onClick={() => navigate(path)}>{label}</a>,
      };
    }),
  ];

  const userMenuItems = [
    { key: 'user', label: user?.username || 'User', icon: <UserOutlined />, disabled: true },
    { type: 'divider' as const },
    { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, danger: true },
  ];

  const handleUserMenu = ({ key }: { key: string }) => {
    if (key === 'logout') {
      logout();
      navigate('/login');
    }
  };

  // Keyboard shortcut: Cmd/Ctrl + K for search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        navigate('/logs');
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [navigate]);

  // Inject alert badge into menu items
  const processedMenuItems = menuItems.map(item => {
    if (item.children) {
      return {
        ...item,
        children: item.children.map(child =>
          child.key === '/alerts' && alertCount > 0
            ? { ...child, label: <Space>{child.label}<Badge count={alertCount} size="small" /></Space> }
            : child
        ),
      };
    }
    return item;
  });

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null}
        width={220}
        style={{
          background: 'var(--lm-bg-container)',
          borderRight: '1px solid var(--lm-border-light)',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
          zIndex: 100,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{
          height: 56,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'flex-start',
          padding: collapsed ? '0' : '0 16px',
          borderBottom: '1px solid var(--lm-border-light)',
          gap: 10,
          flexShrink: 0,
        }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8,
            background: 'linear-gradient(135deg, rgba(22,119,255,0.2) 0%, rgba(114,46,209,0.2) 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            border: '1px solid rgba(22,119,255,0.15)',
            flexShrink: 0,
          }}>
            <ThunderboltOutlined style={{ fontSize: 16, color: '#1677ff' }} />
          </div>
          {!collapsed && (
            <Text strong className="lm-gradient-text" style={{ fontSize: 16, letterSpacing: 1.5 }}>
              LogMind
            </Text>
          )}
        </div>
        <div style={{ flex: 1, overflow: 'auto', overflowX: 'hidden' }}>
          <Menu
            mode="inline"
            selectedKeys={[selectedKey]}
            openKeys={collapsed ? [] : openKeys}
            onOpenChange={keys => setOpenKeys(keys as string[])}
            items={processedMenuItems}
            onClick={({ key }) => { if (!key.startsWith('grp-')) navigate(key); }}
            style={{
              background: 'transparent',
              border: 'none',
              fontSize: 13,
            }}
          />
        </div>

        {/* Sidebar Footer — pinned to bottom */}
        {!collapsed && (
          <div style={{
            padding: '10px 16px',
            borderTop: '1px solid var(--lm-border-light)',
            fontSize: 10,
            color: 'var(--lm-text-tertiary)',
            flexShrink: 0,
            marginTop: 'auto',
          }}>
            <div>LogMind v5.0</div>
            <div style={{ opacity: 0.5 }}>AI 智能日志分析平台</div>
          </div>
        )}
        </div>
      </Sider>

      <Layout style={{ marginLeft: collapsed ? 64 : 220, transition: 'margin-left 0.2s' }}>
        <Header style={{
          background: 'var(--lm-bg-container)',
          borderBottom: '1px solid var(--lm-border-light)',
          padding: '0 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          position: 'sticky',
          top: 0,
          zIndex: 50,
          height: 56,
        }}>
          <Space>
            <div
              onClick={() => setCollapsed(!collapsed)}
              style={{ cursor: 'pointer', fontSize: 18, color: 'var(--lm-text-secondary)' }}
            >
              {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            </div>
            <Breadcrumb items={breadcrumbItems} style={{ marginLeft: 16 }} />
          </Space>

          <Space size={16}>
            <Tooltip title="搜索日志 (⌘K)">
              <FileSearchOutlined
                onClick={() => navigate('/logs')}
                style={{ fontSize: 16, color: 'var(--lm-text-tertiary)', cursor: 'pointer' }}
              />
            </Tooltip>
            <Tooltip title={isDark ? '切换浅色主题' : '切换深色主题'}>
              <BulbOutlined
                onClick={toggleTheme}
                style={{ fontSize: 16, color: 'var(--lm-text-tertiary)', cursor: 'pointer' }}
              />
            </Tooltip>
            <NotificationBell />
            <Dropdown menu={{ items: userMenuItems, onClick: handleUserMenu }} placement="bottomRight">
              <Space style={{ cursor: 'pointer' }}>
                <Avatar size={30} icon={<UserOutlined />} style={{ background: 'var(--lm-primary)' }} />
                <Text style={{ color: 'var(--lm-text-secondary)', fontSize: 13 }}>{user?.username}</Text>
              </Space>
            </Dropdown>
          </Space>
        </Header>

        <Content style={{
          padding: 24,
          minHeight: 'calc(100vh - 56px)',
          background: 'var(--lm-bg-layout)',
          position: 'relative',
        }}>
          <div className="lm-ambient" />
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </Content>
      </Layout>
    </Layout>
  );
};

export default AppLayout;
