"""Tests for the GUARDIAN Trace Store.

Covers: writing a trace, reading it back, list_traces filtering,
get_recent_failures, and DB initialization idempotency.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from guardian.store.db import get_session, init_db, reset_engine
from guardian.store.models import TraceEventRecord
from guardian.store.reader import get_recent_failures, get_trace, list_traces
from guardian.store.writer import write_sync


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Any) -> Any:
    """Create a fresh in-memory SQLite database for each test."""
    reset_engine()
    db_path = tmp_path / "test_guardian.db"
    init_db(f"sqlite:///{db_path}")
    yield
    reset_engine()


def _make_trace_event(
    session_id: str | None = None,
    agent_name: str = "test_agent",
    status: str = "success",
    duration_ms: int = 100,
    calls: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a synthetic trace event dict for testing."""
    now = datetime.now(timezone.utc)
    return {
        "session_id": session_id or str(uuid.uuid4()),
        "agent_name": agent_name,
        "started_at": now.isoformat(),
        "ended_at": now.isoformat(),
        "duration_ms": duration_ms,
        "status": status,
        "calls": calls or [
            {
                "function": "test_func",
                "args_preview": "(1, 2)",
                "result_preview": "3",
                "duration_ms": 50,
                "error": None,
                "retry_count": 0,
                "estimated_tokens": 10,
            }
        ],
        "metadata": metadata or {},
    }


# ────────────────────────────────────────────────────────────
# Write + Read round-trip
# ────────────────────────────────────────────────────────────


class TestWriteAndRead:
    """Tests for writing a trace and reading it back."""

    def test_write_and_get_trace(self) -> None:
        """A written trace must be retrievable by session_id."""
        trace = _make_trace_event(session_id="test-session-001")
        write_sync(trace)

        result = get_trace("test-session-001")
        assert result is not None
        assert result["session_id"] == "test-session-001"
        assert result["agent_name"] == "test_agent"
        assert result["status"] == "success"

    def test_get_nonexistent_trace_returns_none(self) -> None:
        """Querying a non-existent session_id must return None."""
        result = get_trace("nonexistent-id")
        assert result is None

    def test_write_preserves_calls(self) -> None:
        """The calls list must be preserved through write/read."""
        calls = [
            {
                "function": "call_llm",
                "args_preview": "('prompt',)",
                "result_preview": "'response'",
                "duration_ms": 200,
                "error": None,
                "retry_count": 0,
                "estimated_tokens": 50,
            },
            {
                "function": "search_db",
                "args_preview": "('query',)",
                "result_preview": "[results]",
                "duration_ms": 30,
                "error": None,
                "retry_count": 1,
                "estimated_tokens": 20,
            },
        ]
        trace = _make_trace_event(session_id="calls-test", calls=calls)
        write_sync(trace)

        result = get_trace("calls-test")
        assert result is not None
        assert len(result["calls"]) == 2
        assert result["calls"][0]["function"] == "call_llm"
        assert result["calls"][1]["retry_count"] == 1

    def test_write_preserves_metadata(self) -> None:
        """Metadata dict must survive the write/read round-trip."""
        metadata = {"policy": "ethics.yaml", "env": "staging", "version": 2}
        trace = _make_trace_event(session_id="meta-test", metadata=metadata)
        write_sync(trace)

        result = get_trace("meta-test")
        assert result is not None
        assert result["metadata"]["policy"] == "ethics.yaml"
        assert result["metadata"]["version"] == 2

    def test_write_error_trace(self) -> None:
        """Error traces must be stored and retrievable."""
        error_calls = [
            {
                "function": "failing_func",
                "args_preview": "()",
                "result_preview": "",
                "duration_ms": 10,
                "error": {"type": "ValueError", "message": "bad input"},
                "retry_count": 0,
                "estimated_tokens": 5,
            }
        ]
        trace = _make_trace_event(
            session_id="error-test", status="error", calls=error_calls
        )
        write_sync(trace)

        result = get_trace("error-test")
        assert result is not None
        assert result["status"] == "error"
        assert result["calls"][0]["error"]["type"] == "ValueError"


