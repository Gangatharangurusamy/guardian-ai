"""Trace context management for GUARDIAN SDK.

Provides the TraceContext class representing a single agent run, along with
ContextVar-based storage for async-safe, thread-safe context isolation.
Nested/concurrent agent calls each get their own context without state leakage.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator
from guardian.sdk.capture import CapturedCall as ToolCallRecord


@dataclass
class TraceContext:
    """Represents one agent run and all its captured events.

    Attributes:
        session_id: Unique identifier for this trace session (UUID4 string).
        agent_name: Human-readable name of the agent being traced.
        started_at: UTC timestamp when the trace began.
        calls: Mutable list of ToolCallRecord events captured during the run.
        metadata: Arbitrary key-value metadata (e.g. policy path, user tags).
        parent_session_id: Session ID of the parent trace if this is a nested call.
        ended_at: UTC timestamp when the trace ended (set on completion).
        status: Final status of the trace ('success', 'error', or 'retried').
        raw_input: Full un-truncated first argument, set when recovery_policy
            is active on @guardian.watch. None otherwise.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    calls: list[ToolCallRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_session_id: str | None = None
    ended_at: datetime | None = None
    status: str = "success"
    raw_input: Any = None


# Module-level ContextVar for async-safe trace isolation.
# Each async task / thread inherits or gets its own value.
_current_context: ContextVar[TraceContext | None] = ContextVar(
    "guardian_trace_context", default=None
)


def get_current_context() -> TraceContext | None:
    """Return the active TraceContext for the current execution context.

    Returns:
        The current TraceContext if one is active, None otherwise.
    """
    return _current_context.get()


@contextmanager
def trace_context(
    agent_name: str,
    metadata: dict[str, Any] | None = None,
) -> Generator[TraceContext, None, None]:
    """Context manager that creates and activates a new TraceContext.

    If a TraceContext is already active (nested call), the new context records
    the parent's session_id for correlation. The context var is always reset
    on exit, even if an exception occurs.

    Args:
        agent_name: Name to assign to the new trace context.
        metadata: Optional metadata dict to attach to the context.

    Yields:
        The newly created TraceContext.
    """
    parent = _current_context.get()

    ctx = TraceContext(
        agent_name=agent_name,
        metadata=metadata or {},
        parent_session_id=parent.session_id if parent else None,
    )

    token: Token[TraceContext | None] = _current_context.set(ctx)
    try:
        yield ctx
    finally:
        # Always reset, even on exception — prevents context leakage
        _current_context.reset(token)
