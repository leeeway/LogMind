# LogMind 开发调试与使用指南

> 最后更新: 2026-04-29 | 适用版本: LogMind v2.x

---

## 📁 项目结构

```
LogMind/
├── src/logmind/              # 后端 Python (FastAPI)
│   ├── main.py               #   应用入口 (uvicorn + FastAPI)
│   ├── core/                  #   核心模块 (配置、安全、中间件、ES、Redis)
│   ├── domain/                #   业务领域
│   │   ├── auth/              #     认证 (JWT 登录/注册)
│   │   ├── tenant/            #     租户/业务线管理
│   │   ├── log/               #     日志查询 (ES 搜索、实时 Live Tail)
│   │   ├── analysis/          #     日志分析 (AI Pipeline、Agent Tools)
│   │   ├── chat/              #     AI 诊断助手 (多轮 ReAct 对话)
│   │   ├── provider/          #     AI 模型提供商管理
│   │   ├── alert/             #     告警规则与通知
│   │   └── rag/               #     知识库 (RAG 向量检索)
│   ├── shared/                #   共享工具 (BaseRepository, 分页等)
│   └── scripts/               #   维护脚本
├── frontend/                  # 前端 React (Vite + Ant Design)
│   ├── src/
│   │   ├── pages/             #   页面组件
│   │   ├── components/        #   公共组件
│   │   ├── api/               #   API 封装 (axios)
│   │   └── stores/            #   状态管理 (zustand)
│   ├── vite.config.ts         #   Vite 配置 (端口3000, API代理→8000)
│   └── package.json
├── .env                       # 环境变量 (数据库、ES、Redis 等)
├── Makefile                   # 快捷命令
├── docker-compose.yml         # Docker 开发环境
├── deploy/                    # K8s 部署清单
└── migrations/                # Alembic 数据库迁移
```

---

## 🚀 本地开发启动（完整流程）

### 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.12+ | 后端运行时 |
| Node.js | 18+ | 前端构建 |
| PostgreSQL | 15+ | 主数据库（远程 10.12.54.31:31005）|
| Elasticsearch | 8.x | 日志存储（远程 10.14.3.101:9200）|
| Redis | 7+ | 缓存 + Celery Broker（远程 10.12.55.40:30002）|

> ⚠️ 数据库、ES、Redis 已部署在远程服务器，本地开发不需要安装这些服务。

### Step 1: 安装依赖（首次）

```bash
# 后端
cd /Users/leeway/github/LogMind
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 前端
cd frontend
npm install
```

### Step 2: 启动后端（二选一）

#### 方式 A：PyCharm 调试启动（推荐）

1. 用 PyCharm 打开项目 `/Users/leeway/github/LogMind`
2. 配置 Python Interpreter：选择 `.venv/bin/python`
3. 新建 **Run/Debug Configuration**：

   ```
   类型:           Python
   脚本路径:       /Users/leeway/github/LogMind/src/logmind/main.py
   工作目录:       /Users/leeway/github/LogMind
   Python 解释器:  .venv/bin/python
   环境变量:       PYTHONPATH=/Users/leeway/github/LogMind/src
   ```

   > **关键**: `PYTHONPATH` 必须设为 `src` 目录，否则 `import logmind` 找不到模块

4. 点击 **Debug (🐞)** 按钮启动
5. 控制台看到 `Application startup complete.` 即成功
6. 后端地址: `http://127.0.0.1:8000`

**调试技巧**:
- 在 `src/logmind/domain/chat/service.py` 的 `execute_tool_call()` 方法打断点，可拦截 AI 工具调用
- 在 `src/logmind/domain/log/service.py` 的 `search_logs()` 方法打断点，可查看 ES 查询 body
- 在 `src/logmind/domain/analysis/agent_tools.py` 的 `_exec_search_logs()` 打断点，可查看工具返回结果

#### 方式 B：命令行启动

```bash
cd /Users/leeway/github/LogMind

# 方法1: Makefile
make run

# 方法2: 直接 uvicorn
PYTHONPATH=src .venv/bin/python src/logmind/main.py

# 方法3: uvicorn 命令
PYTHONPATH=src uvicorn logmind.main:app --host 127.0.0.1 --port 8000 --reload --app-dir src
```

### Step 3: 启动前端

```bash
cd /Users/leeway/github/LogMind/frontend
npm run dev
```

输出：
```
VITE v8.0.10  ready in 365 ms
  ➜  Local:   http://localhost:3000/
```

> 前端 Vite dev server 会自动将 `/api/*` 请求代理到后端 `http://localhost:8000`

### Step 4: 访问应用

| 地址 | 说明 |
|------|------|
| `http://localhost:3000` | 前端页面（开发模式，HMR 热更新）|
| `http://127.0.0.1:8000/docs` | 后端 Swagger API 文档 |
| `http://127.0.0.1:8000/api/v1/health` | 健康检查 |

### Step 5: 登录账号

- 默认管理员: `admin` / 对应密码
- 首次使用需在「设置 → 业务线管理」中配置 ES 索引模式

---

## 🔧 可选服务

### Celery Worker（后台任务，如深度分析）

```bash
cd /Users/leeway/github/LogMind
PYTHONPATH=src .venv/bin/celery -A logmind.core.celery_app worker \
  --loglevel=info --concurrency=4 -Q celery,analysis,alert,rag
```

### Celery Beat（定时任务调度）

```bash
cd /Users/leeway/github/LogMind
PYTHONPATH=src .venv/bin/celery -A logmind.core.celery_app beat --loglevel=info
```

> 如果不启动 Celery，AI 诊断助手的实时工具调用仍正常工作（在 FastAPI 进程内执行）。Celery 仅用于后台深度分析任务。

