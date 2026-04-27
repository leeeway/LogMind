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
  BellOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import { alertsApi } from '@/api/alerts';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Sider, Header, Content } = Layout;
const { Text } = Typography;

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '总览' },
  { key: '/analysis', icon: <ExperimentOutlined />, label: '分析中心' },
  { key: '/alerts', icon: <AlertOutlined />, label: '告警管理' },
  { key: '/logs', icon: <FileSearchOutlined />, label: '日志搜索' },
  { key: '/business-lines', icon: <ClusterOutlined />, label: '服务管理' },
  { key: '/ai-insights', icon: <ThunderboltOutlined />, label: 'AI 洞察' },
  { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
];

const breadcrumbMap: Record<string, string> = {
  '': '总览',
  analysis: '分析中心',
  alerts: '告警管理',
  logs: '日志搜索',
  'business-lines': '服务管理',
  'ai-insights': 'AI 洞察',
  knowledge: '知识库',
  settings: '系统设置',
  compare: '对比分析',
};

const AppLayout: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [alertCount, setAlertCount] = useState(0);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();

  const selectedKey = '/' + (location.pathname.split('/')[1] || '');

  // Fetch unresolved alert count
  useEffect(() => {
    const fetchAlertCount = async () => {
      try {
        const { data } = await alertsApi.listHistory({ page: 1, page_size: 1 });
        // Count non-resolved alerts
        setAlertCount(data.total || 0);
      } catch { /* ignore */ }
    };
    fetchAlertCount();
    const timer = setInterval(fetchAlertCount, 120000); // every 2 min
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

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null}
        width={240}
        style={{
          background: 'var(--lm-bg-container)',
          borderRight: '1px solid var(--lm-border-light)',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
          zIndex: 100,
          overflow: 'auto',
        }}
      >
        <div style={{
          height: 60,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'flex-start',
          padding: collapsed ? '0' : '0 18px',
          borderBottom: '1px solid var(--lm-border-light)',
          gap: 10,
        }}>
          <div style={{
            width: 34, height: 34, borderRadius: 10,
            background: 'linear-gradient(135deg, rgba(22,119,255,0.2) 0%, rgba(114,46,209,0.2) 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            border: '1px solid rgba(22,119,255,0.15)',
            flexShrink: 0,
          }}>
            <ThunderboltOutlined style={{ fontSize: 18, color: '#1677ff' }} />
          </div>
          {!collapsed && (
            <Text strong className="lm-gradient-text" style={{ fontSize: 17, letterSpacing: 1.5 }}>
              LogMind
            </Text>
          )}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems.map(item => item.key === '/alerts' ? {
            ...item,
            label: (
              <Space>
                {item.label}
                {alertCount > 0 && <Badge count={alertCount} size="small" offset={[0, 0]} />}
              </Space>
            ),
          } : item)}
          onClick={({ key }) => navigate(key)}
          style={{
            background: 'transparent',
            border: 'none',
            marginTop: 8,
          }}
        />

        {/* Sidebar Footer */}
        {!collapsed && (
          <div style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            padding: '12px 20px',
            borderTop: '1px solid var(--lm-border-light)',
            fontSize: 11,
            color: 'var(--lm-text-tertiary)',
          }}>
            <div>LogMind v2.5</div>
            <div style={{ opacity: 0.5 }}>AI 智能日志分析平台</div>
          </div>
        )}
      </Sider>

      <Layout style={{ marginLeft: collapsed ? 64 : 240, transition: 'margin-left 0.2s' }}>
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
            <Tooltip title="告警">
              <Badge count={alertCount} size="small" offset={[-2, 4]}>
                <BellOutlined
                  onClick={() => navigate('/alerts')}
                  style={{ fontSize: 16, color: alertCount > 0 ? '#fa8c16' : 'var(--lm-text-tertiary)', cursor: 'pointer' }}
                />
              </Badge>
            </Tooltip>
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
