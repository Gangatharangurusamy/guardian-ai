"""Trace reader for GUARDIAN store.

Provides simple query functions for retrieving trace events from the
database. Returns plain dicts (JSON-serializable), not ORM objects,
so callers don't need SQLAlchemy knowledge.

Note: These are direct DB queries for Phase 1. Phase 4 will expose
these through a FastAPI layer.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from guardian.store.db import get_session
from guardian.store.models import EthicsFlagRecord, RecoveryActionRecord, TraceEventRecord

logger = logging.getLogger("guardian")


def _record_to_dict(record: TraceEventRecord) -> dict[str, Any]:
    """Convert a TraceEventRecord ORM instance to a plain dict.

    Args:
        record: The ORM record to convert.

    Returns:
        A JSON-serializable dict matching the TraceEvent schema.
    """
    try:
        calls = json.loads(record.calls_json) if record.calls_json else []
    except (json.JSONDecodeError, TypeError):
        calls = []

    try:
        metadata = json.loads(record.metadata_json) if record.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    return {
        "id": record.id,
        "session_id": record.session_id,
        "agent_name": record.agent_name,
        "started_at": record.started_at.isoformat() if record.started_at else "",
        "ended_at": record.ended_at.isoformat() if record.ended_at else "",
        "duration_ms": record.duration_ms,
        "status": record.status,
        "calls": calls,
        "metadata": metadata,
        "created_at": record.created_at.isoformat() if record.created_at else "",
    }


def _flag_to_dict(record: EthicsFlagRecord) -> dict[str, Any]:
    """Convert an EthicsFlagRecord ORM instance to a plain dict.

    Args:
        record: The ORM record to convert.

    Returns:
        A JSON-serializable dict representation of the flag.
    """
    try:
        metadata = json.loads(record.metadata_json) if record.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    return {
        "id": record.id,
        "session_id": record.session_id,
        "agent_name": record.agent_name,
        "violation_type": record.violation_type,
        "severity": record.severity,
        "description": record.description,
        "evidence": record.evidence,
        "field_path": record.field_path,
        "confidence": record.confidence,
        "metadata": metadata,
        "detected_at": record.detected_at.isoformat() if record.detected_at else "",
    }


def get_trace(session_id: str) -> dict[str, Any] | None:
    """Retrieve a single trace event by session ID.

    Args:
        session_id: The UUID4 session identifier to look up.

    Returns:
        A TraceEvent dict if found, None otherwise.
    """
    with get_session() as session:
        stmt = select(TraceEventRecord).where(
            TraceEventRecord.session_id == session_id
        )
        record = session.execute(stmt).scalar_one_or_none()
        if record is None:
            return None
        return _record_to_dict(record)


def list_traces(
    agent_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List trace events, optionally filtered by agent name.

    Returns traces ordered by creation time (newest first).

    Args:
        agent_name: If provided, only return traces for this agent.
        limit: Maximum number of traces to return. Defaults to 50.
        offset: Number of traces to skip for pagination. Defaults to 0.
            Applied at the database query level for efficiency.

    Returns:
        A list of TraceEvent dicts.
    """
    with get_session() as session:
        stmt = select(TraceEventRecord).order_by(
            TraceEventRecord.created_at.desc()
        )

        if agent_name is not None:
            stmt = stmt.where(TraceEventRecord.agent_name == agent_name)

        stmt = stmt.offset(offset).limit(limit)
        records = session.execute(stmt).scalars().all()
        return [_record_to_dict(r) for r in records]


def get_recent_failures(limit: int = 20) -> list[dict[str, Any]]:
    """Retrieve recent trace events where status is not 'success'.

    Returns failed/errored traces ordered by creation time (newest first).

    Args:
        limit: Maximum number of failure traces to return. Defaults to 20.

    Returns:
        A list of TraceEvent dicts with non-success status.
    """
    with get_session() as session:
        stmt = (
            select(TraceEventRecord)
            .where(TraceEventRecord.status != "success")
            .order_by(TraceEventRecord.created_at.desc())
            .limit(limit)
        )
        records = session.execute(stmt).scalars().all()
        return [_record_to_dict(r) for r in records]


