"""Tests for PII detector module."""

from __future__ import annotations

import pytest
from guardian.ethics.pii_detector import PIIDetector, _mask_evidence
from guardian.ethics.violations import Severity, ViolationType


def test_mask_evidence() -> None:
    # Empty string
    assert _mask_evidence("") == ""

    # Too short to mask
    assert _mask_evidence("abc") == "ab****"

    # Standard email masking
    email = "john.doe@example.com"
    masked = _mask_evidence(email)
    assert masked.startswith("john.d")
    assert masked.endswith(".com")
    assert "****" in masked

    # Long string masking limit
    long_txt = "a" * 150
    assert len(_mask_evidence(long_txt)) == 61


def test_pii_regex_detection() -> None:
    detector = PIIDetector(use_spacy=False)

    # Email
    v = detector.scan("Contact me at test@example.com", "sess-1", "agent-1", "path")
    assert len(v) == 1
    assert v[0].violation_type == ViolationType.PII_DETECTED
    assert v[0].severity == Severity.WARN
    assert v[0].metadata["pii_type"] == "email"
    assert "te**" in v[0].evidence or "test@ex****" in v[0].evidence or "****" in v[0].evidence

    # SSN
    v = detector.scan("My SSN is 123-45-6789", "sess-1", "agent-1", "path")
    assert len(v) == 1
    assert v[0].severity == Severity.BLOCK
    assert v[0].metadata["pii_type"] == "ssn"

    # Credit Card
    v = detector.scan("Card number 4111-1111-1111-1111 here", "sess-1", "agent-1", "path")
    assert len(v) == 1
    assert v[0].severity == Severity.BLOCK
    assert v[0].metadata["pii_type"] == "credit_card"

    # IP Addresses
    v = detector.scan("IP address is 192.168.1.1", "sess-1", "agent-1", "path")
    assert len(v) == 1
    assert v[0].severity == Severity.LOG
    assert v[0].metadata["pii_type"] == "ip_address"


def test_pii_heuristics_detection() -> None:
    detector = PIIDetector(use_spacy=False)

    # AWS Key
    v = detector.scan("AWS key: AKIAIOSFODNN7EXAMPLE", "sess-1", "agent-1", "path")
    assert len(v) == 1
    assert v[0].severity == Severity.BLOCK
    assert v[0].metadata["pii_type"] == "aws_key"

    # JWT Token
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    v = detector.scan(f"token is {jwt}", "sess-1", "agent-1", "path")
    assert len(v) == 1
    assert v[0].severity == Severity.BLOCK
    assert v[0].metadata["pii_type"] == "api_key"
    assert v[0].metadata["pattern"] == "jwt"


def test_clean_text() -> None:
    detector = PIIDetector(use_spacy=False)
    v = detector.scan("This is completely clean text with no personal identifiers.", "sess-1", "agent-1", "path")
    assert len(v) == 0


def test_long_string_safety() -> None:
    detector = PIIDetector(use_spacy=False)
    # 60k characters long string (exceeds cap)
    long_string = "Hello world. " * 5000
    assert len(long_string) > 50000

    # Ensure it scans without crashing or timing out
    v = detector.scan(long_string, "sess-1", "agent-1", "path")
    assert isinstance(v, list)


def test_scan_trace_event() -> None:
    detector = PIIDetector(use_spacy=False)
    trace_event = {
        "session_id": "session-uuid",
        "agent_name": "support-bot",
        "calls": [
            {
                "function": "help",
                "args_preview": "('user1@example.com',)",
                "result_preview": "SSN on file is 999-99-9999",
            }
        ],
    }

    violations = detector.scan_trace_event(trace_event)
    # Should catch email in args_preview and SSN in result_preview
    assert len(violations) >= 2
    types = [v.metadata["pii_type"] for v in violations]
    assert "email" in types
    assert "ssn" in types
