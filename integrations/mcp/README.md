# LogMind MCP Server

将 LogMind REST API 封装为 [MCP (Model Context Protocol)](https://modelcontextprotocol.io) 工具服务器，让 Hermes Agent、OpenClaw、Claude Code、Cursor 等 MCP 客户端通过自然语言对话直接操控 LogMind。

## 安装

```bash
cd integrations/mcp
pip install -r requirements.txt
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `LOGMIND_API_URL` | `http://localhost:8000` | LogMind API 地址 |
| `LOGMIND_USERNAME` | `admin` | 登录用户名 |
| `LOGMIND_PASSWORD` | `logmind2024!` | 登录密码 |
| `LOGMIND_TOKEN` | — | 预设 JWT Token（设置后跳过登录） |

## 在 Hermes Agent 中使用

编辑 `~/.hermes/config.yaml`：

```yaml
mcp_servers:
  logmind:
    command: "python"
    args: ["/path/to/LogMind/integrations/mcp/logmind_mcp_server.py"]
    env:
      LOGMIND_API_URL: "http://your-logmind:8000"
      LOGMIND_TOKEN: "your-jwt-token"
```

## 在 Claude Code 中使用

编辑 `~/.claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "logmind": {
      "command": "python",
      "args": ["/path/to/LogMind/integrations/mcp/logmind_mcp_server.py"],
      "env": {
        "LOGMIND_API_URL": "http://your-logmind:8000",
        "LOGMIND_TOKEN": "your-jwt-token"
      }
    }
  }
}
```

## 暴露的工具

| 工具 | 说明 |
|------|------|
| `logmind_health` | 平台健康检查 |
| `logmind_list_business_lines` | 列出所有业务线 |
| `logmind_search_logs` | 搜索 ES 错误日志 |
| `logmind_log_stats` | 日志统计聚合 |
| `logmind_trigger_analysis` | 触发 AI 分析任务 |
| `logmind_get_analysis` | 获取分析结果 |
| `logmind_list_alerts` | 查看告警历史 |
| `logmind_ack_alert` | 确认告警 |
| `logmind_resolve_alert` | 解决告警 |
| `logmind_submit_feedback` | 提交分析反馈 |
| `logmind_toggle_ai` | 切换业务线 AI 开关 |
