"""GUARDIAN Approval Gate — human-in-the-loop recovery approval.

Presents a formatted recovery proposal to a human operator and waits
for approval before the RecoveryEngine executes a recovery action.

CLI mode: prints to stdout and waits for y/n input with a timeout.
Webhook mode: stub — falls back to CLI (Phase 4 will implement webhook).

Never raises — returns ApprovalResult.TIMEOUT on any error.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from guardian.watchdog.models import Diagnosis

logger = logging.getLogger("guardian")


class ApprovalResult(str, Enum):
    """Result of a human-in-the-loop approval request.

    APPROVED — Human typed 'y' or 'yes'.
    REJECTED — Human typed anything else.
    TIMEOUT  — No input received within the timeout window.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


class ApprovalGate:
    """Human-in-the-loop approval gate for recovery actions.

    Presents a formatted prompt to the operator and returns their
    decision. Supports CLI mode (terminal) with a configurable timeout.

    Args:
        method: Approval mechanism — 'cli' (default) or 'webhook' (Phase 4 stub).
        timeout_seconds: How long to wait for human input before auto-escalating.
        webhook_url: Webhook URL for Phase 4 (unused until then).
    """

    def __init__(
        self,
        method: str = "cli",
        timeout_seconds: int = 300,
        webhook_url: str = "",
    ) -> None:
        self.method = method
        self.timeout_seconds = timeout_seconds
        self.webhook_url = webhook_url

    async def request_approval(
        self,
        session_id: str,
        agent_name: str,
        diagnosis: "Diagnosis",
        proposed_action: str,
    ) -> ApprovalResult:
        """Request human approval for a recovery action.

        Presents a formatted summary and waits for y/n input.
        Falls back to CLI if webhook mode is configured (Phase 4 stub).

        Args:
            session_id: The session being recovered.
            agent_name: Name of the failing agent.
            diagnosis: LLM-generated diagnosis with root_cause etc.
            proposed_action: Human-readable description of the recovery action.

        Returns:
            ApprovalResult enum value. Never raises.
        """
        try:
            if self.method == "webhook":
                logger.warning(
                    "GUARDIAN: Webhook approval is not yet implemented (Phase 4). "
                    "Falling back to CLI approval."
                )
                # Fall through to CLI
            return await self._cli_approval(
                session_id=session_id,
                agent_name=agent_name,
                diagnosis=diagnosis,
                proposed_action=proposed_action,
            )
        except Exception as exc:
            logger.warning("GUARDIAN approval gate error: %s", exc)
            return ApprovalResult.TIMEOUT

    async def _cli_approval(
        self,
        session_id: str,
        agent_name: str,
        diagnosis: "Diagnosis",
        proposed_action: str,
    ) -> ApprovalResult:
        """Non-blocking CLI prompt with timeout using asyncio.

        Uses run_in_executor so the event loop is not blocked while
        waiting for user input.

        Args:
            session_id: The session being recovered.
            agent_name: Name of the failing agent.
            diagnosis: Diagnosis with root_cause, confidence.
            proposed_action: What the recovery engine wants to do.

        Returns:
            ApprovalResult based on user input or timeout.
        """
        banner = (
            "\n"
            "═══════════════════════════════════════════════════════════\n"
            "  GUARDIAN — Recovery Approval Required\n"
            "═══════════════════════════════════════════════════════════\n"
            f"  Agent:      {agent_name}\n"
            f"  Session:    {session_id}\n"
            f"  Failure:    {diagnosis.root_cause}\n"
            f"  Action:     {proposed_action}\n"
            f"  Confidence: {diagnosis.confidence:.0%}\n"
            "───────────────────────────────────────────────────────────"
        )
        sys.stdout.write(banner + "\n")
        sys.stdout.flush()

        prompt = f"  Approve? [y/n] (timeout in {self.timeout_seconds}s): "

        loop = asyncio.get_event_loop()
        try:
            user_input: str = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: input(prompt)),
                timeout=float(self.timeout_seconds),
            )
            user_input = user_input.strip().lower()
            if user_input in ("y", "yes"):
                sys.stdout.write("  → Approved.\n\n")
                sys.stdout.flush()
                return ApprovalResult.APPROVED
            else:
                sys.stdout.write("  → Rejected.\n\n")
                sys.stdout.flush()
                return ApprovalResult.REJECTED
        except asyncio.TimeoutError:
            sys.stdout.write(
                f"\n  → Timeout after {self.timeout_seconds}s — escalating.\n\n"
            )
            sys.stdout.flush()
            return ApprovalResult.TIMEOUT