# ────────────────────────────────────────────────────────────
# list_traces
# ────────────────────────────────────────────────────────────


class TestListTraces:
    """Tests for listing and filtering traces."""

    def test_list_returns_all_traces(self) -> None:
        """list_traces with no filter should return all traces."""
        for i in range(5):
            write_sync(_make_trace_event(session_id=f"list-{i}"))

        traces = list_traces()
        assert len(traces) == 5

    def test_list_filters_by_agent_name(self) -> None:
        """list_traces with agent_name should only return matching traces."""
        write_sync(_make_trace_event(agent_name="agent_a"))
        write_sync(_make_trace_event(agent_name="agent_b"))
        write_sync(_make_trace_event(agent_name="agent_a"))

        a_traces = list_traces(agent_name="agent_a")
        assert len(a_traces) == 2
        assert all(t["agent_name"] == "agent_a" for t in a_traces)

        b_traces = list_traces(agent_name="agent_b")
        assert len(b_traces) == 1

    def test_list_respects_limit(self) -> None:
        """list_traces must not return more than the specified limit."""
        for i in range(10):
            write_sync(_make_trace_event(session_id=f"limit-{i}"))

        traces = list_traces(limit=3)
        assert len(traces) == 3

    def test_list_empty_db(self) -> None:
        """list_traces on an empty DB should return an empty list."""
        traces = list_traces()
        assert traces == []

    def test_list_nonexistent_agent_returns_empty(self) -> None:
        """Filtering by a non-existent agent name returns empty list."""
        write_sync(_make_trace_event(agent_name="exists"))
        traces = list_traces(agent_name="does_not_exist")
        assert traces == []


# ────────────────────────────────────────────────────────────
# get_recent_failures
# ────────────────────────────────────────────────────────────


class TestGetRecentFailures:
    """Tests for retrieving recent failure traces."""

    def test_only_returns_non_success(self) -> None:
        """get_recent_failures must exclude success traces."""
        write_sync(_make_trace_event(status="success"))
        write_sync(_make_trace_event(status="error"))
        write_sync(_make_trace_event(status="success"))
        write_sync(_make_trace_event(status="retried"))

        failures = get_recent_failures()
        assert len(failures) == 2
        statuses = {f["status"] for f in failures}
        assert "success" not in statuses
        assert "error" in statuses
        assert "retried" in statuses

    def test_respects_limit(self) -> None:
        """get_recent_failures must respect the limit parameter."""
        for i in range(10):
            write_sync(_make_trace_event(session_id=f"fail-{i}", status="error"))

        failures = get_recent_failures(limit=3)
        assert len(failures) == 3

    def test_no_failures_returns_empty(self) -> None:
        """If all traces are successful, get_recent_failures returns empty."""
        write_sync(_make_trace_event(status="success"))
        write_sync(_make_trace_event(status="success"))

        failures = get_recent_failures()
        assert failures == []


# ────────────────────────────────────────────────────────────
# DB initialization idempotency
# ────────────────────────────────────────────────────────────


class TestDBInit:
    """Tests for database initialization behavior."""

    def test_init_is_idempotent(self, tmp_path: Any) -> None:
        """Calling init_db multiple times must not raise or corrupt data."""
        reset_engine()
        db_path = tmp_path / "idempotent_test.db"
        url = f"sqlite:///{db_path}"

        # Init twice
        init_db(url)
        init_db(url)

        # Write and read should still work
        write_sync(_make_trace_event(session_id="idempotent-check"))
        result = get_trace("idempotent-check")
        assert result is not None

    def test_multiple_writes_no_conflict(self) -> None:
        """Multiple sequential writes should not cause conflicts."""
        for i in range(20):
            write_sync(
                _make_trace_event(
                    session_id=f"multi-{i}",
                    agent_name=f"agent_{i % 3}",
                    status="error" if i % 5 == 0 else "success",
                )
            )

        all_traces = list_traces(limit=100)
        assert len(all_traces) == 20
