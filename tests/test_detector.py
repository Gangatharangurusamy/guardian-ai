"""Tests for guardian.watchdog.detector — pure-Python failure detection."""

from __future__ import annotations

import pytest
from guardian.watchdog.detector import FailureDetector
from guardian.watchdog.models import FailureType


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_trace(
    session_id="sess-001",
    agent_name="test_agent",
    status="success",
    duration_ms=100,
    calls=None,
):
    """Build a minimal TraceEvent dict for testing."""
    return {
        "session_id": session_id,
        "agent_name": agent_name,
        "status": status,
        "duration_ms": duration_ms,
        "calls": calls or [],
    }


def make_call(
    function_name="search_tool",
    result_preview="some result",
    duration_ms=50,
    error=None,
    retry_count=0,
):
    return {
        "function_name": function_name,
        "result_preview": result_preview,
        "duration_ms": duration_ms,
        "error": error,
        "retry_count": retry_count,
    }


# ---------------------------------------------------------------------------
# Tool Loop Detection
# ---------------------------------------------------------------------------

class TestToolLoopDetection:

    def test_tool_loop_detected_identical_results(self):
        """Same function 3+ times with identical result_preview → TOOL_LOOP."""
        detector = FailureDetector(loop_threshold=3)
        calls = [make_call("search_tool", "same result") for _ in range(3)]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)

        assert len(signals) >= 1
        types = [s.failure_type for s in signals]
        assert FailureType.TOOL_LOOP in types

    def test_tool_loop_detected_empty_results(self):
        """Same function 3+ times with empty result_preview → TOOL_LOOP."""
        detector = FailureDetector(loop_threshold=3)
        calls = [make_call("llm_call", "") for _ in range(4)]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.TOOL_LOOP in types

    def test_tool_loop_not_detected_different_results(self):
        """Same function 3x but different results → NO tool loop (progress made)."""
        detector = FailureDetector(loop_threshold=3)
        calls = [
            make_call("search_tool", "result 1"),
            make_call("search_tool", "result 2"),
            make_call("search_tool", "result 3"),
        ]
        trace = make_trace(calls=calls)

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.TOOL_LOOP not in types

    def test_tool_loop_not_detected_below_threshold(self):
        """Same function called only twice (below threshold of 3) → no signal."""
        detector = FailureDetector(loop_threshold=3)
        calls = [make_call("search_tool", "same") for _ in range(2)]
        trace = make_trace(calls=calls)

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.TOOL_LOOP not in types

    def test_tool_loop_confidence_is_high(self):
        """Tool loop signals should have confidence 0.95."""
        detector = FailureDetector(loop_threshold=3)
        calls = [make_call("tool", "same") for _ in range(3)]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        loop_signals = [s for s in signals if s.failure_type == FailureType.TOOL_LOOP]
        assert loop_signals
        assert all(s.confidence == 0.95 for s in loop_signals)


# ---------------------------------------------------------------------------
# Timeout Detection
# ---------------------------------------------------------------------------

class TestTimeoutDetection:

    def test_timeout_detected_on_total_duration(self):
        """trace duration_ms > timeout_ms → TIMEOUT signal."""
        detector = FailureDetector(timeout_ms=30_000)
        trace = make_trace(duration_ms=45_000, status="error")

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.TIMEOUT in types

    def test_timeout_not_detected_below_threshold(self):
        """trace duration_ms < timeout_ms → no timeout signal."""
        detector = FailureDetector(timeout_ms=30_000)
        trace = make_trace(duration_ms=5_000)

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.TIMEOUT not in types

    def test_timeout_detected_on_individual_call(self):
        """Single call > 80% of timeout → TIMEOUT signal for that call."""
        detector = FailureDetector(timeout_ms=30_000)
        # 80% of 30000 = 24000; use 25000 to trigger
        calls = [make_call("slow_fn", "ok", duration_ms=25_000)]
        trace = make_trace(duration_ms=100, calls=calls)

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.TIMEOUT in types


# ---------------------------------------------------------------------------
# Repeated Error Detection
# ---------------------------------------------------------------------------

class TestRepeatedErrorDetection:

    def test_repeated_error_detected(self):
        """Same error type 2+ times → REPEATED_ERROR."""
        detector = FailureDetector(error_repeat_threshold=2)
        calls = [
            make_call(error={"type": "TimeoutError", "message": "timed out"}),
            make_call(error={"type": "TimeoutError", "message": "timed out again"}),
        ]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.REPEATED_ERROR in types

    def test_repeated_error_not_detected_once(self):
        """Same error only once (below threshold) → no signal."""
        detector = FailureDetector(error_repeat_threshold=2)
        calls = [make_call(error={"type": "TimeoutError", "message": "once"})]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.REPEATED_ERROR not in types

    def test_different_errors_not_flagged(self):
        """Two different error types → no repeated error signal."""
        detector = FailureDetector(error_repeat_threshold=2)
        calls = [
            make_call(error={"type": "TimeoutError", "message": "timeout"}),
            make_call(error={"type": "ConnectionError", "message": "connection"}),
        ]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.REPEATED_ERROR not in types


