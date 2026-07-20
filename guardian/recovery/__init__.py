"""GUARDIAN Recovery module — Recovery Engine + Circuit Breaker + Approval Gate.

Public API for Phase 3 recovery functionality.
"""

from .engine import RecoveryEngine, RecoveryOutcome
from .policy import RecoveryPolicy
from .circuit_breaker import CircuitBreaker, CircuitState
from .approval import ApprovalGate, ApprovalResult
from .actions import action_retry, action_switch_model, action_pause, action_escalate

__all__ = [
    "RecoveryEngine",
    "RecoveryOutcome",
    "RecoveryPolicy",
    "CircuitBreaker",
    "CircuitState",
    "ApprovalGate",
    "ApprovalResult",
    "action_retry",
    "action_switch_model",
    "action_pause",
    "action_escalate",
]
