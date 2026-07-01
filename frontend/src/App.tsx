import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, theme, App as AntApp, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { useAuthStore } from '@/stores/authStore';
import AppLayout from '@/components/Layout/AppLayout';
import { QuickDiagnoseProvider } from '@/components/QuickDiagnose';

// Hydrate auth synchronously on module load — before any component renders
useAuthStore.getState().hydrate();

const Login = React.lazy(() => import('@/pages/Login'));
const Dashboard = React.lazy(() => import('@/pages/Dashboard'));
const TaskList = React.lazy(() => import('@/pages/Analysis/TaskList'));
const TaskDetail = React.lazy(() => import('@/pages/Analysis/TaskDetail'));
const TaskCompare = React.lazy(() => import('@/pages/Analysis/TaskCompare'));
const AlertList = React.lazy(() => import('@/pages/Alerts/AlertList'));
const LogSearch = React.lazy(() => import('@/pages/Logs/LogSearch'));
const BusinessLines = React.lazy(() => import('@/pages/BusinessLines/LineList'));
const AIInsights = React.lazy(() => import('@/pages/AIInsights'));
const KnownIssues = React.lazy(() => import('@/pages/KnownIssues'));
const KnowledgeBase = React.lazy(() => import('@/pages/Knowledge'));
const Settings = React.lazy(() => import('@/pages/Settings'));
const BusinessLineDetail = React.lazy(() => import('@/pages/BusinessLines/Detail'));
const SLADashboard = React.lazy(() => import('@/pages/Dashboard/SLADashboard'));
const ChatPage = React.lazy(() => import('@/pages/Chat'));
const ServiceTopology = React.lazy(() => import('@/pages/Topology'));
const CommandCenter = React.lazy(() => import('@/pages/CommandCenter'));
const ErrorHeatmap = React.lazy(() => import('@/pages/Dashboard/ErrorHeatmap'));
const TimeTravel = React.lazy(() => import('@/pages/Logs/TimeTravel'));
const WeeklyReport = React.lazy(() => import('@/pages/Dashboard/WeeklyReport'));
const PivotTable = React.lazy(() => import('@/pages/Dashboard/PivotTable'));
const PatrolRadar = React.lazy(() => import('@/pages/Dashboard/PatrolRadar'));
const LiveTail = React.lazy(() => import('@/pages/Logs/LiveTail'));
const IncidentList = React.lazy(() => import('@/pages/Incidents/IncidentList'));
const WarRoom = React.lazy(() => import('@/pages/Incidents/WarRoom'));
const DashboardBuilder = React.lazy(() => import('@/pages/Dashboard/DashboardBuilder'));
const RootCauseGraph = React.lazy(() => import('@/pages/Analysis/RootCauseGraph'));
const DailyStandup = React.lazy(() => import('@/pages/Dashboard/DailyStandup'));
const ChangeTimeline = React.lazy(() => import('@/pages/Changes/ChangeTimeline'));
const OnCallSchedule = React.lazy(() => import('@/pages/OnCall/Schedule'));
const ErrorDNA = React.lazy(() => import('@/pages/Analysis/ErrorDNA'));
const AlertFatigue = React.lazy(() => import('@/pages/Dashboard/AlertFatigue'));
const CorrelationMatrix = React.lazy(() => import('@/pages/Dashboard/CorrelationMatrix'));
const MTTRHealth = React.lazy(() => import('@/pages/Dashboard/MTTRHealth'));
const HealthScorecard = React.lazy(() => import('@/pages/Dashboard/HealthScorecard'));
const CostIntelligence = React.lazy(() => import('@/pages/Dashboard/CostIntelligence'));
const Playbooks = React.lazy(() => import('@/pages/Analysis/Playbooks'));
const OpsEfficiency = React.lazy(() => import('@/pages/Dashboard/OpsEfficiency'));

const FullPageSpin: React.FC = () => (
  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
    <Spin size="large" />
  </div>
);

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, isHydrated } = useAuthStore();
  // Wait for hydration before making redirect decision
  if (!isHydrated) {
    return <FullPageSpin />;
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
          <React.Suspense fallback={<FullPageSpin />}>
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
          </React.Suspense>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  );
};

export default App;
