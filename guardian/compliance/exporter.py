"""GUARDIAN Compliance Exporter.

Combines EU AI Act and OWASP LLM Top 10 assessments into a single
ComplianceReport. Provides JSON serialization and file export helpers.

Never raises — all public methods catch and log exceptions.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
from datetime import datetime, timezone

from guardian.compliance.eu_ai_act import EUAIActExporter
from guardian.compliance.owasp import OWASPExporter
from guardian.compliance.schemas import ComplianceReport, ComplianceSummary

logger = logging.getLogger("guardian")

_RISK_ORDER = {"minimal": 0, "limited": 1, "high": 2, "unknown": -1}


class ComplianceExporter:
    """Generates and serializes full compliance reports for GUARDIAN sessions."""

    def __init__(self) -> None:
        self._eu = EUAIActExporter()
        self._owasp = OWASPExporter()

    def generate_report(self, session_id: str) -> ComplianceReport:
        """Generate a full compliance report for the given session.

        Combines EU AI Act and OWASP assessments. Never raises — returns
        a partial report on any error.

        Args:
            session_id: The UUID4 session identifier.

        Returns:
            A ComplianceReport dataclass instance.
        """
        try:
            eu_report = self._eu.generate(session_id)
            owasp_report = self._owasp.generate(session_id)

            # Determine overall risk as the maximum of EU AI Act and OWASP
            eu_risk = eu_report.risk_level
            owasp_risk = "high" if owasp_report.highest_severity in ("high", "critical") else (
                "limited" if owasp_report.highest_severity in ("medium", "low") else "minimal"
            )
            overall_risk = max(eu_risk, owasp_risk, key=lambda r: _RISK_ORDER.get(r, -1))

            # Count total flags from EU report audit trail
            total_flags = sum(
                1 for e in eu_report.audit_trail
                if e.get("event_type") == "ethics_flag"
            )

            recommendation = self._build_recommendation(overall_risk, owasp_report.total_triggered)

            summary = ComplianceSummary(
                overall_risk=overall_risk,
                total_flags=total_flags,
                owasp_triggered=owasp_report.total_triggered,
                recommendation=recommendation,
            )

            return ComplianceReport(
                session_id=session_id,
                agent_name=eu_report.agent_name,
                generated_at=datetime.now(timezone.utc).isoformat(),
                eu_ai_act=eu_report,
                owasp=owasp_report,
                summary=summary,
            )
        except Exception as exc:
            logger.warning("ComplianceExporter: failed for session %s: %s", session_id, exc)
            return ComplianceReport(
                session_id=session_id,
                agent_name="unknown",
                generated_at=datetime.now(timezone.utc).isoformat(),
            )

    def to_json(self, report: ComplianceReport) -> str:
        """Serialize a ComplianceReport to a JSON string.

        Handles nested dataclasses and datetime serialization.

        Args:
            report: The ComplianceReport to serialize.

        Returns:
            A JSON string.
        """
        return json.dumps(
            dataclasses.asdict(report),
            indent=2,
            default=str,
        )

    def export_to_file(self, session_id: str, output_path: str | pathlib.Path) -> pathlib.Path:
        """Generate a compliance report and write it as JSON to a file.

        Creates parent directories as needed. Never raises — logs errors.

        Args:
            session_id: The UUID4 session identifier.
            output_path: Destination file path.

        Returns:
            The resolved output path.
        """
        output_path = pathlib.Path(output_path)
        try:
            report = self.generate_report(session_id)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(self.to_json(report), encoding="utf-8")
            logger.info("GUARDIAN: Compliance report written to %s", output_path)
        except Exception as exc:
            logger.warning("ComplianceExporter: file export failed: %s", exc)
        return output_path.resolve()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_recommendation(self, overall_risk: str, owasp_triggered: int) -> str:
        if overall_risk == "high":
            return (
                "IMMEDIATE ACTION REQUIRED: High-risk AI system detected. "
                "Conduct mandatory human oversight review, document all flagged "
                "incidents, and consider halting deployment pending audit."
            )
        if overall_risk == "limited":
            return (
                "MONITORING REQUIRED: Limited-risk AI system with active warnings. "
                f"{owasp_triggered} OWASP risk(s) triggered. "
                "Review ethics flags and recovery actions; increase monitoring frequency."
            )
        return (
            "COMPLIANT: No ethics violations or recovery actions detected. "
            "Continue standard monitoring and periodic compliance reviews."
        )
