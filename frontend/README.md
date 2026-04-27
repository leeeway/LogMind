<div align="center">
  <h1>⚡ LogMind Web Dashboard</h1>
  <p><b>AI 智能日志分析平台 — 运维管理控制台</b></p>
  <p>
    <img src="https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react" />
    <img src="https://img.shields.io/badge/TypeScript-5.x-3178C6?style=flat-square&logo=typescript" />
    <img src="https://img.shields.io/badge/Vite-6.x-646CFF?style=flat-square&logo=vite" />
    <img src="https://img.shields.io/badge/Ant_Design-5.x-0170FE?style=flat-square&logo=antdesign" />
  </p>
</div>

---

## 📸 界面预览

| 功能 | 说明 |
|------|------|
| 🔐 登录页 | Canvas 粒子动画 + 毛玻璃卡片 + 渐变品牌 |
| 📊 运维总览 | 6 KPI 卡片 + 多系列趋势图 + 服务健康矩阵 |
| 🔍 日志搜索 | 快速时间预设 + 关键词高亮 + 行复制/导出 |
| 📋 分析中心 | 任务列表 + 详情页 + 两次分析对比 Diff |
| 🔔 告警管理 | 状态筛选 + ACK/Resolve + 活跃脉冲动画 |
| 🏢 服务管理 | 语言/权重/DAU + AI Switch + 夜间策略 |
| 🤖 AI 洞察 | AI 效果追踪 + Agent 工具策略分析 |
| 📚 知识库 | CRUD 管理 + 文档上传 + 嵌入模型配置 |
| ⚙️ 系统设置 | Provider 管理 + Prompt 模板 + 系统健康 |

---

## 🛠 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| **React** | 18 | UI 框架 |
| **TypeScript** | 5.x | 类型安全 |
| **Vite** | 6.x | 构建工具，HMR 开发 |
| **Ant Design** | 5.x | UI 组件库（暗色主题） |
| **@ant-design/charts** | 2.x | 图表（Line / Pie / Column） |
| **Zustand** | 5.x | 轻量状态管理（Auth Store） |
| **Axios** | 1.x | HTTP 客户端 + JWT 拦截器 |
| **React Router** | 7.x | 路由管理 |
| **Day.js** | 1.x | 时间处理 |
| **Google Fonts (Inter)** | — | 专业字体 |

---

## 🚀 快速开始

### 环境要求

- **Node.js** ≥ 18
- **npm** ≥ 9

### 启动开发服务器

```bash
# 安装依赖
npm install

# 启动 (默认端口 3000，代理 API 到 localhost:8000)
npm run dev
```

> 开发模式下前端运行在 `http://localhost:3000`，API 请求自动代理到后端 `http://localhost:8000`。

### 生产构建

```bash
npm run build    # 输出到 dist/
npm run preview  # 本地预览 production build
```

> **注意**：`dist/` 已添加到 `.gitignore`。生产环境通过 Dockerfile 多阶段构建自动处理。

---

## 📁 项目结构

