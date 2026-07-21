"""Tests for the GUARDIAN Compliance Exporter (EU AI Act + OWASP)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from guardian.compliance.exporter import ComplianceExporter
from guardian.compliance.schemas import ComplianceReport
from guardian.store.db import init_db, reset_engine, get_session
from guardian.store.models import TraceEventRecord, EthicsFlagRecord, RecoveryActionRecord

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    """Use an isolated in-memory SQLite database for each test."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    import guardian.store.db as db_module

    db_module.reset_engine()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_module._engine = engine
    db_module._SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    
    from guardian.store.models import Base
    Base.metadata.create_all(engine)
    monkeypatch.setenv("GUARDIAN_DB_URL", "sqlite:///:memory:")
    yield
    db_module.reset_engine()


def _write_trace(session_id: str, agent_name: str = "test_agent", status: str = "success"):
    from datetime import datetime, timezone
    with get_session() as sess:
        sess.add(TraceEventRecord(
            session_id=session_id,
            agent_name=agent_name,
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            duration_ms=100,
            status=status,
            calls_json="[]",
            metadata_json="{}",
        ))


def _write_flag(session_id: str, severity: str, violation_type: str, description: str = "test"):
    from datetime import datetime, timezone
    with get_session() as sess:
        sess.add(EthicsFlagRecord(
            session_id=session_id,
            agent_name="test_agent",
            violation_type=violation_type,
            severity=severity,
            description=description,
            evidence="some evidence",
            field_path="output.text",
            confidence=0.95,
            metadata_json="{}",
            detected_at=datetime.now(timezone.utc),
        ))


def _write_recovery(session_id: str, action: str, failure_type: str, success: bool = True):
    from datetime import datetime, timezone
    with get_session() as sess:
        sess.add(RecoveryActionRecord(
            session_id=session_id,
            agent_name="test_agent",
            failure_type=failure_type,
            action_taken=action,
            success=success,
            retries_attempted=0,
            metadata_json="{}",
            recovered_at=datetime.now(timezone.utc),
        ))


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestEUAIActExporter:
    def test_generates_report_for_pii_session(self):
        sid = "pii-session-001"
        _write_trace(sid, agent_name="data_processor")
        _write_flag(sid, severity="warn", violation_type="pii_leak", description="SSN detected")

        report = ComplianceExporter().generate_report(sid)

        assert report.session_id == sid
        assert report.eu_ai_act is not None
        assert report.eu_ai_act.data_governance["pii_incidents"] == 1

    def test_risk_high_for_block_flag(self):
        sid = "block-session-001"
        _write_trace(sid)
        _write_flag(sid, severity="block", violation_type="ethics_violation")

        report = ComplianceExporter().generate_report(sid)

        assert report.eu_ai_act is not None
        assert report.eu_ai_act.risk_level == "high"
        assert report.summary is not None      
        assert report.summary.overall_risk == "high"

    def test_risk_minimal_for_clean_session(self):
        sid = "clean-session-001"
        _write_trace(sid)

        report = ComplianceExporter().generate_report(sid)

        assert report.eu_ai_act is not None
        assert report.eu_ai_act.risk_level == "minimal"
        assert report.summary is not None  
        assert report.summary.overall_risk == "minimal"


class TestOWASPExporter:
    def test_owasp_a06_triggered_by_pii_flag(self):
        sid = "owasp-pii-001"
        _write_trace(sid)
        _write_flag(sid, severity="warn", violation_type="pii_leak")

        report = ComplianceExporter().generate_report(sid)

        owasp = report.owasp
        assert owasp is not None
        a06 = next((r for r in owasp.risks if r.risk_id == "OWASP-A06"), None)
        assert a06 is not None
        assert a06.triggered is True

    def test_owasp_a07_triggered_by_tool_loop(self):
        sid = "owasp-loop-001"
        _write_trace(sid)
        _write_recovery(sid, action="log_only", failure_type="tool_loop")

        report = ComplianceExporter().generate_report(sid)

        owasp = report.owasp
        assert owasp is not None
        a07 = next((r for r in owasp.risks if r.risk_id == "OWASP-A07"), None)
        assert a07 is not None
        assert a07.triggered is True


class TestComplianceExporter:
    def test_to_json_produces_valid_json(self):
        sid = "json-test-001"
        _write_trace(sid)

        exporter = ComplianceExporter()
        report = exporter.generate_report(sid)
        json_str = exporter.to_json(report)

        parsed = json.loads(json_str)
        assert parsed["session_id"] == sid
        assert "eu_ai_act" in parsed
        assert "owasp" in parsed

    def test_generate_report_never_raises_for_unknown_session(self):
        """Must return a ComplianceReport even for a non-existent session_id."""
        exporter = ComplianceExporter()
        report = exporter.generate_report("nonexistent-session-xyz")

        assert isinstance(report, ComplianceReport)
        assert report.session_id == "nonexistent-session-xyz"
