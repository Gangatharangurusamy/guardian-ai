"""GUARDIAN API health and metrics routes.

GET  /health   -- Always 200. Returns system status and db connectivity.
GET  /metrics  -- Prometheus text format exposition.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from guardian.api.schemas import HealthResponse

logger = logging.getLogger("guardian")

router = APIRouter(tags=["health"])

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _PROMETHEUS_AVAILABLE = True

    # Module-level singletons — created once, never recreated
    _traces_total = Counter(
        "guardian_traces_total",
        "Total number of trace events recorded",
    )
    _ethics_flags_total = Counter(
        "guardian_ethics_flags_total",
        "Total number of ethics violations detected",
        ["severity"],
    )
    _recovery_actions_total = Counter(
        "guardian_recovery_actions_total",
        "Total number of recovery actions triggered",
        ["action"],
    )
    _active_sessions = Gauge(
        "guardian_active_sessions",
        "Number of active agent sessions in the last 60 seconds",
    )
    _trace_duration_ms = Histogram(
        "guardian_trace_duration_ms",
        "Agent trace duration in milliseconds",
        buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
    )

except ImportError:
    _PROMETHEUS_AVAILABLE = False
    logger.warning("GUARDIAN: prometheus_client not installed; /metrics will return plain text.")


def _update_metrics_from_db() -> None:
    """Sync Prometheus counters/gauges from the current DB state."""
    if not _PROMETHEUS_AVAILABLE:
        return
    try:
        from guardian.store.reader import (
            get_recent_ethics_flags,
            get_recent_recovery_actions,
            list_traces,
        )
        traces = list_traces(limit=1000)
        flags = get_recent_ethics_flags(limit=1000)
        actions = get_recent_recovery_actions(limit=1000)

        # Reset and re-set counters by using _value directly isn't clean;
        # instead we just expose totals as gauges via labels
        # (Prometheus counters only go up; on startup we set them once)
        _traces_total._value.set(len(traces))  # type: ignore[attr-defined]

        severity_counts: dict[str, int] = {}
        for f in flags:
            sev = f.get("severity", "unknown").lower()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        for sev, count in severity_counts.items():
            _ethics_flags_total.labels(severity=sev)._value.set(count)  # type: ignore

        action_counts: dict[str, int] = {}
        for a in actions:
            act = a.get("action_taken", "unknown")
            action_counts[act] = action_counts.get(act, 0) + 1
        for act, count in action_counts.items():
            _recovery_actions_total.labels(action=act)._value.set(count)  # type: ignore

        _active_sessions.set(len([t for t in traces if t.get("status") == "running"]))

    except Exception as exc:
        logger.warning("GUARDIAN: metrics update failed: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check. Always returns 200. Never raises."""
    db_ok = False
    try:
        from guardian.store.reader import list_traces
        list_traces(limit=1)
        db_ok = True
    except Exception:
        pass
    return HealthResponse(status="healthy", version="0.1.0", db_connected=db_ok)


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    """Prometheus text format metrics exposition."""
    if _PROMETHEUS_AVAILABLE:
        _update_metrics_from_db()
        return PlainTextResponse(
            content=generate_latest(REGISTRY).decode("utf-8"),
            media_type=CONTENT_TYPE_LATEST,
        )
    # Fallback: plain text with a minimal exposition
    try:
        from guardian.store.reader import get_recent_ethics_flags, list_traces
        trace_count = len(list_traces(limit=1000))
        flag_count = len(get_recent_ethics_flags(limit=1000))
    except Exception:
        trace_count = 0
        flag_count = 0
    body = (
        "# HELP guardian_traces_total Total trace events\n"
        "# TYPE guardian_traces_total counter\n"
        f"guardian_traces_total {trace_count}\n"
        "# HELP guardian_ethics_flags_total Total ethics flags\n"
        "# TYPE guardian_ethics_flags_total counter\n"
        f"guardian_ethics_flags_total {flag_count}\n"
    )
    return PlainTextResponse(content=body, media_type="text/plain; version=0.0.4")
