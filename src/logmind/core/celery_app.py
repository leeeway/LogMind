"""
Celery Application Configuration

Async task queue for log analysis, scheduled patrols, and RAG indexing.
"""

from celery import Celery
from celery.schedules import crontab

from logmind.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "logmind",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    # Reliability
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Results
    result_expires=3600,
    # Auto-discover tasks from domain modules
    task_routes={
        "logmind.domain.analysis.tasks.*": {"queue": "analysis"},
        "logmind.domain.alert.tasks.*": {"queue": "alert"},
        "logmind.domain.rag.tasks.*": {"queue": "rag"},
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks([
    "logmind.domain.analysis",
    "logmind.domain.alert",
    "logmind.domain.rag",
])

# ── Beat Schedule (定时任务) ──────────────────────────────
celery_app.conf.beat_schedule = {
    # Scheduled log patrol — runs every 30 minutes by default
    # Only analyzes ERROR/CRITICAL severity to control AI costs
    "scheduled-log-patrol": {
        "task": "logmind.domain.alert.tasks.scheduled_log_patrol",
        "schedule": crontab(minute=f"*/{settings.analysis_cooldown_minutes}"),
        "options": {"queue": "alert"},
    },
    # Cleanup old analysis tasks — daily at 3 AM
    "cleanup-old-tasks": {
        "task": "logmind.domain.analysis.tasks.cleanup_old_tasks",
        "schedule": crontab(hour=3, minute=0),
        "options": {"queue": "analysis"},
    },
}
