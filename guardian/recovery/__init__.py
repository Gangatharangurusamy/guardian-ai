"""GUARDIAN Recovery module — Recovery Engine + Circuit Breaker + Approval Gate.

Public API for Phase 3 recovery functionality.
"""

from .actions import action_escalate, action_pause, action_retry, action_switch_model
from .approval import ApprovalGate, ApprovalResult
from .circuit_breaker import CircuitBreaker, CircuitState
from .engine import RecoveryEngine, RecoveryOutcome
from .policy import RecoveryPolicy

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
