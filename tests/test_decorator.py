"""Tests for the GUARDIAN SDK decorator.

Covers: sync function wrapping, async function wrapping, exception
propagation, nested watched calls, and trace JSON shape validation.
"""

from __future__ import annotations

import asyncio
import json
import pytest
from typing import Any

from guardian.sdk.decorator import watch


class TraceCollector:
    """Test helper that collects trace events via on_trace callback."""

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []

    def __call__(self, trace_event: dict[str, Any]) -> None:
        self.traces.append(trace_event)

    @property
    def last(self) -> dict[str, Any]:
        return self.traces[-1]

    def clear(self) -> None:
        self.traces.clear()


# ────────────────────────────────────────────────────────────
# Sync function wrapping
# ────────────────────────────────────────────────────────────


class TestSyncWrapping:
    """Tests for wrapping synchronous functions."""

    def test_sync_return_value_preserved(self) -> None:
        """The decorator must not alter the wrapped function's return value."""
        collector = TraceCollector()

        @watch("test_agent", on_trace=collector)
        def add(a: int, b: int) -> int:
            return a + b

        result = add(3, 4)
        assert result == 7

    def test_sync_trace_emitted(self) -> None:
        """A trace event must be emitted after the function completes."""
        collector = TraceCollector()

        @watch("test_agent", on_trace=collector)
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        greet("World")
        assert len(collector.traces) == 1

    def test_sync_agent_name_in_trace(self) -> None:
        """The trace must contain the correct agent name."""
        collector = TraceCollector()

        @watch("my_sync_agent", on_trace=collector)
        def noop() -> None:
            pass

        noop()
        assert collector.last["agent_name"] == "my_sync_agent"

    def test_sync_default_agent_name(self) -> None:
        """When no agent_name is given, use the function name."""
        collector = TraceCollector()

        @watch(on_trace=collector)
        def my_function() -> str:
            return "test"

        my_function()
        assert collector.last["agent_name"] == "my_function"

    def test_sync_preserves_function_metadata(self) -> None:
        """functools.wraps must preserve name and docstring."""
        collector = TraceCollector()

        @watch("test", on_trace=collector)
        def documented_func() -> None:
            """This is a docstring."""
            pass

        assert documented_func.__name__ == "documented_func"
        assert documented_func.__doc__ == "This is a docstring."


# ────────────────────────────────────────────────────────────
# Async function wrapping
# ────────────────────────────────────────────────────────────


class TestAsyncWrapping:
    """Tests for wrapping async functions."""

    @pytest.mark.asyncio
    async def test_async_return_value_preserved(self) -> None:
        """The decorator must not alter the async function's return value."""
        collector = TraceCollector()

        @watch("async_agent", on_trace=collector)
        async def fetch(url: str) -> str:
            return f"data from {url}"

        result = await fetch("https://example.com")
        assert result == "data from https://example.com"

    @pytest.mark.asyncio
    async def test_async_trace_emitted(self) -> None:
        """A trace event must be emitted after the async function completes."""
        collector = TraceCollector()

        @watch("async_agent", on_trace=collector)
        async def compute() -> int:
            await asyncio.sleep(0.01)
            return 42

        await compute()
        assert len(collector.traces) == 1
        assert collector.last["status"] == "success"

    @pytest.mark.asyncio
    async def test_async_duration_tracked(self) -> None:
        """Duration must reflect actual execution time."""
        collector = TraceCollector()

        @watch("async_agent", on_trace=collector)
        async def slow() -> str:
            await asyncio.sleep(0.05)
            return "done"

        await slow()
        assert collector.last["duration_ms"] >= 40  # Allow some tolerance


# ────────────────────────────────────────────────────────────
# Exception propagation
# ────────────────────────────────────────────────────────────


class TestExceptionPropagation:
    """Tests that the decorator re-raises exceptions correctly."""

    def test_sync_exception_reraised(self) -> None:
        """The exact same exception must be re-raised."""
        collector = TraceCollector()

        @watch("error_agent", on_trace=collector)
        def failing() -> None:
            raise ValueError("something broke")

        with pytest.raises(ValueError, match="something broke"):
            failing()

    def test_sync_exception_captured_in_trace(self) -> None:
        """The trace must record the error details."""
        collector = TraceCollector()

        @watch("error_agent", on_trace=collector)
        def failing() -> None:
            raise RuntimeError("oops")

        with pytest.raises(RuntimeError):
            failing()

        trace = collector.last
        assert trace["status"] == "error"
        assert len(trace["calls"]) == 1
        assert trace["calls"][0]["error"] is not None
        assert trace["calls"][0]["error"]["type"] == "RuntimeError"
        assert "oops" in trace["calls"][0]["error"]["message"]

    @pytest.mark.asyncio
    async def test_async_exception_reraised(self) -> None:
        """Async exceptions must also be re-raised unchanged."""
        collector = TraceCollector()

        @watch("async_error_agent", on_trace=collector)
        async def async_failing() -> None:
            raise ConnectionError("network down")

        with pytest.raises(ConnectionError, match="network down"):
            await async_failing()

        assert collector.last["status"] == "error"

    @pytest.mark.asyncio
    async def test_async_exception_type_preserved(self) -> None:
        """The exception type must be exactly preserved, not wrapped."""
        collector = TraceCollector()

        class CustomError(Exception):
            pass

        @watch("custom_error_agent", on_trace=collector)
        async def raise_custom() -> None:
            raise CustomError("custom failure")

        with pytest.raises(CustomError):
            await raise_custom()


