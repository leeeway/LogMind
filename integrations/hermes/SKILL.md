---
name: logmind-ops
description: LogMind AI 智能日志分析平台运维技能 — 搜索日志、触发 AI 分析、查看告警、管理业务线
version: 1.0.0
platforms: [macos, linux]
metadata:
  hermes:
    tags: [devops, logging, aiops, elasticsearch, sre]
    category: devops
    requires_toolsets: [terminal]
    config:
      - key: logmind.api_url
        description: "LogMind API base URL"
        default: "http://localhost:8000"
        prompt: "LogMind API URL"
required_environment_variables:
  - name: LOGMIND_TOKEN
    prompt: "LogMind JWT Token"
    help: "Get it via: curl -X POST http://<host>:8000/api/v1/auth/login -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"logmind2024!\"}'"
    required_for: full functionality
---

# LogMind 运维技能

LogMind 是一个企业级 AI 日志分析平台，集成 ELK 基础设施，通过 LLM 自动分析错误根因、生成修复建议并推送告警。

## When to Use

- 用户需要搜索或查看服务日志中的错误
- 用户需要触发 AI 分析来排查问题
- 用户需要查看、确认或解决告警
- 用户想了解某个业务线的错误趋势和统计
- 用户需要管理业务线配置或切换 AI 开关
- 用户需要查看 LogMind 平台健康状态

## Procedure

### 1. 认证

所有 API 调用都需要 Bearer Token。如果 `$LOGMIND_TOKEN` 已设置则直接使用，否则先登录获取：

```bash
# 登录获取 Token
curl -s -X POST "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "logmind2024!"}' | jq -r '.access_token'
```

### 2. 查看平台状态

```bash
# 健康检查 (检查 DB/Redis/ES/Celery 所有组件)
curl -s "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/health" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" | jq .

# 轻量存活检查
curl -s "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/health/live" | jq .
```

### 3. 列出业务线

```bash
curl -s "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/business-lines" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" | jq '.[] | {id, name, ai_enabled, language}'
```

### 4. 搜索日志

```bash
# 搜索指定业务线的 ERROR 日志 (最近 1 小时)
curl -s "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/logs/search?business_line_id=<BIZ_ID>&severity=error&limit=20" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" | jq .
```

### 5. 日志统计

```bash
# 获取错误日志统计聚合
curl -s "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/logs/stats?business_line_id=<BIZ_ID>" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" | jq .
```

### 6. 触发 AI 分析

```bash
# 手动触发分析任务
curl -s -X POST "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/analysis/tasks" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "business_line_id": "<BIZ_ID>",
    "task_type": "manual",
    "time_from": "2026-04-24T14:00:00Z",
    "time_to": "2026-04-24T22:00:00Z"
  }' | jq .
```

分析是异步的，会返回 `task_id`。用以下命令查看结果：

```bash
# 查看分析结果
curl -s "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/analysis/tasks/<TASK_ID>" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" | jq .
```

### 7. 查看告警历史

```bash
# 查看告警列表
curl -s "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/alerts/history" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" | jq .

# 确认告警
curl -s -X POST "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/alerts/history/<ALERT_ID>/ack" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" | jq .

# 解决告警
curl -s -X POST "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/alerts/history/<ALERT_ID>/resolve" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" | jq .
```

### 8. 提交分析反馈

```bash
# 正面反馈 (有帮助)
curl -s -X PUT "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/analysis/results/<RESULT_ID>/feedback" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"score": 1, "comment": "分析准确"}' | jq .

# 负面反馈 (不准确)
curl -s -X PUT "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/analysis/results/<RESULT_ID>/feedback" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"score": -1, "comment": "误判"}' | jq .
```

### 9. 切换 AI 开关

```bash
# 关闭某业务线的 AI 分析
curl -s -X PUT "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/business-lines/<BIZ_ID>" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ai_enabled": false}' | jq .

# 开启 AI 分析
curl -s -X PUT "${LOGMIND_API_URL:-http://localhost:8000}/api/v1/business-lines/<BIZ_ID>" \
  -H "Authorization: Bearer $LOGMIND_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ai_enabled": true}' | jq .
```

## Pitfalls

- **Token 过期**: JWT Token 有有效期，过期后需要重新登录获取
- **分析是异步的**: `POST /analysis/tasks` 返回 task_id，需要轮询 `GET /analysis/tasks/{id}` 获取结果。通常 30 秒内完成
- **时间格式**: 所有时间参数使用 ISO 8601 UTC 格式，如 `2026-04-24T14:00:00Z`
- **业务线 ID**: 大多数操作需要先查询 business_line_id，用 `GET /business-lines` 获取
- **AI 依赖**: 如果业务线 `ai_enabled=false`，分析将跳过 AI 推理，只发送原始日志摘要

## Verification

- 健康检查返回 `status: "healthy"` 且所有组件状态正常
- 业务线列表能正确返回配置的服务
- 日志搜索返回 ES 中的真实日志数据
- 分析任务状态最终变为 `completed`
