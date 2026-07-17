"""Integration tests for EthicsEngine module."""

from __future__ import annotations

import pytest
from guardian.ethics.engine import EthicsEngine
from guardian.ethics.exceptions import EthicsBlockException
from guardian.ethics.policy import EthicsPolicy
from guardian.ethics.violations import Severity, ViolationType
from guardian.store.db import get_engine, init_db, reset_engine
from guardian.store.reader import get_ethics_flags, get_recent_ethics_flags


@pytest.fixture(autouse=True)
def setup_test_db() -> None:
    """Isolate database tests using an in-memory SQLite database."""
    # Reset SQLAlchemy engine
    reset_engine()
    init_db("sqlite:///:memory:")



@pytest.mark.asyncio
async def test_ethics_engine_warn_violation() -> None:
    # Set up policy where email is warn (default) and ssn is block
    policy = EthicsPolicy.default()
    engine = EthicsEngine(policy)

    trace_event = {
        "session_id": "test-session-warn",
        "agent_name": "test-agent",
        "calls": [
            {
                "args_preview": "()",
                "result_preview": "Send inquiry to john.doe@example.com",
            }
        ],
    }

    # Should run and return the email warning without throwing exceptions
    violations = await engine.evaluate(trace_event)
    assert len(violations) == 1
    assert violations[0].violation_type == ViolationType.PII_DETECTED
    assert violations[0].severity == Severity.WARN
    assert violations[0].metadata["pii_type"] == "email"

    # Verify violation was persisted to DB
    flags = get_ethics_flags("test-session-warn")
    assert len(flags) == 1
    assert flags[0]["violation_type"] == "pii_detected"
    assert flags[0]["severity"] == "warn"


@pytest.mark.asyncio
async def test_ethics_engine_block_violation() -> None:
    policy = EthicsPolicy.default()
    engine = EthicsEngine(policy)

    # Trace containing SSN (BLOCK severity by default)
    trace_event = {
        "session_id": "test-session-block",
        "agent_name": "test-agent",
        "calls": [
            {
                "args_preview": "()",
                "result_preview": "Member SSN is 111-22-3333",
            }
        ],
    }

    # Should raise EthicsBlockException
    with pytest.raises(EthicsBlockException) as exc_info:
        await engine.evaluate(trace_event)

    assert len(exc_info.value.violations) == 1
    assert exc_info.value.violations[0].severity == Severity.BLOCK
    assert exc_info.value.violations[0].metadata["pii_type"] == "ssn"

    # Verify violation was still stored in DB before raising
    flags = get_ethics_flags("test-session-block")
    assert len(flags) == 1
    assert flags[0]["severity"] == "block"


@pytest.mark.asyncio
async def test_ethics_engine_disabled_checks() -> None:
    # Disable PII check in policy
    policy_dict = {
        "version": "1.0",
        "pii": {
            "enabled": False,
        },
    }
    policy = EthicsPolicy.from_dict(policy_dict)
    engine = EthicsEngine(policy)

    # Output has SSN, but check is disabled
    trace_event = {
        "session_id": "test-session-disabled",
        "agent_name": "test-agent",
        "calls": [
            {
                "args_preview": "()",
                "result_preview": "Member SSN is 111-22-3333",
            }
        ],
    }

    violations = await engine.evaluate(trace_event)
    assert len(violations) == 0

    # No flags in DB
    flags = get_ethics_flags("test-session-disabled")
    assert len(flags) == 0


@pytest.mark.asyncio
async def test_ethics_engine_clean_trace() -> None:
    policy = EthicsPolicy.default()
    engine = EthicsEngine(policy)

    trace_event = {
        "session_id": "test-session-clean",
        "agent_name": "test-agent",
        "calls": [
            {
                "args_preview": "()",
                "result_preview": "This is completely clean agent response with no problems.",
            }
        ],
    }

    violations = await engine.evaluate(trace_event)
    assert len(violations) == 0
