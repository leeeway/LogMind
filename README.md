<div align="center">
  <br/>
  <h1>🧠 LogMind</h1>
  <p><b>智能日志分析与告警平台</b></p>
  <p>AI-Powered Log Analysis & Alert Platform for Cloud-Native and Hybrid Infrastructure</p>

  <p>
    <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.13-blue.svg?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13" /></a>
    <a href="https://fastapi.tiangolo.com"><img src="https://img.shields.io/badge/FastAPI-0.111-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" /></a>
    <img src="https://img.shields.io/badge/Elasticsearch-8.x-yellow?style=for-the-badge&logo=elasticsearch" alt="ES" />
    <img src="https://img.shields.io/badge/Celery-Distributed-lightgreen.svg?style=for-the-badge" alt="Celery" />
    <img src="https://img.shields.io/badge/Docker-Ready-2496ED.svg?style=for-the-badge&logo=docker" alt="Docker" />
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-purple.svg?style=for-the-badge" alt="License MIT" /></a>
  </p>
</div>

<br/>

> LogMind 对接企业已有的 ELK 日志基础设施，通过 AI 大模型自动识别错误模式、追踪异常根因、生成修复建议，并将告警推送至企业微信 / 钉钉 / 飞书。  
> 支持 **Java (K8s)** 和 **C# (.NET/VM)** 混合架构，提供灵活的 **AI 开关**：开启时进行深度智能分析，关闭时自动降级为轻量化异常通知。  
> 内置 **AI Agent 自主推理**：AI 可主动调用 ES 工具进行多步查询，像真正的 SRE 一样逐步缩小排查范围，而不仅仅是分析一次性日志快照。  
> 内置 **三层智能去重**：MD5 指纹 → 向量语义匹配 → 分析记忆闭环，最大程度减少重复 LLM 调用，节省 40-60% Token 成本。

---

### 📑 目录

