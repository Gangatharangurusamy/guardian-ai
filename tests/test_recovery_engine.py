"""Tests for guardian.recovery.engine — RecoveryEngine orchestration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from guardian.recovery.approval import ApprovalResult
from guardian.recovery.circuit_breaker import CircuitBreaker, CircuitState
from guardian.recovery.engine import RecoveryEngine, RecoveryOutcome, _ethics_strikes
from guardian.recovery.policy import RecoveryPolicy
from guardian.watchdog.models import Diagnosis, FailureSignal, FailureType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_policy(
    mode="auto_retry",
    actions=None,
    fallback_model="claude-3-5-sonnet-20241022",
    approval_method="cli",
    circuit_enabled=True,
):
    """Build a RecoveryPolicy from a dict (no file I/O)."""
    policy_dict = {
        "diagnosis": {"enabled": True, "model": "gpt-4o", "max_trace_chars": 8000},
        "failure_detection": {"loop_threshold": 3, "timeout_ms": 30000, "error_repeat_threshold": 2},
        "recovery": {
            "mode": mode,
            "max_retries": 2,
            "retry_delay_ms": 0,  # no delay in tests
            "actions": actions or {
                "tool_loop": "switch_model",
                "timeout": "retry",
                "repeated_error": "escalate",
                "schema_mismatch": "retry",
                "ethics_block": "escalate",
                "confidence_drop": "switch_model",
                "unknown": "escalate",
            },
            "fallback_model": fallback_model,
        },
        "circuit_breaker": {"enabled": circuit_enabled, "failure_threshold": 5,
                             "window_seconds": 60, "recovery_timeout_s": 120},
        "approval": {"method": approval_method, "timeout_seconds": 300},
    }
    return RecoveryPolicy.from_dict(policy_dict)


def make_diagnosis(
    failure_type=FailureType.TOOL_LOOP,
    session_id="sess-engine-001",
    agent_name="test_engine_agent",
    root_cause="Tool loop detected",
    suggestion="Switch to fallback model",
    confidence=0.85,
    model_used="gpt-4o",
):
    signal = FailureSignal(
        failure_type=failure_type,
        description="detected failure",
        evidence="tool called 4 times with same result",
        confidence=0.95,
        session_id=session_id,
        agent_name=agent_name,
    )
    return Diagnosis(
        session_id=session_id,
        agent_name=agent_name,
        failure_signals=[signal],
        root_cause=root_cause,
        suggestion=suggestion,
        confidence=confidence,
        model_used=model_used,
    )


async def dummy_fn(*args, **kwargs):
    return "recovered result"


# ---------------------------------------------------------------------------
# Human-in-the-loop tests
# ---------------------------------------------------------------------------

class TestHumanInLoop:

    @pytest.mark.asyncio
    async def test_rejected_approval_does_not_execute_action(self):
        """REJECTED approval → outcome success=False, action not executed."""
        policy = make_policy(mode="human_in_loop", actions={"tool_loop": "retry"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis()

        with patch.object(engine._approval_gate, "request_approval",
                          new_callable=AsyncMock) as mock_gate:
            mock_gate.return_value = ApprovalResult.REJECTED
            with patch("guardian.recovery.engine.action_retry", new_callable=AsyncMock) as mock_retry:
                outcome = await engine.recover(
                    diagnosis=diagnosis,
                    original_fn=dummy_fn,
                    original_args=("query",),
                    original_kwargs={},
                )

        assert outcome.success is False
        assert outcome.approval_result == "rejected"
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_approved_executes_action(self):
        """APPROVED approval → retry action is executed."""
        policy = make_policy(mode="human_in_loop", actions={"tool_loop": "retry"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis()

        with patch.object(engine._approval_gate, "request_approval",
                          new_callable=AsyncMock) as mock_gate:
            mock_gate.return_value = ApprovalResult.APPROVED
            with patch("guardian.recovery.engine.action_retry",
                       new_callable=AsyncMock, return_value=(True, "result")) as mock_retry:
                outcome = await engine.recover(
                    diagnosis=diagnosis,
                    original_fn=dummy_fn,
                    original_args=("query",),
                    original_kwargs={},
                )

        assert outcome.approval_result == "approved"
        mock_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_approval_escalates(self):
        """TIMEOUT approval → escalates instead of executing original action."""
        policy = make_policy(mode="human_in_loop", actions={"tool_loop": "retry"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis()

        with patch.object(engine._approval_gate, "request_approval",
                          new_callable=AsyncMock) as mock_gate:
            mock_gate.return_value = ApprovalResult.TIMEOUT
            with patch("guardian.recovery.engine.action_escalate",
                       new_callable=AsyncMock) as mock_escalate:
                with patch("guardian.recovery.engine.action_retry",
                           new_callable=AsyncMock) as mock_retry:
                    outcome = await engine.recover(
                        diagnosis=diagnosis,
                        original_fn=dummy_fn,
                        original_args=("query",),
                        original_kwargs={},
                    )

        mock_escalate.assert_called_once()
        mock_retry.assert_not_called()


# ---------------------------------------------------------------------------
# Mode tests
# ---------------------------------------------------------------------------

class TestRecoveryModes:

    @pytest.mark.asyncio
    async def test_notify_then_retry_no_approval_gate(self):
        """notify_then_retry mode → approval gate NOT called."""
        policy = make_policy(mode="notify_then_retry", actions={"tool_loop": "retry"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis()

        with patch.object(engine._approval_gate, "request_approval",
                          new_callable=AsyncMock) as mock_gate:
            with patch("guardian.recovery.engine.action_retry",
                       new_callable=AsyncMock, return_value=(True, "ok")) as mock_retry:
                outcome = await engine.recover(
                    diagnosis=diagnosis,
                    original_fn=dummy_fn,
                    original_args=(),
                    original_kwargs={},
                )

        mock_gate.assert_not_called()
        mock_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_retry_silent_no_gate(self):
        """auto_retry mode → gate NOT called, action executes silently."""
        policy = make_policy(mode="auto_retry", actions={"tool_loop": "retry"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis()

        with patch.object(engine._approval_gate, "request_approval",
                          new_callable=AsyncMock) as mock_gate:
            with patch("guardian.recovery.engine.action_retry",
                       new_callable=AsyncMock, return_value=(True, "ok")):
                outcome = await engine.recover(
                    diagnosis=diagnosis,
                    original_fn=dummy_fn,
                    original_args=(),
                    original_kwargs={},
                )

        mock_gate.assert_not_called()


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class TestCircuitBreakerIntegration:

    @pytest.mark.asyncio
    async def test_open_circuit_switches_to_fallback_model(self):
        """When circuit is open, override action to switch_model."""
        policy = make_policy(
            mode="auto_retry",
            actions={"tool_loop": "retry"},  # would normally retry
            fallback_model="claude-3-5-sonnet-20241022",
        )
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis()

        # Force circuit open
        with patch.object(engine._circuit_breaker, "is_open",
                          new_callable=AsyncMock, return_value=True):
            with patch("guardian.recovery.engine.action_switch_model",
                       new_callable=AsyncMock, return_value=(True, "switched")) as mock_switch:
                with patch("guardian.recovery.engine.action_retry",
                           new_callable=AsyncMock) as mock_retry:
                    outcome = await engine.recover(
                        diagnosis=diagnosis,
                        original_fn=dummy_fn,
                        original_args=(),
                        original_kwargs={},
                    )

        mock_switch.assert_called_once()
        mock_retry.assert_not_called()


# ---------------------------------------------------------------------------
# DB Write
# ---------------------------------------------------------------------------

class TestRecoveryRecordWritten:

    @pytest.mark.asyncio
    async def test_recovery_record_written_to_db(self):
        """A RecoveryActionRecord is written to the DB after every recover() call."""
        from guardian.store.db import init_db, reset_engine
        from guardian.store.reader import get_recovery_actions

        reset_engine()
        init_db("sqlite:///:memory:")

        policy = make_policy(mode="auto_retry", actions={"tool_loop": "escalate"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis(session_id="sess-db-write-001")

        with patch("guardian.recovery.engine.action_escalate", new_callable=AsyncMock):
            await engine.recover(
                diagnosis=diagnosis,
                original_fn=dummy_fn,
                original_args=(),
                original_kwargs={},
            )

        records = get_recovery_actions("sess-db-write-001")
        assert len(records) >= 1
        assert records[0]["session_id"] == "sess-db-write-001"
        assert records[0]["action_taken"] == "escalate"

        reset_engine()


# ---------------------------------------------------------------------------
# Never Raises
# ---------------------------------------------------------------------------

class TestRecoverNeverRaises:

    @pytest.mark.asyncio
    async def test_action_throws_returns_outcome_not_raise(self):
        """If the action function itself throws, recover() returns outcome, does not raise."""
        policy = make_policy(mode="auto_retry", actions={"tool_loop": "retry"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis()

        with patch("guardian.recovery.engine.action_retry",
                   new_callable=AsyncMock) as mock_retry:
            mock_retry.side_effect = RuntimeError("action exploded")
            # Should NOT raise
            outcome = await engine.recover(
                diagnosis=diagnosis,
                original_fn=dummy_fn,
                original_args=(),
                original_kwargs={},
            )

        assert outcome.success is False
        assert "exploded" in outcome.error


# ---------------------------------------------------------------------------
# 2-Strike Ethics Rule
# ---------------------------------------------------------------------------

class TestTwoStrikeEthicsRule:

    @pytest.mark.asyncio
    async def test_second_ethics_strike_escalates_immediately(self):
        """Second ETHICS_BLOCK for same violation_type → immediate escalation."""
        # Clear in-process strikes dict
        _ethics_strikes.clear()

        policy = make_policy(mode="auto_retry", actions={"ethics_block": "log_only"})
        engine = RecoveryEngine(policy=policy)

        diagnosis = make_diagnosis(
            failure_type=FailureType.ETHICS_BLOCK,
            session_id="sess-ethics-001",
        )

        # First strike — should execute log_only (normal flow)
        with patch("guardian.recovery.engine.action_log_only", new_callable=AsyncMock) as mock_log:
            with patch("guardian.recovery.engine.action_escalate", new_callable=AsyncMock) as mock_esc:
                await engine.recover(
                    diagnosis=diagnosis,
                    original_fn=dummy_fn,
                    original_args=(),
                    original_kwargs={},
                )
                mock_esc.assert_not_called()  # no escalation on first strike

        # Second strike — same session_id, same violation type → escalation
        with patch("guardian.recovery.engine.action_log_only", new_callable=AsyncMock) as mock_log:
            with patch("guardian.recovery.engine.action_escalate", new_callable=AsyncMock) as mock_esc:
                outcome = await engine.recover(
                    diagnosis=diagnosis,
                    original_fn=dummy_fn,
                    original_args=(),
                    original_kwargs={},
                )
                mock_esc.assert_called_once()  # escalation triggered on 2nd strike
                mock_log.assert_not_called()  # normal action skipped

        _ethics_strikes.clear()

    @pytest.mark.asyncio
    async def test_first_ethics_strike_proceeds_normally(self):
        """First ETHICS_BLOCK → proceeds with policy-defined action, NOT escalation."""
        _ethics_strikes.clear()

        policy = make_policy(mode="auto_retry", actions={"ethics_block": "log_only"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis(
            failure_type=FailureType.ETHICS_BLOCK,
            session_id="sess-ethics-first-001",
        )

        with patch("guardian.recovery.engine.action_log_only", new_callable=AsyncMock) as mock_log:
            with patch("guardian.recovery.engine.action_escalate", new_callable=AsyncMock) as mock_esc:
                await engine.recover(
                    diagnosis=diagnosis,
                    original_fn=dummy_fn,
                    original_args=(),
                    original_kwargs={},
                )
                mock_log.assert_called_once()
                mock_esc.assert_not_called()

        _ethics_strikes.clear()


# ---------------------------------------------------------------------------
# recover_sync
# ---------------------------------------------------------------------------

class TestRecoverSync:

    def test_recover_sync_returns_outcome(self):
        """recover_sync() wraps recover() and returns RecoveryOutcome."""
        policy = make_policy(mode="auto_retry", actions={"tool_loop": "log_only"})
        engine = RecoveryEngine(policy=policy)
        diagnosis = make_diagnosis()

        with patch("guardian.recovery.engine.action_log_only", new_callable=AsyncMock):
            outcome = engine.recover_sync(
                diagnosis=diagnosis,
                original_fn=dummy_fn,
                original_args=(),
                original_kwargs={},
            )

        assert isinstance(outcome, RecoveryOutcome)
