"""GUARDIAN Circuit Breaker — per-provider failure tracking.

Prevents GUARDIAN from hammering a failing LLM provider by tracking
failure rates in a rolling time window and opening the circuit when
the failure threshold is exceeded.

In-memory only — circuit state resets on process restart.
Thread-safe using asyncio.Lock per provider.

Module-level singleton: import `circuit_breaker` to get the shared instance.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("guardian")


class CircuitState(str, Enum):
    """State of a circuit breaker for a single LLM provider.

    CLOSED  — Normal operation. Calls are allowed.
    OPEN    — Too many failures. Calls are blocked.
    HALF_OPEN — Recovery timeout elapsed. One test call allowed.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _ProviderState:
    """Internal per-provider state for the circuit breaker."""

    state: CircuitState = CircuitState.CLOSED
    failure_timestamps: deque[float] = field(default_factory=deque)
    opened_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CircuitBreaker:
    """Per-provider in-memory circuit breaker.

    Tracks LLM call failures per provider (extracted from model name)
    and opens the circuit when the failure count within the rolling
    window reaches the threshold.

    Provider extraction from model name:
        "gpt-4o"                    → "openai"
        "gpt-4o-mini"               → "openai"
        "claude-3-5-sonnet-20241022"→ "anthropic"
        "groq/llama3-70b-8192"     → "groq"
        "mistral/mistral-large"     → "mistral"
        "bedrock/..."               → "bedrock"
        "any-other/model"           → "any-other" (prefix before /)

    Args:
        failure_threshold: Number of failures within window to open circuit.
        window_seconds: Rolling time window for counting failures.
        recovery_timeout_s: Seconds to wait in OPEN state before testing recovery.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        window_seconds: int = 60,
        recovery_timeout_s: int = 120,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.recovery_timeout_s = recovery_timeout_s
        self._providers: dict[str, _ProviderState] = {}
        self._global_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record_success(self, model_name: str) -> None:
        """Record a successful call — resets failure count, closes circuit.

        Args:
            model_name: LiteLLM model string for the call that succeeded.
        """
        provider = self._extract_provider(model_name)
        state = await self._get_or_create(provider)

        async with state.lock:
            state.failure_timestamps.clear()
            state.opened_at = 0.0
            if state.state != CircuitState.CLOSED:
                logger.info(
                    "GUARDIAN circuit breaker CLOSED for provider '%s'", provider
                )
            state.state = CircuitState.CLOSED

    async def record_failure(self, model_name: str) -> None:
        """Record a failed call — may open the circuit.

        Failures outside the rolling window are pruned before counting.

        Args:
            model_name: LiteLLM model string for the call that failed.
        """
        provider = self._extract_provider(model_name)
        state = await self._get_or_create(provider)

        async with state.lock:
            now = time.monotonic()
            state.failure_timestamps.append(now)
            self._prune_old_failures(state, now)

            failure_count = len(state.failure_timestamps)
            logger.debug(
                "GUARDIAN circuit breaker: %d failures for provider '%s'",
                failure_count,
                provider,
            )

            if (
                failure_count >= self.failure_threshold
                and state.state == CircuitState.CLOSED
            ):
                state.state = CircuitState.OPEN
                state.opened_at = now
                logger.warning(
                    "GUARDIAN circuit breaker OPENED for provider '%s' "
                    "after %d failures in %ds window",
                    provider,
                    failure_count,
                    self.window_seconds,
                )

    async def is_open(self, model_name: str) -> bool:
        """Return True if the circuit is open for this model's provider.

        Automatically transitions OPEN → HALF_OPEN after recovery_timeout_s.

        Args:
            model_name: LiteLLM model string to check.

        Returns:
            True if calls to this provider should be blocked.
        """
        provider = self._extract_provider(model_name)
        state = await self._get_or_create(provider)

        async with state.lock:
            if state.state == CircuitState.CLOSED:
                return False

            if state.state == CircuitState.OPEN:
                now = time.monotonic()
                if now - state.opened_at >= self.recovery_timeout_s:
                    state.state = CircuitState.HALF_OPEN
                    logger.info(
                        "GUARDIAN circuit breaker HALF_OPEN for provider '%s' "
                        "(recovery timeout elapsed)",
                        provider,
                    )
                    return True  # Still blocking — waiting for test call
                return True

            # HALF_OPEN — still blocking, waiting for success
            return True

    async def get_state(self, model_name: str) -> CircuitState:
        """Return the current circuit state for a model's provider.

        Args:
            model_name: LiteLLM model string.

        Returns:
            The current CircuitState for the provider.
        """
        provider = self._extract_provider(model_name)
        state = await self._get_or_create(provider)
        async with state.lock:
            return state.state

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_or_create(self, provider: str) -> _ProviderState:
        """Get or lazily create the state object for a provider."""
        async with self._global_lock:
            if provider not in self._providers:
                self._providers[provider] = _ProviderState()
            return self._providers[provider]

    def _prune_old_failures(self, state: _ProviderState, now: float) -> None:
        """Remove failure timestamps outside the rolling window."""
        cutoff = now - self.window_seconds
        while state.failure_timestamps and state.failure_timestamps[0] < cutoff:
            state.failure_timestamps.popleft()

    def _extract_provider(self, model_name: str) -> str:
        """Extract the provider name from a LiteLLM model string.

        Args:
            model_name: LiteLLM model string (e.g. "gpt-4o", "groq/llama3-70b-8192").

        Returns:
            Provider name string (e.g. "openai", "groq").
        """
        if not model_name:
            return "unknown"

        model_lower = model_name.lower()

        # Explicit prefix-based routing takes priority
        if "/" in model_lower:
            prefix = model_lower.split("/")[0]
            return prefix

        # Well-known model families without a prefix separator
        if model_lower.startswith("gpt-") or model_lower.startswith("o1") or model_lower.startswith("o3"):
            return "openai"
        if model_lower.startswith("claude-"):
            return "anthropic"
        if model_lower.startswith("gemini"):
            return "google"
        if model_lower.startswith("command"):
            return "cohere"

        # Unknown — use the full name as the provider key
        return model_lower


# Module-level singleton — all parts of GUARDIAN share this instance
circuit_breaker = CircuitBreaker()
