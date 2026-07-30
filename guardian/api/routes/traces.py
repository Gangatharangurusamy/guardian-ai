"""GUARDIAN API trace routes.

Route registration order matters — /traces/failures MUST come before
/traces/{session_id} to avoid "failures" being parsed as a session_id.

GET  /traces/failures                   -- recent failed traces
GET  /traces                            -- list traces (filterable, paginated)
GET  /traces/{session_id}               -- single trace (404 if not found)
GET  /traces/{session_id}/ethics        -- ethics flags for session
GET  /traces/{session_id}/recovery      -- recovery actions for session
GET  /traces/{session_id}/compliance    -- full compliance report for session
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from guardian.api.schemas import (
    EthicsFlagResponse,
    RecoveryActionResponse,
    TraceEventResponse,
)

logger = logging.getLogger("guardian")

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("/failures", response_model=list[TraceEventResponse])
async def get_failures(limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, Any]]:
    """Return recent trace events where status is not 'success'."""
    from guardian.store.reader import get_recent_failures
    return get_recent_failures(limit=limit)


@router.get("", response_model=list[TraceEventResponse])
async def list_traces(
    agent_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List trace events with optional agent_name filter and DB-level pagination."""
    from guardian.store.reader import list_traces as _list_traces
    return _list_traces(agent_name=agent_name, limit=limit, offset=offset)


@router.get("/{session_id}", response_model=TraceEventResponse)
async def get_trace(session_id: str) -> dict[str, Any]:
    """Return a single trace by session_id. Raises 404 if not found."""
    from guardian.store.reader import get_trace as _get_trace
    result = _get_trace(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Trace '{session_id}' not found")
    return result


@router.get("/{session_id}/ethics", response_model=list[EthicsFlagResponse])
async def get_ethics(session_id: str) -> list[dict[str, Any]]:
    """Return all ethics flags for a session."""
    from guardian.store.reader import get_ethics_flags
    return get_ethics_flags(session_id)


@router.get("/{session_id}/recovery", response_model=list[RecoveryActionResponse])
async def get_recovery(session_id: str) -> list[dict[str, Any]]:
    """Return all recovery actions for a session."""
    from guardian.store.reader import get_recovery_actions
    return get_recovery_actions(session_id)


@router.get("/{session_id}/compliance")
async def get_compliance(session_id: str) -> JSONResponse:
    """Return a full EU AI Act + OWASP compliance report for a session."""
    from guardian.compliance.exporter import ComplianceExporter
    report = ComplianceExporter().generate_report(session_id)
    return JSONResponse(content=dataclasses.asdict(report))
