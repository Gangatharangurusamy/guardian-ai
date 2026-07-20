"""GUARDIAN Recovery Policy — loads and validates recovery-policy.yaml.

Provides a typed, immutable configuration object for the Recovery Engine.
All fields have sensible defaults so the policy can be used in tests
without a YAML file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger("guardian")

# Valid values for string-enum fields
_VALID_MODES = {"human_in_loop", "notify_then_retry", "auto_retry"}
_VALID_ACTIONS = {"retry", "switch_model", "pause", "escalate", "log_only"}
_VALID_METHODS = {"cli", "webhook"}


@dataclass
class DiagnosisConfig:
    """Configuration for the LLM-powered Diagnosis Agent."""

    enabled: bool = True
    model: str = "gpt-4o"
    max_trace_chars: int = 8000
    cross_session_lookback: int = 5


@dataclass
class FailureDetectionConfig:
    """Thresholds for the pure-Python FailureDetector."""

    loop_threshold: int = 3
    timeout_ms: int = 30_000
    error_repeat_threshold: int = 2


@dataclass
class RecoveryConfig:
    """Configuration for the RecoveryEngine action selection."""

    mode: str = "human_in_loop"
    max_retries: int = 2
    retry_delay_ms: int = 1000
    actions: dict[str, str] = field(
        default_factory=lambda: {
            "tool_loop": "switch_model",
            "timeout": "retry",
            "repeated_error": "escalate",
            "schema_mismatch": "retry",
            "ethics_block": "escalate",
            "confidence_drop": "switch_model",
            "unknown": "escalate",
        }
    )
    fallback_model: str = "claude-3-5-sonnet-20241022"


@dataclass
class CircuitBreakerConfig:
    """Configuration for the per-provider circuit breaker."""

    enabled: bool = True
    failure_threshold: int = 5
    window_seconds: int = 60
    recovery_timeout_s: int = 120


@dataclass
class ApprovalConfig:
    """Configuration for the human-in-the-loop approval gate."""

    method: str = "cli"
    timeout_seconds: int = 300
    approval_webhook_url: str = ""


@dataclass
class RecoveryPolicy:
    """Typed configuration object for Phase 3 recovery behaviour.

    Load from a YAML file or construct programmatically for tests.

    Args:
        diagnosis: Diagnosis Agent configuration.
        failure_detection: FailureDetector thresholds.
        recovery: Recovery mode and action mapping.
        circuit_breaker: Circuit breaker settings.
        approval: Approval gate settings.
        version: Policy schema version string.
    """

    diagnosis: DiagnosisConfig = field(default_factory=DiagnosisConfig)
    failure_detection: FailureDetectionConfig = field(
        default_factory=FailureDetectionConfig
    )
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    circuit_breaker: CircuitBreakerConfig = field(
        default_factory=CircuitBreakerConfig
    )
    approval: ApprovalConfig = field(default_factory=ApprovalConfig)
    version: str = "1.0"

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str) -> "RecoveryPolicy":
        """Load and validate a RecoveryPolicy from a YAML file.

        Args:
            path: Filesystem path to the YAML file.

        Returns:
            A validated RecoveryPolicy instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            ValueError: If the YAML contains invalid field values.
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RecoveryPolicy":
        """Construct a RecoveryPolicy from a plain dict (e.g. parsed YAML).

        Useful for tests — no file I/O required.

        Args:
            d: Dict matching the recovery-policy.yaml schema.

        Returns:
            A validated RecoveryPolicy instance.

        Raises:
            ValueError: If the dict contains invalid field values.
        """
        version = str(d.get("version", "1.0"))

        # --- Diagnosis ---
        diag_raw = d.get("diagnosis", {}) or {}
        diagnosis = DiagnosisConfig(
            enabled=bool(diag_raw.get("enabled", True)),
            model=str(diag_raw.get("model", "gpt-4o")),
            max_trace_chars=int(diag_raw.get("max_trace_chars", 8000)),
            cross_session_lookback=int(diag_raw.get("cross_session_lookback", 5)),
        )

        # --- Failure Detection ---
        fd_raw = d.get("failure_detection", {}) or {}
        failure_detection = FailureDetectionConfig(
            loop_threshold=int(fd_raw.get("loop_threshold", 3)),
            timeout_ms=int(fd_raw.get("timeout_ms", 30_000)),
            error_repeat_threshold=int(fd_raw.get("error_repeat_threshold", 2)),
        )

        # --- Recovery ---
        rec_raw = d.get("recovery", {}) or {}
        mode = str(rec_raw.get("mode", "human_in_loop"))
        if mode not in _VALID_MODES:
            raise ValueError(
                f"Invalid recovery mode '{mode}'. "
                f"Must be one of: {sorted(_VALID_MODES)}"
            )

        raw_actions = rec_raw.get("actions", {}) or {}
        actions: dict[str, str] = {}
        for ft, action in raw_actions.items():
            action_str = str(action)
            if action_str not in _VALID_ACTIONS:
                raise ValueError(
                    f"Invalid recovery action '{action_str}' for failure type '{ft}'. "
                    f"Must be one of: {sorted(_VALID_ACTIONS)}"
                )
            actions[str(ft)] = action_str

        # Merge with defaults for any missing failure types
        default_actions = RecoveryConfig().actions
        for ft, action in default_actions.items():
            actions.setdefault(ft, action)

        recovery = RecoveryConfig(
            mode=mode,
            max_retries=int(rec_raw.get("max_retries", 2)),
            retry_delay_ms=int(rec_raw.get("retry_delay_ms", 1000)),
            actions=actions,
            fallback_model=str(rec_raw.get("fallback_model", "claude-3-5-sonnet-20241022")),
        )

        # --- Circuit Breaker ---
        cb_raw = d.get("circuit_breaker", {}) or {}
        circuit_breaker = CircuitBreakerConfig(
            enabled=bool(cb_raw.get("enabled", True)),
            failure_threshold=int(cb_raw.get("failure_threshold", 5)),
            window_seconds=int(cb_raw.get("window_seconds", 60)),
            recovery_timeout_s=int(cb_raw.get("recovery_timeout_s", 120)),
        )

        # --- Approval ---
        appr_raw = d.get("approval", {}) or {}
        method = str(appr_raw.get("method", "cli"))
        if method not in _VALID_METHODS:
            raise ValueError(
                f"Invalid approval method '{method}'. "
                f"Must be one of: {sorted(_VALID_METHODS)}"
            )

        approval = ApprovalConfig(
            method=method,
            timeout_seconds=int(appr_raw.get("timeout_seconds", 300)),
            approval_webhook_url=str(appr_raw.get("approval_webhook_url", "")),
        )

        return cls(
            diagnosis=diagnosis,
            failure_detection=failure_detection,
            recovery=recovery,
            circuit_breaker=circuit_breaker,
            approval=approval,
            version=version,
        )

    @classmethod
    def default(cls) -> "RecoveryPolicy":
        """Return a RecoveryPolicy with sensible defaults. No file needed.

        Useful when testing individual components without a config file.

        Returns:
            A default RecoveryPolicy instance.
        """
        return cls()
