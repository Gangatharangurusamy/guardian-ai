"""Trace serialization for GUARDIAN SDK.

Converts a TraceContext and its CapturedCall events into a single
JSON-serializable dictionary (TraceEvent). Designed to never raise —
any serialization error is caught and replaced with '<unserializable>'.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from guardian.sdk.capture import CapturedCall
from guardian.sdk.context import TraceContext

logger = logging.getLogger("guardian")


def _safe_isoformat(dt: datetime | None) -> str:
    """Convert a datetime to ISO 8601 string, safely.

    Args:
        dt: A datetime object, or None.

    Returns:
        ISO 8601 formatted string, or '<unserializable>' on failure.
    """
    if dt is None:
        return ""
    try:
        return dt.isoformat()
    except Exception:
        return "<unserializable>"


def _serialize_call(call: CapturedCall) -> dict[str, Any]:
    """Convert a CapturedCall to a JSON-serializable dict.

    Args:
        call: The CapturedCall to serialize.

    Returns:
        Dict matching the TraceEvent.calls[] schema.
    """
    try:
        return {
            "function": call.function_name,
            "start_time": _safe_isoformat(call.start_time),
            "end_time": _safe_isoformat(call.end_time),
            "args_preview": call.args_preview,
            "result_preview": call.result_preview,
            "duration_ms": call.duration_ms,
            "error": call.exception_info,
            "retry_count": call.retry_count,
            "estimated_tokens": call.estimated_tokens,
        }
    except Exception:
        return {
            "function": "<unserializable>",
            "start_time": "",
            "end_time": "",
            "args_preview": "<unserializable>",
            "result_preview": "<unserializable>",
            "duration_ms": 0,
            "error": None,
            "retry_count": 0,
            "estimated_tokens": 0,
        }


def to_json(context: TraceContext) -> dict[str, Any]:
    """Convert a TraceContext into a JSON-serializable TraceEvent dict.

    Produces the canonical TraceEvent schema used for logging, storage,
    and future API responses. This function is guaranteed to never raise —
    any serialization error is caught and replaced with safe fallback values.

    Args:
        context: The TraceContext to serialize.

    Returns:
        A dict conforming to the TraceEvent schema::

            {
                "session_id": "uuid",
                "agent_name": "string",
                "started_at": "iso8601",
                "ended_at": "iso8601",
                "duration_ms": 0,
                "status": "success | error | retried",
                "calls": [...],
                "metadata": {}
            }
    """
    try:
        started = context.started_at
        ended = context.ended_at or datetime.now(timezone.utc)

        # Calculate total duration
        try:
            duration_ms = (ended - started).total_seconds() * 1000
        except Exception:
            duration_ms = 0.0

        # Serialize calls
        calls: list[dict[str, Any]] = []
        for call in context.calls:
            calls.append(_serialize_call(call))

        # Build metadata — ensure it's serializable
        try:
            metadata = context.metadata.copy()
            # Verify it's JSON-serializable by doing a round-trip
            json.dumps(metadata, default=str)
        except Exception:
            metadata = {"_serialization_error": "<unserializable>"}

        return {
            "session_id": str(context.session_id),
            "agent_name": str(context.agent_name),
            "started_at": _safe_isoformat(started),
            "ended_at": _safe_isoformat(ended),
            "duration_ms": round(duration_ms, 2),
            "status": str(context.status),
            "calls": calls,
            "metadata": metadata,
        }
    except Exception as exc:
        # Last resort: return a minimal valid trace
        logger.warning("Failed to serialize trace context: %s", exc)
        return {
            "session_id": "<unserializable>",
            "agent_name": "<unserializable>",
            "started_at": "<unserializable>",
            "ended_at": "<unserializable>",
            "duration_ms": 0,
            "status": "error",
            "calls": [],
            "metadata": {"_serialization_error": str(exc)},
        }
