"""Tests for guardian.recovery.circuit_breaker."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from guardian.recovery.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreakerInitialState:

    @pytest.mark.asyncio
    async def test_starts_closed(self):
        """A fresh circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker()
        state = await cb.get_state("gpt-4o")
        assert state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_is_open_returns_false_when_closed(self):
        """is_open() returns False when circuit is closed."""
        cb = CircuitBreaker()
        assert await cb.is_open("gpt-4o") is False


class TestCircuitBreakerOpening:

    @pytest.mark.asyncio
    async def test_opens_after_failure_threshold(self):
        """Circuit opens after recording failure_threshold failures."""
        cb = CircuitBreaker(failure_threshold=3, window_seconds=60)
        for _ in range(3):
            await cb.record_failure("gpt-4o")

        state = await cb.get_state("gpt-4o")
        assert state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_is_open_returns_true_when_open(self):
        """is_open() returns True when circuit is open."""
        cb = CircuitBreaker(failure_threshold=2)
        await cb.record_failure("gpt-4o")
        await cb.record_failure("gpt-4o")

        assert await cb.is_open("gpt-4o") is True

    @pytest.mark.asyncio
    async def test_does_not_open_below_threshold(self):
        """Circuit stays closed when failures < threshold."""
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            await cb.record_failure("claude-3-5-sonnet-20241022")

        state = await cb.get_state("claude-3-5-sonnet-20241022")
        assert state == CircuitState.CLOSED


class TestCircuitBreakerRecovery:

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self):
        """OPEN → HALF_OPEN after recovery_timeout_s elapses."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=1)
        await cb.record_failure("gpt-4o")
        assert await cb.get_state("gpt-4o") == CircuitState.OPEN

        # Simulate time passing by patching time.monotonic
        original_time = time.monotonic()
        with patch("guardian.recovery.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = original_time + 5  # 5 seconds later
            state = await cb.get_state("gpt-4o")
            # is_open() triggers the transition
            await cb.is_open("gpt-4o")

        state = await cb.get_state("gpt-4o")
        assert state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_success_closes_circuit(self):
        """Recording a success resets state to CLOSED."""
        cb = CircuitBreaker(failure_threshold=2)
        await cb.record_failure("gpt-4o")
        await cb.record_failure("gpt-4o")
        assert await cb.get_state("gpt-4o") == CircuitState.OPEN

        await cb.record_success("gpt-4o")
        assert await cb.get_state("gpt-4o") == CircuitState.CLOSED
        assert await cb.is_open("gpt-4o") is False


class TestProviderExtraction:

    def test_gpt_model_extracts_openai(self):
        cb = CircuitBreaker()
        assert cb._extract_provider("gpt-4o") == "openai"
        assert cb._extract_provider("gpt-4o-mini") == "openai"
        assert cb._extract_provider("gpt-4-turbo") == "openai"

    def test_claude_model_extracts_anthropic(self):
        cb = CircuitBreaker()
        assert cb._extract_provider("claude-3-5-sonnet-20241022") == "anthropic"
        assert cb._extract_provider("claude-3-haiku-20240307") == "anthropic"

    def test_groq_prefix_extracts_groq(self):
        cb = CircuitBreaker()
        assert cb._extract_provider("groq/llama3-70b-8192") == "groq"
        assert cb._extract_provider("groq/mixtral-8x7b-32768") == "groq"

    def test_mistral_prefix_extracts_mistral(self):
        cb = CircuitBreaker()
        assert cb._extract_provider("mistral/mistral-large") == "mistral"

    def test_custom_prefix_uses_prefix(self):
        cb = CircuitBreaker()
        assert cb._extract_provider("bedrock/claude-3-sonnet") == "bedrock"


class TestCircuitBreakerIsolation:

    @pytest.mark.asyncio
    async def test_multiple_providers_are_isolated(self):
        """Failures on one provider do not affect another provider's circuit."""
        cb = CircuitBreaker(failure_threshold=2)
        # Fail openai
        await cb.record_failure("gpt-4o")
        await cb.record_failure("gpt-4o")

        # anthropic should still be closed
        state_anthropic = await cb.get_state("claude-3-5-sonnet-20241022")
        assert state_anthropic == CircuitState.CLOSED

        # openai should be open
        state_openai = await cb.get_state("gpt-4o")
        assert state_openai == CircuitState.OPEN