```
frontend/
├── index.html                 # 入口 HTML (含 Google Fonts)
├── vite.config.ts             # Vite 配置 (含 API 代理)
├── tsconfig.app.json          # TypeScript 配置
├── package.json
├── public/                    # 静态资源
└── src/
    ├── main.tsx               # React 入口
    ├── App.tsx                # 路由 + AntD Theme + ProtectedRoute
    ├── api/                   # API 层
    │   ├── client.ts          # Axios 实例 + JWT 拦截器 + 401 处理
    │   ├── auth.ts            # 登录 API
    │   ├── alerts.ts          # 告警 API
    │   ├── dashboard.ts       # Dashboard 多端点聚合
    │   └── services.ts        # 日志/业务线/Provider/Prompt/RAG API
    ├── stores/
    │   └── authStore.ts       # Zustand Auth Store (含同步 hydrate)
    ├── hooks/
    │   └── usePolling.ts      # 自动轮询 Hook (含 Tab 可见性感知)
    ├── components/
    │   ├── ErrorBoundary.tsx   # 路由感知错误边界 (resetKey)
    │   ├── RefreshIndicator.tsx # 倒计时自动刷新指示器
    │   └── Layout/
    │       └── AppLayout.tsx  # 主布局 (Sidebar + Header + Breadcrumb)
    ├── pages/
    │   ├── Login.tsx          # 登录 (粒子动画 + 毛玻璃)
    │   ├── Dashboard/         # 运维总览 (KPI + 趋势 + 健康 + 最近任务)
    │   ├── Analysis/          # 分析中心 (TaskList / TaskDetail / TaskCompare)
    │   ├── Alerts/            # 告警管理 (记录 + 规则 + 状态筛选)
    │   ├── Logs/              # 日志搜索 (快速时间 + 高亮 + 导出)
    │   ├── BusinessLines/     # 服务管理 (完整 CRUD + AI Switch)
    │   ├── AIInsights/        # AI 洞察 (效果追踪 + 工具分析)
    │   ├── Knowledge/         # 知识库管理
    │   └── Settings/          # 系统设置 (Provider + Prompt + 健康)
    └── styles/
        └── global.css         # 全局样式 (Glassmorphism + 动画 + 主题)
```

---

## 🎨 设计系统

### 色彩方案

| Token | 色值 | 用途 |
|-------|------|------|
| `--lm-bg-layout` | `#060a13` | 页面背景 |
| `--lm-bg-card` | `rgba(20,28,46,0.8)` | 卡片背景 (含毛玻璃) |
| `--lm-primary` | `#1677ff` | 主色 |
| `--lm-accent` | `#722ed1` | 强调色 |
| `--lm-gradient-primary` | `#1677ff → #722ed1` | 渐变主色 (按钮/Logo) |
| `--lm-critical` | `#ff4d4f` | 错误/P0 |
| `--lm-warning` | `#faad14` | 警告/P1 |
| `--lm-success` | `#52c41a` | 成功/健康 |

### 视觉特性

- **Glassmorphism** — 卡片毛玻璃 `backdrop-filter: blur(12px)`
- **Gradient Borders** — 登录卡片顶部渐变边框
- **Ambient Glow** — 内容区域呼吸式背景光晕
- **Particle Animation** — 登录页 Canvas 粒子连线
- **Micro-animations** — KPI 卡片上浮、告警脉冲、数字滚动
- **Gradient Buttons** — 主按钮蓝→紫渐变 + 发光阴影
- **Sidebar Indicator** — 选中菜单项左侧渐变指示条

### 交互增强

- ⌘+K 全局日志搜索
- ⌘+Enter 快速执行搜索
- Tab 可见性感知自动轮询
- ErrorBoundary 路由感知重置
- 刷新页面不丢失登录态

---

## 🔗 后端 API 代理

开发模式下 Vite 配置了反向代理：

```ts
// vite.config.ts
server: {
  port: 3000,
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
    }
  }
}
```

生产环境通过 `LOGMIND_SERVE_FRONTEND=1` 环境变量启用 FastAPI SPA 服务。

---

## 🐳 Docker 部署

前端在 Dockerfile 中通过多阶段构建自动集成：

```dockerfile
# Stage 1: Build frontend
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.13-slim
# ...
COPY --from=frontend /frontend/dist ./frontend/dist/
ENV LOGMIND_SERVE_FRONTEND=1
```

---

## 📝 开发注意事项

1. **不要提交 `dist/`** — 已在 `.gitignore` 中排除
2. **本地开发时确保后端在 8000 端口运行** — `uvicorn logmind.main:app --reload`
3. **API 字段名以后端实际返回为准** — 使用 Swagger (`/docs`) 确认
4. **AntD 5 暗色主题** — 通过 `theme.darkAlgorithm` 全局配置，组件级覆盖在 `App.tsx`
5. **Global CSS 优先级** — `global.css` 使用 `!important` 覆盖 AntD 默认样式
