"""
Incident Router — CRUD + Timeline for Incident War Room.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.incident import Incident, IncidentEvent
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)
router = APIRouter(prefix="/incidents", tags=["Incidents"])
incident_repo = BaseRepository(Incident)
event_repo = BaseRepository(IncidentEvent)


class CreateIncidentRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    severity: str = "P2"


class UpdateIncidentRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    severity: str | None = None
    status: str | None = None
    assignee: str | None = None
    postmortem: str | None = None


class AddEventRequest(BaseModel):
    event_type: str = "message"  # alert / action / message / ai
    content: str = Field(..., min_length=1)


@router.get("")
async def list_incidents(db: DBSession, user: CurrentUser):
    """List all incidents for the tenant."""
    incidents = await incident_repo.get_all(db, tenant_id=user.tenant_id, limit=100)
    results = []
    for inc in incidents:
        # Calculate live duration
        if inc.status != "resolved":
            duration = int((datetime.now(timezone.utc) - inc.created_at.replace(tzinfo=timezone.utc)).total_seconds()) if inc.created_at else 0
        else:
            duration = inc.duration_seconds or 0

        results.append({
            "id": inc.id,
            "title": inc.title,
            "description": inc.description,
            "severity": inc.severity,
            "status": inc.status,
            "assignee": inc.assignee,
            "duration_seconds": duration,
            "related_alert_ids": inc.related_alert_ids or [],
            "related_task_ids": inc.related_task_ids or [],
            "tags": inc.tags or [],
            "created_at": str(inc.created_at),
            "updated_at": str(inc.updated_at),
            "resolved_at": str(inc.resolved_at) if inc.resolved_at else None,
        })
    # Sort by status (active first), then by created_at desc
    status_order = {"investigating": 0, "identified": 1, "monitoring": 2, "resolved": 3}
    results.sort(key=lambda x: (status_order.get(x["status"], 9), x["created_at"]), reverse=False)
    results.sort(key=lambda x: status_order.get(x["status"], 9))
    return {"incidents": results, "total": len(results)}


@router.post("")
async def create_incident(req: CreateIncidentRequest, db: DBSession, user: CurrentUser):
    """Create a new incident."""
    inc = Incident(
        id=str(uuid.uuid4()),
        tenant_id=user.tenant_id,
        title=req.title,
        description=req.description,
        severity=req.severity,
        status="investigating",
        assignee=user.sub,
    )
    db.add(inc)
    # Auto-create first timeline event
    evt = IncidentEvent(
        id=str(uuid.uuid4()),
        incident_id=inc.id,
        event_type="status_change",
        content=f"故障创建 — {req.severity} {req.title}",
        user=user.sub,
    )
    db.add(evt)
    await db.commit()
    return {"id": inc.id, "status": "created"}


@router.get("/{incident_id}")
async def get_incident(incident_id: str, db: DBSession, user: CurrentUser):
    """Get incident detail with timeline."""
    inc = await incident_repo.get_by_id(db, incident_id)
    if not inc or inc.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Incident not found")

    events = await event_repo.get_all(db, incident_id=incident_id, limit=200)
    timeline = [
        {
            "id": e.id,
            "type": e.event_type,
            "content": e.content,
            "user": e.user,
            "metadata": e.event_metadata or {},
            "created_at": str(e.created_at),
        }
        for e in sorted(events, key=lambda x: x.created_at or datetime.min)
    ]

    if inc.status != "resolved":
        duration = int((datetime.now(timezone.utc) - inc.created_at.replace(tzinfo=timezone.utc)).total_seconds()) if inc.created_at else 0
    else:
        duration = inc.duration_seconds or 0

    return {
        "id": inc.id,
        "title": inc.title,
        "description": inc.description,
        "severity": inc.severity,
        "status": inc.status,
        "assignee": inc.assignee,
        "duration_seconds": duration,
        "related_alert_ids": inc.related_alert_ids or [],
        "related_task_ids": inc.related_task_ids or [],
        "tags": inc.tags or [],
        "postmortem": inc.postmortem or "",
        "timeline": timeline,
        "created_at": str(inc.created_at),
        "resolved_at": str(inc.resolved_at) if inc.resolved_at else None,
    }


@router.patch("/{incident_id}")
async def update_incident(incident_id: str, req: UpdateIncidentRequest, db: DBSession, user: CurrentUser):
    """Update incident fields."""
    inc = await incident_repo.get_by_id(db, incident_id)
    if not inc or inc.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Incident not found")

    changes = []
    if req.title is not None:
        inc.title = req.title
    if req.description is not None:
        inc.description = req.description
    if req.severity is not None and req.severity != inc.severity:
        changes.append(f"严重度: {inc.severity} → {req.severity}")
        inc.severity = req.severity
    if req.assignee is not None and req.assignee != inc.assignee:
        changes.append(f"指派: {inc.assignee or '无'} → {req.assignee}")
        inc.assignee = req.assignee
    if req.status is not None and req.status != inc.status:
        changes.append(f"状态: {inc.status} → {req.status}")
        inc.status = req.status
        if req.status == "resolved":
            inc.resolved_at = datetime.now(timezone.utc)
            if inc.created_at:
                inc.duration_seconds = int((inc.resolved_at - inc.created_at.replace(tzinfo=timezone.utc)).total_seconds())
    if req.postmortem is not None:
        inc.postmortem = req.postmortem

    # Log status change event
    if changes:
        evt = IncidentEvent(
            id=str(uuid.uuid4()),
            incident_id=incident_id,
            event_type="status_change",
            content=" | ".join(changes),
            user=user.sub,
        )
        db.add(evt)

    await db.commit()
    return {"ok": True}


@router.post("/{incident_id}/events")
async def add_event(incident_id: str, req: AddEventRequest, db: DBSession, user: CurrentUser):
    """Add a timeline event to an incident."""
    inc = await incident_repo.get_by_id(db, incident_id)
    if not inc or inc.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Incident not found")

    evt = IncidentEvent(
        id=str(uuid.uuid4()),
        incident_id=incident_id,
        event_type=req.event_type,
        content=req.content,
        user=user.sub,
    )
    db.add(evt)
    await db.commit()
    return {"id": evt.id, "ok": True}


@router.post("/{incident_id}/generate-postmortem")
async def generate_postmortem(incident_id: str, db: DBSession, user: CurrentUser):
    """
    AI-powered postmortem generation.

    Collects all related analysis results, alert history, and timeline events,
    then calls LLM to generate a structured RCA (Root Cause Analysis) report.
    """
    inc = await incident_repo.get_by_id(db, incident_id)
    if not inc or inc.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Incident not found")

    # 1. Collect related analysis results
    from logmind.domain.analysis.models import LogAnalysisTask, AnalysisResult
    from sqlalchemy import select

    analysis_summaries = []
    for tid in (inc.related_task_ids or []):
        task = await db.get(LogAnalysisTask, tid)
        if not task:
            continue
        results = (await db.execute(
            select(AnalysisResult).where(AnalysisResult.task_id == tid)
        )).scalars().all()
        for r in results:
            analysis_summaries.append(
                f"[{r.severity.upper()}] {r.content[:500]}"
            )

    # 2. Collect timeline events
    events = await event_repo.get_all(db, incident_id=incident_id, limit=200)
    timeline_text = "\n".join([
        f"- {e.created_at.strftime('%H:%M:%S') if e.created_at else '??:??'} "
        f"[{e.event_type}] {e.content[:200]}"
        for e in sorted(events, key=lambda x: x.created_at or datetime.min)
    ])

    # 3. Collect alert messages
    from logmind.domain.alert.models import AlertHistory
    alert_messages = []
    for aid in (inc.related_alert_ids or []):
        alert = await db.get(AlertHistory, aid)
        if alert:
            alert_messages.append(f"[{alert.severity}] {alert.message[:300]}")

    # 4. Build AI prompt
    duration = inc.duration_seconds or 0
    duration_text = f"{duration // 3600}h {(duration % 3600) // 60}m" if duration > 0 else "进行中"

    prompt = f"""请基于以下故障信息生成一份专业的故障复盘报告（RCA）。

