"""
Multi-Agent Collaboration Engine

Orchestrates multiple specialized AI agents working in parallel to diagnose
complex issues. Each agent has a focused role and limited tool set.

Architecture:
  - Orchestrator: Analyzes user question, selects agents, merges findings
  - LogAnalyst: Searches logs, counts patterns, traces links
  - PerformanceExpert: Checks service health, compares windows, predicts trends
  - HistoryExpert: Searches knowledge base, finds similar incidents
  - ChangeCorrelator: Looks for recent deployments/changes correlated with errors
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from logmind.core.logging import get_logger
from logmind.domain.provider.base import ChatMessage, ChatRequest
from logmind.domain.provider.manager import provider_manager

logger = get_logger(__name__)

MAX_AGENT_ROUNDS = 2


@dataclass
class AgentRole:
    name: str
    display_name: str
    description: str
    tools: list[str]
    system_prompt: str


@dataclass
class AgentFinding:
    agent_name: str
    display_name: str
    status: str = "pending"  # pending / running / done / error
    summary: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    raw_content: str = ""


# --- PLACEHOLDER_MULTI_AGENT_BODY ---

AGENT_ROLES = {
    "LogAnalyst": AgentRole(
        name="LogAnalyst",
        display_name="日志分析师",
        description="搜索日志、统计错误模式、追踪链路",
        tools=["search_logs", "count_error_patterns", "get_log_context", "trace_linked_operations"],
        system_prompt=(
            "你是日志分析专家。你的任务是搜索相关日志、统计错误模式、追踪调用链路。"
            "只使用你的工具收集事实，不要给出最终结论——那是 Orchestrator 的工作。"
            "用 1-2 轮工具调用收集关键信息，然后简洁总结你的发现。"
        ),
    ),
    "PerformanceExpert": AgentRole(
        name="PerformanceExpert",
        display_name="性能专家",
        description="分析服务健康、对比时间窗口、预测趋势",
        tools=["get_service_health", "compare_time_windows", "predict_service_trend"],
        system_prompt=(
            "你是性能分析专家。你的任务是检查服务健康状态、对比不同时间窗口的错误分布、预测趋势。"
            "只使用你的工具收集事实，不要给出最终结论。"
            "用 1-2 轮工具调用收集关键信息，然后简洁总结你的发现。"
        ),
    ),
    "HistoryExpert": AgentRole(
        name="HistoryExpert",
        display_name="历史故障专家",
        description="搜索知识库、查找历史相似故障",
        tools=["search_knowledge_base", "search_similar_incidents", "get_alerts"],
        system_prompt=(
            "你是历史故障分析专家。你的任务是搜索知识库中的 SOP 和故障报告、查找历史相似故障、检查告警记录。"
            "只使用你的工具收集事实，不要给出最终结论。"
            "用 1-2 轮工具调用收集关键信息，然后简洁总结你的发现。"
        ),
    ),
    "ChangeCorrelator": AgentRole(
        name="ChangeCorrelator",
        display_name="变更关联员",
        description="关联最近的部署和配置变更",
        tools=["search_logs", "query_operation_timeline"],
        system_prompt=(
            "你是变更关联分析专家。你的任务是搜索最近的部署日志（关键词：deploy、release、发布、上线、重启、restart）"
            "和配置变更，判断问题是否与最近的变更有关。"
            "只使用你的工具收集事实，不要给出最终结论。"
            "用 1-2 轮工具调用收集关键信息，然后简洁总结你的发现。"
        ),
    ),
}


class MultiAgentOrchestrator:
    """
    Orchestrates parallel agent execution and synthesizes findings.
    """

    def __init__(self, chat_service):
        self.chat_service = chat_service

    def select_agents(self, user_message: str) -> list[str]:
        """Select which agents to activate based on user question."""
        agents = []
        msg = user_message.lower()

        # Always include LogAnalyst for any diagnostic question
        agents.append("LogAnalyst")

        # Performance if health/performance/trend mentioned
        if any(kw in msg for kw in ("健康", "性能", "延迟", "超时", "趋势", "恶化", "qps", "慢")):
            agents.append("PerformanceExpert")

        # History if asking about similar/past/known issues
        if any(kw in msg for kw in ("历史", "之前", "类似", "知识库", "sop", "以前", "故障")):
            agents.append("HistoryExpert")

        # Change correlator if asking about changes/deployments
        if any(kw in msg for kw in ("变更", "部署", "发布", "上线", "deploy", "release", "最近改了")):
            agents.append("ChangeCorrelator")

        # For "全面分析" / "深度诊断" — activate all
        if any(kw in msg for kw in ("全面分析", "深度诊断", "彻底排查", "完整分析", "所有可能")):
            agents = list(AGENT_ROLES.keys())

        # Always at least 2 agents for multi-agent mode to be worthwhile
        if len(agents) < 2:
            agents.append("PerformanceExpert")

        return agents

    async def run_agent(
        self,
        role: AgentRole,
        user_message: str,
        tenant_id: str,
        db_session,
        default_index: str,
        biz_lines: list,
        service_list: str,
    ) -> AgentFinding:
        """Run a single agent with its specialized tools and prompt."""
        finding = AgentFinding(agent_name=role.name, display_name=role.display_name, status="running")

        # Build agent-specific tool list
        from logmind.domain.chat.service import CHAT_TOOLS
        agent_tools = [t for t in CHAT_TOOLS if t.get("function", {}).get("name") in role.tools]

        beijing_tz = timezone(timedelta(hours=8))
        current_time = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M")

        messages = [
            ChatMessage(role="system", content=(
                f"{role.system_prompt}\n\n"
                f"当前北京时间: {current_time}\n"
                f"可用业务线: {service_list}\n"
                f"用户问题: {user_message}"
            )),
            ChatMessage(role="user", content=user_message),
        ]

        try:
            for round_num in range(1, MAX_AGENT_ROUNDS + 1):
                request = ChatRequest(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=2048,
                    tools=agent_tools,
                )

                response, _ = await provider_manager.chat_with_fallback(
                    session=db_session,
                    tenant_id=tenant_id,
                    request=request,
                )

                if response.tool_calls:
                    for tc in response.tool_calls:
                        func_name = tc.get("function", {}).get("name", "")
                        func_args_str = tc.get("function", {}).get("arguments", "{}")
                        try:
                            func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
                        except json.JSONDecodeError:
                            func_args = {}

                        finding.tool_calls.append({"name": func_name, "args": func_args})

                        tool_result = await self.chat_service.execute_tool_call(
                            func_name, func_args, tenant_id, db_session,
                            es_index_pattern=default_index,
                        )

                        messages.append(ChatMessage(
                            role="assistant",
                            content=f"[调用 {func_name}]",
                        ))
                        messages.append(ChatMessage(
                            role="user",
                            content=f"工具结果:\n{tool_result[:2000]}",
                        ))
                else:
                    finding.raw_content = response.content
                    finding.summary = response.content[:500]
                    finding.status = "done"
                    return finding

            # If exhausted rounds, do final summary
            final_req = ChatRequest(
                messages=messages + [ChatMessage(role="user", content="请简洁总结你的发现（3-5句话）。")],
                temperature=0.2,
                max_tokens=512,
            )
            final_resp, _ = await provider_manager.chat_with_fallback(
                session=db_session, tenant_id=tenant_id, request=final_req,
            )
            finding.raw_content = final_resp.content
            finding.summary = final_resp.content[:500]
            finding.status = "done"

        except Exception as e:
            logger.error("agent_failed", agent=role.name, error=str(e))
            finding.status = "error"
            finding.summary = f"调查失败: {str(e)[:100]}"

        return finding

    async def orchestrate(
        self,
        user_message: str,
        tenant_id: str,
        db_session,
        default_index: str,
        biz_lines: list,
        service_list: str,
    ) -> tuple[list[AgentFinding], str]:
        """
        Run multiple agents in parallel and synthesize their findings.

        Returns (findings, synthesis_content).
        """
        agent_names = self.select_agents(user_message)
        roles = [AGENT_ROLES[name] for name in agent_names if name in AGENT_ROLES]

        # Run agents in parallel
        tasks = [
            self.run_agent(role, user_message, tenant_id, db_session, default_index, biz_lines, service_list)
            for role in roles
        ]
        findings = await asyncio.gather(*tasks)

        # Synthesize findings
        synthesis_prompt = (
            "你是 LogMind 首席诊断官。以下是多位专家的独立调查结果，请综合分析并给出最终诊断报告。\n\n"
            f"用户问题: {user_message}\n\n"
        )
        for finding in findings:
            synthesis_prompt += f"## {finding.display_name}的发现\n{finding.summary}\n\n"

        synthesis_prompt += (
            "请综合以上信息，给出：\n"
            "1. 根因分析（最可能的原因）\n"
            "2. 影响范围\n"
            "3. 建议的修复方案\n"
            "4. 是否需要进一步调查\n\n"
            "用 Markdown 格式回复。"
        )

        synthesis_messages = [
            ChatMessage(role="system", content="你是资深 SRE，负责综合多位专家的调查结果给出最终诊断。"),
            ChatMessage(role="user", content=synthesis_prompt),
        ]

        try:
            synthesis_req = ChatRequest(
                messages=synthesis_messages,
                temperature=0.3,
                max_tokens=4096,
            )
            synthesis_resp, _ = await provider_manager.chat_with_fallback(
                session=db_session, tenant_id=tenant_id, request=synthesis_req,
            )
            synthesis_content = synthesis_resp.content
        except Exception as e:
            logger.error("synthesis_failed", error=str(e))
            synthesis_content = "综合分析失败，请查看各专家的独立发现。"

        return list(findings), synthesis_content