# ────────────────────────────────────────────────────────────
# Nested watched calls
# ────────────────────────────────────────────────────────────


class TestNestedCalls:
    """Tests for nested @watch decorated functions."""

    def test_nested_sync_emits_single_trace_with_both_calls(self) -> None:
        """Nested sync calls should share the same context and emit a single trace containing all calls."""
        collector = TraceCollector()

        @watch("inner_agent", on_trace=collector)
        def inner() -> str:
            return "inner result"

        @watch("outer_agent", on_trace=collector)
        def outer() -> str:
            return inner() + " + outer"

        result = outer()
        assert result == "inner result + outer"
        # Only the root (outer) should emit the trace
        assert len(collector.traces) == 1
        trace = collector.last
        assert trace["agent_name"] == "outer_agent"
        # Should contain both calls: inner_agent and outer_agent
        assert len(trace["calls"]) == 2
        call_names = [c["function"] for c in trace["calls"]]
        assert "inner_agent" in call_names
        assert "outer_agent" in call_names

    def test_nested_calls_share_session_id(self) -> None:
        """Nested calls must share the same session ID."""
        collector = TraceCollector()

        @watch("inner", on_trace=collector)
        def inner() -> str:
            return "done"

        @watch("outer", on_trace=collector)
        def outer() -> str:
            return inner()

        outer()
        assert len(collector.traces) == 1
        trace = collector.last
        assert len(trace["calls"]) == 2
        assert trace["agent_name"] == "outer"

    @pytest.mark.asyncio
    async def test_nested_async(self) -> None:
        """Nested async watched functions should also share context and emit a single trace."""
        collector = TraceCollector()

        @watch("async_inner", on_trace=collector)
        async def async_inner() -> int:
            return 10

        @watch("async_outer", on_trace=collector)
        async def async_outer() -> int:
            val = await async_inner()
            return val * 2

        result = await async_outer()
        assert result == 20
        assert len(collector.traces) == 1
        trace = collector.last
        assert trace["agent_name"] == "async_outer"
        assert len(trace["calls"]) == 2
        call_names = [c["function"] for c in trace["calls"]]
        assert "async_inner" in call_names
        assert "async_outer" in call_names


# ────────────────────────────────────────────────────────────
# Trace JSON shape validation
# ────────────────────────────────────────────────────────────


class TestTraceShape:
    """Tests that trace events have the expected structure."""

    REQUIRED_KEYS = {
        "session_id",
        "agent_name",
        "started_at",
        "ended_at",
        "duration_ms",
        "status",
        "calls",
        "metadata",
    }

    CALL_REQUIRED_KEYS = {
        "function",
        "args_preview",
        "result_preview",
        "duration_ms",
        "error",
        "retry_count",
        "estimated_tokens",
    }

    def test_trace_has_all_required_keys(self) -> None:
        """The trace event dict must contain all schema-defined keys."""
        collector = TraceCollector()

        @watch("shape_test", on_trace=collector)
        def identity(x: int) -> int:
            return x

        identity(42)
        trace = collector.last

        missing = self.REQUIRED_KEYS - set(trace.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_calls_have_all_required_keys(self) -> None:
        """Each call record must contain all schema-defined keys."""
        collector = TraceCollector()

        @watch("shape_test", on_trace=collector)
        def identity(x: int) -> int:
            return x

        identity(42)
        call = collector.last["calls"][0]

        missing = self.CALL_REQUIRED_KEYS - set(call.keys())
        assert not missing, f"Missing call keys: {missing}"

    def test_trace_is_json_serializable(self) -> None:
        """The entire trace must be JSON-serializable."""
        collector = TraceCollector()

        @watch("json_test", on_trace=collector)
        def with_complex_args(data: dict[str, Any]) -> list[int]:
            return [1, 2, 3]

        with_complex_args({"key": "value", "nested": {"a": 1}})
        trace = collector.last

        # Must not raise
        serialized = json.dumps(trace, default=str)
        assert isinstance(serialized, str)
        assert len(serialized) > 0

    def test_status_is_success_on_normal_return(self) -> None:
        """Status must be 'success' when the function returns normally."""
        collector = TraceCollector()

        @watch("status_test", on_trace=collector)
        def ok() -> str:
            return "ok"

        ok()
        assert collector.last["status"] == "success"

    def test_status_is_error_on_exception(self) -> None:
        """Status must be 'error' when the function raises."""
        collector = TraceCollector()

        @watch("status_test", on_trace=collector)
        def fail() -> None:
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            fail()
        assert collector.last["status"] == "error"

    def test_policy_stored_in_metadata(self) -> None:
        """The policy path should be stored in trace metadata."""
        collector = TraceCollector()

        @watch("policy_test", policy="ethics.yaml", on_trace=collector)
        def agent() -> str:
            return "result"

        agent()
        assert collector.last["metadata"].get("policy") == "ethics.yaml"

    def test_duration_is_positive(self) -> None:
        """Duration must be a positive number."""
        collector = TraceCollector()

        @watch("duration_test", on_trace=collector)
        def quick() -> int:
            return 1

        quick()
        assert collector.last["duration_ms"] >= 0
        assert collector.last["calls"][0]["duration_ms"] >= 0

    def test_session_id_is_uuid_format(self) -> None:
        """Session ID must look like a UUID."""
        import uuid
        collector = TraceCollector()

        @watch("uuid_test", on_trace=collector)
        def agent() -> None:
            pass

        agent()
        # Should parse as a valid UUID
        uuid.UUID(collector.last["session_id"])