- [核心能力](#-核心能力)
- [架构设计](#-架构设计)
- [功能矩阵](#-功能矩阵)
- [成本控制配置](#-成本控制配置)
- [快速开始](#-快速开始)
- [业务线配置指南](#-业务线配置指南)
- [通知模板说明](#-通知模板说明)
- [API 接口参考](#-api-接口参考)
- [项目结构](#-项目结构)
- [未来路标](#-未来路标)
- [参与贡献](#-参与贡献)
- [开源协议](#-开源协议)

---

## ✨ 核心能力

### 🔌 无缝对接企业 ELK

直接读取已有 Elasticsearch 中的 Filebeat 日志，无需改造日志采集链路。支持 Data Stream 索引（`.ds-*`）和传统索引。

### 🤖 AI 大模型弹性分析

- 开箱即用对接 OpenAI / Claude / Gemini / DeepSeek / 内网私有模型
- **AI 开关**：按业务线独立控制，关闭即零 Token 消耗
- AI 异常自动降级：模型故障时自动切换为原始日志通知，告警不丢失

### 🧠 AI Agent 自主推理（多步工具调用）

区别于传统「一次性 Prompt-Response」模式，LogMind 内置 Agent 推理循环。AI 拥有主动查询能力，可在一次分析中发起多步工具调用：

| Agent Tool | 作用 |
|-----------|------|
| `search_logs` | 自由构造条件搜索更多日志（关键词、级别、域名、时间段） |
| `get_log_context` | 查看某个时间点前后 N 分钟的完整上下文 |
| `count_error_patterns` | 按异常类型 / 域名 / 时间段聚合统计错误频率 |
| `list_available_indices` | 发现其他相关服务的 ES 索引 |
| `search_knowledge_base`| 根据相关性智能检索内部知识库、SOP 和历史故障处理手册 |
| `search_similar_incidents`| 🆕 搜索历史上语义相似的 AI 分析记录，参考过去的根因结论 |
| `search_cross_service_logs`| 🆕 跨业务线搜索其他服务的错误日志，关联上下游故障 |

**典型推理链**：  
发现大量连接超时 → 调用 `search_similar_incidents` 查看历史是否有相似事件 → 调用 `search_cross_service_logs` 检查上游服务是否有异常 → 调用 `count_error_patterns` 确认频率趋势 → 调用 `search_knowledge_base` 查阅 SOP → 给出根因结论和修复建议。

### 🧬 三层智能去重 (Token 节省)

LogMind 通过三层递进式去重机制，最大程度减少对同类错误的重复 AI 分析：

```
                          ┌─────────────────────┐
    新错误日志 ──────────▶│  Layer 1: MD5 指纹   │  字面完全相同 → 跳过 (零成本)
                          │  Redis 缓存, TTL=6h  │
                          └────────┬────────────┘
                                   │ 未命中
                          ┌────────▼────────────┐
                          │ Layer 2: 向量语义匹配 │  堆栈相似 → 复用历史结论 (跳过 LLM)
                          │ ES KNN, cosine>0.92  │
                          └────────┬────────────┘
                                   │ 未命中
                          ┌────────▼────────────┐
                          │ Layer 3: Agent 分析   │  全新错误 → 完整 AI 推理
                          │ 分析后自动向量化回写   │  ──▶ 构建"分析记忆"供下次命中
                          └─────────────────────┘
```

| 层级 | 机制 | 成本 | 精度 |
|------|------|------|------|
| **Layer 1** | Redis MD5 指纹缓存 | 零 API 调用 | 仅匹配完全相同 |
| **Layer 2** | ES 向量 KNN 搜索 | 1 次 Embedding | 语义相似即命中 |
| **Layer 3** | 完整 Agent 推理 | 完整 LLM 调用 | 最高精度 |

> **实测效果**：在有历史分析积累的场景下，可减少 **40-60%** 重复 LLM 调用。Embedding 结果 Redis 缓存后，向量搜索的 API 成本也大幅降低。

### 📚 RAG 知识库

内置知识库管理，支持上传 SOP 文档、历史故障报告、排查手册等。文档自动分块、向量化，存储在 ES 8.x `dense_vector` 索引中。Agent 可在分析过程中按需检索相关知识。

- **完整 CRUD API**：创建知识库 → 上传文档 → 异步索引 → Agent 检索
- **ES 原生向量存储**：无需外部向量数据库
- **Embedding 缓存**：热点查询 Redis 缓存，减少 API 调用

### 🌐 多语言日志智能解析

| 语言 | 日志级别提取 | 堆栈检测 | 部署环境 |
|------|------------|---------|---------| 
| **Java** | `gy.filetype` 映射 (error.log / info.log) | `at pkg.Class(File.java:123)` + `Caused by:` | K8s Pod |
| **C#** | message NLog 正则 (`时间 [线程] ERROR 类名`) | `at NS.Class() in File.cs:line N` | Windows VM |
| **Python** | message 关键词 | `Traceback` + `File "xxx", line N` | 通用 |
| **Go** | message 关键词 | `goroutine` + `panic` | K8s / VM |

### 📨 模板化 Webhook 通知

三种告警模板自动匹配场景，支持企业微信 / 钉钉 / 飞书 webhook 自动适配：

| 模板 | 触发场景 | 包含信息 |
|------|---------|---------| 
| ⚠️ 日志异常告警 | AI 关闭，检测到错误日志 | 业务线、站点、环境、语言、日志数、异常摘要 |
| 🔴 AI 分析告警 | AI 分析发现 Critical 问题 | 告警级别、AI 结论、影响范围、任务 ID |
| 🛑 AI 流程异常 | AI 模型调用失败 | 错误信息、故障原因 + 降级通知 |

### 🏢 企业级多租户

天然基于 **租户 → 业务线** 层级隔离。每个业务线独立配置 ES 索引、开发语言、AI 开关、webhook 地址、告警阈值。

### ⚡ 多业务线并行巡检

采用 **Fan-out 调度模式**：Celery Beat 触发调度器 → 为每个业务线创建独立 Worker 任务 → 真正并行执行。  
**单个业务线巡检失败不影响其他**，线性扩展 Worker 数量即可支撑更大规模。

---

## 🏗 架构设计

### 系统架构

```mermaid
graph TD
    subgraph 日志采集层
        A["Java 服务 (K8s)"] -->|Filebeat 8.x| ES[(ES 集群)]
        B["C# 服务 (VM)"] -->|Filebeat 8.x| ES
    end

    subgraph LogMind 平台
        API[FastAPI 接口层] -->|配置管理| DB[(PostgreSQL)]
        Beat[Celery Beat] -->|Fan-out 调度| Worker[Celery Workers]
        Worker -->|查询日志| ES
        Worker -->|向量检索| ES
        Worker -->|指纹缓存 + Embedding 缓存| Redis[(Redis)]
    end

    subgraph AI 模型层
        Worker -->|"ai_enabled=true"| LLM((LLM Provider))
        LLM -->|分析结果| Worker
    end

    subgraph 知识层
        Worker -->|RAG 检索| KB[知识库向量索引]
        Worker -->|分析记忆| AV[分析历史向量索引]
    end

    subgraph 通知层
        Worker -->|"企业微信 / 钉钉 / 飞书"| Notify[📱 开发者]
    end

    Worker -->|结果归档| DB
```

### 分析流程

```mermaid
sequenceDiagram
    participant Beat as Celery Beat
    participant Dispatcher as Patrol 调度器
    participant Worker as Worker (per 业务线)
    participant Redis as Redis
    participant ES as Elasticsearch
    participant LLM as AI Agent
    participant WH as Webhook

    Beat->>Dispatcher: scheduled_log_patrol()
    Dispatcher->>Dispatcher: 查询活跃业务线 & 冷却检查
    loop 每个合格业务线
        Dispatcher->>Worker: patrol_single_business_line(biz_id)
    end

    Worker->>ES: 查询 ERROR 日志
    ES-->>Worker: 返回日志数据

    Worker->>Worker: 预处理 (去重 + 堆栈合并)

    Worker->>Redis: Layer 1: MD5 指纹查询
    Redis-->>Worker: 已见/未见

    alt 全部指纹已见
        Worker-->>Beat: ✅ 跳过 (token_usage=0)
    else 存在新错误模式
        Worker->>Redis: 写入新指纹 (TTL=6h)

        Worker->>ES: Layer 2: 向量语义去重 (KNN)
        ES-->>Worker: 历史分析匹配结果

        alt 语义去重命中 (cosine>0.92)
            Worker-->>Worker: 复用历史结论，跳过 LLM
        else 全新错误模式
            alt ai_enabled = true
                loop Agent 推理循环 (最多 5 步)
                    Worker->>LLM: 消息 + 7 个可用工具
                    alt AI 需要更多信息
                        LLM-->>Worker: tool_calls
                        Worker->>ES: 执行工具查询
                        ES-->>Worker: 查询结果
                    else AI 完成推理
                        LLM-->>Worker: JSON 分析结论
                    end
                end
                Worker->>ES: 异步向量化回写 (构建分析记忆)
            end
        end

        Worker->>Worker: 持久化分析结果
        opt 发现 Critical/Warning
            Worker->>WH: 🔴 告警通知
        end
    end
```

---

## 📋 功能矩阵

| 模块 | 功能 | 状态 |
|------|------|------|
| **日志接入** | Filebeat → ES 日志读取 | ✅ |
| | Data Stream 索引 (`.ds-*`) 支持 | ✅ |
| | 自定义 ES 索引模式 | ✅ |
| **日志解析** | Java `gy.filetype` 级别映射 | ✅ |
| | C# NLog/log4net 级别解析 | ✅ |
| | Java 堆栈异常合并 | ✅ |
| | C# .NET 堆栈异常合并 | ✅ |
| | Filebeat multiline 感知 | ✅ |
| **AI 分析** | 多模型支持 (OpenAI/Claude/DeepSeek...) | ✅ |
| | 配置化 Prompt 模板 (YAML + DB) | ✅ |
| | Java / C# 双语言堆栈分析 Prompt | ✅ |
| | 业务线级 AI 开关 | ✅ |
| | AI 失败降级通知 | ✅ |
| | AI Agent 多步推理 (Function Calling) | ✅ |
| | Agent ES 工具 (7 个工具) | ✅ |
| | 🆕 跨业务线关联分析 | ✅ |
| **智能去重** | Layer 1: Redis MD5 错误指纹 | ✅ |
| | 🆕 Layer 2: 向量语义匹配 (ES KNN) | ✅ |
| | 🆕 Layer 3: 分析记忆自动回写 | ✅ |
| | 🆕 Embedding Redis 缓存 | ✅ |
| **RAG 知识库** | 文本文档分块 (Chunking) | ✅ |
| | ES 8.x `dense_vector` 原生向量存储 | ✅ |
| | Agent 智能 KNN 检索 (按需唤醒) | ✅ |
| | 🆕 知识库 CRUD 管理 API | ✅ |
| | 🆕 文档上传 + 异步索引 | ✅ |
| **告警通知** | 企业微信 / 钉钉 / 飞书 Webhook | ✅ |
| | 模板化通知 (3 种场景) | ✅ |
| | 业务线独立 Webhook URL | ✅ |
| **平台能力** | 多租户隔离 | ✅ |
| | JWT 认证 + 角色鉴权 | ✅ |
| | API Key Fernet 加密存储 | ✅ |
| | Celery Beat 定时巡检 | ✅ |
| | 🆕 Fan-out 多业务线并行巡检 | ✅ |
| | 巡检冷却控制 | ✅ |

---

## 💰 成本控制配置

LogMind 内置多层 Token 消耗控制机制，通过 `.env` 配置：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ANALYSIS_MAX_LOGS_PER_TASK` | `500` | 单次分析最大抓取日志数 |
| `ANALYSIS_COOLDOWN_MINUTES` | `30` | 同一业务线两次自动巡检最小间隔（分钟） |
| `ANALYSIS_FINGERPRINT_ENABLED` | `true` | 是否启用 Layer 1 MD5 指纹去重 |
| `ANALYSIS_FINGERPRINT_TTL_HOURS` | `6` | MD5 指纹缓存 TTL（小时） |
| `ANALYSIS_SEMANTIC_DEDUP_ENABLED` | `true` | 🆕 是否启用 Layer 2 向量语义去重 |
| `ANALYSIS_SEMANTIC_DEDUP_THRESHOLD` | `0.92` | 🆕 语义匹配阈值 (0-1, 越高越严格) |
| `ANALYSIS_SEMANTIC_DEDUP_TTL_HOURS` | `24` | 🆕 历史分析结论有效期 |
| `ANALYSIS_EMBEDDING_CACHE_TTL_SECONDS` | `3600` | 🆕 Embedding 向量 Redis 缓存 TTL |
| `ANALYSIS_AGENT_ENABLED` | `true` | 是否启用 Agent 多步推理 |
| `ANALYSIS_AGENT_MAX_STEPS` | `5` | Agent 最大工具调用步数 |

> **关闭 Agent 不影响分析功能**，只影响分析深度。设置 `ANALYSIS_AGENT_ENABLED=false` 可立即降低 Token 消耗 30-50%。  
> **向量语义去重**可单独开关，不影响 MD5 指纹去重。两层级联兼顾速度和精度。

---

## 🚀 快速开始

### 环境要求

| 组件 | 版本要求 |
|------|---------| 
| Python | ≥ 3.13 |
| PostgreSQL / MySQL | 任选其一 |
| Redis | ≥ 6.0 |
| Elasticsearch | ≥ 8.x（已部署，含 Filebeat 日志数据） |

### 源码部署

```bash
# 1. 克隆项目
git clone https://github.com/leeeway/LogMind.git
cd LogMind

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，配置数据库、ES、Redis 连接信息

# 5. 初始化数据库 + 播种默认数据
python -m logmind.scripts.seed_prompts

# 6. 启动服务
make run      # FastAPI 主服务 (端口 8000)
make worker   # Celery Worker (新终端)
make beat     # Celery Beat 调度器 (新终端)
```

### Docker Compose 部署

```bash
# 一键启动（含 PostgreSQL + Redis）
docker-compose --env-file .env.production up -d --build
```

### 首次配置

1. **登录获取 Token**
   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "logmind2024!"}'
   ```

2. **注册 AI 模型提供商**（可选，仅 `ai_enabled=true` 时需要）
   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/providers \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
       "provider_type": "openai",
       "name": "主分析引擎",
       "api_base_url": "https://api.openai.com/v1",
       "api_key": "sk-xxx",
       "default_model": "gpt-4o",
       "priority": 1
     }'
   ```

3. **创建业务线** → 见下一节

---

## ⚙️ 业务线配置指南

业务线是 LogMind 的核心配置单元。每个业务线对应一组 ES 索引，独立控制日志解析策略、AI 开关和告警通道。

### Java 服务（K8s 部署 + AI 分析）

```json
{
  "name": "tong-kernel",
  "description": "通行证内核服务",
  "es_index_pattern": "master-stage-tong-kernel.cn*",
  "severity_threshold": "error",
  "language": "java",
  "ai_enabled": true,
  "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
}
```

### C# 服务（Windows VM + 仅通知）

```json
{
  "name": "interface-security",
  "description": "安全接口服务 (C#)",
  "es_index_pattern": "master-interface.security.cn*",
  "severity_threshold": "error",
  "language": "csharp",
  "ai_enabled": false,
  "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=yyy"
}
```

### 配置字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 业务线名称 |
| `es_index_pattern` | string | ✅ | ES 索引模式，支持通配符。多个用逗号分隔 |
| `severity_threshold` | string | — | 告警阈值：`debug` / `info` / `warning` / `error` / `critical` |
| `language` | string | — | 开发语言：`java` / `csharp` / `python` / `go` / `other`。决定日志解析策略 |
| `ai_enabled` | boolean | — | 大模型开关。`false` 时跳过 AI 推理，直接发送异常日志通知 |
| `webhook_url` | string | — | 业务线专属 webhook URL。为空时使用全局配置 |
| `field_mapping` | object | — | 自定义字段映射（高级用法） |

---

## 📨 通知模板说明

### AI 关闭 — 日志异常告警

当 `ai_enabled=false` 且检测到错误日志时，自动推送：

```
## ⚠️ 日志异常告警

**业务线**: interface-security
**站点**: interface.security.cn
**语言**: C#
**时间范围**: 2026-04-13 22:00 ~ 22:30
**异常日志数**: 15 条

---

**异常摘要**:
2026-04-13 19:09:56,856 [155] ERROR .Core.DBUtility.DataHelper
- SqlException: Timeout expired...

---
> 请及时排查处理。登录 LogMind 平台查看完整日志。
```

### AI 开启 — AI 分析告警

当 AI 分析发现 Critical 级别问题时推送：

```
## 🔴 LogMind AI 分析告警

**告警级别**: CRITICAL
**业务线**: tong-kernel
**站点**: stage-tong-kernel.cn (正式环境)
**分析日志数**: 23 条

---

**AI 分析结论**:
1. NullPointerException 根因：cn.tong.filter.ConvertToHumpFilter
   第 96 行 phoneToken 参数未做空值校验...

---
> 请及时处理。登录 LogMind 平台查看完整分析报告。
```

### AI 异常降级 — 流程异常通知

当 AI 模型调用失败（超时 / 配额 / Key 过期）时：

```
## 🛑 AI 分析流程异常

**业务线**: tong-kernel
**站点**: stage-tong-kernel.cn

**错误信息**: API Error: quota exceeded

---
> AI 模型调用异常，请检查模型配置和 API Key。
```

随后自动降级发送原始日志摘要通知。

---

## 🔌 API 接口参考

完整 Swagger 文档：`http://127.0.0.1:8000/docs`

### 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/auth/login` | 登录获取 JWT Token |
| `POST` | `/api/v1/business-lines` | 创建业务线 |
| `PUT` | `/api/v1/business-lines/{id}` | 更新业务线（可单独切换 AI 开关） |
| `POST` | `/api/v1/analysis/tasks` | 手动触发分析任务 |
| `GET` | `/api/v1/analysis/tasks/{id}` | 获取分析任务结果 |
| `POST` | `/api/v1/providers` | 注册 AI 模型提供商 |
| `POST` | `/api/v1/alerts/rules` | 创建告警规则 |
| `GET` | `/api/v1/alerts/history` | 查看告警历史 |
| `GET` | `/api/v1/logs/search` | 搜索 ES 日志 |
| `GET` | `/api/v1/logs/stats` | 日志统计聚合 |
| `GET` | `/api/v1/logs/indices` | 列出 ES 索引 |

### 🆕 知识库管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/knowledge-base` | 创建知识库 |
| `GET` | `/api/v1/knowledge-base` | 列出知识库 |
| `GET` | `/api/v1/knowledge-base/{id}` | 查看知识库详情含文档列表 |
| `PUT` | `/api/v1/knowledge-base/{id}` | 更新知识库配置 |
| `DELETE` | `/api/v1/knowledge-base/{id}` | 删除知识库及其文档 |
| `POST` | `/api/v1/knowledge-base/{id}/documents` | 上传文档（文本内容） |
| `GET` | `/api/v1/knowledge-base/{id}/documents` | 列出知识库文档 |
| `DELETE` | `/api/v1/knowledge-base/{id}/documents/{doc_id}` | 删除文档 |

### 手动触发分析

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/analysis/tasks" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "business_line_id": "<BUSINESS_LINE_ID>",
    "task_type": "manual",
    "time_from": "2026-04-13T14:00:00Z",
    "time_to": "2026-04-13T22:00:00Z"
  }'
```

### 动态切换 AI 开关

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/business-lines/<ID>" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"ai_enabled": false}'
```

### 🆕 上传知识库文档

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/knowledge-base/<KB_ID>/documents" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "redis-troubleshooting.md",
    "content": "# Redis 连接池故障排查指南\n\n## 现象\n连接池耗尽时..."
  }'
```

---

## 📁 项目结构

```
LogMind/
├── src/logmind/
│   ├── core/                    # 基础设施层
│   │   ├── config.py            # Pydantic 配置管理
│   │   ├── database.py          # SQLAlchemy 异步引擎
│   │   ├── elasticsearch.py     # ES 客户端
│   │   ├── redis.py             # Redis 客户端
│   │   ├── celery_app.py        # Celery 配置 + Beat 调度
│   │   ├── security.py          # JWT + Fernet 加密
│   │   └── dependencies.py      # FastAPI 依赖注入
│   ├── domain/                  # 业务领域层 (DDD)
│   │   ├── tenant/              # 租户 + 用户 + 业务线
│   │   ├── log/                 # ES 日志查询、解析与向量搜索
│   │   ├── analysis/            # AI 分析 Pipeline
│   │   │   ├── pipeline.py      # 9 阶段流水线定义
│   │   │   ├── agent_stage.py   # AI Agent 多步推理 Stage
│   │   │   ├── agent_tools.py   # 7 个 Agent 工具 (Function Calling)
│   │   │   ├── fingerprint_stage.py # Layer 1: MD5 指纹去重
│   │   │   ├── semantic_dedup.py    # 🆕 Layer 2: 向量语义去重
│   │   │   ├── analysis_indexer.py  # 🆕 Layer 3: 分析结论自动回写
│   │   │   └── tasks.py         # Celery 任务入口
│   │   ├── alert/               # 告警规则 + 并行巡检调度
│   │   ├── provider/            # AI 模型提供商管理
│   │   │   └── adapters/        # OpenAI/Claude/Gemini/DeepSeek/Ollama
│   │   ├── prompt/              # Prompt 模板引擎
│   │   ├── rag/                 # 🆕 RAG 知识库 (ES 向量检索 + 管理 API)
│   │   └── dashboard/           # 仪表盘统计
│   ├── shared/                  # 通用组件
│   └── main.py                  # FastAPI 入口
├── configs/prompts/             # 内置 Prompt 模板 (YAML)
│   ├── log_analysis.yaml        # 通用日志分析模板
│   └── stack_trace_analysis.yaml # 堆栈异常分析模板
├── migrations/                  # 数据库迁移脚本
├── deploy/                      # 部署配置
├── docker-compose.yml           # Docker Compose 编排
├── Makefile                     # 常用命令
└── .env.example                 # 环境变量模板
```

---

## 🎯 未来路标 (Roadmap)

### v1.0 — 基础版 ✅

- [x] 多租户 + 业务线隔离架构
- [x] Java / C# 双语言日志解析引擎
- [x] 多模型 AI Provider 管理 (OpenAI / DeepSeek / 内网模型)
- [x] Celery 分布式定时巡检 + 冷却控制
- [x] 业务线级 AI 开关 + AI 异常降级通知
- [x] 模板化 Webhook 多平台推送
- [x] Prompt 模板化管理 (YAML + DB 双源)

### v1.1 — Agent 智能化 ✅

- [x] AI Agent 多步推理 (Function Calling + ES 工具)
- [x] Redis 错误指纹去重 (Layer 1)
- [x] RAG 知识库检索 (ES 原生向量 + Agent Tool)

### v1.2 — 智能去重 + 高并发扩展 ✅ ← 当前

- [x] 向量语义去重 (Layer 2 — ES KNN)
- [x] 分析记忆自动回写 (Layer 3 — 闭环)
- [x] Embedding Redis 缓存
- [x] Agent 历史事件搜索工具
- [x] Agent 跨业务线关联分析
- [x] Fan-out 多业务线并行巡检
- [x] Knowledge Base 完整 CRUD API

### v1.3 — 近期计划

- [ ] Web 管理界面 (Vue.js / React Dashboard)
- [ ] 告警规则引擎增强 (关键词 / 正则 / 阈值触发)
- [ ] 日志趋势分析看板 + 异常基线检测
- [ ] Python / Go 堆栈解析深度支持
- [ ] 告警静默 / 聚合 / 升级策略

### v2.0 — 中期目标 (运维深度集成)

- [ ] K8s Event 关联分析 + ConfigMap 变更追踪
- [ ] 部署系统联动：近期发布记录与错误关联
- [ ] 多 ES 集群联邦查询
- [ ] MCP 协议 Agent 工具解耦

### v3.0 — 远期愿景 (Auto-Remediation 自动自愈)

- [ ] Agent 自治行动：`restart_pod`, `scale_deployment`
- [ ] 交互式审批修复：企微审批卡片 → 一键执行
- [ ] AI Fix PR 建议 (对接 GitLab/GitHub)
- [ ] 跨服务分布式链路追踪 (Trace) 关联
- [ ] Text-to-DSL 自然语言日志查询

---

## 🤝 参与贡献

LogMind 欢迎社区贡献！

1. **Fork** 本仓库
2. 创建特性分支 `git checkout -b feature/your-feature`
3. 遵循代码规范 `make lint && make format`
4. 提交更改 `git commit -m 'feat: add your feature'`
5. 推送分支 `git push origin feature/your-feature`
6. 提交 **Pull Request**

### 开发命令

```bash
make help       # 查看所有可用命令
make dev        # 安装开发依赖
make run        # 启动开发服务器
make worker     # 启动 Celery Worker
make beat       # 启动定时调度器
make test       # 运行测试
make lint       # 代码检查
make format     # 代码格式化
```

---

## 📜 开源协议

[MIT License](LICENSE) — 允许商业使用和私有化部署。

> 项目本身不对 AI 模型调用产生的费用和算力消耗提供任何担保。请参阅 LICENSE 了解完整条款。
