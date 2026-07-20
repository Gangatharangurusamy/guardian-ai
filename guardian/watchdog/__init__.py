"""GUARDIAN Watchdog module — Failure Detector + Diagnosis Agent.

Public API for Phase 3 watchdog functionality.
"""

from .detector import FailureDetector
from .diagnoser import Diagnoser
from .models import Diagnosis, FailureSignal, FailureType

__all__ = [
    "FailureDetector",
    "Diagnoser",
    "FailureSignal",
    "FailureType",
    "Diagnosis",
]
