"""GUARDIAN Recovery Actions — standalone async action functions.

Each function implements one recovery strategy. They handle the I/O
side effects (sleeping, calling functions, printing alerts) but do NOT
write to the database — that is owned by RecoveryEngine.recover().

All functions accept the same basic signature for easy dispatch.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from guardian.watchdog.models import Diagnosis

logger = logging.getLogger("guardian")


async def action_retry(
    original_fn: Callable,
    original_args: tuple,
    original_kwargs: dict,
    delay_ms: int = 1000,
) -> tuple[bool, Any]:
    """Wait delay_ms then re-call original_fn with the same args.

    Handles both sync and async original functions transparently.

    Args:
        original_fn: The original agent function to retry.
        original_args: Positional arguments originally passed to the function.
        original_kwargs: Keyword arguments originally passed to the function.
        delay_ms: Milliseconds to wait before retrying.

    Returns:
        Tuple of (success: bool, result: Any).
        On exception: (False, the exception object).
    """
    await asyncio.sleep(delay_ms / 1000.0)
    try:
        if inspect.iscoroutinefunction(original_fn):
            result = await original_fn(*original_args, **original_kwargs)
        else:
            result = original_fn(*original_args, **original_kwargs)
        logger.info(
            "GUARDIAN action_retry: retry succeeded for '%s'",
            getattr(original_fn, "__name__", str(original_fn)),
        )
        return (True, result)
    except Exception as exc:
        logger.warning(
            "GUARDIAN action_retry: retry failed for '%s': %s",
            getattr(original_fn, "__name__", str(original_fn)),
            exc,
        )
        return (False, exc)


async def action_switch_model(
    original_fn: Callable,
    original_args: tuple,
    original_kwargs: dict,
    fallback_model: str,
    delay_ms: int = 1000,
) -> tuple[bool, Any]:
    """Retry with a different LLM model.

    Injects fallback_model into kwargs if the function accepts a 'model'
    parameter. Otherwise sets GUARDIAN_ACTIVE_MODEL env var so LiteLLM
    picks it up automatically.

    Args:
        original_fn: The original agent function to retry.
        original_args: Positional arguments originally passed to the function.
        original_kwargs: Keyword arguments originally passed to the function.
        fallback_model: LiteLLM model string to switch to.
        delay_ms: Milliseconds to wait before retrying.

    Returns:
        Tuple of (success: bool, result: Any).
        On exception: (False, the exception object).
    """
    await asyncio.sleep(delay_ms / 1000.0)

    # Check if the function accepts a 'model' kwarg
    try:
        sig = inspect.signature(original_fn)
        accepts_model = "model" in sig.parameters
    except (ValueError, TypeError):
        accepts_model = False

    modified_kwargs = dict(original_kwargs)
    env_was_set = False

    if accepts_model:
        modified_kwargs["model"] = fallback_model
        logger.info(
            "GUARDIAN action_switch_model: injecting model='%s' into '%s' kwargs",
            fallback_model,
            getattr(original_fn, "__name__", str(original_fn)),
        )
    else:
        # Set env var — LiteLLM reads GUARDIAN_ACTIVE_MODEL automatically
        os.environ["GUARDIAN_ACTIVE_MODEL"] = fallback_model
        env_was_set = True
        logger.info(
            "GUARDIAN action_switch_model: set GUARDIAN_ACTIVE_MODEL='%s' for '%s'",
            fallback_model,
            getattr(original_fn, "__name__", str(original_fn)),
        )

    try:
        if inspect.iscoroutinefunction(original_fn):
            result = await original_fn(*original_args, **modified_kwargs)
        else:
            result = original_fn(*original_args, **modified_kwargs)
        logger.info(
            "GUARDIAN action_switch_model: succeeded with model '%s'",
            fallback_model,
        )
        return (True, result)
    except Exception as exc:
        logger.warning(
            "GUARDIAN action_switch_model: failed with model '%s': %s",
            fallback_model,
            exc,
        )
        return (False, exc)
    finally:
        # Clean up env var to avoid leaking into subsequent calls
        if env_was_set:
            os.environ.pop("GUARDIAN_ACTIVE_MODEL", None)


async def action_pause(
    session_id: str,
    agent_name: str,
    diagnosis: "Diagnosis",
) -> None:
    """Log a structured pause event.

    Does not retry. Prints a clear pause message to stdout.
    DB write is owned by RecoveryEngine — not done here.

    Args:
        session_id: The session being paused.
        agent_name: Name of the agent.
        diagnosis: The diagnosis that triggered the pause.
    """
    msg = (
        f"\n[GUARDIAN PAUSE] Agent '{agent_name}' (session {session_id}) paused.\n"
        f"  Root cause: {diagnosis.root_cause}\n"
        f"  Suggestion: {diagnosis.suggestion}\n"
        f"  Confidence: {diagnosis.confidence:.0%}\n"
    )
    sys.stdout.write(msg)
    sys.stdout.flush()
    logger.info(
        "GUARDIAN PAUSE: agent='%s' session='%s' reason='%s'",
        agent_name,
        session_id,
        diagnosis.root_cause,
    )


async def action_escalate(
    session_id: str,
    agent_name: str,
    diagnosis: "Diagnosis",
) -> None:
    """Log escalation and alert to stderr.

    Prints a clear human-readable alert. Phase 4 will add Slack/email here.
    DB write is owned by RecoveryEngine — not done here.

    Args:
        session_id: The session being escalated.
        agent_name: Name of the agent.
        diagnosis: The diagnosis that triggered escalation.
    """
    alert = (
        f"\n{'='*60}\n"
        f"  ⚠  GUARDIAN ESCALATION ALERT\n"
        f"{'='*60}\n"
        f"  Agent:    {agent_name}\n"
        f"  Session:  {session_id}\n"
        f"  Failure:  {diagnosis.root_cause}\n"
        f"  Action:   {diagnosis.suggestion}\n"
        f"  Model:    {diagnosis.model_used}\n"
        f"{'='*60}\n"
        f"  Manual intervention required.\n\n"
    )
    sys.stderr.write(alert)
    sys.stderr.flush()
    logger.warning(
        "GUARDIAN ESCALATE: agent='%s' session='%s' reason='%s'",
        agent_name,
        session_id,
        diagnosis.root_cause,
    )


async def action_log_only(
    session_id: str,
    agent_name: str,
    diagnosis: "Diagnosis",
) -> None:
    """Simply log the failure — no retry, no alert.

    DB write is owned by RecoveryEngine — not done here.

    Args:
        session_id: The session to log.
        agent_name: Name of the agent.
        diagnosis: The diagnosis to log.
    """
    logger.info(
        "GUARDIAN LOG_ONLY: agent='%s' session='%s' root_cause='%s'",
        agent_name,
        session_id,
        diagnosis.root_cause,
    )
