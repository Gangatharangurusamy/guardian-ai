"""Data models for GUARDIAN watchdog output.

Defines the data structures used to represent detected failure signals
and LLM-generated diagnoses. These are the core data types that flow
from the FailureDetector through the Diagnoser to the RecoveryEngine.

No dependencies on other guardian modules except stdlib.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class FailureType(str, Enum):
    """Enumeration of failure patterns that GUARDIAN can detect.

    Each value corresponds to a specific detection heuristic in
    FailureDetector and a recovery action in recovery-policy.yaml.
    """

    TOOL_LOOP = "tool_loop"
    """Same tool called 3+ times with no progress (identical/empty results)."""

    TIMEOUT = "timeout"
    """Duration exceeded the configured timeout threshold."""

    REPEATED_ERROR = "repeated_error"
    """Same error type raised 2+ times within one session."""

    SCHEMA_MISMATCH = "schema_mismatch"
    """Result structure doesn't match expected schema."""

    ETHICS_BLOCK = "ethics_block"
    """An EthicsBlockException was raised during agent execution."""

    CONFIDENCE_DROP = "confidence_drop"
    """Result preview shrinks on each retry — proxy for hallucination."""

    UNKNOWN = "unknown"
    """Catch-all for unclassified failures."""


@dataclass
class FailureSignal:
    """A single detected failure signal from the FailureDetector.

    Represents one specific pattern found in a trace event, with
    supporting evidence and a confidence score.

    Attributes:
        failure_type: The category of failure detected.
        description: Human-readable description of what was detected.
        evidence: The specific data excerpt that triggered detection.
        confidence: Detection confidence score from 0.0 to 1.0.
        session_id: Session ID of the trace this signal came from.
        agent_name: Name of the agent that produced the trace.
        detected_at: UTC timestamp when detection occurred.
        metadata: Additional key-value data for debugging.
    """

    failure_type: FailureType
    description: str
    evidence: str
    confidence: float
    session_id: str
    agent_name: str
    detected_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-serializable dict."""
        return {
            "failure_type": self.failure_type.value,
            "description": self.description,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "detected_at": self.detected_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class Diagnosis:
    """LLM-generated root-cause diagnosis for a failed agent run.

    Produced by the Diagnoser after sending failure signals and the
    trace event to an LLM. Contains the model's analysis and suggested
    recovery action.

    Attributes:
        session_id: Session ID of the diagnosed trace.
        agent_name: Name of the agent that was diagnosed.
        failure_signals: The FailureSignals that triggered this diagnosis.
        root_cause: Plain-English explanation of the primary failure cause.
        suggestion: What the RecoveryEngine should try next.
        confidence: LLM's self-reported confidence score (0.0–1.0).
        model_used: LiteLLM model string that produced this diagnosis.
        diagnosed_at: UTC timestamp when diagnosis was completed.
        raw_llm_response: Full raw LLM response text for audit purposes.
    """

    session_id: str
    agent_name: str
    failure_signals: list[FailureSignal]
    root_cause: str
    suggestion: str
    confidence: float
    model_used: str
    diagnosed_at: datetime = field(default_factory=datetime.utcnow)
    raw_llm_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-serializable dict."""
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "failure_signals": [s.to_dict() for s in self.failure_signals],
            "root_cause": self.root_cause,
            "suggestion": self.suggestion,
            "confidence": self.confidence,
            "model_used": self.model_used,
            "diagnosed_at": self.diagnosed_at.isoformat(),
            "raw_llm_response": self.raw_llm_response,
        }

    def primary_failure_type(self) -> FailureType:
        """Return the highest-confidence failure type from signals.

        Returns FailureType.UNKNOWN if no signals are present.
        """
        if not self.failure_signals:
            return FailureType.UNKNOWN
        return max(self.failure_signals, key=lambda s: s.confidence).failure_type

    def to_summary_json(self) -> str:
        """Return a compact JSON summary for logging."""
        return json.dumps(
            {
                "session_id": self.session_id,
                "agent_name": self.agent_name,
                "root_cause": self.root_cause,
                "suggestion": self.suggestion,
                "confidence": self.confidence,
                "model_used": self.model_used,
                "failure_count": len(self.failure_signals),
            }
        )
