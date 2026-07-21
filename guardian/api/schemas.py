"""GUARDIAN API Pydantic response schemas.

All models use pydantic v2. Field names match the dict keys returned
by guardian.store.reader functions exactly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response model for GET /health."""

    status: str
    version: str
    db_connected: bool


class TraceEventResponse(BaseModel):
    """Response model for a single trace event."""

    id: int | None = None
    session_id: str
    agent_name: str
    started_at: str
    ended_at: str
    duration_ms: int
    status: str
    calls: list[Any] = []
    metadata: dict[str, Any] = {}
    created_at: str = ""


class EthicsFlagResponse(BaseModel):
    """Response model for a single ethics flag."""

    id: int | None = None
    session_id: str
    agent_name: str
    violation_type: str
    severity: str
    description: str
    evidence: str = ""
    field_path: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = {}
    detected_at: str


class RecoveryActionResponse(BaseModel):
    """Response model for a single recovery action."""

    id: int | None = None
    session_id: str
    agent_name: str
    failure_type: str
    root_cause: str = ""
    suggestion: str = ""
    action_taken: str
    success: bool
    approval_result: str | None = None
    retries_attempted: int = 0
    model_used: str = ""
    metadata: dict[str, Any] = {}
    recovered_at: str


class ComplianceReportResponse(BaseModel):
    """Response model for a compliance report (full JSON payload)."""

    session_id: str
    agent_name: str
    generated_at: str
    eu_ai_act: dict[str, Any] | None = None
    owasp: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
