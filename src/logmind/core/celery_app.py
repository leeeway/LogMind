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
        "logmind.domain.http_access.tasks.*": {"queue": "alert"},
        "logmind.domain.dashboard.tasks.*": {"queue": "alert"},
        "logmind.domain.rag.tasks.*": {"queue": "rag"},
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks([
    "logmind.domain.analysis",
    "logmind.domain.alert",
    "logmind.domain.http_access",
    "logmind.domain.dashboard",
    "logmind.domain.rag",
    "logmind.domain.tenant",
])

# ── Beat Schedule (定时任务) ──────────────────────────────
celery_app.conf.beat_schedule = {
    # Scheduled log patrol — runs every 5 minutes by default
    # Only analyzes ERROR/CRITICAL severity to control AI costs
    "scheduled-log-patrol": {
        "task": "logmind.domain.alert.tasks.scheduled_log_patrol",
        "schedule": crontab(minute=f"*/{settings.effective_patrol_interval_minutes}"),
        "options": {"queue": "alert"},
    },
    # Global Nginx/Ingress patrol. The task itself is a no-op until enabled.
    "scheduled-http-access-patrol": {
        "task": "logmind.domain.http_access.tasks.scheduled_http_access_patrol",
        "schedule": crontab(minute=f"*/{settings.http_access_window_minutes}"),
        "options": {"queue": "alert"},
    },
    "cleanup-http-access-metrics": {
        "task": "logmind.domain.http_access.tasks.cleanup_http_access_metrics",
        "schedule": crontab(hour=3, minute=30),
        "options": {"queue": "alert"},
    },
    "http-access-pending-digest": {
        "task": "logmind.domain.http_access.tasks.send_http_access_pending_digest",
        "schedule": crontab(hour=9, minute=30, day_of_week="1-5"),
        "options": {"queue": "alert"},
    },
    "sync-http-access-repositories": {
        "task": "logmind.domain.http_access.tasks.scheduled_sync_http_repositories",
        "schedule": crontab(minute=f"*/{settings.http_access_repo_sync_minutes}"),
        "options": {"queue": "alert"},
    },
    # Cleanup old analysis tasks — daily at 3 AM
    "cleanup-old-tasks": {
        "task": "logmind.domain.analysis.tasks.cleanup_old_tasks",
        "schedule": crontab(hour=3, minute=0),
        "options": {"queue": "analysis"},
    },
    # Daily analysis digest report — every day at 9:00 AM
    "daily-digest": {
        "task": "logmind.domain.alert.tasks.send_daily_digest",
        "schedule": crontab(hour=9, minute=0),
        "options": {"queue": "alert"},
    },
    # Weekly analysis digest report — Monday at 9:00 AM
    "weekly-digest": {
        "task": "logmind.domain.alert.tasks.send_weekly_digest",
        "schedule": crontab(hour=9, minute=0, day_of_week=1),
        "options": {"queue": "alert"},
    },
    # Daily standup auto-share dispatcher — every 5 minutes
    "daily-standup-auto-share": {
        "task": "logmind.domain.dashboard.tasks.dispatch_daily_standup_auto_share",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "alert"},
    },
    # ES index auto-discovery — scan for new master-* indices
    "discover-business-lines": {
        "task": "logmind.domain.tenant.tasks.discover_business_lines",
        "schedule": crontab(minute=f"*/{settings.auto_discover_interval_minutes}"),
        "options": {"queue": "alert"},
    },
}