def get_ethics_flags(session_id: str) -> list[dict[str, Any]]:
    """Retrieve all ethics violations for a specific session ID.

    Args:
        session_id: The UUID4 session identifier.

    Returns:
        A list of EthicsViolation dictionaries.
    """
    with get_session() as session:
        stmt = (
            select(EthicsFlagRecord)
            .where(EthicsFlagRecord.session_id == session_id)
            .order_by(EthicsFlagRecord.detected_at.asc())
        )
        records = session.execute(stmt).scalars().all()
        return [_flag_to_dict(r) for r in records]


def get_recent_ethics_flags(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve recent ethics violations, ordered by detection time (newest first).

    Args:
        limit: Maximum number of records to return. Defaults to 50.

    Returns:
        A list of EthicsViolation dictionaries.
    """
    with get_session() as session:
        stmt = (
            select(EthicsFlagRecord)
            .order_by(EthicsFlagRecord.detected_at.desc())
            .limit(limit)
        )
        records = session.execute(stmt).scalars().all()
        return [_flag_to_dict(r) for r in records]


def get_flags_by_severity(severity: str, limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve recent ethics violations filtered by severity level.

    Args:
        severity: Severity string ('log', 'warn', 'block').
        limit: Maximum number of records to return. Defaults to 50.

    Returns:
        A list of EthicsViolation dictionaries.
    """
    with get_session() as session:
        stmt = (
            select(EthicsFlagRecord)
            .where(EthicsFlagRecord.severity == severity.lower())
            .order_by(EthicsFlagRecord.detected_at.desc())
            .limit(limit)
        )
        records = session.execute(stmt).scalars().all()
        return [_flag_to_dict(r) for r in records]



def _recovery_to_dict(record):
    """Convert a RecoveryActionRecord ORM instance to a plain dict.

    Args:
        record: The ORM record to convert.

    Returns:
        A JSON-serializable dict representation of the recovery action.
    """
    import json
    try:
        metadata = json.loads(record.metadata_json) if record.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    return {
        "id": record.id,
        "session_id": record.session_id,
        "agent_name": record.agent_name,
        "failure_type": record.failure_type,
        "root_cause": record.root_cause,
        "suggestion": record.suggestion,
        "action_taken": record.action_taken,
        "success": record.success,
        "approval_result": record.approval_result,
        "retries_attempted": record.retries_attempted,
        "model_used": record.model_used,
        "metadata": metadata,
        "recovered_at": record.recovered_at.isoformat() if record.recovered_at else "",
    }


def get_recovery_actions(session_id):
    """Retrieve all recovery actions for a specific session ID.

    Args:
        session_id: The UUID4 session identifier.

    Returns:
        A list of recovery action dictionaries ordered by recovered_at ascending.
    """
    from sqlalchemy import select

    from guardian.store.db import get_session
    with get_session() as session:
        stmt = (
            select(RecoveryActionRecord)
            .where(RecoveryActionRecord.session_id == session_id)
            .order_by(RecoveryActionRecord.recovered_at.asc())
        )
        records = session.execute(stmt).scalars().all()
        return [_recovery_to_dict(r) for r in records]


def get_recent_recovery_actions(limit=50):
    """Retrieve recent recovery actions ordered newest first.

    Args:
        limit: Maximum number of records to return. Defaults to 50.

    Returns:
        A list of recovery action dictionaries.
    """
    from sqlalchemy import select

    from guardian.store.db import get_session
    with get_session() as session:
        stmt = (
            select(RecoveryActionRecord)
            .order_by(RecoveryActionRecord.recovered_at.desc())
            .limit(limit)
        )
        records = session.execute(stmt).scalars().all()
        return [_recovery_to_dict(r) for r in records]


def get_recovery_actions_by_agent(agent_name, limit=50):
    """Retrieve recent recovery actions for a specific agent.

    Args:
        agent_name: The agent name to filter by.
        limit: Maximum number of records to return. Defaults to 50.

    Returns:
        A list of recovery action dictionaries ordered newest first.
    """
    from sqlalchemy import select

    from guardian.store.db import get_session
    with get_session() as session:
        stmt = (
            select(RecoveryActionRecord)
            .where(RecoveryActionRecord.agent_name == agent_name)
            .order_by(RecoveryActionRecord.recovered_at.desc())
            .limit(limit)
        )
        records = session.execute(stmt).scalars().all()
        return [_recovery_to_dict(r) for r in records]
