"""Bias detection for GUARDIAN Ethics Engine.

IMPORTANT LIMITATIONS AND INTENDED USE
=======================================

This module detects *patterns* in agent outputs that *may indicate* biased
decision-making. It is designed as a **screening tool for human review**,
NOT as an automated judge of discrimination.

What this module does:
- Flags outputs where protected attribute terms co-occur with evaluative
  or decision-related language within a configurable text window.
- Detects known domain-specific bias patterns (hiring, lending, healthcare).
- Identifies potential fairness issues when similar inputs produce very
  different outputs within the same session.

What this module does NOT do:
- Prove that discrimination occurred.
- Infer demographic group from names (this is explicitly out of scope
  because name-based inference itself introduces bias).
- Make automated blocking decisions (bias violations are always WARN
  severity by default — never BLOCK).

All flagged items should be reviewed by a human compliance team.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from guardian.ethics.violations import EthicsViolation, Severity, ViolationType

logger = logging.getLogger("guardian.ethics")

# ---------------------------------------------------------------------------
# Default vocabulary
# ---------------------------------------------------------------------------

_DEFAULT_PROTECTED_ATTRIBUTES: list[str] = [
    # Gender
    "female", "male", "woman", "man", "women", "men",
    "transgender", "non-binary", "nonbinary", "gender",
    # Race / Ethnicity
    "race", "racial", "ethnicity", "ethnic",
    "black", "white", "asian", "hispanic", "latino", "latina",
    "african american", "caucasian", "indigenous", "native american",
    # Religion
    "muslim", "christian", "jewish", "hindu", "buddhist",
    "sikh", "atheist", "religion", "religious",
    # Age
    "elderly", "senior", "young", "older", "younger", "age",
    # Disability
    "disabled", "disability", "handicapped", "impaired",
    "wheelchair", "blind", "deaf",
    # Nationality
    "immigrant", "foreign", "nationality", "citizen", "migrant",
    # Sexual orientation
    "gay", "lesbian", "bisexual", "homosexual", "heterosexual",
    "sexual orientation", "lgbtq",
]

_EVALUATIVE_TERMS: list[str] = [
    # Negative decision language
    "rejected", "denied", "declined", "unsuitable", "unqualified",
    "not suitable", "not qualified", "not recommended", "unfit",
    "disqualified", "eliminated", "excluded", "less likely",
    "less suitable", "less qualified", "inferior", "poor fit",
    "not a good fit", "concerns about",
    # Positive decision language (can indicate differential treatment)
    "preferred", "better", "superior", "ideal", "perfect fit",
    "more suitable", "more qualified", "recommended", "selected",
    "approved", "accepted",
    # Hiring-specific
    "overqualified", "underqualified", "culture fit", "cultural fit",
    "not a culture fit", "team fit",
    # General evaluative
    "typically", "usually", "tend to", "likely to", "less capable",
    "more capable", "inherently", "naturally",
]

# Domain-specific patterns
_HIRING_BIAS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:culture\s+fit|cultural\s+fit)\b.*?"
        r"\b(?:" + "|".join(re.escape(a) for a in _DEFAULT_PROTECTED_ATTRIBUTES[:20]) + r")\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:" + "|".join(re.escape(a) for a in _DEFAULT_PROTECTED_ATTRIBUTES[:20]) + r")\b"
        r".*?\b(?:culture\s+fit|cultural\s+fit|overqualified|underqualified)\b",
        re.IGNORECASE | re.DOTALL,
    ),
]

_LENDING_BIAS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:zip\s*code|neighborhood|area|district|location|region)\b"
        r".*?\b(?:denied|rejected|declined|risk|high\s*risk|low\s*score)\b",
        re.IGNORECASE | re.DOTALL,
    ),
]

_HEALTHCARE_BIAS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:" + "|".join(re.escape(a) for a in _DEFAULT_PROTECTED_ATTRIBUTES[:20]) + r")\b"
        r".*?\b(?:treatment|medication|prescription|therapy|dosage|diagnosis)\b"
        r".*?\b(?:not\s+(?:recommended|necessary|needed)|less\s+effective)\b",
        re.IGNORECASE | re.DOTALL,
    ),
]


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer.

    Args:
        text: Input text to tokenize.

    Returns:
        List of lowercase word tokens.
    """
    return re.findall(r"\b\w+\b", text.lower())


def _jaccard_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
    """Compute Jaccard similarity between two token lists.

    Args:
        tokens_a: First token list.
        tokens_b: Second token list.

    Returns:
        Jaccard similarity coefficient (0.0 to 1.0).
    """
    if not tokens_a or not tokens_b:
        return 0.0
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


# ---------------------------------------------------------------------------
# BiasChecker class
# ---------------------------------------------------------------------------


