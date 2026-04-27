import React, { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Avatar, Dropdown, Typography, Space } from 'antd';
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
} from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';

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

const AppLayout: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();

  const selectedKey = '/' + (location.pathname.split('/')[1] || '');

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
          height: 56,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'flex-start',
          padding: collapsed ? '0' : '0 20px',
          borderBottom: '1px solid var(--lm-border-light)',
        }}>
          <ThunderboltOutlined style={{ fontSize: 22, color: 'var(--lm-primary)' }} />
          {!collapsed && (
            <Text strong style={{ fontSize: 18, marginLeft: 10, color: 'var(--lm-text)', letterSpacing: 1 }}>
              LogMind
            </Text>
          )}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{
            background: 'transparent',
            border: 'none',
            marginTop: 8,
          }}
        />
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
          <div
            onClick={() => setCollapsed(!collapsed)}
            style={{ cursor: 'pointer', fontSize: 18, color: 'var(--lm-text-secondary)' }}
          >
            {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          </div>

          <Dropdown menu={{ items: userMenuItems, onClick: handleUserMenu }} placement="bottomRight">
            <Space style={{ cursor: 'pointer' }}>
              <Avatar size={32} icon={<UserOutlined />} style={{ background: 'var(--lm-primary)' }} />
              <Text style={{ color: 'var(--lm-text-secondary)' }}>{user?.username}</Text>
            </Space>
          </Dropdown>
        </Header>

        <Content style={{
          padding: 24,
          minHeight: 'calc(100vh - 56px)',
          background: 'var(--lm-bg-layout)',
        }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
};

export default AppLayout;
