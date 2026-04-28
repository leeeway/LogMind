"""
Alert Context — Rich context for smart alert cards.

Provides similar alerts, frequency trends, and AI suggestions.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

from logmind.core.dependencies import CurrentUser, DBSession
from logmind.core.logging import get_logger
from logmind.domain.alert.models import AlertHistory, AlertRule
from logmind.shared.base_repository import BaseRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/alerts", tags=["Alerts"])
alert_repo = BaseRepository(AlertHistory)
rule_repo = BaseRepository(AlertRule)


class SimilarAlert(BaseModel):
    id: str
    severity: str
    status: str
    message: str
    fired_at: str


class FrequencyPoint(BaseModel):
    date: str
    count: int


class AlertContextResponse(BaseModel):
    alert_id: str
    similar_alerts: list[SimilarAlert]
    frequency_trend: list[FrequencyPoint]
    ai_suggestion: str
    total_similar: int


@router.get("/{alert_id}/context", response_model=AlertContextResponse)
async def get_alert_context(
    alert_id: str,
    session: DBSession,
    user: CurrentUser,
):
    """
    Get rich context for a specific alert.

    Includes:
    - Similar historical alerts (same rule, last 30 days)
    - 7-day firing frequency trend
    - AI-generated handling suggestion
    """
    # Get the alert
    alert = await alert_repo.get_by_id(session, alert_id)
    if not alert or alert.tenant_id != user.tenant_id:
        raise HTTPException(404, "Alert not found")

    # 1. Similar alerts — same rule, last 30 days
    similar: list[SimilarAlert] = []
    if alert.alert_rule_id:
        since_30d = datetime.now(timezone.utc) - timedelta(days=30)
        stmt = (
            select(AlertHistory)
            .where(
                AlertHistory.alert_rule_id == alert.alert_rule_id,
                AlertHistory.tenant_id == user.tenant_id,
                AlertHistory.id != alert_id,
                AlertHistory.fired_at >= since_30d,
            )
            .order_by(AlertHistory.fired_at.desc())
            .limit(10)
        )
        result = await session.execute(stmt)
        for a in result.scalars().all():
            similar.append(SimilarAlert(
                id=a.id,
                severity=a.severity,
                status=a.status,
                message=(a.message or "")[:200],
                fired_at=a.fired_at.isoformat() if a.fired_at else "",
            ))

    # 2. Frequency trend — last 7 days, count per day
    trend: list[FrequencyPoint] = []
    if alert.alert_rule_id:
        since_7d = datetime.now(timezone.utc) - timedelta(days=7)
        trunc_day = func.date_trunc("day", AlertHistory.fired_at)
        stmt = (
            select(trunc_day.label("day"), func.count().label("cnt"))
            .where(
                AlertHistory.alert_rule_id == alert.alert_rule_id,
                AlertHistory.tenant_id == user.tenant_id,
                AlertHistory.fired_at >= since_7d,
            )
            .group_by(trunc_day)
            .order_by(trunc_day)
        )
        result = await session.execute(stmt)
        for row in result.all():
            trend.append(FrequencyPoint(
                date=row[0].strftime("%m-%d") if row[0] else "",
                count=int(row[1] or 0),
            ))

    # 3. AI suggestion (simple template — production would call LLM)
    severity_label = {"critical": "严重", "warning": "警告", "info": "信息"}.get(alert.severity, alert.severity)
    suggestion = f"""### 处置建议

**告警级别**: {severity_label}

**建议操作**:
1. 检查关联服务的日志和资源使用情况
2. 确认是否有近期的部署变更或配置修改
3. {"立即介入处理，通知值班负责人" if alert.severity == "critical" else "持续观察，如果频率增加则升级处理"}

**历史参考**: 过去 30 天内有 {len(similar)} 次类似告警{"，建议排查根因以防止反复触发" if len(similar) > 3 else ""}。

---
💡 *可以在 AI 诊断页面输入问题描述，获取更深入的分析*
"""

    return AlertContextResponse(
        alert_id=alert_id,
        similar_alerts=similar,
        frequency_trend=trend,
        ai_suggestion=suggestion,
        total_similar=len(similar),
    )
