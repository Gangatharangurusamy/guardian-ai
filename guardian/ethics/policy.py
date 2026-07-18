"""Ethics policy loader and validator for GUARDIAN.

Loads the user's ethics policy from a YAML file and provides a typed
configuration object that all other ethics modules read from.

Depends on ``pyyaml`` (hard dependency added in Phase 2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("guardian.ethics")

# ---------------------------------------------------------------------------
# Valid values for validation
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = frozenset({"log", "warn", "block"})

_VALID_TOP_LEVEL_KEYS = frozenset({
    "version", "pii", "bias", "sensitive_domains", "blocking",
})

_VALID_PII_SEVERITY_KEYS = frozenset({
    "default", "ssn", "credit_card", "api_key", "email", "phone",
    "ip_address", "passport", "uk_ni_number", "private_key",
    "aws_key", "jwt", "person_name", "identifying_combination",
})


# ---------------------------------------------------------------------------
# Policy sub-config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PIIPolicy:
    """Configuration for PII detection.

    Attributes:
        enabled: Whether PII detection is active.
        severity: Dict mapping PII type names to severity levels.
            The ``"default"`` key sets the fallback severity.
    """

    enabled: bool = True
    severity: dict[str, str] = field(
        default_factory=lambda: {
            "default": "warn",
            "ssn": "block",
            "credit_card": "block",
            "api_key": "block",
            "email": "warn",
            "phone": "warn",
            "ip_address": "log",
        }
    )


@dataclass
class BiasPolicy:
    """Configuration for bias detection.

    Attributes:
        enabled: Whether bias detection is active.
        severity: Default severity for bias violations.
        protected_attributes: List of protected attribute terms to check.
        evaluative_window: Word window for co-occurrence detection.
        contexts: Domain contexts to activate domain-specific checks.
    """

    enabled: bool = True
    severity: str = "warn"
    protected_attributes: list[str] = field(
        default_factory=lambda: [
            "gender", "race", "ethnicity", "religion",
            "age", "disability", "nationality", "sexual_orientation",
        ]
    )
    evaluative_window: int = 15
    contexts: list[str] = field(
        default_factory=lambda: ["hiring", "lending", "healthcare"]
    )


@dataclass
class SensitiveDomainsPolicy:
    """Configuration for sensitive domain classification.

    Attributes:
        enabled: Whether domain classification is active.
        severity: Severity for sensitive domain violations.
        use_llm_classifier: Whether to use LLM for ambiguous classifications.
    """

    enabled: bool = True
    severity: str = "log"
    use_llm_classifier: bool = True
    model_name: str = "gpt-4o-mini"



@dataclass
class BlockingPolicy:
    """Configuration for blocking behavior.

    Attributes:
        halt_on_block: If True, raise EthicsBlockException when a
            BLOCK-severity violation is detected. If False, log it
            without halting.
    """

    halt_on_block: bool = True


# ---------------------------------------------------------------------------
# Main EthicsPolicy class
# ---------------------------------------------------------------------------


@dataclass
class EthicsPolicy:
    """Complete ethics policy configuration.

    Loaded from a YAML file or constructed programmatically.
    Provides typed access to all policy settings with sensible defaults.

    Attributes:
        version: Policy schema version.
        pii: PII detection settings.
        bias: Bias detection settings.
        sensitive_domains: Domain classification settings.
        blocking: Blocking/halting behavior settings.
    """

    version: str = "1.0"
    pii: PIIPolicy = field(default_factory=PIIPolicy)
    bias: BiasPolicy = field(default_factory=BiasPolicy)
    sensitive_domains: SensitiveDomainsPolicy = field(
        default_factory=SensitiveDomainsPolicy
    )
    blocking: BlockingPolicy = field(default_factory=BlockingPolicy)

    @classmethod
    def load(cls, path: str) -> EthicsPolicy:
        """Load and validate an ethics policy from a YAML file.

        Args:
            path: Path to the YAML policy file.

        Returns:
            A fully validated EthicsPolicy instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            ValueError: If the YAML contains invalid keys or values.
            yaml.YAMLError: If the YAML is malformed.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Ethics policy file not found: {path}")

        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(
                f"Ethics policy must be a YAML mapping, got {type(data).__name__}"
            )

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EthicsPolicy:
        """Create an EthicsPolicy from a plain dictionary.

        Useful for tests and programmatic construction.

        Args:
            d: Dictionary matching the YAML schema.

        Returns:
            A validated EthicsPolicy instance.

        Raises:
            ValueError: If unknown keys or invalid severity values are found.
        """
        # Validate top-level keys
        unknown_keys = set(d.keys()) - _VALID_TOP_LEVEL_KEYS
        if unknown_keys:
            raise ValueError(
                f"Unknown top-level keys in ethics policy: {unknown_keys}. "
                f"Valid keys: {_VALID_TOP_LEVEL_KEYS}"
            )

        policy = cls()
        policy.version = str(d.get("version", "1.0"))

        # Parse PII section
        pii_data = d.get("pii", {})
        if isinstance(pii_data, dict):
            policy.pii = PIIPolicy(
                enabled=bool(pii_data.get("enabled", True)),
                severity=policy.pii.severity.copy(),
            )
            severity_data = pii_data.get("severity", {})
            if isinstance(severity_data, dict):
                for key, value in severity_data.items():
                    _validate_severity(value, f"pii.severity.{key}")
                    policy.pii.severity[key] = str(value)

        # Parse bias section
        bias_data = d.get("bias", {})
        if isinstance(bias_data, dict):
            severity = str(bias_data.get("severity", "warn"))
            _validate_severity(severity, "bias.severity")
            policy.bias = BiasPolicy(
                enabled=bool(bias_data.get("enabled", True)),
                severity=severity,
                protected_attributes=list(
                    bias_data.get("protected_attributes", policy.bias.protected_attributes)
                ),
                evaluative_window=int(
                    bias_data.get("evaluative_window", 15)
                ),
                contexts=list(
                    bias_data.get("contexts", policy.bias.contexts)
                ),
            )

        # Parse sensitive_domains section
        sd_data = d.get("sensitive_domains", {})
        if isinstance(sd_data, dict):
            severity = str(sd_data.get("severity", "log"))
            _validate_severity(severity, "sensitive_domains.severity")
            policy.sensitive_domains = SensitiveDomainsPolicy(
                enabled=bool(sd_data.get("enabled", True)),
                severity=severity,
                use_llm_classifier=bool(sd_data.get("use_llm_classifier", True)),
                model_name=str(sd_data.get("model", "gpt-4o-mini")),
            )

        # Parse blocking section
        blocking_data = d.get("blocking", {})
        if isinstance(blocking_data, dict):
            policy.blocking = BlockingPolicy(
                halt_on_block=bool(blocking_data.get("halt_on_block", True)),
            )

        return policy

    @classmethod
    def default(cls) -> EthicsPolicy:
        """Return an EthicsPolicy with sensible default values.

        The defaults match the schema documented in ``config/ethics-policy.yaml``.
        No file is required.

        Returns:
            An EthicsPolicy with all defaults applied.
        """
        return cls()

    def get_pii_severity(self, pii_type: str) -> str:
        """Get the configured severity for a specific PII type.

        Falls back to the ``default`` severity if the specific type
        is not configured.

        Args:
            pii_type: The PII type name (e.g. ``"ssn"``, ``"email"``).

        Returns:
            Severity string (``"log"``, ``"warn"``, or ``"block"``).
        """
        return self.pii.severity.get(
            pii_type, self.pii.severity.get("default", "warn")
        )


def _validate_severity(value: Any, context: str) -> None:
    """Validate that a severity value is one of the allowed values.

    Args:
        value: The severity value to check.
        context: Human-readable location for the error message.

    Raises:
        ValueError: If the value is not a valid severity.
    """
    if str(value).lower() not in _VALID_SEVERITIES:
        raise ValueError(
            f"Invalid severity '{value}' at {context}. "
            f"Must be one of: {', '.join(sorted(_VALID_SEVERITIES))}"
        )
