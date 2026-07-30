"""GUARDIAN SDK decorator — the primary public API.

Provides the ``watch`` decorator factory that wraps any agent function
(sync or async) to capture execution traces without changing behavior.

Usage::

    import guardian

    @guardian.watch("my_agent")
    def run_agent(query: str) -> str:
        return llm.call(query)

    # Or async:
    @guardian.watch("my_async_agent")
    async def run_agent_async(query: str) -> str:
        return await llm.acall(query)
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from guardian.sdk.capture import CapturedCall, estimate_tokens, truncate
from guardian.sdk.context import TraceContext, get_current_context, trace_context
from guardian.sdk.serializer import to_json

logger = logging.getLogger("guardian")

# Global default callback, set by guardian.init() to wire traces to the store.
# When None, traces are only logged.
_default_on_trace: Callable[[dict[str, Any]], Any] | None = None


def _set_default_on_trace(callback: Callable[[dict[str, Any]], Any] | None) -> None:
    """Set the global default on_trace callback.

    Called by ``guardian.init()`` to wire the decorator output to the trace
    store. Not part of the public API.

    Args:
        callback: A callable that receives a TraceEvent dict, or None to
            revert to logging-only behavior.
    """
    global _default_on_trace
    _default_on_trace = callback


def _log_trace(trace_event: dict[str, Any]) -> None:
    """Default trace handler: log the trace event as JSON.

    Args:
        trace_event: The serialized TraceEvent dict to log.
    """
    try:
        logger.info("GUARDIAN trace: %s", json.dumps(trace_event, default=str))
    except Exception as exc:
        logger.warning("Failed to log trace event: %s", exc)


def _build_captured_call(
    func_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    start_time: datetime,
    start_perf: float,
    result: Any = None,
    exception: BaseException | None = None,
) -> CapturedCall:
    """Build a CapturedCall from function execution data.

    Args:
        func_name: Name of the executed function.
        args: Positional arguments passed to the function.
        kwargs: Keyword arguments passed to the function.
        start_time: UTC datetime when execution began.
        start_perf: perf_counter value when execution began.
        result: Return value of the function (if successful).
        exception: Exception raised by the function (if any).

    Returns:
        A fully populated CapturedCall instance.
    """
    end_time = datetime.now(timezone.utc)
    duration_ms = (time.perf_counter() - start_perf) * 1000

    args_preview = truncate(args)
    kwargs_preview = truncate(kwargs)
    result_preview = truncate(result) if exception is None else ""

    exception_info: dict[str, str] | None = None
    if exception is not None:
        exception_info = {
            "type": type(exception).__name__,
            "message": truncate(str(exception), max_len=1000),
        }

    # Estimate tokens from all text content
    all_text = f"{args_preview} {kwargs_preview} {result_preview}"
    tokens = estimate_tokens(all_text)

    return CapturedCall(
        function_name=func_name,
        args_preview=args_preview,
        kwargs_preview=kwargs_preview,
        start_time=start_time,
        end_time=end_time,
        duration_ms=round(duration_ms, 2),
        result_preview=result_preview,
        exception_info=exception_info,
        retry_count=0,
        estimated_tokens=tokens,
    )


def _emit_trace(
    ctx: TraceContext,
    captured: CapturedCall,
    is_root: bool,
    on_trace: Callable[[dict[str, Any]], Any] | None,
) -> None:
    """Record a captured call and optionally emit the full trace.

    Appends the captured call directly to the context calls.
    If this is the root (outermost) watched call, serializes the full
    trace and dispatches it to the on_trace callback.

    Args:
        ctx: The active TraceContext.
        captured: The CapturedCall for this invocation.
        is_root: Whether this is the outermost watched function.
        on_trace: Optional callback override; if None, uses the global default.
    """
    # Append tool call record directly to context
    ctx.calls.append(captured)

    # Only the root call emits the full trace
    if is_root:
        ctx.ended_at = captured.end_time
        if captured.exception_info is not None:
            ctx.status = "error"

        trace_event = to_json(ctx)
        callback = on_trace or _default_on_trace or _log_trace
        try:
            callback(trace_event)
        except Exception as exc:
            logger.warning("on_trace callback failed: %s", exc)


def watch(
    agent_name: str | None = None,
    policy: str | None = None,
    on_trace: Callable[[dict[str, Any]], Any] | None = None,
    recovery_policy: str | None = None,
) -> Callable[..., Any]:
    """Decorator factory that instruments an agent function for tracing.

    Wraps both sync and async functions transparently. Captures inputs,
    outputs, timing, and exceptions without changing the wrapped function's
    behavior or return value.

    Args:
        agent_name: Human-readable name for the agent. Defaults to the
            function name if not provided.
        policy: Path to an ethics policy YAML file (Phase 2).
            Stored in trace metadata and processed by the Ethics Engine.
        on_trace: Optional callback ``(trace_event: dict) -> None`` called
            with the serialized trace when the outermost watched function
            completes. If None, uses the global callback set by
            ``guardian.init()``, or falls back to logging.
        recovery_policy: Path to a recovery policy YAML file (Phase 3).
            Enables automatic failure detection, LLM diagnosis, and
            recovery actions after the agent run completes.
            If None, watchdog and recovery are completely skipped.

    Returns:
        A decorator that wraps the target function with tracing.

    Example::

        @watch("my_agent", recovery_policy="config/recovery-policy.yaml")
        async def agent_fn(prompt: str) -> str:
            return "response"
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        resolved_name = agent_name or func.__name__

        # Cache recovery policy at decoration time (Q4) — load once, not per call.
        _recovery_policy = None
        if recovery_policy is not None:
            try:
                from guardian.recovery.policy import RecoveryPolicy as _RecPol
                _recovery_policy = _RecPol.load(recovery_policy)
                if _recovery_policy.diagnosis.enabled:
                    _validate_api_key_for_model(_recovery_policy.diagnosis.model)
            except Exception as _rp_exc:
                logger.warning(
                    "GUARDIAN: Failed to load recovery policy '%s': %s",
                    recovery_policy,
                    _rp_exc,
                )

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                parent = get_current_context()
                is_root = parent is None

                metadata: dict[str, Any] = {}
                if policy is not None:
                    metadata["policy"] = policy

                start_time = datetime.now(timezone.utc)
                start_perf = time.perf_counter()
                exception: BaseException | None = None
                result: Any = None

                if is_root:
                    with trace_context(resolved_name, metadata) as ctx:
                        try:
                            result = await func(*args, **kwargs)
                            return result
                        except BaseException as exc:
                            exception = exc
                            raise
                        finally:
                            captured = _build_captured_call(
                                func_name=resolved_name,
                                args=args,
                                kwargs=kwargs,
                                start_time=start_time,
                                start_perf=start_perf,
                                result=result,
                                exception=exception,
                            )
                            _emit_trace(ctx, captured, is_root, on_trace)
                            if policy is not None:
                                trace_event = to_json(ctx)
                                from guardian.ethics.engine import EthicsEngine
                                from guardian.ethics.exceptions import EthicsBlockException
                                from guardian.ethics.policy import EthicsPolicy
                                try:
                                    eth_policy = EthicsPolicy.load(policy)
                                    engine = EthicsEngine(eth_policy)
                                    await engine.evaluate(trace_event)
                                except EthicsBlockException:
                                    raise
                                except Exception as eth_exc:
                                    logger.warning("Ethics evaluation failed: %s", eth_exc, exc_info=True)
                            # Watchdog + Recovery (Phase 3)
                            if _recovery_policy is not None:
                                # Set raw_input for full first-arg capture (Correction 3)
                                if args:
                                    ctx.raw_input = args[0]
                                _trace_event = to_json(ctx)
                                if _should_run_watchdog(_trace_event, _recovery_policy):
                                    try:
                                        from guardian.recovery.engine import RecoveryEngine
                                        from guardian.watchdog import Diagnoser, FailureDetector
                                        _detector = FailureDetector(
                                            loop_threshold=_recovery_policy.failure_detection.loop_threshold,
                                            timeout_ms=_recovery_policy.failure_detection.timeout_ms,
                                            error_repeat_threshold=_recovery_policy.failure_detection.error_repeat_threshold,
                                        )
                                        _signals = _detector.detect(_trace_event)
                                        if _signals:
                                            _diagnoser = Diagnoser(
                                                model_name=_recovery_policy.diagnosis.model,
                                                max_trace_chars=_recovery_policy.diagnosis.max_trace_chars,
                                            )
                                            _diagnosis = await _diagnoser.diagnose(_trace_event, _signals)
                                            _rec_engine = RecoveryEngine(policy=_recovery_policy)
                                            await _rec_engine.recover(
                                                diagnosis=_diagnosis,
                                                original_fn=func,
                                                original_args=args,
                                                original_kwargs=kwargs,
                                            )
                                    except Exception as _wd_exc:
                                        logger.warning("GUARDIAN watchdog/recovery error: %s", _wd_exc)
                else:
                    ctx = parent
                    try:
                        result = await func(*args, **kwargs)
                        return result
                    except BaseException as exc:
                        exception = exc
                        raise
                    finally:
                        captured = _build_captured_call(
                            func_name=resolved_name,
                            args=args,
                            kwargs=kwargs,
                            start_time=start_time,
                            start_perf=start_perf,
                            result=result,
                            exception=exception,
                        )
                        _emit_trace(ctx, captured, is_root, on_trace)

            return async_wrapper

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                parent = get_current_context()
                is_root = parent is None

                metadata: dict[str, Any] = {}
                if policy is not None:
                    metadata["policy"] = policy

                start_time = datetime.now(timezone.utc)
                start_perf = time.perf_counter()
                exception: BaseException | None = None
                result: Any = None

                if is_root:
                    with trace_context(resolved_name, metadata) as ctx:
                        try:
                            result = func(*args, **kwargs)
                            return result
                        except BaseException as exc:
                            exception = exc
                            raise
                        finally:
                            captured = _build_captured_call(
                                func_name=resolved_name,
                                args=args,
                                kwargs=kwargs,
                                start_time=start_time,
                                start_perf=start_perf,
                                result=result,
                                exception=exception,
                            )
                            _emit_trace(ctx, captured, is_root, on_trace)
                            if policy is not None:
                                trace_event = to_json(ctx)
                                from guardian.ethics.engine import EthicsEngine
                                from guardian.ethics.exceptions import EthicsBlockException
                                from guardian.ethics.policy import EthicsPolicy
                                try:
                                    eth_policy = EthicsPolicy.load(policy)
                                    engine = EthicsEngine(eth_policy)
                                    engine.evaluate_sync(trace_event)
                                except EthicsBlockException:
                                    raise
                                except Exception as eth_exc:
                                    logger.warning("Ethics evaluation failed: %s", eth_exc, exc_info=True)
                            # Watchdog + Recovery (Phase 3)
                            if _recovery_policy is not None:
                                # Set raw_input for full first-arg capture (Correction 3)
                                if args:
                                    ctx.raw_input = args[0]
                                _trace_event = to_json(ctx)
                                if _should_run_watchdog(_trace_event, _recovery_policy):
                                    try:
                                        from guardian.recovery.engine import RecoveryEngine
                                        from guardian.watchdog import Diagnoser, FailureDetector
                                        _detector = FailureDetector(
                                            loop_threshold=_recovery_policy.failure_detection.loop_threshold,
                                            timeout_ms=_recovery_policy.failure_detection.timeout_ms,
                                            error_repeat_threshold=_recovery_policy.failure_detection.error_repeat_threshold,
                                        )
                                        _signals = _detector.detect(_trace_event)
                                        if _signals:
                                            _diagnoser = Diagnoser(
                                                model_name=_recovery_policy.diagnosis.model,
                                                max_trace_chars=_recovery_policy.diagnosis.max_trace_chars,
                                            )
                                            _diagnosis = _diagnoser.diagnose_sync(_trace_event, _signals)
                                            _rec_engine = RecoveryEngine(policy=_recovery_policy)
                                            _rec_engine.recover_sync(
                                                diagnosis=_diagnosis,
                                                original_fn=func,
                                                original_args=args,
                                                original_kwargs=kwargs,
                                            )
                                    except Exception as _wd_exc:
                                        logger.warning("GUARDIAN watchdog/recovery error: %s", _wd_exc)
                else:
                    ctx = parent
                    try:
                        result = func(*args, **kwargs)
                        return result
                    except BaseException as exc:
                        exception = exc
                        raise
                    finally:
                        captured = _build_captured_call(
                            func_name=resolved_name,
                            args=args,
                            kwargs=kwargs,
                            start_time=start_time,
                            start_perf=start_perf,
                            result=result,
                            exception=exception,
                        )
                        _emit_trace(ctx, captured, is_root, on_trace)


            return sync_wrapper

    return decorator


