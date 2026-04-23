"""
Runtime Detection — Determine current execution environment.

Uses LOGMIND_RUNTIME env var with sys.argv fallback.
Set LOGMIND_RUNTIME=celery in your worker/beat container or supervisord.
"""

import os
import sys


def is_celery_runtime() -> bool:
    """Detect if we're running inside a Celery worker/beat process."""
    # Prefer explicit env var
    runtime = os.getenv("LOGMIND_RUNTIME", "").lower()
    if runtime:
        return runtime == "celery"
    # Fallback to sys.argv heuristic
    return (
        "celery" in sys.argv[0]
        or (len(sys.argv) > 1 and "celery" in sys.argv[1])
    )
