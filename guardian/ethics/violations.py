"""Core data structures for GUARDIAN Ethics Engine.

Defines the enumerations and data classes used by all ethics checkers
to report violations. Every checker returns ``list[EthicsViolation]`` —
violations are never signaled by raising exceptions. The engine decides
what to do based on the severity level and the loaded policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """How serious a detected violation is.

    Controls the engine's response:
    - LOG:   Record the violation, continue execution silently.
    - WARN:  Record + alert the dashboard, continue execution.
    - BLOCK: Stop agent execution immediately, raise EthicsBlockException.
    """

    LOG = "log"
    WARN = "warn"
    BLOCK = "block"


class ViolationType(str, Enum):
    """Categories of ethics violations the engine can detect."""

    PII_DETECTED = "pii_detected"
    BIAS_DETECTED = "bias_detected"
    SENSITIVE_DOMAIN = "sensitive_domain"
    FAIRNESS_VIOLATION = "fairness_violation"


@dataclass
class EthicsViolation:
    """A single ethics violation detected by one of the checkers.

    Attributes:
        violation_type: Which category of violation was detected.
        severity: How serious this violation is (log/warn/block).
        description: Human-readable explanation of what was found.
        evidence: The actual text fragment that triggered detection,
            masked and truncated for safe storage.
        field_path: Where in the trace this was found, e.g.
            ``"calls[0].result_preview"``.
        confidence: Detection confidence from 0.0 to 1.0.
        session_id: The trace session this violation belongs to.
        agent_name: Name of the agent that produced the flagged output.
        detected_at: UTC timestamp of when the violation was detected.
        metadata: Additional key-value data (e.g. PII type, bias category).
    """

    violation_type: ViolationType
    severity: Severity
    description: str
    evidence: str
    field_path: str
    confidence: float
    session_id: str
    agent_name: str
    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this violation.

        Returns:
            A plain dict suitable for JSON encoding, database storage,
            or API responses.
        """
        return {
            "violation_type": self.violation_type.value,
            "severity": self.severity.value,
            "description": self.description,
            "evidence": self.evidence,
            "field_path": self.field_path,
            "confidence": round(self.confidence, 4),
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "detected_at": self.detected_at.isoformat(),
            "metadata": self.metadata,
        }
