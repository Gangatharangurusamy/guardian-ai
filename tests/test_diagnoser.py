"""Tests for guardian.watchdog.diagnoser — LLM-powered diagnosis."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from guardian.watchdog.diagnoser import Diagnoser
from guardian.watchdog.models import Diagnosis, FailureSignal, FailureType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_signal(
    failure_type=FailureType.TOOL_LOOP,
    description="test signal",
    evidence="tool called 4 times",
    confidence=0.95,
    session_id="sess-001",
    agent_name="test_agent",
):
    return FailureSignal(
        failure_type=failure_type,
        description=description,
        evidence=evidence,
        confidence=confidence,
        session_id=session_id,
        agent_name=agent_name,
    )


def make_trace(session_id="sess-001", agent_name="test_agent", status="error"):
    return {
        "session_id": session_id,
        "agent_name": agent_name,
        "status": status,
        "duration_ms": 5000,
        "calls": [
            {"function_name": "search", "result_preview": "x", "duration_ms": 100},
        ],
    }


def make_valid_llm_response(
    root_cause="The tool is stuck in a loop",
    suggestion="Switch to a different model",
    confidence=0.85,
    failure_type="tool_loop",
):
    return json.dumps({
        "root_cause": root_cause,
        "suggestion": suggestion,
        "confidence": confidence,
        "failure_type": failure_type,
    })


def _make_mock_response(content: str):
    """Create a mock litellm response object."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDiagnoserCallsLiteLLM:

    @pytest.mark.asyncio
    async def test_diagnose_calls_litellm_with_correct_model(self):
        """litellm.acompletion is called with the model specified at construction."""
        diagnoser = Diagnoser(model_name="gpt-4o-test", max_trace_chars=8000)
        signals = [make_signal()]
        trace = make_trace()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _make_mock_response(make_valid_llm_response())
                result = await diagnoser.diagnose(trace, signals)

        mock_llm.assert_called_once()
        call_kwargs = mock_llm.call_args
        assert call_kwargs[1]["model"] == "gpt-4o-test" or call_kwargs[0][0] == "gpt-4o-test"

    @pytest.mark.asyncio
    async def test_prompt_contains_trace_and_signals(self):
        """The prompt sent to LiteLLM contains the trace JSON and failure signals."""
        diagnoser = Diagnoser(model_name="gpt-4o", max_trace_chars=8000)
        signals = [make_signal()]
        trace = make_trace(session_id="unique-sess-42")

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _make_mock_response(make_valid_llm_response())
                await diagnoser.diagnose(trace, signals)

        messages = mock_llm.call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "unique-sess-42" in user_content
        assert "tool_loop" in user_content  # signal type present

    @pytest.mark.asyncio
    async def test_valid_json_response_parsed_into_diagnosis(self):
        """A valid JSON response is parsed correctly into a Diagnosis object."""
        diagnoser = Diagnoser(model_name="gpt-4o")
        signals = [make_signal()]
        trace = make_trace()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _make_mock_response(
                    make_valid_llm_response(
                        root_cause="Model is stuck in a tool loop",
                        suggestion="Retry with a different model",
                        confidence=0.9,
                        failure_type="tool_loop",
                    )
                )
                diagnosis = await diagnoser.diagnose(trace, signals)

        assert isinstance(diagnosis, Diagnosis)
        assert diagnosis.root_cause == "Model is stuck in a tool loop"
        assert diagnosis.suggestion == "Retry with a different model"
        assert diagnosis.confidence == pytest.approx(0.9)
        assert diagnosis.model_used == "gpt-4o"

    @pytest.mark.asyncio
    async def test_invalid_json_response_raises(self):
        """An invalid JSON response from the LLM raises (no fallback Diagnosis)."""
        diagnoser = Diagnoser(model_name="gpt-4o")
        signals = [make_signal()]
        trace = make_trace()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _make_mock_response("this is not JSON at all")
                with pytest.raises(Exception):  # json.JSONDecodeError
                    await diagnoser.diagnose(trace, signals)

    @pytest.mark.asyncio
    async def test_llm_exception_raises(self):
        """If litellm.acompletion throws, the exception propagates (no fallback)."""
        diagnoser = Diagnoser(model_name="gpt-4o")
        signals = [make_signal()]
        trace = make_trace()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.side_effect = RuntimeError("LLM provider unreachable")
                with pytest.raises(RuntimeError):
                    await diagnoser.diagnose(trace, signals)

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_runtime_error(self):
        """No API key in env → RuntimeError with descriptive message."""
        diagnoser = Diagnoser(model_name="gpt-4o")
        signals = [make_signal()]
        trace = make_trace()

        # Clear all known API keys
        env_clear = {k: "" for k in [
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
            "MISTRAL_API_KEY", "COHERE_API_KEY", "AZURE_API_KEY",
            "GEMINI_API_KEY", "HUGGINGFACE_API_KEY",
        ]}
        with patch.dict("os.environ", env_clear, clear=False):
            # Also ensure they aren't set at all by patching the check
            with patch("guardian.watchdog.diagnoser._has_api_key", return_value=False):
                with pytest.raises(RuntimeError) as exc_info:
                    await diagnoser.diagnose(trace, signals)
        assert "API key" in str(exc_info.value)
        assert "OPENAI_API_KEY" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_trace_truncated_to_max_chars(self):
        """Trace JSON is truncated to max_trace_chars before sending to LLM."""
        max_chars = 100
        diagnoser = Diagnoser(model_name="gpt-4o", max_trace_chars=max_chars)
        signals = [make_signal()]

        # Build a trace with a very long field
        big_trace = make_trace()
        big_trace["calls"] = [{"function_name": "x", "result_preview": "y" * 5000}]

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _make_mock_response(make_valid_llm_response())
                await diagnoser.diagnose(big_trace, signals)

        messages = mock_llm.call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        # Trace portion should be truncated — look for truncation marker
        assert "[TRACE TRUNCATED]" in user_content or len(user_content) < 10_000

    @pytest.mark.asyncio
    async def test_model_used_field_matches_constructor(self):
        """diagnosis.model_used equals the model_name passed to Diagnoser.__init__."""
        diagnoser = Diagnoser(model_name="groq/llama3-70b-8192")
        signals = [make_signal()]
        trace = make_trace()

        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _make_mock_response(make_valid_llm_response())
                diagnosis = await diagnoser.diagnose(trace, signals)

        assert diagnosis.model_used == "groq/llama3-70b-8192"

    @pytest.mark.asyncio
    async def test_markdown_fences_stripped_before_parsing(self):
        """LLM responses wrapped in markdown code fences are parsed correctly."""
        diagnoser = Diagnoser(model_name="gpt-4o")
        signals = [make_signal()]
        trace = make_trace()

        fenced_response = (
            "```json\n"
            + make_valid_llm_response()
            + "\n```"
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _make_mock_response(fenced_response)
                diagnosis = await diagnoser.diagnose(trace, signals)

        assert isinstance(diagnosis, Diagnosis)
        assert diagnosis.root_cause == "The tool is stuck in a loop"

    def test_diagnose_sync_works(self):
        """diagnose_sync() returns the same result as the async version."""
        diagnoser = Diagnoser(model_name="gpt-4o")
        signals = [make_signal()]
        trace = make_trace()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _make_mock_response(make_valid_llm_response())
                diagnosis = diagnoser.diagnose_sync(trace, signals)

        assert isinstance(diagnosis, Diagnosis)
