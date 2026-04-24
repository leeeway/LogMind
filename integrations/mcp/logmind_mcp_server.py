"""
LogMind MCP Server — Model Context Protocol integration for LogMind.

Exposes LogMind REST API as MCP tools so that MCP-compatible agents
(Hermes Agent, OpenClaw, Claude Code, Cursor, etc.) can interact with
LogMind through natural language.

Usage (stdio):
    python logmind_mcp_server.py

Configuration (env vars):
    LOGMIND_API_URL   — LogMind API base URL (default: http://localhost:8000)
    LOGMIND_USERNAME  — LogMind login username (default: admin)
    LOGMIND_PASSWORD  — LogMind login password (default: logmind2024!)
    LOGMIND_TOKEN     — Pre-set JWT token (overrides username/password login)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import httpx
except ImportError:
    print(
        "ERROR: httpx is required.  Install with:  pip install httpx",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    from mcp.server import Server
    from mcp.types import TextContent, Tool
    from mcp.server.stdio import stdio_server
except ImportError:
    print(
        "ERROR: mcp SDK is required.  Install with:  pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOGMIND_API_URL = os.environ.get("LOGMIND_API_URL", "http://localhost:8000")
LOGMIND_USERNAME = os.environ.get("LOGMIND_USERNAME", "admin")
LOGMIND_PASSWORD = os.environ.get("LOGMIND_PASSWORD", "logmind2024!")
LOGMIND_TOKEN = os.environ.get("LOGMIND_TOKEN", "")

API_PREFIX = f"{LOGMIND_API_URL}/api/v1"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_cached_token: str = LOGMIND_TOKEN


async def _get_token() -> str:
    """Obtain a JWT token, caching it across calls."""
    global _cached_token
    if _cached_token:
        return _cached_token
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{API_PREFIX}/auth/login",
            json={"username": LOGMIND_USERNAME, "password": LOGMIND_PASSWORD},
        )
        resp.raise_for_status()
        _cached_token = resp.json()["access_token"]
        return _cached_token


async def _api_get(path: str, params: dict[str, Any] | None = None) -> dict:
    token = await _get_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_PREFIX}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def _api_post(path: str, body: dict[str, Any] | None = None) -> dict:
    token = await _get_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{API_PREFIX}{path}",
            json=body or {},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def _api_put(path: str, body: dict[str, Any] | None = None) -> dict:
    token = await _get_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{API_PREFIX}{path}",
            json=body or {},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


def _json_summary(data: Any, max_len: int = 4000) -> str:
    """Return a JSON string, truncated if too long."""
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    if len(text) > max_len:
        text = text[:max_len] + "\n... (truncated)"
    return text


# ---------------------------------------------------------------------------
# MCP Server definition
# ---------------------------------------------------------------------------

server = Server("logmind")


# ── Tool 1: Health Check ────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="logmind_health",
            description=(
                "Check LogMind platform health status. "
                "Returns component status for Database, Redis, Elasticsearch, and Celery."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="logmind_list_business_lines",
            description=(
                "List all configured business lines (services being monitored). "
                "Returns name, ID, language, AI toggle status, and ES index pattern."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="logmind_search_logs",
            description=(
                "Search error logs from Elasticsearch for a specific business line. "
                "Returns the most recent error log entries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "business_line_id": {
                        "type": "string",
                        "description": "UUID of the business line to search logs for",
                    },
                    "severity": {
                        "type": "string",
                        "description": "Log severity filter (default: error)",
                        "enum": ["debug", "info", "warning", "error", "critical"],
                        "default": "error",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "Optional keyword to filter logs",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max logs to return (default: 20)",
                        "default": 20,
                    },
                    "minutes_ago": {
                        "type": "integer",
                        "description": "Look back N minutes (default: 60)",
                        "default": 60,
                    },
                },
                "required": ["business_line_id"],
            },
        ),
        Tool(
            name="logmind_log_stats",
            description=(
                "Get aggregated error statistics for a business line. "
                "Returns error counts by time bucket, severity distribution, "
                "and top error patterns."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "business_line_id": {
                        "type": "string",
                        "description": "UUID of the business line",
                    },
                },
                "required": ["business_line_id"],
            },
        ),
        Tool(
            name="logmind_trigger_analysis",
            description=(
                "Trigger an AI analysis task for a business line. "
                "The analysis is async — use logmind_get_analysis to poll results. "
                "Returns the task ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "business_line_id": {
                        "type": "string",
                        "description": "UUID of the business line to analyze",
                    },
                    "minutes_ago": {
                        "type": "integer",
                        "description": "Analyze logs from the last N minutes (default: 30)",
                        "default": 30,
                    },
                },
                "required": ["business_line_id"],
            },
        ),
        Tool(
            name="logmind_get_analysis",
            description=(
                "Get the result of a previously triggered analysis task. "
                "Returns task status, AI conclusions, severity, affected services, "
                "and fix suggestions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The analysis task UUID returned by logmind_trigger_analysis",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="logmind_list_alerts",
            description=(
                "List recent alert history. "
                "Returns alert priority (P0/P1/P2), business line, timestamp, "
                "and acknowledgment status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max alerts to return (default: 20)",
                        "default": 20,
                    },
                    "priority": {
                        "type": "string",
                        "description": "Filter by priority: P0, P1, P2",
                        "enum": ["P0", "P1", "P2"],
                    },
                },
            },
        ),
        Tool(
            name="logmind_ack_alert",
            description="Acknowledge an alert, marking it as seen by the on-call engineer.",
            inputSchema={
                "type": "object",
                "properties": {
                    "alert_id": {
                        "type": "string",
                        "description": "UUID of the alert to acknowledge",
                    },
                },
                "required": ["alert_id"],
            },
        ),
        Tool(
            name="logmind_resolve_alert",
            description="Mark an alert as resolved.",
            inputSchema={
                "type": "object",
                "properties": {
                    "alert_id": {
                        "type": "string",
                        "description": "UUID of the alert to resolve",
                    },
                },
                "required": ["alert_id"],
            },
        ),
        Tool(
            name="logmind_submit_feedback",
            description=(
                "Submit feedback on an AI analysis result. "
                "+1 means the analysis was accurate (extends TTL to 365d). "
                "-1 means the analysis was inaccurate (excludes from future matching)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "result_id": {
                        "type": "string",
                        "description": "UUID of the analysis result",
                    },
                    "score": {
                        "type": "integer",
                        "description": "Feedback score: 1 (helpful) or -1 (inaccurate)",
                        "enum": [1, -1],
                    },
                    "comment": {
                        "type": "string",
                        "description": "Optional comment explaining the feedback",
                    },
                },
                "required": ["result_id", "score"],
            },
        ),
        Tool(
            name="logmind_toggle_ai",
            description=(
                "Enable or disable AI analysis for a business line. "
                "When disabled, LogMind sends raw log notifications without AI processing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "business_line_id": {
                        "type": "string",
                        "description": "UUID of the business line",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "true to enable AI, false to disable",
                    },
                },
                "required": ["business_line_id", "enabled"],
            },
        ),
        Tool(
            name="logmind_ai_effectiveness",
            description=(
                "Get AI analysis effectiveness metrics: accuracy trend based on user feedback, "
                "MTTR (mean time to resolution), token savings from dedup, and top error patterns. "
                "Use this to assess how well the AI is performing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Look back N days (default: 7)",
                        "default": 7,
                    },
                    "business_line_id": {
                        "type": "string",
                        "description": "Optional: filter by business line UUID",
                    },
                },
            },
        ),
        Tool(
            name="logmind_agent_analytics",
            description=(
                "Get Agent tool usage analytics: which tools are used most, success rates, "
                "common tool chain patterns, and correlation with analysis quality. "
                "Use this to understand and optimize the AI agent's investigation strategy."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Look back N days (default: 7)",
                        "default": 7,
                    },
                    "business_line_id": {
                        "type": "string",
                        "description": "Optional: filter by business line UUID",
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch MCP tool calls to LogMind API."""
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=result)]
    except httpx.HTTPStatusError as e:
        error_body = e.response.text[:500] if e.response else "No response body"
        return [
            TextContent(
                type="text",
                text=f"LogMind API error ({e.response.status_code}): {error_body}",
            )
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict) -> str:
    if name == "logmind_health":
        data = await _api_get("/health")
        return _json_summary(data)

    elif name == "logmind_list_business_lines":
        data = await _api_get("/business-lines")
        return _json_summary(data)

    elif name == "logmind_search_logs":
        minutes = args.get("minutes_ago", 60)
        params: dict[str, Any] = {
            "business_line_id": args["business_line_id"],
            "severity": args.get("severity", "error"),
            "limit": args.get("limit", 20),
        }
        if args.get("keyword"):
            params["keyword"] = args["keyword"]
        data = await _api_get("/logs/search", params=params)
        return _json_summary(data)

    elif name == "logmind_log_stats":
        data = await _api_get(
            "/logs/stats",
            params={"business_line_id": args["business_line_id"]},
        )
        return _json_summary(data)

    elif name == "logmind_trigger_analysis":
        minutes = args.get("minutes_ago", 30)
        now = datetime.now(timezone.utc)
        time_from = (now - timedelta(minutes=minutes)).isoformat()
        time_to = now.isoformat()
        data = await _api_post(
            "/analysis/tasks",
            body={
                "business_line_id": args["business_line_id"],
                "task_type": "manual",
                "time_from": time_from,
                "time_to": time_to,
            },
        )
        return _json_summary(data)

    elif name == "logmind_get_analysis":
        data = await _api_get(f"/analysis/tasks/{args['task_id']}")
        return _json_summary(data)

    elif name == "logmind_list_alerts":
        params = {"limit": args.get("limit", 20)}
        if args.get("priority"):
            params["priority"] = args["priority"]
        data = await _api_get("/alerts/history", params=params)
        return _json_summary(data)

    elif name == "logmind_ack_alert":
        data = await _api_post(f"/alerts/history/{args['alert_id']}/ack")
        return _json_summary(data)

    elif name == "logmind_resolve_alert":
        data = await _api_post(f"/alerts/history/{args['alert_id']}/resolve")
        return _json_summary(data)

    elif name == "logmind_submit_feedback":
        body: dict[str, Any] = {"score": args["score"]}
        if args.get("comment"):
            body["comment"] = args["comment"]
        data = await _api_put(
            f"/analysis/results/{args['result_id']}/feedback",
            body=body,
        )
        return _json_summary(data)

    elif name == "logmind_toggle_ai":
        data = await _api_put(
            f"/business-lines/{args['business_line_id']}",
            body={"ai_enabled": args["enabled"]},
        )
        return _json_summary(data)

    elif name == "logmind_ai_effectiveness":
        params: dict[str, Any] = {"days": args.get("days", 7)}
        if args.get("business_line_id"):
            params["business_line_id"] = args["business_line_id"]
        data = await _api_get("/dashboard/ai-effectiveness", params=params)
        return _json_summary(data)

    elif name == "logmind_agent_analytics":
        params = {"days": args.get("days", 7)}
        if args.get("business_line_id"):
            params["business_line_id"] = args["business_line_id"]
        data = await _api_get("/dashboard/agent-tool-analytics", params=params)
        return _json_summary(data)

    else:
        return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
