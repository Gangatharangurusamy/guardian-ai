"""GUARDIAN Diagnosis Agent — LLM-powered root-cause analysis.

Sends failure signals and a truncated execution trace to an LLM via
LiteLLM and parses the structured diagnosis response.

Correction (user-approved): No fallback Diagnosis is returned.
If no API key is present, a RuntimeError is raised immediately.
All other LLM or parse errors also propagate — callers (e.g.,
RecoveryEngine.recover()) are responsible for catching them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from guardian.watchdog.models import Diagnosis, FailureSignal, FailureType
from guardian.watchdog.prompts import (
    CROSS_SESSION_DIAGNOSIS_USER_PROMPT,
    DIAGNOSIS_SYSTEM_PROMPT,
    DIAGNOSIS_USER_PROMPT,
)

logger = logging.getLogger("guardian")

# API key environment variable names checked in order
_API_KEY_ENV_VARS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "AZURE_API_KEY",
    "GEMINI_API_KEY",
    "HUGGINGFACE_API_KEY",
]


def _has_api_key() -> bool:
    """Return True if at least one LiteLLM-supported API key is set in env."""
    return any(os.environ.get(var) for var in _API_KEY_ENV_VARS)


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap JSON in."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening fence (```json or ```)
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class Diagnoser:
    """Sends failure signals + trace to an LLM and parses the diagnosis.

    Uses LiteLLM so any supported provider works — just set the matching
    API key in .env and point to the right model string in recovery-policy.yaml.

    Args:
        model_name: LiteLLM model string (e.g. "gpt-4o", "claude-3-5-sonnet-20241022",
            "groq/llama3-70b-8192"). Read from recovery-policy.yaml at runtime.
        max_trace_chars: Maximum characters of trace JSON to include in the
            LLM prompt. Traces are truncated to this limit to avoid exceeding
            context windows.

    Raises:
        RuntimeError: If no LiteLLM-compatible API key is found in the environment.
            Raised at diagnosis time (not at construction time).
    """

    def __init__(
        self,
        model_name: str = "gpt-4o",
        max_trace_chars: int = 8000,
    ) -> None:
        self.model_name = model_name
        self.max_trace_chars = max_trace_chars

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def diagnose(
        self,
        trace_event: dict[str, Any],
        failure_signals: list[FailureSignal],
    ) -> Diagnosis:
        """Send trace + signals to LLM, parse response into Diagnosis.

        Args:
            trace_event: The serialized TraceEvent dict from the SDK.
            failure_signals: List of FailureSignals from FailureDetector.

        Returns:
            A fully populated Diagnosis dataclass.

        Raises:
            RuntimeError: If no API key is configured.
            Exception: On LLM call failure or JSON parse failure.
        """
        if not _has_api_key():
            raise RuntimeError(
                "GUARDIAN diagnosis requires an LLM API key. "
                "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY or any "
                "LiteLLM-supported key in your .env"
            )

        import litellm  # Lazy import — not required unless diagnosis is used

        session_id = str(trace_event.get("session_id", ""))
        agent_name = str(trace_event.get("agent_name", ""))

        trace_str = self._truncate_trace(trace_event)
        signals_json = json.dumps(
            [s.to_dict() for s in failure_signals], indent=2
        )

        user_prompt = DIAGNOSIS_USER_PROMPT.format(
            agent_name=agent_name,
            session_id=session_id,
            failure_signals_json=signals_json,
            trace_json=trace_str,
        )

        messages = [
            {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        response = await litellm.acompletion(
            model=self.model_name,
            messages=messages,
            temperature=0.1,
        )

        raw_content = response.choices[0].message.content or ""
        logger.debug("GUARDIAN diagnoser raw response: %s", raw_content[:500])

        return self._parse_response(
            raw=raw_content,
            session_id=session_id,
            agent_name=agent_name,
            signals=failure_signals,
            model_used=self.model_name,
        )

    async def diagnose_across_sessions(
        self,
        agent_name: str,
        trace_events: list[dict[str, Any]],
        failure_signals: list[FailureSignal],
    ) -> Diagnosis:
        """Cross-session diagnosis using multiple traces.

        Sends the pattern summary and most recent trace to the LLM
        for cross-session root-cause analysis.

        Args:
            agent_name: Name of the agent being diagnosed.
            trace_events: List of recent trace event dicts.
            failure_signals: Combined signals from detect_across_sessions().

        Returns:
            A Diagnosis based on cross-session analysis.

        Raises:
            RuntimeError: If no API key is configured.
            Exception: On LLM call failure or JSON parse failure.
        """
        if not _has_api_key():
            raise RuntimeError(
                "GUARDIAN diagnosis requires an LLM API key. "
                "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY or any "
                "LiteLLM-supported key in your .env"
            )

        import litellm

        latest_trace = trace_events[-1] if trace_events else {}
        session_id = str(latest_trace.get("session_id", ""))

        latest_trace_str = self._truncate_trace(latest_trace)
        signals_json = json.dumps(
            [s.to_dict() for s in failure_signals], indent=2
        )

        user_prompt = CROSS_SESSION_DIAGNOSIS_USER_PROMPT.format(
            agent_name=agent_name,
            trace_count=len(trace_events),
            failure_signals_json=signals_json,
            latest_trace_json=latest_trace_str,
        )

        messages = [
            {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        response = await litellm.acompletion(
            model=self.model_name,
            messages=messages,
            temperature=0.1,
        )

        raw_content = response.choices[0].message.content or ""

        return self._parse_response(
            raw=raw_content,
            session_id=session_id,
            agent_name=agent_name,
            signals=failure_signals,
            model_used=self.model_name,
        )

    def diagnose_sync(
        self,
        trace_event: dict[str, Any],
        failure_signals: list[FailureSignal],
    ) -> Diagnosis:
        """Synchronous wrapper for diagnose().

        Uses asyncio.run() — safe to call from sync contexts.
        Do not call from within a running event loop (use diagnose() instead).

        Args:
            trace_event: The serialized TraceEvent dict from the SDK.
            failure_signals: List of FailureSignals from FailureDetector.

        Returns:
            A fully populated Diagnosis dataclass.

        Raises:
            RuntimeError: If no API key is configured.
            Exception: On LLM call failure or JSON parse failure.
        """
        return asyncio.run(self.diagnose(trace_event, failure_signals))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _truncate_trace(self, trace_event: dict[str, Any]) -> str:
        """Serialize trace to string and truncate to max_trace_chars.

        Always includes failure signals in the prompt even if trace
        is truncated. The truncation marker is appended so the LLM
        knows the trace was cut.

        Args:
            trace_event: The trace event dict to serialize.

        Returns:
            A string of at most max_trace_chars characters.
        """
        try:
            full_json = json.dumps(trace_event, indent=2, default=str)
        except Exception:
            full_json = str(trace_event)

        if len(full_json) <= self.max_trace_chars:
            return full_json

        truncated = full_json[: self.max_trace_chars]
        return truncated + "\n... [TRACE TRUNCATED]"

    def _parse_response(
        self,
        raw: str,
        session_id: str,
        agent_name: str,
        signals: list[FailureSignal],
        model_used: str,
    ) -> Diagnosis:
        """Parse the LLM JSON response into a Diagnosis.

        Strips markdown fences before parsing. Raises on bad JSON.

        Args:
            raw: Raw LLM response text.
            session_id: Session ID to embed in the Diagnosis.
            agent_name: Agent name to embed in the Diagnosis.
            signals: The failure signals that were diagnosed.
            model_used: Model string for audit.

        Returns:
            A populated Diagnosis dataclass.

        Raises:
            json.JSONDecodeError: If the response is not valid JSON.
            KeyError: If required fields are missing from the response.
        """
        cleaned = _strip_markdown_fences(raw)
        parsed = json.loads(cleaned)

        # Validate and extract fields
        root_cause = str(parsed["root_cause"])
        suggestion = str(parsed["suggestion"])
        confidence = float(parsed.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))  # clamp to [0, 1]

        # Parse failure_type — fall back to primary signal type if invalid
        ft_value = str(parsed.get("failure_type", "unknown"))
        try:
            failure_type = FailureType(ft_value)
        except ValueError:
            failure_type = (
                signals[0].failure_type if signals else FailureType.UNKNOWN
            )

        # If the LLM returned a different failure type, prepend a note
        _ = failure_type  # Used for context; stored in raw_llm_response

        return Diagnosis(
            session_id=session_id,
            agent_name=agent_name,
            failure_signals=signals,
            root_cause=root_cause,
            suggestion=suggestion,
            confidence=confidence,
            model_used=model_used,
            raw_llm_response=raw,
        )
