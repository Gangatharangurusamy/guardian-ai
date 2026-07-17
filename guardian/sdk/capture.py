"""Capture utilities for GUARDIAN SDK.

Provides the CapturedCall dataclass for recording function call details,
a safe truncation helper, and a token estimation heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CapturedCall:
    """Records all details of a single captured function call.

    Attributes:
        function_name: Fully qualified name of the function that was called.
        args_preview: Truncated string representation of positional arguments.
        kwargs_preview: Truncated string representation of keyword arguments.
        start_time: UTC timestamp when execution began.
        end_time: UTC timestamp when execution completed (or failed).
        duration_ms: Wall-clock execution time in milliseconds.
        result_preview: Truncated string representation of the return value.
        exception_info: Dict with 'type' and 'message' if an exception occurred, None otherwise.
        retry_count: Number of retries observed for this call.
        estimated_tokens: Rough token count estimate for the call's text content.
    """

    function_name: str = ""
    args_preview: str = ""
    kwargs_preview: str = ""
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0
    result_preview: str = ""
    exception_info: dict[str, str] | None = None
    retry_count: int = 0
    estimated_tokens: int = 0


def truncate(value: object, max_len: int = 2000) -> str:
    """Safely convert any value to a truncated string representation.

    Attempts str() first, falls back to repr() if that fails, and ultimately
    returns a safe placeholder if nothing works. Guaranteed to never raise.

    Args:
        value: Any Python object to stringify.
        max_len: Maximum length of the returned string. Defaults to 2000.

    Returns:
        A string representation of the value, truncated with '...[truncated]'
        suffix if it exceeds max_len.
    """
    try:
        text = str(value)
    except Exception:
        try:
            text = repr(value)
        except Exception:
            return "<unrepresentable>"

    if len(text) > max_len:
        return text[:max_len] + "...[truncated]"
    return text


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses a simple heuristic of ~4 characters per token, which is a rough
    approximation for English text with most LLM tokenizers.

    Note:
        Phase 3+ may replace this with a real tokenizer (e.g. tiktoken)
        for accurate cost tracking per model.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count (always >= 0).
    """
    if not text:
        return 0
    return max(1, len(text) // 4)
