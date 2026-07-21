"""GUARDIAN OWASP LLM Top 10 Exporter.

Maps GUARDIAN-detected events to the OWASP LLM Top 10 (2025) risk categories.

Risk mapping:
  OWASP-A01 Prompt Injection        -- ethics block flagged as prompt injection
  OWASP-A02 Insecure Output         -- ethics block (general)
  OWASP-A03 Training Data Poisoning -- not detectable at runtime (always False)
  OWASP-A04 Model DoS               -- tool_loop failure or timeout
  OWASP-A05 Supply Chain            -- not detectable at runtime (always False)
  OWASP-A06 Sensitive Info Exposure -- PII ethics flag
  OWASP-A07 Insecure Plugin Design  -- tool_loop failure
  OWASP-A08 Excessive Agency        -- any recovery action taken
  OWASP-A09 Overreliance            -- confidence_drop ethics flag
  OWASP-A10 Model Theft             -- not detectable at runtime (always False)
"""

from __future__ import annotations

import logging
from typing import Any

from guardian.compliance.schemas import OWASPReport, OWASPRisk

logger = logging.getLogger("guardian")

# Full OWASP LLM Top 10 (2025) risk catalogue
_OWASP_CATALOGUE: list[dict[str, str]] = [
    {"id": "OWASP-A01", "name": "Prompt Injection"},
    {"id": "OWASP-A02", "name": "Insecure Output Handling"},
    {"id": "OWASP-A03", "name": "Training Data Poisoning"},
    {"id": "OWASP-A04", "name": "Model Denial of Service"},
    {"id": "OWASP-A05", "name": "Supply Chain Vulnerabilities"},
    {"id": "OWASP-A06", "name": "Sensitive Information Disclosure"},
    {"id": "OWASP-A07", "name": "Insecure Plugin Design"},
    {"id": "OWASP-A08", "name": "Excessive Agency"},
    {"id": "OWASP-A09", "name": "Overreliance"},
    {"id": "OWASP-A10", "name": "Model Theft"},
]

_SEVERITY_ORDER = ["none", "low", "medium", "high", "critical"]


