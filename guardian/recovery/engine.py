"""GUARDIAN Recovery Engine — orchestrates failure diagnosis and recovery.

Given a Diagnosis from the Diagnoser, selects and executes the appropriate
recovery action based on the loaded RecoveryPolicy. Handles human-in-the-loop
approval, circuit breaking, 2-strike ethics escalation, and DB persistence.

Never raises — all exceptions are caught and returned as RecoveryOutcome errors.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from guardian.recovery.actions import (
    action_escalate,
    action_log_only,
    action_pause,
    action_retry,
    action_switch_model,
)
from guardian.recovery.approval import ApprovalGate, ApprovalResult
from guardian.recovery.circuit_breaker import circuit_breaker
from guardian.recovery.policy import RecoveryPolicy
from guardian.watchdog.models import Diagnosis, FailureType

logger = logging.getLogger("guardian")

# ---------------------------------------------------------------------------
# 2-Strike Ethics Rule
# Module-level state — in-process only, resets on restart.
# Maps session_id → set of violation_types already seen.
# ---------------------------------------------------------------------------
_ethics_strikes: dict[str, set[str]] = {}


@dataclass
class RecoveryOutcome:
    """Result of a recovery attempt by RecoveryEngine.

    Attributes:
        session_id: The session that was recovered.
        agent_name: Name of the agent that was recovered.
        action_taken: Human-readable description of what was done.
        success: Whether recovery succeeded.
        result: Return value from retry if successful.
        error: Error message if recovery failed.
        approval_result: 'approved' / 'rejected' / 'timeout' / 'not_required'.
        retries_attempted: Number of retry attempts made.
        recovered_at: UTC timestamp when recovery completed.
    """

    session_id: str
    agent_name: str
    action_taken: str
    success: bool
    result: Any = None
    error: str = ""
    approval_result: str = ""
    retries_attempted: int = 0
    recovered_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-serializable dict."""
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "action_taken": self.action_taken,
            "success": self.success,
            "result": str(self.result) if self.result is not None else None,
            "error": self.error,
            "approval_result": self.approval_result,
            "retries_attempted": self.retries_attempted,
            "recovered_at": self.recovered_at.isoformat(),
        }


