import React, { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, theme, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { useAuthStore } from '@/stores/authStore';
import AppLayout from '@/components/Layout/AppLayout';
import Login from '@/pages/Login';
import Dashboard from '@/pages/Dashboard';
import TaskList from '@/pages/Analysis/TaskList';
import TaskDetail from '@/pages/Analysis/TaskDetail';
import TaskCompare from '@/pages/Analysis/TaskCompare';
import AlertList from '@/pages/Alerts/AlertList';
import LogSearch from '@/pages/Logs/LogSearch';
import BusinessLines from '@/pages/BusinessLines/LineList';
import AIInsights from '@/pages/AIInsights';
import KnowledgeBase from '@/pages/Knowledge';
import Settings from '@/pages/Settings';

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated } = useAuthStore();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
};

const App: React.FC = () => {
  const { hydrate } = useAuthStore();

  useEffect(() => { hydrate(); }, []);

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#1677ff',
          colorBgContainer: '#111827',
          colorBgLayout: '#0a0e17',
          colorBgElevated: '#1a2332',
          colorBorder: '#1e3a5f',
          colorBorderSecondary: 'rgba(255,255,255,0.06)',
          borderRadius: 8,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          Menu: {
            itemBg: 'transparent',
            itemSelectedBg: 'rgba(22, 119, 255, 0.15)',
            itemHoverBg: 'rgba(255, 255, 255, 0.04)',
            itemSelectedColor: '#1677ff',
          },
          Table: {
            headerBg: 'rgba(255,255,255,0.02)',
            rowHoverBg: 'rgba(255,255,255,0.03)',
          },
          Card: {
            headerBg: 'transparent',
          },
        },
      }}
    >
      <AntApp>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
              <Route index element={<Dashboard />} />
              <Route path="analysis" element={<TaskList />} />
              <Route path="analysis/:taskId" element={<TaskDetail />} />
              <Route path="analysis/compare" element={<TaskCompare />} />
              <Route path="alerts" element={<AlertList />} />
              <Route path="logs" element={<LogSearch />} />
              <Route path="business-lines" element={<BusinessLines />} />
              <Route path="ai-insights" element={<AIInsights />} />
              <Route path="knowledge" element={<KnowledgeBase />} />
              <Route path="settings" element={<Settings />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  );
};

export default App;