## 故障基本信息
- 标题: {inc.title}
- 严重度: {inc.severity}
- 状态: {inc.status}
- 持续时间: {duration_text}
- 创建时间: {inc.created_at}
- 描述: {inc.description or '无'}

## AI 分析结论
{chr(10).join(analysis_summaries) or '无分析数据'}

## 告警记录
{chr(10).join(alert_messages) or '无告警记录'}

## 故障时间线
{timeline_text or '无时间线事件'}

## 输出要求
请用 Markdown 格式输出结构化复盘报告，包含：

### 1. 故障概述
一句话总结故障现象和影响

### 2. 时间线
关键节点的时间轴（发现 → 定位 → 修复 → 恢复）

### 3. 根因分析
基于 AI 分析结论，推导故障根因（技术层面）

### 4. 影响范围
受影响的服务、用户、业务

### 5. 改进措施
给出 3-5 条可操作的改进建议，每条包含：
- 措施描述
- 负责人建议（角色）
- 优先级（P0/P1/P2）

### 6. 经验教训
从本次故障中学到的关键教训

请直接输出报告，不要加前缀。"""

    # 5. Call AI
    try:
        from logmind.domain.provider.base import ChatMessage, ChatRequest
        from logmind.domain.provider.manager import provider_manager

        provider = await provider_manager.get_provider(db, user.tenant_id)
        if not provider:
            raise HTTPException(status_code=400, detail="No AI provider configured")

        request = ChatRequest(
            messages=[
                ChatMessage(role="system", content="你是 LogMind 故障复盘专家，擅长撰写结构化的 RCA（根因分析）报告。"),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.3,
            max_tokens=3000,
        )
        response = await provider.chat(request)
        postmortem_text = response.content

        # 6. Save to incident
        inc.postmortem = postmortem_text
        evt = IncidentEvent(
            id=str(uuid.uuid4()),
            incident_id=incident_id,
            event_type="ai",
            content="🤖 AI 自动生成复盘报告",
            user="LogMind AI",
        )
        db.add(evt)
        await db.commit()

        logger.info("ai_postmortem_generated", incident_id=incident_id)
        return {"ok": True, "postmortem": postmortem_text}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("ai_postmortem_failed", incident_id=incident_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"AI 复盘生成失败: {str(e)[:200]}")