class OWASPExporter:
    """Generates OWASP LLM Top 10 compliance reports from GUARDIAN trace data."""

    def generate(self, session_id: str) -> OWASPReport:
        """Generate an OWASP Top 10 report for the given session.

        Fetches trace, ethics flags, and recovery actions from the store.
        Never raises -- returns a clean report on any error.

        Args:
            session_id: The UUID4 session identifier.

        Returns:
            An OWASPReport dataclass instance.
        """
        try:
            from guardian.store.reader import (
                get_ethics_flags,
                get_recovery_actions,
                get_trace,
            )

            trace = get_trace(session_id) or {}
            flags = get_ethics_flags(session_id)
            actions = get_recovery_actions(session_id)

            agent_name = trace.get("agent_name", "unknown")
            risks = self._assess_risks(trace, flags, actions)

            triggered = [r for r in risks if r.triggered]
            highest = self._highest_severity([r.severity for r in triggered])

            return OWASPReport(
                session_id=session_id,
                agent_name=agent_name,
                risks=risks,
                total_triggered=len(triggered),
                highest_severity=highest,
            )
        except Exception as exc:
            logger.warning("OWASPExporter: failed for session %s: %s", session_id, exc)
            return OWASPReport(session_id=session_id, agent_name="unknown")

    # ------------------------------------------------------------------
    # Risk assessment
    # ------------------------------------------------------------------

    def _assess_risks(
        self,
        trace: dict[str, Any],
        flags: list[dict[str, Any]],
        actions: list[dict[str, Any]],
    ) -> list[OWASPRisk]:
        violation_types = [f.get("violation_type", "").lower() for f in flags]
        severities = [f.get("severity", "").lower() for f in flags]
        failure_types = [a.get("failure_type", "").lower() for a in actions]

        risks: list[OWASPRisk] = []

        # OWASP-A01: Prompt Injection
        prompt_injection = any("prompt_injection" in vt or "injection" in vt for vt in violation_types)
        risks.append(OWASPRisk(
            risk_id="OWASP-A01",
            risk_name="Prompt Injection",
            triggered=prompt_injection,
            evidence=self._first_evidence(flags, ["prompt_injection", "injection"]) if prompt_injection else "",
            severity="critical" if prompt_injection else "none",
        ))

        # OWASP-A02: Insecure Output Handling (ethics block)
        has_block = any(s == "block" for s in severities)
        block_flags = [f for f in flags if f.get("severity", "").lower() == "block"]
        risks.append(OWASPRisk(
            risk_id="OWASP-A02",
            risk_name="Insecure Output Handling",
            triggered=has_block,
            evidence=block_flags[0].get("description", "") if block_flags else "",
            severity="high" if has_block else "none",
        ))

        # OWASP-A03: Training Data Poisoning (not detectable at runtime)
        risks.append(OWASPRisk(
            risk_id="OWASP-A03",
            risk_name="Training Data Poisoning",
            triggered=False,
            evidence="",
            severity="none",
        ))

        # OWASP-A04: Model Denial of Service (tool_loop failure or timeout)
        dos = any("tool_loop" in ft or "timeout" in ft for ft in failure_types)
        risks.append(OWASPRisk(
            risk_id="OWASP-A04",
            risk_name="Model Denial of Service",
            triggered=dos,
            evidence=f"Failure types detected: {', '.join(set(failure_types))}" if dos else "",
            severity="medium" if dos else "none",
        ))

        # OWASP-A05: Supply Chain (not detectable at runtime)
        risks.append(OWASPRisk(
            risk_id="OWASP-A05",
            risk_name="Supply Chain Vulnerabilities",
            triggered=False,
            evidence="",
            severity="none",
        ))

        # OWASP-A06: Sensitive Information Disclosure (PII flag)
        pii = any("pii" in vt for vt in violation_types)
        risks.append(OWASPRisk(
            risk_id="OWASP-A06",
            risk_name="Sensitive Information Disclosure",
            triggered=pii,
            evidence=self._first_evidence(flags, ["pii"]) if pii else "",
            severity="high" if pii else "none",
        ))

        # OWASP-A07: Insecure Plugin Design (tool_loop failure)
        tool_loop = any("tool_loop" in ft for ft in failure_types)
        risks.append(OWASPRisk(
            risk_id="OWASP-A07",
            risk_name="Insecure Plugin Design",
            triggered=tool_loop,
            evidence=f"tool_loop failure detected in {len(actions)} recovery action(s)" if tool_loop else "",
            severity="medium" if tool_loop else "none",
        ))

        # OWASP-A08: Excessive Agency (any recovery action taken)
        excessive = len(actions) > 0
        risks.append(OWASPRisk(
            risk_id="OWASP-A08",
            risk_name="Excessive Agency",
            triggered=excessive,
            evidence=f"{len(actions)} recovery action(s) triggered: {', '.join(set(failure_types))}" if excessive else "",
            severity="medium" if excessive else "none",
        ))

        # OWASP-A09: Overreliance (confidence_drop flag)
        overreliance = any("confidence" in vt or "overreliance" in vt for vt in violation_types)
        risks.append(OWASPRisk(
            risk_id="OWASP-A09",
            risk_name="Overreliance",
            triggered=overreliance,
            evidence=self._first_evidence(flags, ["confidence", "overreliance"]) if overreliance else "",
            severity="low" if overreliance else "none",
        ))

        # OWASP-A10: Model Theft (not detectable at runtime)
        risks.append(OWASPRisk(
            risk_id="OWASP-A10",
            risk_name="Model Theft",
            triggered=False,
            evidence="",
            severity="none",
        ))

        return risks

    def _first_evidence(self, flags: list[dict[str, Any]], keywords: list[str]) -> str:
        """Return evidence from the first flag whose violation_type matches a keyword."""
        for flag in flags:
            vt = flag.get("violation_type", "").lower()
            if any(kw in vt for kw in keywords):
                return flag.get("evidence") or flag.get("description", "")
        return ""

    def _highest_severity(self, severities: list[str]) -> str:
        """Return the highest severity from a list."""
        highest = "none"
        for s in severities:
            if _SEVERITY_ORDER.index(s) > _SEVERITY_ORDER.index(highest):
                highest = s
        return highest
