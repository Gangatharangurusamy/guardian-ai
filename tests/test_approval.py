"""Tests for guardian.recovery.approval — human-in-the-loop approval gate."""

from __future__ import annotations

import asyncio
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from guardian.recovery.approval import ApprovalGate, ApprovalResult
from guardian.watchdog.models import Diagnosis, FailureSignal, FailureType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_diagnosis(
    session_id="sess-001",
    agent_name="test_agent",
    root_cause="The tool is stuck in a loop",
    suggestion="Switch to a different model",
    confidence=0.85,
):
    signal = FailureSignal(
        failure_type=FailureType.TOOL_LOOP,
        description="tool loop detected",
        evidence="search_tool called 4 times",
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
        model_used="gpt-4o",
    )


# ---------------------------------------------------------------------------
# CLI Approval Tests
# ---------------------------------------------------------------------------

class TestCLIApproval:

    @pytest.mark.asyncio
    async def test_yes_input_returns_approved(self):
        """Input 'y' → ApprovalResult.APPROVED."""
        gate = ApprovalGate(method="cli", timeout_seconds=30)
        diagnosis = make_diagnosis()

        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout", new_callable=StringIO):
                result = await gate.request_approval(
                    session_id="sess-001",
                    agent_name="test_agent",
                    diagnosis=diagnosis,
                    proposed_action="Retry with fallback model",
                )

        assert result == ApprovalResult.APPROVED

    @pytest.mark.asyncio
    async def test_yes_full_word_returns_approved(self):
        """Input 'yes' → ApprovalResult.APPROVED."""
        gate = ApprovalGate(method="cli", timeout_seconds=30)
        diagnosis = make_diagnosis()

        with patch("builtins.input", return_value="yes"):
            with patch("sys.stdout", new_callable=StringIO):
                result = await gate.request_approval(
                    session_id="sess-001",
                    agent_name="test_agent",
                    diagnosis=diagnosis,
                    proposed_action="Retry",
                )

        assert result == ApprovalResult.APPROVED

    @pytest.mark.asyncio
    async def test_no_input_returns_rejected(self):
        """Input 'n' → ApprovalResult.REJECTED."""
        gate = ApprovalGate(method="cli", timeout_seconds=30)
        diagnosis = make_diagnosis()

        with patch("builtins.input", return_value="n"):
            with patch("sys.stdout", new_callable=StringIO):
                result = await gate.request_approval(
                    session_id="sess-001",
                    agent_name="test_agent",
                    diagnosis=diagnosis,
                    proposed_action="Switch model",
                )

        assert result == ApprovalResult.REJECTED

    @pytest.mark.asyncio
    async def test_arbitrary_input_returns_rejected(self):
        """Input 'maybe' or empty string → REJECTED (not y/yes)."""
        gate = ApprovalGate(method="cli", timeout_seconds=30)
        diagnosis = make_diagnosis()

        for user_input in ["maybe", "", "no", "cancel"]:
            with patch("builtins.input", return_value=user_input):
                with patch("sys.stdout", new_callable=StringIO):
                    result = await gate.request_approval(
                        session_id="sess-001",
                        agent_name="test_agent",
                        diagnosis=diagnosis,
                        proposed_action="Retry",
                    )
            assert result == ApprovalResult.REJECTED

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout(self):
        """asyncio.TimeoutError from wait_for → ApprovalResult.TIMEOUT."""
        gate = ApprovalGate(method="cli", timeout_seconds=1)
        diagnosis = make_diagnosis()

        async def slow_input():
            await asyncio.sleep(10)
            return "y"

        with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.side_effect = asyncio.TimeoutError()
            with patch("sys.stdout", new_callable=StringIO):
                result = await gate.request_approval(
                    session_id="sess-001",
                    agent_name="test_agent",
                    diagnosis=diagnosis,
                    proposed_action="Retry",
                )

        assert result == ApprovalResult.TIMEOUT

    @pytest.mark.asyncio
    async def test_prints_required_fields_to_stdout(self):
        """Stdout output must contain session_id, agent_name, root_cause, proposed_action."""
        gate = ApprovalGate(method="cli", timeout_seconds=30)
        diagnosis = make_diagnosis(
            session_id="sess-xyz-99",
            agent_name="my_special_agent",
            root_cause="Tool 'search' stuck in infinite loop",
        )
        proposed_action = "Switch to claude-3-5-sonnet-20241022"

        captured = StringIO()
        with patch("builtins.input", return_value="n"):
            with patch("sys.stdout", captured):
                await gate.request_approval(
                    session_id="sess-xyz-99",
                    agent_name="my_special_agent",
                    diagnosis=diagnosis,
                    proposed_action=proposed_action,
                )

        output = captured.getvalue()
        assert "sess-xyz-99" in output
        assert "my_special_agent" in output
        assert "Tool 'search' stuck in infinite loop" in output
        assert "Switch to claude-3-5-sonnet-20241022" in output


class TestWebhookFallback:

    @pytest.mark.asyncio
    async def test_webhook_method_falls_back_to_cli(self):
        """Webhook mode falls back to CLI (Phase 4 stub)."""
        gate = ApprovalGate(method="webhook", timeout_seconds=30, webhook_url="http://example.com")
        diagnosis = make_diagnosis()

        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout", new_callable=StringIO):
                result = await gate.request_approval(
                    session_id="sess-001",
                    agent_name="test_agent",
                    diagnosis=diagnosis,
                    proposed_action="Retry",
                )

        # Should still work (CLI fallback) and return APPROVED
        assert result == ApprovalResult.APPROVED


class TestApprovalNeverRaises:

    @pytest.mark.asyncio
    async def test_exception_returns_timeout_not_raise(self):
        """Any unexpected exception → TIMEOUT (never raises)."""
        gate = ApprovalGate(method="cli", timeout_seconds=30)
        diagnosis = make_diagnosis()

        with patch("guardian.recovery.approval.ApprovalGate._cli_approval",
                   new_callable=AsyncMock) as mock_cli:
            mock_cli.side_effect = RuntimeError("unexpected failure")
            result = await gate.request_approval(
                session_id="sess-001",
                agent_name="test_agent",
                diagnosis=diagnosis,
                proposed_action="Retry",
            )

        assert result == ApprovalResult.TIMEOUT
