"""GUARDIAN API recovery routes.

GET   /recovery/actions                   -- recent recovery actions
GET   /recovery/summary                   -- aggregate success/failure stats
POST  /recovery/approve/{session_id}      -- manual approval stub
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("guardian")

router = APIRouter(prefix="/recovery", tags=["recovery"])


@router.get("/actions")
async def get_actions(
    agent_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Return recent recovery actions, optionally filtered by agent_name."""
    from guardian.store.reader import (
        get_recent_recovery_actions,
        get_recovery_actions_by_agent,
    )
    if agent_name:
        return get_recovery_actions_by_agent(agent_name, limit=limit)
    return get_recent_recovery_actions(limit=limit)


@router.get("/summary")
async def get_summary() -> dict[str, Any]:
    """Return aggregate recovery action statistics."""
    from guardian.store.reader import get_recent_recovery_actions

    all_actions = get_recent_recovery_actions(limit=5000)
    total = len(all_actions)
    successes = sum(1 for a in all_actions if a.get("success"))
    success_rate = round(successes / total, 4) if total > 0 else 0.0

    by_action: dict[str, int] = {}
    by_failure: dict[str, int] = {}
    for a in all_actions:
        act = a.get("action_taken", "unknown")
        ft = a.get("failure_type", "unknown")
        by_action[act] = by_action.get(act, 0) + 1
        by_failure[ft] = by_failure.get(ft, 0) + 1

    return {
        "total": total,
        "successes": successes,
        "failures": total - successes,
        "success_rate": success_rate,
        "by_action": by_action,
        "by_failure_type": by_failure,
    }


@router.post("/approve/{session_id}")
async def approve(session_id: str) -> JSONResponse:
    """Manual approval stub. Always returns 'approved' for the given session."""
    return JSONResponse(content={"status": "approved", "session_id": session_id})
