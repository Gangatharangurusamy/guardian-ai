"""PII (Personally Identifiable Information) detector for GUARDIAN Ethics Engine.

Scans text for personally identifiable information using three detection layers,
applied cheapest-first:

1. **Regex patterns** (zero latency, no dependencies) — emails, phones, SSNs,
   credit cards, passports, IP addresses, UK NI numbers, auth tokens.
2. **spaCy NER** (optional) — extracts named entities and flags identifying
   combinations (name + date + location).
3. **Heuristics** — private keys, AWS access keys, JWT tokens.

The detector never crashes on any input and caps processing at 50,000 characters.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from guardian.ethics.violations import EthicsViolation, Severity, ViolationType

logger = logging.getLogger("guardian.ethics")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_INPUT_LENGTH = 50_000
_MAX_EVIDENCE_LENGTH = 100

# Confidence values per detection layer
_CONFIDENCE_REGEX = 0.95
_CONFIDENCE_SPACY = 0.75
_CONFIDENCE_HEURISTIC = 0.65

# PII types that default to BLOCK severity
_BLOCK_PII_TYPES = frozenset({"ssn", "credit_card", "api_key", "aws_key", "private_key"})
# PII types that default to LOG severity
_LOG_PII_TYPES = frozenset({"ip_address"})

# ---------------------------------------------------------------------------
# Regex patterns — Layer 1
# ---------------------------------------------------------------------------

# Each entry: (pattern_name, compiled_regex, pii_type_label, description)
_PII_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "email",
        re.compile(
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
        ),
        "email",
        "Email address detected",
    ),
    (
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "ssn",
        "Social Security Number (SSN) detected",
    ),
    (
        "credit_card",
        re.compile(
            r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
            r"[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"
        ),
        "credit_card",
        "Credit card number detected",
    ),
    (
        "phone_us",
        re.compile(
            r"\b(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b"
        ),
        "phone",
        "US phone number detected",
    ),
    (
        "phone_intl",
        re.compile(r"\b\+\d{1,3}[\s\-.]?\d{4,14}\b"),
        "phone",
        "International phone number detected",
    ),
    (
        "ip_v4",
        re.compile(
            r"\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        "ip_address",
        "IPv4 address detected",
    ),
    (
        "ip_v6",
        re.compile(
            r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
        ),
        "ip_address",
        "IPv6 address detected",
    ),
    (
        "uk_ni",
        re.compile(
            r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
            re.IGNORECASE,
        ),
        "uk_ni_number",
        "UK National Insurance number detected",
    ),
    (
        "passport",
        re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),
        "passport",
        "Passport number pattern detected",
    ),
    (
        "url_token",
        re.compile(
            r"(?:[\?&](?:token|api_key|apikey|access_token|secret|key)"
            r"=)[A-Za-z0-9\-._~+/]+=*",
            re.IGNORECASE,
        ),
        "api_key",
        "URL containing authentication token or API key detected",
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b"),
        "api_key",
        "Bearer token detected",
    ),
]

# ---------------------------------------------------------------------------
# Heuristic patterns — Layer 3
# ---------------------------------------------------------------------------

_HEURISTIC_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "aws_key",
        re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b"),
        "aws_key",
        "AWS access key pattern detected",
    ),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b"
        ),
        "api_key",
        "JWT token detected",
    ),
    (
        "private_key",
        re.compile(
            r"-----BEGIN\s(?:RSA\s)?(?:PRIVATE|EC)\sKEY-----"
        ),
        "private_key",
        "Private key header detected",
    ),
    (
        "hex_secret",
        re.compile(r"\b[0-9a-fA-F]{40,}\b"),
        "api_key",
        "Long hex string detected (possible secret or hash)",
    ),
]

# ---------------------------------------------------------------------------
# spaCy singleton loader — Layer 2
# ---------------------------------------------------------------------------

_spacy_nlp: Any = None
_spacy_load_attempted: bool = False


def _get_spacy_nlp() -> Any:
    """Load the spaCy model as a module-level singleton.

    Returns None if spaCy is not installed or the model is missing.
    Only attempts to load once — subsequent calls return the cached result.
    """
    global _spacy_nlp, _spacy_load_attempted

    if _spacy_load_attempted:
        return _spacy_nlp

    _spacy_load_attempted = True

    try:
        import spacy  # type: ignore[import-untyped]

        _spacy_nlp = spacy.load("en_core_web_sm")
        logger.debug("GUARDIAN PII: spaCy model loaded successfully.")
    except ImportError:
        logger.debug("GUARDIAN PII: spaCy not installed, using regex-only mode.")
        _spacy_nlp = None
    except OSError:
        logger.debug(
            "GUARDIAN PII: spaCy model 'en_core_web_sm' not found. "
            "Run: python -m spacy download en_core_web_sm"
        )
        _spacy_nlp = None

    return _spacy_nlp


# ---------------------------------------------------------------------------
# Evidence masking
# ---------------------------------------------------------------------------


def _mask_evidence(text: str, max_len: int = _MAX_EVIDENCE_LENGTH) -> str:
    """Mask the middle portion of a PII evidence string for safe storage.

    Replaces middle characters with asterisks, preserving the first and
    last few characters for context. Truncates to max_len.

    Args:
        text: The raw evidence text to mask.
        max_len: Maximum length of the returned string.

    Returns:
        A masked, truncated evidence string. E.g. ``john.doe@gm****l.com``.
    """
    if not text:
        return ""

    text = text[:max_len]

    if len(text) <= 6:
        # Too short to meaningfully mask
        return text[:2] + "****"

    # Keep first ~30% and last ~20%, mask the middle
    keep_start = max(2, len(text) // 3)
    keep_end = max(2, len(text) // 5)

    if keep_start + keep_end >= len(text):
        return text[:2] + "****" + text[-2:]

    masked_len = len(text) - keep_start - keep_end
    return text[:keep_start] + "*" * min(masked_len, 8) + text[-keep_end:]


# ---------------------------------------------------------------------------
# PIIDetector class
# ---------------------------------------------------------------------------


class PIIDetector:
    """Detects personally identifiable information in text.

    Uses a three-layer approach (regex, spaCy NER, heuristics) applied
    cheapest-first. All layers are safe — they never crash on any input.

    Args:
        use_spacy: Whether to attempt loading spaCy for NER-based detection.
            If spaCy is not installed, silently falls back to regex-only mode.
    """

    def __init__(self, use_spacy: bool = True) -> None:
        self._use_spacy = use_spacy

    def scan(
        self,
        text: str,
        session_id: str,
        agent_name: str,
        field_path: str,
    ) -> list[EthicsViolation]:
        """Scan text for PII and return all violations found.

        Args:
            text: The text content to scan.
            session_id: Trace session ID for violation metadata.
            agent_name: Agent name for violation metadata.
            field_path: Where in the trace this text came from,
                e.g. ``"calls[0].result_preview"``.

        Returns:
            List of EthicsViolation objects, one per detected PII instance.
            Returns an empty list if no PII is found.
        """
        if not text or not isinstance(text, str):
            return []

        # Cap input length to prevent regex catastrophic backtracking
        text = text[:_MAX_INPUT_LENGTH]

        violations: list[EthicsViolation] = []

        # Layer 1: Regex patterns
        violations.extend(
            self._scan_regex(text, session_id, agent_name, field_path)
        )

        # Layer 2: spaCy NER (optional)
        if self._use_spacy:
            violations.extend(
                self._scan_spacy(text, session_id, agent_name, field_path)
            )

        # Layer 3: Heuristics
        violations.extend(
            self._scan_heuristics(text, session_id, agent_name, field_path)
        )

        return violations

    def scan_trace_event(
        self, trace_event: dict[str, Any]
    ) -> list[EthicsViolation]:
        """Scan all text fields in a TraceEvent dict for PII.

        Traverses ``calls[*].args_preview`` and ``calls[*].result_preview``.

        Args:
            trace_event: The full TraceEvent dict from Phase 1 serializer.

        Returns:
            Combined list of all PII violations found across all fields.
        """
        violations: list[EthicsViolation] = []
        session_id = str(trace_event.get("session_id", ""))
        agent_name = str(trace_event.get("agent_name", ""))

        calls = trace_event.get("calls", [])
        if not isinstance(calls, list):
            return violations

        for i, call in enumerate(calls):
            if not isinstance(call, dict):
                continue

            # Scan args_preview
            args_text = call.get("args_preview", "")
            if args_text and isinstance(args_text, str):
                violations.extend(
                    self.scan(
                        args_text,
                        session_id,
                        agent_name,
                        f"calls[{i}].args_preview",
                    )
                )

            # Scan result_preview
            result_text = call.get("result_preview", "")
            if result_text and isinstance(result_text, str):
                violations.extend(
                    self.scan(
                        result_text,
                        session_id,
                        agent_name,
                        f"calls[{i}].result_preview",
                    )
                )

        return violations

    # -------------------------------------------------------------------
    # Private scanning methods
    # -------------------------------------------------------------------

    def _scan_regex(
        self,
        text: str,
        session_id: str,
        agent_name: str,
        field_path: str,
    ) -> list[EthicsViolation]:
        """Layer 1: Scan using compiled regex patterns."""
        violations: list[EthicsViolation] = []
        seen: set[str] = set()

        for name, pattern, pii_type, description in _PII_PATTERNS:
            try:
                for match in pattern.finditer(text):
                    matched_text = match.group()

                    # De-duplicate matches within the same scan
                    dedup_key = f"{name}:{matched_text}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    severity = self._default_severity(pii_type)
                    violations.append(
                        EthicsViolation(
                            violation_type=ViolationType.PII_DETECTED,
                            severity=severity,
                            description=description,
                            evidence=_mask_evidence(matched_text),
                            field_path=field_path,
                            confidence=_CONFIDENCE_REGEX,
                            session_id=session_id,
                            agent_name=agent_name,
                            metadata={"pii_type": pii_type, "detector": "regex", "pattern": name},
                        )
                    )
            except Exception as exc:
                logger.debug("PII regex '%s' failed: %s", name, exc)

        return violations

    def _scan_spacy(
        self,
        text: str,
        session_id: str,
        agent_name: str,
        field_path: str,
    ) -> list[EthicsViolation]:
        """Layer 2: Scan using spaCy NER for identifying entity combinations."""
        nlp = _get_spacy_nlp()
        if nlp is None:
            return []

        violations: list[EthicsViolation] = []

        try:
            # Process a truncated version for NER (spaCy can be slow on long text)
            doc = nlp(text[:10_000])

            # Collect entities by type
            persons: list[str] = []
            dates: list[str] = []
            locations: list[str] = []

            for ent in doc.ents:
                if ent.label_ == "PERSON":
                    persons.append(ent.text)
                elif ent.label_ == "DATE":
                    dates.append(ent.text)
                elif ent.label_ in ("GPE", "LOC"):
                    locations.append(ent.text)

            # Flag identifying triplets: name + date + location
            if persons and dates and locations:
                evidence_parts = []
                if persons:
                    evidence_parts.append(f"Name: {persons[0]}")
                if dates:
                    evidence_parts.append(f"Date: {dates[0]}")
                if locations:
                    evidence_parts.append(f"Location: {locations[0]}")
                evidence = ", ".join(evidence_parts)

                violations.append(
                    EthicsViolation(
                        violation_type=ViolationType.PII_DETECTED,
                        severity=Severity.WARN,
                        description="Identifying information combination detected (name + date + location)",
                        evidence=_mask_evidence(evidence),
                        field_path=field_path,
                        confidence=_CONFIDENCE_SPACY,
                        session_id=session_id,
                        agent_name=agent_name,
                        metadata={
                            "pii_type": "identifying_combination",
                            "detector": "spacy",
                            "person_count": len(persons),
                            "date_count": len(dates),
                            "location_count": len(locations),
                        },
                    )
                )

            # Also flag individual PERSON entities as potential PII
            for person in persons:
                violations.append(
                    EthicsViolation(
                        violation_type=ViolationType.PII_DETECTED,
                        severity=Severity.LOG,
                        description="Person name detected",
                        evidence=_mask_evidence(person),
                        field_path=field_path,
                        confidence=_CONFIDENCE_SPACY,
                        session_id=session_id,
                        agent_name=agent_name,
                        metadata={"pii_type": "person_name", "detector": "spacy"},
                    )
                )

        except Exception as exc:
            logger.debug("spaCy NER scan failed: %s", exc)

        return violations

    def _scan_heuristics(
        self,
        text: str,
        session_id: str,
        agent_name: str,
        field_path: str,
    ) -> list[EthicsViolation]:
        """Layer 3: Scan using heuristic patterns for secrets and keys."""
        violations: list[EthicsViolation] = []
        seen: set[str] = set()

        for name, pattern, pii_type, description in _HEURISTIC_PATTERNS:
            try:
                for match in pattern.finditer(text):
                    matched_text = match.group()

                    dedup_key = f"{name}:{matched_text}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    severity = self._default_severity(pii_type)
                    violations.append(
                        EthicsViolation(
                            violation_type=ViolationType.PII_DETECTED,
                            severity=severity,
                            description=description,
                            evidence=_mask_evidence(matched_text),
                            field_path=field_path,
                            confidence=_CONFIDENCE_HEURISTIC,
                            session_id=session_id,
                            agent_name=agent_name,
                            metadata={"pii_type": pii_type, "detector": "heuristic", "pattern": name},
                        )
                    )
            except Exception as exc:
                logger.debug("PII heuristic '%s' failed: %s", name, exc)

        return violations

    @staticmethod
    def _default_severity(pii_type: str) -> Severity:
        """Return the default severity for a PII type.

        BLOCK for SSN, credit cards, API keys, private keys.
        LOG for IP addresses.
        WARN for everything else (emails, phones, etc.).
        """
        if pii_type in _BLOCK_PII_TYPES:
            return Severity.BLOCK
        if pii_type in _LOG_PII_TYPES:
            return Severity.LOG
        return Severity.WARN
