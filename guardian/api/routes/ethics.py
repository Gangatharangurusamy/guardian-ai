"""GUARDIAN API ethics routes.

GET  /ethics/flags    -- recent ethics flags (optional severity filter)
GET  /ethics/summary  -- aggregate counts by severity, type, and 24h window
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger("guardian")

router = APIRouter(prefix="/ethics", tags=["ethics"])


@router.get("/flags")
async def get_flags(
    severity: str | None = Query(default=None, description="Filter by severity: log, warn, block"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Return recent ethics flags, optionally filtered by severity."""
    from guardian.store.reader import get_flags_by_severity, get_recent_ethics_flags
    if severity:
        return get_flags_by_severity(severity.lower(), limit=limit)
    return get_recent_ethics_flags(limit=limit)


@router.get("/summary")
async def get_summary() -> dict[str, Any]:
    """Return aggregate ethics flag counts by severity and violation type."""
    from guardian.store.reader import get_recent_ethics_flags

    all_flags = get_recent_ethics_flags(limit=5000)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    recent_24h = 0

    for flag in all_flags:
        sev = flag.get("severity", "unknown").lower()
        vt = flag.get("violation_type", "unknown")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_type[vt] = by_type.get(vt, 0) + 1

        detected_at_str = flag.get("detected_at", "")
        if detected_at_str:
            try:
                detected_at = datetime.fromisoformat(detected_at_str)
                if detected_at.tzinfo is None:
                    detected_at = detected_at.replace(tzinfo=timezone.utc)
                if detected_at >= cutoff:
                    recent_24h += 1
            except (ValueError, TypeError):
                pass

    return {
        "total": len(all_flags),
        "recent_24h": recent_24h,
        "by_severity": by_severity,
        "by_type": by_type,
    }
