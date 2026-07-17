"""GUARDIAN Ethics Engine package.

Exposes the EthicsEngine, policy loader, violation models, and exceptions.
"""

from __future__ import annotations

from guardian.ethics.engine import EthicsEngine
from guardian.ethics.exceptions import EthicsBlockException
from guardian.ethics.policy import EthicsPolicy
from guardian.ethics.violations import EthicsViolation, Severity, ViolationType
from guardian.ethics.category_classifier import SensitiveDomain

__all__ = [
    "EthicsEngine",
    "EthicsBlockException",
    "EthicsPolicy",
    "EthicsViolation",
    "Severity",
    "ViolationType",
    "SensitiveDomain",
]
