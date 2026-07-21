"""GUARDIAN Compliance Schemas.

Dataclasses for EU AI Act and OWASP LLM Top 10 compliance reports.
All fields are JSON-serializable (use isoformat() for datetimes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EUAIActReport:
    """EU AI Act compliance report for a single agent session."""

    session_id: str
    agent_name: str
    risk_level: str  # "minimal" | "limited" | "high"
    transparency: dict[str, Any] = field(default_factory=dict)
    human_oversight: dict[str, Any] = field(default_factory=dict)
    bias_documentation: dict[str, Any] = field(default_factory=dict)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)
    data_governance: dict[str, Any] = field(default_factory=dict)
    technical_documentation: dict[str, Any] = field(default_factory=dict)


@dataclass
class OWASPRisk:
    """Assessment for a single OWASP LLM Top 10 risk."""

    risk_id: str        # e.g. "OWASP-A06"
    risk_name: str
    triggered: bool
    evidence: str       # Empty string if not triggered
    severity: str       # "none" | "low" | "medium" | "high" | "critical"


@dataclass
class OWASPReport:
    """OWASP LLM Top 10 compliance report for a single agent session."""

    session_id: str
    agent_name: str
    risks: list[OWASPRisk] = field(default_factory=list)
    total_triggered: int = 0
    highest_severity: str = "none"


@dataclass
class ComplianceSummary:
    """High-level summary combining EU AI Act and OWASP findings."""

    overall_risk: str       # "minimal" | "limited" | "high"
    total_flags: int
    owasp_triggered: int
    recommendation: str


@dataclass
class ComplianceReport:
    """Full compliance report combining EU AI Act and OWASP assessments."""

    session_id: str
    agent_name: str
    generated_at: str       # ISO 8601 UTC timestamp
    eu_ai_act: EUAIActReport | None = None
    owasp: OWASPReport | None = None
    summary: ComplianceSummary | None = None