class RecoveryEngine:
    """Orchestrates recovery for a diagnosed agent failure.

    Reads a Diagnosis, selects the correct action from the policy,
    handles approval gating, and writes a RecoveryActionRecord to the DB.

    Args:
        policy: Loaded RecoveryPolicy. Defaults to RecoveryPolicy.default()
            if not provided.
    """

    def __init__(self, policy: RecoveryPolicy | None = None) -> None:
        self.policy = policy or RecoveryPolicy.default()
        self._circuit_breaker = circuit_breaker
        self._approval_gate = ApprovalGate(
            method=self.policy.approval.method,
            timeout_seconds=self.policy.approval.timeout_seconds,
            webhook_url=self.policy.approval.approval_webhook_url,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def recover(
        self,
        diagnosis: Diagnosis,
        original_fn: Callable,
        original_args: tuple,
        original_kwargs: dict,
    ) -> RecoveryOutcome:
        """Main entry point — given a diagnosis, execute the right recovery action.

        Flow:
        1. Check 2-strike ethics rule — immediate escalation on second strike
        2. Check circuit breaker — redirect to switch_model if provider is down
        3. Determine action from policy.recovery.actions
        4. Apply mode logic (human_in_loop / notify_then_retry / auto_retry)
        5. Execute the action
        6. Record circuit breaker result
        7. Write RecoveryActionRecord to DB (graceful on failure)
        8. Return RecoveryOutcome

        Args:
            diagnosis: LLM-generated or signal-based diagnosis.
            original_fn: The original unwrapped agent function.
            original_args: Positional args originally passed to the function.
            original_kwargs: Keyword args originally passed to the function.

        Returns:
            RecoveryOutcome with full details. Never raises.
        """
        session_id = diagnosis.session_id
        agent_name = diagnosis.agent_name
        failure_type = diagnosis.primary_failure_type()
        failure_type_str = failure_type.value

        try:
            # ----------------------------------------------------------------
            # Step 1: 2-Strike Ethics Rule
            # ----------------------------------------------------------------
            if failure_type == FailureType.ETHICS_BLOCK:
                violation_type = self._extract_violation_type(diagnosis)
                global _ethics_strikes

                if session_id in _ethics_strikes and violation_type in _ethics_strikes[session_id]:
                    # Second strike — escalate immediately
                    logger.warning(
                        "GUARDIAN 2-strike ethics rule triggered for session '%s', "
                        "violation '%s' — escalating immediately",
                        session_id,
                        violation_type,
                    )
                    await action_escalate(session_id, agent_name, diagnosis)
                    outcome = RecoveryOutcome(
                        session_id=session_id,
                        agent_name=agent_name,
                        action_taken="escalate",
                        success=False,
                        approval_result="not_required",
                        error=f"2-strike ethics escalation: {violation_type}",
                    )
                    self._write_record(diagnosis, outcome, failure_type_str)
                    return outcome
                else:
                    # First strike — record it, continue with normal recovery
                    _ethics_strikes.setdefault(session_id, set()).add(violation_type)
                    logger.info(
                        "GUARDIAN: First ethics strike for session '%s', violation '%s'",
                        session_id,
                        violation_type,
                    )

            # ----------------------------------------------------------------
            # Step 2: Circuit Breaker Check
            # ----------------------------------------------------------------
            primary_model = self.policy.diagnosis.model
            circuit_open = await self._circuit_breaker.is_open(primary_model)

            # Start with policy-defined action
            action_str = self.policy.recovery.actions.get(failure_type_str, "escalate")

            if circuit_open and self.policy.circuit_breaker.enabled:
                logger.warning(
                    "GUARDIAN: Circuit open for provider of model '%s' — "
                    "overriding action to switch_model",
                    primary_model,
                )
                action_str = "switch_model"

            # ----------------------------------------------------------------
            # Step 3: Human-in-the-loop approval (if mode requires it)
            # ----------------------------------------------------------------
            mode = self.policy.recovery.mode
            approval_result_str = "not_required"

            if mode == "human_in_loop":
                proposed_action = self._describe_action(action_str)
                approval = await self._approval_gate.request_approval(
                    session_id=session_id,
                    agent_name=agent_name,
                    diagnosis=diagnosis,
                    proposed_action=proposed_action,
                )

                if approval == ApprovalResult.REJECTED:
                    outcome = RecoveryOutcome(
                        session_id=session_id,
                        agent_name=agent_name,
                        action_taken="rejected",
                        success=False,
                        approval_result="rejected",
                        error="Recovery action rejected by human operator",
                    )
                    self._write_record(diagnosis, outcome, failure_type_str)
                    return outcome

                if approval == ApprovalResult.TIMEOUT:
                    # Timeout → escalate
                    logger.warning(
                        "GUARDIAN: Approval timeout for session '%s' — escalating",
                        session_id,
                    )
                    action_str = "escalate"
                    approval_result_str = "timeout"
                else:
                    approval_result_str = "approved"

            elif mode == "notify_then_retry":
                logger.info(
                    "GUARDIAN notify_then_retry: executing '%s' for session '%s'",
                    action_str,
                    session_id,
                )

            # auto_retry: silent — no logging, no gate

            # ----------------------------------------------------------------
            # Step 4: Execute the Action
            # ----------------------------------------------------------------
            success = False
            result = None
            retries_attempted = 0
            error_str = ""

            if action_str == "retry":
                retries_attempted = 1
                success, result = await action_retry(
                    original_fn,
                    original_args,
                    original_kwargs,
                    delay_ms=self.policy.recovery.retry_delay_ms,
                )
                if not success:
                    error_str = str(result)
                    result = None

            elif action_str == "switch_model":
                retries_attempted = 1
                success, result = await action_switch_model(
                    original_fn,
                    original_args,
                    original_kwargs,
                    fallback_model=self.policy.recovery.fallback_model,
                    delay_ms=self.policy.recovery.retry_delay_ms,
                )
                if not success:
                    error_str = str(result)
                    result = None

            elif action_str == "pause":
                await action_pause(session_id, agent_name, diagnosis)
                success = True

            elif action_str == "escalate":
                await action_escalate(session_id, agent_name, diagnosis)
                success = False
                error_str = "Escalated to human — requires manual intervention"

            elif action_str == "log_only":
                await action_log_only(session_id, agent_name, diagnosis)
                success = True

            else:
                logger.warning("GUARDIAN: Unknown action '%s' — logging only", action_str)
                await action_log_only(session_id, agent_name, diagnosis)
                success = True

            # ----------------------------------------------------------------
            # Step 5: Update Circuit Breaker
            # ----------------------------------------------------------------
            if action_str in ("retry", "switch_model"):
                if success:
                    await self._circuit_breaker.record_success(primary_model)
                else:
                    await self._circuit_breaker.record_failure(primary_model)

            # ----------------------------------------------------------------
            # Step 6: Build Outcome
            # ----------------------------------------------------------------
            outcome = RecoveryOutcome(
                session_id=session_id,
                agent_name=agent_name,
                action_taken=action_str,
                success=success,
                result=result,
                error=error_str,
                approval_result=approval_result_str,
                retries_attempted=retries_attempted,
            )

            # ----------------------------------------------------------------
            # Step 7: Write to DB (Q5: graceful on failure)
            # ----------------------------------------------------------------
            self._write_record(diagnosis, outcome, failure_type_str)

            return outcome

        except Exception as exc:
            logger.warning("GUARDIAN recover() encountered an error: %s", exc, exc_info=True)
            return RecoveryOutcome(
                session_id=session_id,
                agent_name=agent_name,
                action_taken="error",
                success=False,
                error=str(exc),
                approval_result="",
            )

    def recover_sync(
        self,
        diagnosis: Diagnosis,
        original_fn: Callable,
        original_args: tuple,
        original_kwargs: dict,
    ) -> RecoveryOutcome:
        """Synchronous wrapper for recover().

        Uses asyncio.run() — safe for sync contexts. Do not call from
        within a running event loop.

        Args:
            diagnosis: The diagnosis to act on.
            original_fn: The original agent function.
            original_args: Positional args for the function.
            original_kwargs: Keyword args for the function.

        Returns:
            RecoveryOutcome. Never raises.
        """
        return asyncio.run(
            self.recover(diagnosis, original_fn, original_args, original_kwargs)
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_violation_type(self, diagnosis: Diagnosis) -> str:
        """Extract the violation type string for the 2-strike ethics rule.

        Tries the first FailureSignal's evidence, then falls back to
        parsing the raw_llm_response, then falls back to 'unknown'.
        """
        if diagnosis.failure_signals:
            evidence = diagnosis.failure_signals[0].evidence
            if evidence:
                return evidence[:100]  # Use as-is — unique enough for matching
        return "unknown"

    def _describe_action(self, action_str: str) -> str:
        """Return a human-readable description of a recovery action."""
        descriptions = {
            "retry": "Retry the agent with the same model and arguments",
            "switch_model": (
                f"Switch to fallback model '{self.policy.recovery.fallback_model}' and retry"
            ),
            "pause": "Pause agent execution pending human review",
            "escalate": "Escalate to human operator — no automatic action taken",
            "log_only": "Log the failure and continue without retrying",
        }
        return descriptions.get(action_str, f"Execute '{action_str}' recovery action")

    def _write_record(
        self,
        diagnosis: Diagnosis,
        outcome: RecoveryOutcome,
        failure_type_str: str,
    ) -> None:
        """Write a RecoveryActionRecord to the database.

        Gracefully handles the case where guardian.init() has not been called
        (DB not initialized) — logs a warning and continues.

        Args:
            diagnosis: The diagnosis that triggered recovery.
            outcome: The outcome of the recovery attempt.
            failure_type_str: The failure type string for the DB record.
        """
        try:
            from guardian.store.db import get_session
            from guardian.store.models import RecoveryActionRecord

            record = RecoveryActionRecord(
                session_id=outcome.session_id,
                agent_name=outcome.agent_name,
                failure_type=failure_type_str,
                root_cause=diagnosis.root_cause,
                suggestion=diagnosis.suggestion,
                action_taken=outcome.action_taken,
                success=outcome.success,
                approval_result=outcome.approval_result or "not_required",
                retries_attempted=outcome.retries_attempted,
                model_used=diagnosis.model_used,
                metadata_json=json.dumps({"error": outcome.error} if outcome.error else {}),
            )

            with get_session() as session:
                session.add(record)

            logger.debug(
                "GUARDIAN: Recovery record written for session '%s'",
                outcome.session_id,
            )
        except Exception as db_exc:
            logger.warning(
                "GUARDIAN: Could not write recovery record to DB: %s "
                "(call guardian.init() to enable DB persistence)",
                db_exc,
            )
