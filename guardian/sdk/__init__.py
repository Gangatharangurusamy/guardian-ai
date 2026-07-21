"""GUARDIAN SDK package.

Re-exports the core SDK components for convenient imports:
- TraceContext, ToolCallRecord, get_current_context, trace_context
- CapturedCall, truncate, estimate_tokens
- to_json (serializer)
- watch (decorator)
"""

from guardian.sdk.capture import CapturedCall, estimate_tokens, truncate
from guardian.sdk.context import (
    ToolCallRecord,
    TraceContext,
    get_current_context,
    trace_context,
)
from guardian.sdk.serializer import to_json

__all__ = [
    "CapturedCall",
    "TraceContext",
    "ToolCallRecord",
    "estimate_tokens",
    "get_current_context",
    "to_json",
    "trace_context",
    "truncate",
]