def _should_run_watchdog(trace_event: dict[str, Any], policy: Any) -> bool:
    """Return True if watchdog analysis should run for this trace.

    Runs only when:
    - The trace status is not 'success' (something went wrong), OR
    - At least one call has retry_count > 0 (retries occurred), OR
    - Any function is called loop_threshold+ times (tool loop in a successful run)

    If diagnosis is disabled in the policy, always returns False.

    Args:
        trace_event: Serialized trace event dict.
        policy: Loaded RecoveryPolicy instance.

    Returns:
        True if watchdog should run.
    """
    if not getattr(getattr(policy, 'diagnosis', None), 'enabled', True):
        return False
    if trace_event.get("status") != "success":
        return True
    calls = trace_event.get("calls", [])
    if isinstance(calls, list):
        if any(c.get("retry_count", 0) > 0 for c in calls if isinstance(c, dict)):
            return True
        # Also trigger watchdog for repeated identical tool calls in a "successful" run.
        # A tool loop can complete without raising an exception — this catches that blind spot.
        from collections import Counter
        loop_threshold = getattr(getattr(policy, 'failure_detection', None), 'loop_threshold', 3)
        func_counts = Counter(
            c.get("function", "") for c in calls
            if isinstance(c, dict) and c.get("function", "")
        )
        return any(count >= loop_threshold for count in func_counts.values())
    return False


