import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, theme, App as AntApp, Spin } from 'antd';
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
import KnownIssues from '@/pages/KnownIssues';
import KnowledgeBase from '@/pages/Knowledge';
import Settings from '@/pages/Settings';
import BusinessLineDetail from '@/pages/BusinessLines/Detail';
import SLADashboard from '@/pages/Dashboard/SLADashboard';
import ChatPage from '@/pages/Chat';
import ServiceTopology from '@/pages/Topology';
import CommandCenter from '@/pages/CommandCenter';
import ErrorHeatmap from '@/pages/Dashboard/ErrorHeatmap';
import TimeTravel from '@/pages/Logs/TimeTravel';
import WeeklyReport from '@/pages/Dashboard/WeeklyReport';
import PivotTable from '@/pages/Dashboard/PivotTable';
import PatrolRadar from '@/pages/Dashboard/PatrolRadar';

// Hydrate auth synchronously on module load — before any component renders
useAuthStore.getState().hydrate();

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, isHydrated } = useAuthStore();
  // Wait for hydration before making redirect decision
  if (!isHydrated) {
    return <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}><Spin size="large" /></div>;
  }
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
};

const App: React.FC = () => {

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#1677ff',
          colorBgContainer: '#0d1220',
          colorBgLayout: '#060a13',
          colorBgElevated: '#141c2e',
          colorBorder: '#1e3a5f',
          colorBorderSecondary: 'rgba(255,255,255,0.06)',
          borderRadius: 10,
          fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          Menu: {
            itemBg: 'transparent',
            itemSelectedBg: 'rgba(22, 119, 255, 0.12)',
            itemHoverBg: 'rgba(255, 255, 255, 0.04)',
            itemSelectedColor: '#4096ff',
            itemColor: 'rgba(255,255,255,0.55)',
          },
          Table: {
            headerBg: 'rgba(255,255,255,0.02)',
            rowHoverBg: 'rgba(22, 119, 255, 0.03)',
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
              <Route path="business-lines/:id" element={<BusinessLineDetail />} />
              <Route path="ai-insights" element={<AIInsights />} />
              <Route path="known-issues" element={<KnownIssues />} />
              <Route path="sla" element={<SLADashboard />} />
              <Route path="chat" element={<ChatPage />} />
              <Route path="topology" element={<ServiceTopology />} />
              <Route path="command-center" element={<CommandCenter />} />
              <Route path="heatmap" element={<ErrorHeatmap />} />
              <Route path="time-travel" element={<TimeTravel />} />
              <Route path="weekly-report" element={<WeeklyReport />} />
              <Route path="pivot" element={<PivotTable />} />
              <Route path="patrol" element={<PatrolRadar />} />
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
