"""
Prometheus Metrics — Pipeline Observability

Exports key operational metrics in Prometheus format for Grafana dashboards.

Metrics:
  - logmind_pipeline_stage_duration_seconds: Histogram per stage
  - logmind_tokens_consumed_total: Counter per tenant/provider
  - logmind_dedup_hits_total: Counter per dedup layer
  - logmind_analysis_tasks_total: Counter per status
  - logmind_active_analysis_tasks: Gauge

Usage:
  Metrics are exposed at GET /metrics (no auth).
  Pipeline stages call record_*() functions after execution.
"""

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

from logmind.core.logging import get_logger

logger = get_logger(__name__)

# Use a custom registry to avoid collision with default metrics
if PROMETHEUS_AVAILABLE:
    REGISTRY = CollectorRegistry()

    # ── Pipeline Stage Duration ──────────────────────────
    PIPELINE_STAGE_DURATION = Histogram(
        "logmind_pipeline_stage_duration_seconds",
        "Time spent in each pipeline stage",
        labelnames=["stage", "status"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
        registry=REGISTRY,
    )

    # ── Token Consumption ────────────────────────────────
    TOKENS_CONSUMED = Counter(
        "logmind_tokens_consumed_total",
        "Total tokens consumed by AI providers",
        labelnames=["tenant_id", "provider"],
        registry=REGISTRY,
    )

    # ── Dedup Hit Rates ──────────────────────────────────
    DEDUP_HITS = Counter(
        "logmind_dedup_hits_total",
        "Number of dedup hits by layer",
        labelnames=["layer"],  # quality / fingerprint / semantic
        registry=REGISTRY,
    )

    # ── Analysis Task Counts ─────────────────────────────
    ANALYSIS_TASKS = Counter(
        "logmind_analysis_tasks_total",
        "Total analysis tasks by status",
        labelnames=["status"],  # completed / failed / skipped
        registry=REGISTRY,
    )

    # ── Active Tasks Gauge ───────────────────────────────
    ACTIVE_TASKS = Gauge(
        "logmind_active_analysis_tasks",
        "Currently running analysis tasks",
        registry=REGISTRY,
    )

    # ── Alert Counters ───────────────────────────────────
    ALERTS_FIRED = Counter(
        "logmind_alerts_fired_total",
        "Total alerts fired by priority",
        labelnames=["priority"],  # P0 / P1 / P2
        registry=REGISTRY,
    )


# ── Recording Functions ──────────────────────────────────

def record_stage_duration(stage: str, duration_seconds: float, status: str = "ok"):
    """Record pipeline stage execution duration."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        PIPELINE_STAGE_DURATION.labels(stage=stage, status=status).observe(duration_seconds)
    except Exception:
        pass


def record_tokens(tenant_id: str, provider: str, tokens: int):
    """Record token consumption."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        TOKENS_CONSUMED.labels(tenant_id=tenant_id, provider=provider).inc(tokens)
    except Exception:
        pass


def record_dedup_hit(layer: str):
    """Record a dedup hit. layer: quality / fingerprint / semantic."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        DEDUP_HITS.labels(layer=layer).inc()
    except Exception:
        pass


def record_task_completed(status: str):
    """Record task completion. status: completed / failed / skipped."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        ANALYSIS_TASKS.labels(status=status).inc()
    except Exception:
        pass


def record_alert_fired(priority: str):
    """Record alert fired. priority: P0 / P1 / P2."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        ALERTS_FIRED.labels(priority=priority).inc()
    except Exception:
        pass


def track_active_task():
    """Context manager for tracking active analysis tasks."""
    if not PROMETHEUS_AVAILABLE:
        return _noop_context()

    class _ActiveTaskTracker:
        def __enter__(self):
            ACTIVE_TASKS.inc()
            return self

        def __exit__(self, *args):
            ACTIVE_TASKS.dec()

    return _ActiveTaskTracker()


class _noop_context:
    """No-op context manager when prometheus is not available."""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def get_metrics_response() -> bytes:
    """Generate Prometheus metrics output."""
    if not PROMETHEUS_AVAILABLE:
        return b"# prometheus_client not installed\n"
    return generate_latest(REGISTRY)
