"""GUARDIAN EU AI Act Exporter.

Generates an EU AI Act compliance report for a single agent session by
reading trace events, ethics flags, and recovery actions from the store.

Risk classification:
  - "high"    -- any BLOCK ethics flag, escalation recovery action, or
                 sensitive keyword in agent name
  - "limited" -- any WARN flag or any recovery action
  - "minimal" -- clean trace with no flags or recovery actions
"""

from __future__ import annotations

import logging
from typing import Any

from guardian.compliance.schemas import EUAIActReport

logger = logging.getLogger("guardian")

# Agent name keywords that indicate high-risk contexts
_HIGH_RISK_KEYWORDS = {
    "medical", "health", "clinical", "finance", "legal", "law",
    "credit", "loan", "insurance", "hr", "hiring", "recruitment",
    "police", "court", "justice", "biometric", "surveillance",
}


class EUAIActExporter:
    """Generates EU AI Act compliance reports from GUARDIAN trace data."""

    def generate(self, session_id: str) -> EUAIActReport:
        """Generate an EU AI Act report for the given session.

        Fetches trace, ethics flags, and recovery actions from the store.
        Never raises -- returns a minimal report on any error.

        Args:
            session_id: The UUID4 session identifier.

        Returns:
            An EUAIActReport dataclass instance.
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
            risk_level = self._classify_risk(agent_name, flags, actions)

            return EUAIActReport(
                session_id=session_id,
                agent_name=agent_name,
                risk_level=risk_level,
                transparency=self._build_transparency(trace, flags),
                human_oversight=self._build_human_oversight(actions),
                bias_documentation=self._build_bias_docs(flags),
                audit_trail=self._build_audit_trail(trace, flags, actions),
                data_governance=self._build_data_governance(flags),
                technical_documentation=self._build_technical_docs(trace),
            )
        except Exception as exc:
            logger.warning("EUAIActExporter: failed for session %s: %s", session_id, exc)
            return EUAIActReport(
                session_id=session_id,
                agent_name="unknown",
                risk_level="unknown",
            )

    # ------------------------------------------------------------------
    # Risk classification
    # ------------------------------------------------------------------

    def _classify_risk(
        self,
        agent_name: str,
        flags: list[dict[str, Any]],
        actions: list[dict[str, Any]],
    ) -> str:
        """Classify risk level based on flags, actions, and agent name."""
        # High risk: BLOCK flag, escalation action, or sensitive agent name
        has_block = any(f.get("severity", "").lower() == "block" for f in flags)
        has_escalation = any(a.get("action_taken", "") == "escalate" for a in actions)
        name_lower = agent_name.lower()
        has_sensitive_name = any(kw in name_lower for kw in _HIGH_RISK_KEYWORDS)

        if has_block or has_escalation or has_sensitive_name:
            return "high"

        # Limited risk: WARN flag or any recovery action
        has_warn = any(f.get("severity", "").lower() == "warn" for f in flags)
        if has_warn or actions:
            return "limited"

        return "minimal"

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_transparency(
        self, trace: dict[str, Any], flags: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "agent_name": trace.get("agent_name", "unknown"),
            "session_id": trace.get("session_id", ""),
            "started_at": trace.get("started_at", ""),
            "ended_at": trace.get("ended_at", ""),
            "duration_ms": trace.get("duration_ms", 0),
            "status": trace.get("status", "unknown"),
            "total_tool_calls": len(trace.get("calls", [])),
            "ethics_flags_raised": len(flags),
            "ethics_violation_types": list({f.get("violation_type", "") for f in flags}),
        }

    def _build_human_oversight(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        human_approvals = [a for a in actions if a.get("approval_result") is not None]
        return {
            "recovery_actions_taken": len(actions),
            "human_approvals_requested": len(human_approvals),
            "approval_outcomes": [
                {
                    "action": a.get("action_taken"),
                    "result": a.get("approval_result"),
                    "at": a.get("recovered_at", ""),
                }
                for a in human_approvals
            ],
            "escalations": sum(1 for a in actions if a.get("action_taken") == "escalate"),
        }

    def _build_bias_docs(self, flags: list[dict[str, Any]]) -> dict[str, Any]:
        bias_flags = [
            f for f in flags
            if any(kw in f.get("violation_type", "").lower()
                   for kw in ("bias", "discrimination", "fairness", "protected"))
        ]
        return {
            "bias_flags_detected": len(bias_flags),
            "bias_violation_types": [f.get("violation_type") for f in bias_flags],
            "bias_evidence": [f.get("evidence", "") for f in bias_flags],
        }

    def _build_audit_trail(
        self,
        trace: dict[str, Any],
        flags: list[dict[str, Any]],
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build a chronological merge of all trace events."""
        events: list[dict[str, Any]] = []

        if trace:
            events.append({
                "timestamp": trace.get("started_at", ""),
                "event_type": "agent_start",
                "detail": f"Agent '{trace.get('agent_name')}' started",
            })

        for call in trace.get("calls", []):
            events.append({
                "timestamp": call.get("start_time", ""),
                "event_type": "tool_call",
                "detail": f"Called '{call.get('function', 'unknown')}'",
            })

        for flag in flags:
            events.append({
                "timestamp": flag.get("detected_at", ""),
                "event_type": "ethics_flag",
                "detail": f"[{flag.get('severity', '').upper()}] {flag.get('violation_type')}: {flag.get('description', '')}",
            })

        for action in actions:
            events.append({
                "timestamp": action.get("recovered_at", ""),
                "event_type": "recovery_action",
                "detail": f"Recovery: {action.get('action_taken')} for {action.get('failure_type')}",
            })

        if trace:
            events.append({
                "timestamp": trace.get("ended_at", ""),
                "event_type": "agent_end",
                "detail": f"Agent finished with status '{trace.get('status', 'unknown')}'",
            })

        # Sort by timestamp string (ISO format sorts lexicographically)
        events.sort(key=lambda e: e.get("timestamp", ""))
        return events

    def _build_data_governance(self, flags: list[dict[str, Any]]) -> dict[str, Any]:
        pii_flags = [
            f for f in flags
            if "pii" in f.get("violation_type", "").lower()
        ]
        return {
            "pii_incidents": len(pii_flags),
            "pii_field_paths": [f.get("field_path", "") for f in pii_flags],
            "data_minimization_status": "violation_detected" if pii_flags else "compliant",
        }

    def _build_technical_docs(self, trace: dict[str, Any]) -> dict[str, Any]:
        calls = trace.get("calls", [])
        return {
            "total_calls": len(calls),
            "estimated_tokens": sum(c.get("estimated_tokens", 0) for c in calls),
            "retry_count": sum(c.get("retry_count", 0) for c in calls),
            "tool_names_used": list({c.get("function", c.get("function_name", "")) for c in calls if c.get("function") or c.get("function_name")}),
            "guardian_version": "0.1.0",
        }
