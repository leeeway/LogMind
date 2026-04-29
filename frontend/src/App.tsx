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
import LiveTail from '@/pages/Logs/LiveTail';
import IncidentList from '@/pages/Incidents/IncidentList';
import WarRoom from '@/pages/Incidents/WarRoom';
import DashboardBuilder from '@/pages/Dashboard/DashboardBuilder';
import RootCauseGraph from '@/pages/Analysis/RootCauseGraph';
import DailyStandup from '@/pages/Dashboard/DailyStandup';
import ChangeTimeline from '@/pages/Changes/ChangeTimeline';
import OnCallSchedule from '@/pages/OnCall/Schedule';
import ErrorDNA from '@/pages/Analysis/ErrorDNA';
import AlertFatigue from '@/pages/Dashboard/AlertFatigue';
import CorrelationMatrix from '@/pages/Dashboard/CorrelationMatrix';
import MTTRHealth from '@/pages/Dashboard/MTTRHealth';
import HealthScorecard from '@/pages/Dashboard/HealthScorecard';
import CostIntelligence from '@/pages/Dashboard/CostIntelligence';
import Playbooks from '@/pages/Analysis/Playbooks';
import OpsEfficiency from '@/pages/Dashboard/OpsEfficiency';
import { QuickDiagnoseProvider } from '@/components/QuickDiagnose';

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
  const [currentTheme, setCurrentTheme] = React.useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('lm-theme') as 'dark' | 'light') || 'dark';
  });

  // Listen for theme changes from AppLayout's useTheme hook
  React.useEffect(() => {
    const observer = new MutationObserver(() => {
      const t = document.documentElement.getAttribute('data-theme') as 'dark' | 'light';
      if (t && t !== currentTheme) setCurrentTheme(t);
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, [currentTheme]);

  const isDark = currentTheme === 'dark';

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: isDark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: '#1677ff',
          colorBgContainer: isDark ? '#0d1220' : '#ffffff',
          colorBgLayout: isDark ? '#060a13' : '#f0f2f5',
          colorBgElevated: isDark ? '#141c2e' : '#ffffff',
          colorBorder: isDark ? '#1e3a5f' : '#d9d9d9',
          colorBorderSecondary: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
          borderRadius: 10,
          fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          Menu: {
            itemBg: 'transparent',
            itemSelectedBg: isDark ? 'rgba(22, 119, 255, 0.12)' : 'rgba(22, 119, 255, 0.08)',
            itemHoverBg: isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.03)',
            itemSelectedColor: '#4096ff',
            itemColor: isDark ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.65)',
          },
          Table: {
            headerBg: isDark ? 'rgba(255,255,255,0.02)' : 'rgba(0,0,0,0.02)',
            rowHoverBg: isDark ? 'rgba(22, 119, 255, 0.03)' : 'rgba(22, 119, 255, 0.04)',
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
            <Route path="/" element={<ProtectedRoute><QuickDiagnoseProvider><AppLayout /></QuickDiagnoseProvider></ProtectedRoute>}>
              <Route index element={<Dashboard />} />
              <Route path="analysis" element={<TaskList />} />
              <Route path="analysis/:taskId" element={<TaskDetail />} />
              <Route path="analysis/compare" element={<TaskCompare />} />
              <Route path="analysis/rootcause" element={<RootCauseGraph />} />
              <Route path="alerts" element={<AlertList />} />
              <Route path="logs" element={<LogSearch />} />
              <Route path="live-tail" element={<LiveTail />} />
              <Route path="incidents" element={<IncidentList />} />
              <Route path="incidents/:id" element={<WarRoom />} />
              <Route path="dashboard-builder" element={<DashboardBuilder />} />
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
              <Route path="daily-standup" element={<DailyStandup />} />
              <Route path="changes" element={<ChangeTimeline />} />
              <Route path="oncall" element={<OnCallSchedule />} />
              <Route path="error-dna" element={<ErrorDNA />} />
              <Route path="alert-fatigue" element={<AlertFatigue />} />
              <Route path="correlation" element={<CorrelationMatrix />} />
              <Route path="mttr" element={<MTTRHealth />} />
              <Route path="health-scores" element={<HealthScorecard />} />
              <Route path="ai-cost" element={<CostIntelligence />} />
              <Route path="playbooks" element={<Playbooks />} />
              <Route path="ops-efficiency" element={<OpsEfficiency />} />
              <Route path="settings" element={<Settings />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  );
};

export default App;