class BiasChecker:
    """Detects potentially biased decision-making in agent outputs.

    Checks for protected attribute co-occurrence with evaluative language,
    domain-specific bias patterns, and counterfactual inconsistency across
    calls in the same session.

    All bias violations default to WARN severity — never auto-BLOCK.

    Args:
        protected_attributes: Override the default list of protected
            attribute terms. If None, uses the built-in vocabulary.
        evaluative_window: Number of words for the co-occurrence window.
            A protected attribute term within this many words of an
            evaluative term triggers a flag.
    """

    def __init__(
        self,
        protected_attributes: list[str] | None = None,
        evaluative_window: int = 15,
    ) -> None:
        self._protected = [
            a.lower() for a in (protected_attributes or _DEFAULT_PROTECTED_ATTRIBUTES)
        ]
        self._evaluative = [e.lower() for e in _EVALUATIVE_TERMS]
        self._window = evaluative_window

    def check(
        self,
        text: str,
        context: str = "general",
        session_id: str = "",
        agent_name: str = "",
        field_path: str = "",
    ) -> list[EthicsViolation]:
        """Run all bias checks on text.

        Args:
            text: The agent output or decision text to check.
            context: Domain hint — ``"hiring"``, ``"lending"``,
                ``"healthcare"``, or ``"general"``. Activates
                domain-specific checks.
            session_id: Trace session ID for violation metadata.
            agent_name: Agent name for violation metadata.
            field_path: Where in the trace this text came from.

        Returns:
            List of EthicsViolation with type BIAS_DETECTED or
            FAIRNESS_VIOLATION. Empty if no bias signals found.
        """
        if not text or not isinstance(text, str):
            return []

        violations: list[EthicsViolation] = []

        # Check 1: Protected attribute + evaluative co-occurrence
        violations.extend(
            self._check_cooccurrence(text, session_id, agent_name, field_path)
        )

        # Check 3: Domain-specific patterns
        if context != "general":
            violations.extend(
                self._check_domain_patterns(
                    text, context, session_id, agent_name, field_path
                )
            )

        return violations

    def check_trace_event(
        self,
        trace_event: dict[str, Any],
        context: str = "general",
    ) -> list[EthicsViolation]:
        """Run bias checks across all text fields in a TraceEvent.

        Also performs counterfactual consistency check across multiple
        calls within the same trace session.

        Args:
            trace_event: The full TraceEvent dict from Phase 1 serializer.
            context: Domain hint for domain-specific checks.

        Returns:
            Combined list of all bias violations found.
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

            # Check args_preview
            args_text = call.get("args_preview", "")
            if args_text and isinstance(args_text, str):
                violations.extend(
                    self.check(
                        args_text,
                        context,
                        session_id,
                        agent_name,
                        f"calls[{i}].args_preview",
                    )
                )

            # Check result_preview
            result_text = call.get("result_preview", "")
            if result_text and isinstance(result_text, str):
                violations.extend(
                    self.check(
                        result_text,
                        context,
                        session_id,
                        agent_name,
                        f"calls[{i}].result_preview",
                    )
                )

        # Check 4: Counterfactual inconsistency across calls
        if len(calls) >= 2:
            violations.extend(
                self._check_counterfactual(calls, session_id, agent_name)
            )

        return violations

    # -------------------------------------------------------------------
    # Private check methods
    # -------------------------------------------------------------------

    def _check_cooccurrence(
        self,
        text: str,
        session_id: str,
        agent_name: str,
        field_path: str,
    ) -> list[EthicsViolation]:
        """Check 1: Protected attribute + evaluative term co-occurrence."""
        violations: list[EthicsViolation] = []
        tokens = _tokenize(text)

        if not tokens:
            return violations

        # Find positions of protected attribute terms
        attr_positions: list[tuple[int, str]] = []
        for i, token in enumerate(tokens):
            for attr in self._protected:
                attr_words = attr.split()
                if len(attr_words) == 1:
                    if token == attr:
                        attr_positions.append((i, attr))
                elif len(attr_words) <= len(tokens) - i:
                    # Multi-word attribute check
                    phrase = " ".join(tokens[i : i + len(attr_words)])
                    if phrase == attr:
                        attr_positions.append((i, attr))

        # Find positions of evaluative terms
        eval_positions: list[tuple[int, str]] = []
        for i, token in enumerate(tokens):
            for ev in self._evaluative:
                ev_words = ev.split()
                if len(ev_words) == 1:
                    if token == ev:
                        eval_positions.append((i, ev))
                elif len(ev_words) <= len(tokens) - i:
                    phrase = " ".join(tokens[i : i + len(ev_words)])
                    if phrase == ev:
                        eval_positions.append((i, ev))

        # Check for co-occurrence within window
        seen_pairs: set[tuple[str, str]] = set()
        for attr_pos, attr_term in attr_positions:
            for eval_pos, eval_term in eval_positions:
                distance = abs(attr_pos - eval_pos)
                if distance <= self._window:
                    pair = (attr_term, eval_term)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    # Confidence scales with proximity
                    proximity_factor = 1.0 - (distance / (self._window * 2))
                    confidence = 0.55 + (0.15 * proximity_factor)
                    confidence = min(0.70, max(0.55, confidence))

                    # Extract evidence: text around the co-occurrence
                    start_idx = max(0, min(attr_pos, eval_pos) - 3)
                    end_idx = min(len(tokens), max(attr_pos, eval_pos) + 4)
                    evidence = " ".join(tokens[start_idx:end_idx])

                    violations.append(
                        EthicsViolation(
                            violation_type=ViolationType.BIAS_DETECTED,
                            severity=Severity.WARN,
                            description=(
                                f"Protected attribute '{attr_term}' found near "
                                f"evaluative term '{eval_term}' "
                                f"(within {distance} words)"
                            ),
                            evidence=evidence[:100],
                            field_path=field_path,
                            confidence=confidence,
                            session_id=session_id,
                            agent_name=agent_name,
                            metadata={
                                "check_type": "cooccurrence",
                                "protected_attribute": attr_term,
                                "evaluative_term": eval_term,
                                "word_distance": distance,
                            },
                        )
                    )

        return violations

    def _check_domain_patterns(
        self,
        text: str,
        context: str,
        session_id: str,
        agent_name: str,
        field_path: str,
    ) -> list[EthicsViolation]:
        """Check 3: Domain-specific bias patterns."""
        violations: list[EthicsViolation] = []
        patterns: list[re.Pattern[str]] = []
        domain_label = context

        if context == "hiring":
            patterns = _HIRING_BIAS_PATTERNS
        elif context == "lending":
            patterns = _LENDING_BIAS_PATTERNS
        elif context == "healthcare":
            patterns = _HEALTHCARE_BIAS_PATTERNS
        else:
            return violations

        for pattern in patterns:
            try:
                match = pattern.search(text)
                if match:
                    evidence = match.group()[:100]
                    violations.append(
                        EthicsViolation(
                            violation_type=ViolationType.BIAS_DETECTED,
                            severity=Severity.WARN,
                            description=(
                                f"Domain-specific bias pattern detected "
                                f"in {domain_label} context"
                            ),
                            evidence=evidence,
                            field_path=field_path,
                            confidence=0.65,
                            session_id=session_id,
                            agent_name=agent_name,
                            metadata={
                                "check_type": "domain_specific",
                                "domain": domain_label,
                            },
                        )
                    )
            except Exception as exc:
                logger.debug("Domain bias pattern check failed: %s", exc)

        return violations

    def _check_counterfactual(
        self,
        calls: list[dict[str, Any]],
        session_id: str,
        agent_name: str,
    ) -> list[EthicsViolation]:
        """Check 4: Counterfactual inconsistency across calls.

        If similar inputs produce very different output lengths or quality,
        flag as a potential fairness concern.
        """
        violations: list[EthicsViolation] = []

        # Collect (input_tokens, output_length) pairs
        call_data: list[tuple[list[str], int, int]] = []
        for i, call in enumerate(calls):
            if not isinstance(call, dict):
                continue
            args_text = str(call.get("args_preview", ""))
            result_text = str(call.get("result_preview", ""))
            input_tokens = _tokenize(args_text)
            output_length = len(result_text)
            call_data.append((input_tokens, output_length, i))

        # Compare pairs
        for idx_a in range(len(call_data)):
            for idx_b in range(idx_a + 1, len(call_data)):
                tokens_a, len_a, call_idx_a = call_data[idx_a]
                tokens_b, len_b, call_idx_b = call_data[idx_b]

                similarity = _jaccard_similarity(tokens_a, tokens_b)

                # High input similarity but large output length difference
                if similarity > 0.7:
                    max_len = max(len_a, len_b, 1)
                    min_len = min(len_a, len_b)
                    length_ratio = min_len / max_len

                    if length_ratio < 0.3:  # >70% difference in output length
                        violations.append(
                            EthicsViolation(
                                violation_type=ViolationType.FAIRNESS_VIOLATION,
                                severity=Severity.WARN,
                                description=(
                                    f"Similar inputs (similarity={similarity:.2f}) "
                                    f"produced very different output lengths "
                                    f"({len_a} vs {len_b} chars)"
                                ),
                                evidence=(
                                    f"calls[{call_idx_a}] vs calls[{call_idx_b}]"
                                ),
                                field_path=f"calls[{call_idx_a}]-calls[{call_idx_b}]",
                                confidence=0.50,
                                session_id=session_id,
                                agent_name=agent_name,
                                metadata={
                                    "check_type": "counterfactual",
                                    "input_similarity": round(similarity, 3),
                                    "output_length_a": len_a,
                                    "output_length_b": len_b,
                                },
                            )
                        )

        return violations
