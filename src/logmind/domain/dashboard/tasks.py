"""Celery task registration for dashboard domain."""

from logmind.domain.dashboard.standup_tasks import (  # noqa: F401
    dispatch_daily_standup_auto_share,
    share_daily_standup_for_tenant,
)
