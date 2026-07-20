"""GUARDIAN Failure Detector — pure-Python pattern analysis.

Reads a TraceEvent dict (already captured and serialized by Phase 1)
and returns a list of FailureSignals describing what went wrong.

No LLM calls, no external I/O, no side effects.
detect() never raises — returns an empty list on any error or malformed input.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from guardian.watchdog.models import FailureSignal, FailureType

logger = logging.getLogger("guardian")


class FailureDetector:
    """Analyzes TraceEvent dicts for failure patterns.

    Runs five heuristic detectors in sequence and returns the combined
    list of FailureSignals. Thread-safe and stateless — create one per
    analysis or share across calls.

    Args:
        loop_threshold: Number of identical tool calls that constitutes a loop.
        timeout_ms: Duration in milliseconds above which a timeout is signalled.
        error_repeat_threshold: Number of identical errors that constitutes
            a repeated error pattern.
    """

    def __init__(
        self,
        loop_threshold: int = 3,
        timeout_ms: int = 30_000,
        error_repeat_threshold: int = 2,
    ) -> None:
        self.loop_threshold = loop_threshold
        self.timeout_ms = timeout_ms
        self.error_repeat_threshold = error_repeat_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, trace_event: dict[str, Any]) -> list[FailureSignal]:
        """Analyze a single TraceEvent dict for failure patterns.

        Runs all five sub-detectors and returns the combined list of
        signals. Safe to call on malformed or incomplete trace dicts.

        Args:
            trace_event: Serialized TraceEvent dict from the SDK.

        Returns:
            List of FailureSignals (may be empty if trace is clean).
            Never raises — returns [] on any exception.
        """
        try:
            return self._run_all(trace_event)
        except Exception as exc:
            logger.debug("FailureDetector.detect() encountered an error: %s", exc)
            return []

    def detect_across_sessions(
        self, trace_events: list[dict[str, Any]]
    ) -> list[FailureSignal]:
        """Analyze multiple TraceEvents for cross-session failure patterns.

        Calls detect() on each trace and then looks for patterns that
        repeat across sessions — e.g., the same failure type appearing
        in 2 or more separate sessions.

        Args:
            trace_events: List of TraceEvent dicts (e.g., from get_recent_failures()).

        Returns:
            Combined list of FailureSignals including cross-session patterns.
            Never raises.
        """
        try:
            all_signals: list[FailureSignal] = []
            failure_type_counts: Counter[str] = Counter()

            for trace in trace_events:
                signals = self.detect(trace)
                all_signals.extend(signals)
                for s in signals:
                    failure_type_counts[s.failure_type.value] += 1

            # Emit cross-session signals for patterns seen in 2+ sessions
            session_id = trace_events[-1].get("session_id", "") if trace_events else ""
            agent_name = trace_events[-1].get("agent_name", "") if trace_events else ""

            for ft_value, count in failure_type_counts.items():
                if count >= 2:
                    all_signals.append(
                        FailureSignal(
                            failure_type=FailureType(ft_value),
                            description=(
                                f"Cross-session pattern: '{ft_value}' detected "
                                f"in {count} out of {len(trace_events)} sessions"
                            ),
                            evidence=(
                                f"Failure type '{ft_value}' appeared {count} times "
                                f"across {len(trace_events)} recent sessions"
                            ),
                            confidence=min(0.95, 0.60 + count * 0.10),
                            session_id=session_id,
                            agent_name=agent_name,
                            metadata={"session_count": len(trace_events), "failure_count": count},
                        )
                    )

            return all_signals
        except Exception as exc:
            logger.debug("FailureDetector.detect_across_sessions() error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_all(self, trace_event: dict[str, Any]) -> list[FailureSignal]:
        """Run all sub-detectors and combine results."""
        signals: list[FailureSignal] = []
        signals.extend(self._detect_ethics_block(trace_event))
        signals.extend(self._detect_tool_loop(trace_event))
        signals.extend(self._detect_timeout(trace_event))
        signals.extend(self._detect_repeated_error(trace_event))
        signals.extend(self._detect_confidence_drop(trace_event))
        return signals

    def _get_calls(self, trace_event: dict[str, Any]) -> list[dict[str, Any]]:
        """Safely extract the calls list from a trace event."""
        calls = trace_event.get("calls", [])
        if not isinstance(calls, list):
            return []
        return [c for c in calls if isinstance(c, dict)]

    def _session_info(self, trace_event: dict[str, Any]) -> tuple[str, str]:
        """Extract session_id and agent_name safely."""
        return (
            str(trace_event.get("session_id", "")),
            str(trace_event.get("agent_name", "")),
        )

    def _detect_tool_loop(self, trace_event: dict[str, Any]) -> list[FailureSignal]:
        """Detect when the same tool is called repeatedly with no progress.

        A tool loop is signalled when the same function_name appears
        loop_threshold+ times AND the result_preview values across those
        calls are all identical or all empty.
        """
        calls = self._get_calls(trace_event)
        session_id, agent_name = self._session_info(trace_event)
        signals: list[FailureSignal] = []

        # Group calls by function_name
        by_name: dict[str, list[dict[str, Any]]] = {}
        for call in calls:
            name = str(call.get("function_name", ""))
            if name:
                by_name.setdefault(name, []).append(call)

        for func_name, func_calls in by_name.items():
            if len(func_calls) < self.loop_threshold:
                continue

            previews = [str(c.get("result_preview", "")) for c in func_calls]
            all_empty = all(p == "" for p in previews)
            all_identical = len(set(previews)) == 1

            if all_empty or all_identical:
                sample_preview = previews[0] if previews else ""
                evidence_detail = (
                    "empty result on every call"
                    if all_empty
                    else f"identical result '{sample_preview[:80]}' on every call"
                )
                signals.append(
                    FailureSignal(
                        failure_type=FailureType.TOOL_LOOP,
                        description=(
                            f"Function '{func_name}' called {len(func_calls)} times "
                            f"with no progress"
                        ),
                        evidence=(
                            f"function '{func_name}' called {len(func_calls)} times "
                            f"with {evidence_detail}"
                        ),
                        confidence=0.95,
                        session_id=session_id,
                        agent_name=agent_name,
                        metadata={
                            "function_name": func_name,
                            "call_count": len(func_calls),
                            "all_empty": all_empty,
                            "all_identical": all_identical,
                        },
                    )
                )

        return signals

    def _detect_timeout(self, trace_event: dict[str, Any]) -> list[FailureSignal]:
        """Detect when duration exceeds the configured timeout threshold.

        Checks both the total trace duration AND individual call durations.
        Individual calls are flagged if duration_ms > timeout_ms * 0.8.
        """
        session_id, agent_name = self._session_info(trace_event)
        signals: list[FailureSignal] = []

        # Check total trace duration
        total_duration = trace_event.get("duration_ms", 0)
        if isinstance(total_duration, (int, float)) and total_duration > self.timeout_ms:
            signals.append(
                FailureSignal(
                    failure_type=FailureType.TIMEOUT,
                    description=(
                        f"Total agent duration {total_duration:,.0f}ms exceeded "
                        f"threshold {self.timeout_ms:,}ms"
                    ),
                    evidence=(
                        f"total duration {total_duration:,.0f}ms exceeded "
                        f"threshold {self.timeout_ms:,}ms"
                    ),
                    confidence=0.90,
                    session_id=session_id,
                    agent_name=agent_name,
                    metadata={
                        "duration_ms": total_duration,
                        "threshold_ms": self.timeout_ms,
                        "scope": "total",
                    },
                )
            )

        # Check individual call durations
        call_threshold = self.timeout_ms * 0.8
        for call in self._get_calls(trace_event):
            call_duration = call.get("duration_ms", 0)
            func_name = call.get("function_name", "unknown")
            if isinstance(call_duration, (int, float)) and call_duration > call_threshold:
                signals.append(
                    FailureSignal(
                        failure_type=FailureType.TIMEOUT,
                        description=(
                            f"Call to '{func_name}' took {call_duration:,.0f}ms "
                            f"(>{call_threshold:,.0f}ms call threshold)"
                        ),
                        evidence=(
                            f"call '{func_name}' duration {call_duration:,.0f}ms "
                            f"exceeded 80% threshold {call_threshold:,.0f}ms"
                        ),
                        confidence=0.75,
                        session_id=session_id,
                        agent_name=agent_name,
                        metadata={
                            "function_name": func_name,
                            "call_duration_ms": call_duration,
                            "threshold_ms": call_threshold,
                            "scope": "individual_call",
                        },
                    )
                )

        return signals

    def _detect_repeated_error(self, trace_event: dict[str, Any]) -> list[FailureSignal]:
        """Detect when the same error type occurs multiple times.

        Extracts error type from each call's error dict and counts
        occurrences. Signals if any type appears error_repeat_threshold+ times.
        """
        calls = self._get_calls(trace_event)
        session_id, agent_name = self._session_info(trace_event)
        signals: list[FailureSignal] = []

        error_counts: Counter[str] = Counter()
        for call in calls:
            error = call.get("error")
            if isinstance(error, dict):
                error_type = str(error.get("type", ""))
                if error_type:
                    error_counts[error_type] += 1
            elif isinstance(error, str) and error:
                # Fallback: some serializers store errors as plain strings
                error_counts[error[:50]] += 1

        for error_type, count in error_counts.items():
            if count >= self.error_repeat_threshold:
                signals.append(
                    FailureSignal(
                        failure_type=FailureType.REPEATED_ERROR,
                        description=(
                            f"Error type '{error_type}' raised {count} times "
                            f"in this session"
                        ),
                        evidence=f"{error_type} raised {count} times across calls",
                        confidence=0.85,
                        session_id=session_id,
                        agent_name=agent_name,
                        metadata={"error_type": error_type, "count": count},
                    )
                )

        return signals

    def _detect_confidence_drop(self, trace_event: dict[str, Any]) -> list[FailureSignal]:
        """Detect potential hallucination via shrinking result previews.

        Signals a confidence drop when result_preview lengths across all
        calls are strictly decreasing AND the final length is less than
        20% of the initial length. This is a proxy for the model producing
        increasingly degraded outputs across retries.
        """
        calls = self._get_calls(trace_event)
        session_id, agent_name = self._session_info(trace_event)

        result_lengths = [
            len(str(c.get("result_preview", "")))
            for c in calls
        ]

        if len(result_lengths) < 2:
            return []

        # Check strictly decreasing
        is_strictly_decreasing = all(
            result_lengths[i] > result_lengths[i + 1]
            for i in range(len(result_lengths) - 1)
        )
        if not is_strictly_decreasing:
            return []

        first_len = result_lengths[0]
        last_len = result_lengths[-1]

        # Avoid division by zero; no signal if first result was empty
        if first_len == 0:
            return []

        # Signal only if last result is < 20% of first
        if last_len >= first_len * 0.20:
            return []

        return [
            FailureSignal(
                failure_type=FailureType.CONFIDENCE_DROP,
                description=(
                    f"Result preview length dropped from {first_len} to {last_len} chars "
                    f"across {len(calls)} calls — possible hallucination"
                ),
                evidence=(
                    f"result_preview length dropped from {first_len} to {last_len} chars "
                    f"across retries"
                ),
                confidence=0.60,
                session_id=session_id,
                agent_name=agent_name,
                metadata={
                    "first_result_len": first_len,
                    "last_result_len": last_len,
                    "call_count": len(calls),
                    "drop_ratio": round(last_len / first_len, 3),
                },
            )
        ]

    def _detect_ethics_block(self, trace_event: dict[str, Any]) -> list[FailureSignal]:
        """Detect when an EthicsBlockException halted the agent.

        Checks that the trace status is 'error' AND that at least one
        call's error message contains 'EthicsBlockException'. Extracts
        the violation type from the error message for evidence detail.
        """
        session_id, agent_name = self._session_info(trace_event)

        if trace_event.get("status") != "error":
            return []

        for call in self._get_calls(trace_event):
            error = call.get("error")
            error_str = ""

            if isinstance(error, dict):
                error_str = str(error.get("type", "")) + " " + str(error.get("message", ""))
            elif isinstance(error, str):
                error_str = error

            if "EthicsBlockException" in error_str:
                # Try to extract the violation type from the error message
                violation_detail = error_str.replace("EthicsBlockException", "").strip()
                violation_detail = violation_detail[:200] if violation_detail else "ethics violation"

                return [
                    FailureSignal(
                        failure_type=FailureType.ETHICS_BLOCK,
                        description=(
                            "Agent execution was blocked by the GUARDIAN Ethics Engine"
                        ),
                        evidence=violation_detail or "EthicsBlockException raised",
                        confidence=0.99,
                        session_id=session_id,
                        agent_name=agent_name,
                        metadata={"raw_error": error_str[:500]},
                    )
                ]

        return []