def _validate_api_key_for_model(model_name: str) -> None:
    """Validate that the required API key for the configured model exists in env.

    If missing, logs a warning immediately at decoration/startup time.
    """
    import os
    if not model_name:
        return

    model_lower = model_name.lower()
    provider = ""

    if "/" in model_lower:
        provider = model_lower.split("/")[0]
    elif model_lower.startswith("gpt-") or model_lower.startswith("o1") or model_lower.startswith("o3"):
        provider = "openai"
    elif model_lower.startswith("claude-"):
        provider = "anthropic"
    elif model_lower.startswith("gemini"):
        provider = "google"
    elif model_lower.startswith("command"):
        provider = "cohere"
    else:
        provider = model_lower

    # Map provider to expected environment variables
    provider_to_env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "groq": "GROQ_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "cohere": "COHERE_API_KEY",
        "azure": "AZURE_API_KEY",
        "google": "GEMINI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "huggingface": "HUGGINGFACE_API_KEY",
    }

    env_var = provider_to_env.get(provider)
    if env_var:
        if not os.environ.get(env_var):
            import logging
            logger = logging.getLogger("guardian")
            logger.warning(
                "GUARDIAN: The recovery policy configured model '%s' requires the environment variable '%s', "
                "but it is not set in your environment.",
                model_name,
                env_var,
            )
    else:
        # Generic fallback: check if any API key is set
        from guardian.watchdog.diagnoser import _has_api_key
        if not _has_api_key():
            import logging
            logger = logging.getLogger("guardian")
            logger.warning(
                "GUARDIAN: The recovery policy configured model '%s' has an unknown provider '%s'. "
                "No generic LiteLLM API keys were found in your environment.",
                model_name,
                provider,
            )
