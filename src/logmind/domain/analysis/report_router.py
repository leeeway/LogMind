"""
Report Generation — HTML/Markdown Export

Generates professional analysis reports from task results.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.analysis.models import AnalysisResult, AnalysisTask
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/analysis", tags=["Analysis"])
task_repo = BaseRepository(AnalysisTask)
result_repo = BaseRepository(AnalysisResult)


class ReportResponse(BaseModel):
    """Generated report content."""
    format: str
    content: str
    filename: str


def _generate_html_report(task: dict, results: list[dict], trace: dict | None) -> str:
    """Generate a styled HTML report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    task_time = task.get("created_at", "")
    completed = task.get("completed_at", "")
    duration = ""
    if task_time and completed:
        try:
            from dateutil.parser import parse
            d = (parse(str(completed)) - parse(str(task_time))).total_seconds()
            duration = f"{d:.1f}s"
        except Exception:
            pass

    results_html = ""
    for i, r in enumerate(results):
        sev = r.get("severity", "info")
        color = {"critical": "#ff4d4f", "warning": "#faad14", "info": "#1677ff"}.get(sev, "#8c8c8c")
        confidence = r.get("confidence_score", 0)
        content = r.get("content", "").replace("<", "&lt;").replace(">", "&gt;")
        results_html += f"""
        <div class="result-card">
          <div class="result-header">
            <span class="badge" style="background:{color}">{sev.upper()}</span>
            <span class="badge-outline">{r.get('result_type', 'unknown')}</span>
            <span class="confidence">置信度: {confidence*100:.0f}%</span>
          </div>
          <div class="result-content">{content}</div>
        </div>
        """

    stages_html = ""
    if trace and trace.get("stages"):
        for s in trace["stages"]:
            icon = "✅" if s.get("status") == "ok" else "⏭️" if s.get("status") == "skipped" else "❌"
            stages_html += f"<tr><td>{icon}</td><td>{s.get('stage','')}</td><td>{s.get('duration_ms',0)}ms</td><td>{s.get('status','')}</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LogMind 分析报告 — {task.get('id','')[:8]}</title>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family: -apple-system, 'PingFang SC', 'Segoe UI', sans-serif; background:#0a0f1a; color:#e0e0e0; padding:40px; line-height:1.6; }}
    .container {{ max-width:900px; margin:0 auto; }}
    .header {{ text-align:center; margin-bottom:40px; padding:30px; background:linear-gradient(135deg, rgba(22,119,255,0.1), rgba(114,46,209,0.1)); border:1px solid rgba(255,255,255,0.08); border-radius:16px; }}
    .header h1 {{ font-size:24px; background:linear-gradient(135deg,#1677ff,#722ed1); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    .header .meta {{ color:#888; font-size:13px; margin-top:8px; }}
    .section {{ margin-bottom:24px; }}
    .section-title {{ font-size:16px; font-weight:600; color:#fff; margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.08); }}
    .info-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(200px,1fr)); gap:12px; }}
    .info-item {{ background:rgba(20,28,46,0.8); padding:12px 16px; border-radius:10px; border:1px solid rgba(255,255,255,0.06); }}
    .info-label {{ font-size:11px; color:#666; text-transform:uppercase; letter-spacing:0.5px; }}
    .info-value {{ font-size:16px; font-weight:600; margin-top:4px; }}
    .result-card {{ background:rgba(20,28,46,0.8); border:1px solid rgba(255,255,255,0.06); border-radius:12px; padding:16px; margin-bottom:12px; }}
    .result-header {{ display:flex; align-items:center; gap:8px; margin-bottom:10px; }}
    .badge {{ padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; color:#fff; }}
    .badge-outline {{ padding:2px 8px; border-radius:4px; font-size:11px; border:1px solid rgba(255,255,255,0.15); color:#aaa; }}
    .confidence {{ font-size:12px; color:#888; margin-left:auto; }}
    .result-content {{ white-space:pre-wrap; font-size:13px; line-height:1.7; color:#ccc; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th {{ background:rgba(22,119,255,0.08); text-align:left; padding:8px 12px; color:#aaa; font-weight:500; }}
    td {{ padding:8px 12px; border-bottom:1px solid rgba(255,255,255,0.04); }}
    .footer {{ text-align:center; margin-top:40px; padding:20px; font-size:12px; color:#555; }}
    @media print {{ body {{ background:#fff; color:#333; }} .result-card,.info-item,.header {{ border-color:#ddd; background:#f9f9f9; }} .header h1 {{ color:#1677ff; -webkit-text-fill-color:#1677ff; }} }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>🔍 LogMind AI 分析报告</h1>
      <div class="meta">Task ID: {task.get('id','')} · 生成时间: {now}</div>
    </div>

    <div class="section">
      <div class="section-title">📋 任务概览</div>
      <div class="info-grid">
        <div class="info-item"><div class="info-label">任务类型</div><div class="info-value">{task.get('task_type','N/A')}</div></div>
        <div class="info-item"><div class="info-label">日志数量</div><div class="info-value">{task.get('log_count',0):,}</div></div>
        <div class="info-item"><div class="info-label">Token 消耗</div><div class="info-value">{task.get('token_usage',0):,}</div></div>
        <div class="info-item"><div class="info-label">耗时</div><div class="info-value">{duration or 'N/A'}</div></div>
        <div class="info-item"><div class="info-label">状态</div><div class="info-value" style="color:#52c41a">{task.get('status','unknown')}</div></div>
        <div class="info-item"><div class="info-label">成本</div><div class="info-value" style="color:#52c41a">${task.get('cost_usd',0):.4f}</div></div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">🎯 分析结果 ({len(results)})</div>
      {results_html or '<p style="color:#666;text-align:center;padding:20px">暂无分析结果</p>'}
    </div>

    {"<div class='section'><div class='section-title'>⏱ Pipeline 执行追踪</div><table><tr><th></th><th>阶段</th><th>耗时</th><th>状态</th></tr>" + stages_html + "</table></div>" if stages_html else ""}

    <div class="footer">
      LogMind AI 智能日志分析平台 · 报告自动生成 · {now}
    </div>
  </div>
</body>
</html>"""


def _generate_markdown_report(task: dict, results: list[dict], trace: dict | None) -> str:
    """Generate a Markdown report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# LogMind AI 分析报告",
        f"",
        f"**Task ID**: `{task.get('id','')}`  ",
        f"**生成时间**: {now}",
        f"",
        f"## 📋 任务概览",
        f"",
        f"| 指标 | 值 |",
        f"|------|------|",
        f"| 任务类型 | {task.get('task_type','N/A')} |",
        f"| 日志数量 | {task.get('log_count',0):,} |",
        f"| Token 消耗 | {task.get('token_usage',0):,} |",
        f"| 状态 | {task.get('status','unknown')} |",
        f"| 成本 | ${task.get('cost_usd',0):.4f} |",
        f"",
        f"## 🎯 分析结果 ({len(results)})",
        f"",
    ]

    for i, r in enumerate(results):
        lines.append(f"### [{i+1}] [{r.get('severity','info').upper()}] — {r.get('result_type','unknown')}")
        lines.append(f"")
        lines.append(f"**置信度**: {r.get('confidence_score',0)*100:.0f}%")
        lines.append(f"")
        lines.append(r.get("content", ""))
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

    if trace and trace.get("stages"):
        lines.append(f"## ⏱ Pipeline 执行追踪")
        lines.append(f"")
        lines.append(f"| 阶段 | 耗时 | 状态 |")
        lines.append(f"|------|------|------|")
        for s in trace["stages"]:
            icon = "✅" if s.get("status") == "ok" else "⏭️" if s.get("status") == "skipped" else "❌"
            lines.append(f"| {icon} {s.get('stage','')} | {s.get('duration_ms',0)}ms | {s.get('status','')} |")
        lines.append(f"")

    lines.append(f"---")
    lines.append(f"*LogMind AI 智能日志分析平台 · 报告自动生成*")

    return "\n".join(lines)


@router.post("/{task_id}/report", response_model=ReportResponse)
async def generate_report(
    task_id: str,
    session: DBSession,
    user: CurrentUser,
    format: str = "html",
):
    """
    Generate a downloadable analysis report.

    Supported formats: html, markdown
    HTML reports include inline styles for self-contained viewing.
    """
    task = await task_repo.get_by_id(session, task_id, tenant_id=user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Convert task to dict
    task_dict = {
        "id": task.id,
        "task_type": task.task_type,
        "log_count": task.log_count,
        "token_usage": task.token_usage,
        "cost_usd": task.cost_usd,
        "status": task.status,
        "created_at": str(task.created_at) if task.created_at else "",
        "completed_at": str(task.completed_at) if task.completed_at else "",
    }

    # Get results
    results_raw = await result_repo.get_all(session, filters={"task_id": task_id})
    results = []
    for r in results_raw:
        results.append({
            "severity": r.severity,
            "result_type": r.result_type,
            "content": r.content,
            "confidence_score": r.confidence_score,
        })

    # Get trace (from task metadata if available)
    trace = None
    if hasattr(task, "stage_trace") and task.stage_trace:
        import json as _json
        try:
            trace = _json.loads(task.stage_trace)
        except Exception:
            pass

    if format == "markdown":
        content = _generate_markdown_report(task_dict, results, trace)
        filename = f"logmind-report-{task_id[:8]}.md"
    else:
        content = _generate_html_report(task_dict, results, trace)
        filename = f"logmind-report-{task_id[:8]}.html"

    return ReportResponse(format=format, content=content, filename=filename)