# ---------------------------------------------------------------------------
# Confidence Drop Detection
# ---------------------------------------------------------------------------

class TestConfidenceDropDetection:

    def test_confidence_drop_detected(self):
        """Strictly decreasing result lengths with last < 20% of first → CONFIDENCE_DROP."""
        detector = FailureDetector()
        calls = [
            make_call(result_preview="a" * 500),  # 500
            make_call(result_preview="a" * 200),  # 200
            make_call(result_preview="a" * 10),   # 10 = 2% of 500
        ]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.CONFIDENCE_DROP in types

    def test_confidence_drop_not_detected_not_decreasing(self):
        """Non-monotone length sequence → no confidence drop."""
        detector = FailureDetector()
        calls = [
            make_call(result_preview="a" * 100),
            make_call(result_preview="a" * 200),  # increased — not decreasing
            make_call(result_preview="a" * 5),
        ]
        trace = make_trace(calls=calls)

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.CONFIDENCE_DROP not in types

    def test_confidence_drop_not_detected_not_severe_enough(self):
        """Decreasing but last is 30% of first (not < 20%) → no signal."""
        detector = FailureDetector()
        calls = [
            make_call(result_preview="a" * 100),
            make_call(result_preview="a" * 50),
            make_call(result_preview="a" * 30),  # 30% of 100
        ]
        trace = make_trace(calls=calls)

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.CONFIDENCE_DROP not in types


# ---------------------------------------------------------------------------
# Ethics Block Detection
# ---------------------------------------------------------------------------

class TestEthicsBlockDetection:

    def test_ethics_block_detected(self):
        """Status=error + EthicsBlockException in error → ETHICS_BLOCK."""
        detector = FailureDetector()
        calls = [
            make_call(error={
                "type": "EthicsBlockException",
                "message": "pii_detected: SSN found in output",
            })
        ]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.ETHICS_BLOCK in types

    def test_ethics_block_high_confidence(self):
        """Ethics block signal should have confidence 0.99."""
        detector = FailureDetector()
        calls = [make_call(error={"type": "EthicsBlockException", "message": "blocked"})]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        ethics = [s for s in signals if s.failure_type == FailureType.ETHICS_BLOCK]
        assert ethics
        assert all(s.confidence == 0.99 for s in ethics)

    def test_ethics_block_not_detected_on_success(self):
        """EthicsBlockException in error field but status=success → no signal."""
        detector = FailureDetector()
        calls = [make_call(error={"type": "EthicsBlockException", "message": "blocked"})]
        trace = make_trace(calls=calls, status="success")  # status must be error

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.ETHICS_BLOCK not in types

    def test_ethics_block_not_detected_different_error(self):
        """Status=error but different exception → no ETHICS_BLOCK."""
        detector = FailureDetector()
        calls = [make_call(error={"type": "ValueError", "message": "bad value"})]
        trace = make_trace(calls=calls, status="error")

        signals = detector.detect(trace)
        types = [s.failure_type for s in signals]
        assert FailureType.ETHICS_BLOCK not in types


# ---------------------------------------------------------------------------
# Clean trace and malformed input
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_clean_trace_returns_empty(self):
        """A successful trace with no anomalies → empty signal list."""
        detector = FailureDetector()
        calls = [make_call("fn_a", "result a"), make_call("fn_b", "result b")]
        trace = make_trace(calls=calls, duration_ms=1000, status="success")

        signals = detector.detect(trace)
        assert signals == []

    def test_malformed_trace_returns_empty_no_raise(self):
        """None / empty dict / missing keys → empty list, no exception raised."""
        detector = FailureDetector()

        # Various malformed inputs
        assert detector.detect(None) == []  # type: ignore
        assert detector.detect({}) == []
        assert detector.detect({"calls": None}) == []
        assert detector.detect({"calls": "not a list"}) == []
        assert detector.detect({"status": "error"}) == []

    def test_detect_across_sessions_empty(self):
        """detect_across_sessions with no traces → empty list."""
        detector = FailureDetector()
        assert detector.detect_across_sessions([]) == []

    def test_detect_across_sessions_cross_pattern(self):
        """Same failure type in 2+ sessions → cross-session signal emitted."""
        detector = FailureDetector(timeout_ms=1000)
        trace1 = make_trace(duration_ms=5000, status="error", session_id="s1")
        trace2 = make_trace(duration_ms=5000, status="error", session_id="s2")

        signals = detector.detect_across_sessions([trace1, trace2])
        types = [s.failure_type for s in signals]
        # Should include at least one TIMEOUT signal (possibly cross-session)
        assert FailureType.TIMEOUT in types
