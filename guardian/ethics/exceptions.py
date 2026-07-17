"""Ethics-specific exceptions for GUARDIAN.

Defines exceptions raised by the Ethics Engine when a policy dictates
that agent execution must be halted (BLOCK severity).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from guardian.ethics.violations import EthicsViolation


class EthicsBlockException(Exception):
    """Raised when a BLOCK-severity violation is detected and halt_on_block is True.

    Contains the list of violations that triggered the block so callers
    can inspect exactly what was flagged.

    Attributes:
        violations: The list of EthicsViolation objects that caused the block.

    Example::

        try:
            result = my_agent("sensitive input")
        except EthicsBlockException as exc:
            for v in exc.violations:
                print(f"{v.violation_type}: {v.description}")
    """

    def __init__(
        self,
        violations: list[EthicsViolation],
        message: str = "Ethics policy blocked agent execution",
    ) -> None:
        self.violations = violations
        super().__init__(message)