---

## 🤖 AI 诊断助手使用指南

### 入口
侧边栏 → **AI 诊断** 或 总览页的快捷入口

### 提问技巧

| 问法 | 效果 |
|------|------|
| ✅ `login 站点最近有没有报错` | AI 会自动匹配业务线、查 ES |
| ✅ `帮我分析 auth-service 超时问题` | AI 多轮查询，关联上下游 |
| ✅ `最近1小时有哪些关键错误？` | 全局扫描 |
| ❌ `系统怎么样？` | 太模糊，结果不精确 |

### AI 推理流程 (ReAct)

```
第1轮 侦察 → get_service_health / search_logs
第2轮 聚焦 → 缩小范围，搜索具体关键词
第3轮 关联 → 检查上下游服务
第4轮 验证 → 查知识库 / 历史故障
第5轮 兜底 → 创建深度分析任务
```

最多 5 轮（可配置），信息足够时 AI 会提前结束。

### 对话操作

| 操作 | 说明 |
|------|------|
| 📋 复制 | 用户消息和 AI 回答都有复制按钮 |
| 🔄 重新提问 | 用户消息右下角「重新提问」按钮，一键重发 |
| 📤 导出报告 | 右上角导出按钮，复制整个对话为 Markdown |
| ➕ 新建会话 | 左上角「新建对话」，每个账号独立会话 |

---

## ⚠️ 常见问题排查

### 1. `Address already in use` (端口占用)

```bash
# 查看占用端口8000的进程
lsof -ti:8000

# 杀掉并释放
lsof -ti:8000 | xargs kill -9

# 同理前端端口3000
lsof -ti:3000 | xargs kill -9
```

### 2. 前端 3000 端口访问拒绝

前端 Vite dev server 需要单独启动：

```bash
cd /Users/leeway/github/LogMind/frontend && npm run dev
```

### 3. `ModuleNotFoundError: No module named 'logmind'`

未设置 PYTHONPATH：

```bash
# 命令行启动
export PYTHONPATH=/Users/leeway/github/LogMind/src

# PyCharm: Run Configuration → Environment Variables → PYTHONPATH=src目录绝对路径
```

### 4. AI 诊断搜不到日志

**检查清单**:
- [ ] 业务线配置了正确的 ES 索引模式？（设置 → 业务线管理）
- [ ] ES 服务可达？`curl http://10.14.3.101:9200/_cluster/health`
- [ ] 后端重启了吗？代码改动需要重启后端才生效（PyCharm Debug 模式有 reload）

**手动验证 ES 搜索**:
```bash
cd /Users/leeway/github/LogMind
PYTHONPATH=src .venv/bin/python -c "
import asyncio
from datetime import datetime, timedelta, timezone
from logmind.domain.log.service import log_service
from logmind.domain.log.schemas import LogQueryRequest

async def test():
    beijing = timezone(timedelta(hours=8))
    now = datetime.now(beijing)
    req = LogQueryRequest(
        index_pattern='master-stage-account-login-service.gyyx.cn*',
        time_from=now - timedelta(hours=6),
        time_to=now,
        query='你的搜索关键词',
        size=5,
    )
    result = await log_service.search_logs(req)
    print(f'Total: {result.total}')
    for l in result.logs[:3]:
        print(f'  [{l.timestamp}] {l.message[:100]}')

asyncio.run(test())
"
```

### 5. 数据库连接失败

检查 `.env` 中 `DATABASE_URL` 是否可达：

```bash
# 测试 PostgreSQL 连接
pg_isready -h 10.12.54.31 -p 31005

# 测试 Redis 连接
redis-cli -h 10.12.55.40 -p 30002 -a Passw0rd ping
```

### 6. `logmind-learned-signals` 403 错误

这是 ES 权限问题，不影响核心功能。系统会自动降级使用静态信号列表。  
如需修复：联系 ES 管理员给 `dev_read` 用户添加 `logmind-learned-signals` 索引的读写权限。

---

## 🏗️ 构建与部署

### 前端构建

```bash
cd /Users/leeway/github/LogMind/frontend
npm run build
# 产物: frontend/dist/
```

### Docker 镜像

```bash
cd /Users/leeway/github/LogMind
docker build -t logmind:latest .
```

### K8s 部署

```bash
# 部署配置在 deploy/ 目录
kubectl apply -f deploy/
```

---

## 📋 快捷命令速查

```bash
# ── 日常开发 ──────────────────────────────
make run                    # 启动后端 (命令行)
cd frontend && npm run dev  # 启动前端

# ── 代码质量 ──────────────────────────────
make lint                   # 检查代码
make format                 # 自动格式化
make test                   # 运行测试

# ── 数据库 ────────────────────────────────
make migrate                # 执行迁移
make migrate-create msg="xxx"  # 创建迁移

# ── 端口清理 ──────────────────────────────
lsof -ti:8000 | xargs kill -9  # 释放后端端口
lsof -ti:3000 | xargs kill -9  # 释放前端端口

# ── Docker ────────────────────────────────
make docker-up              # 启动开发环境
make docker-down            # 停止开发环境
make docker-build           # 构建镜像
```

---

## 🔑 关键配置文件

| 文件 | 说明 |
|------|------|
| `.env` | 数据库、ES、Redis、AI 配置 |
| `frontend/vite.config.ts` | 前端端口(3000)、API 代理(→8000) |
| `src/logmind/main.py` | 后端入口，端口 8000 |
| `src/logmind/core/config.py` | 全局配置类 (读取 .env) |
| `deploy/` | K8s 部署文件 |
