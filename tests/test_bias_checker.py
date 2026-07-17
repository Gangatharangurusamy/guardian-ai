"""Tests for bias checker module."""

from __future__ import annotations

import pytest
from guardian.ethics.bias_checker import BiasChecker
from guardian.ethics.violations import Severity, ViolationType


def test_protected_cooccurrence() -> None:
    checker = BiasChecker(evaluative_window=15)

    # Co-occurrence close to word window
    text = "Female candidate was rejected because she did not fit the profile."
    v = checker.check(text, "general", "sess-1", "agent-1", "path")
    assert len(v) == 1
    assert v[0].violation_type == ViolationType.BIAS_DETECTED
    assert v[0].severity == Severity.WARN
    assert v[0].metadata["protected_attribute"] == "female"
    assert v[0].metadata["evaluative_term"] == "rejected"
    assert v[0].confidence >= 0.55 and v[0].confidence <= 0.70


def test_clean_text_no_flags() -> None:
    checker = BiasChecker(evaluative_window=15)

    # Clean examples (no false positives)
    clean_examples = [
        "The candidate has 5 years of Python programming experience and strong SQL skills.",
        "We are looking for a software engineer to build web applications using React and FastAPI.",
        "The patient is recovering well after the prescription treatment dosage was adjusted yesterday.",
    ]

    for text in clean_examples:
        v = checker.check(text, "hiring", "sess-1", "agent-1", "path")
        assert len(v) == 0, f"False positive flagged on clean text: {text}"


def test_domain_specific_patterns() -> None:
    checker = BiasChecker()

    # Hiring domain pattern (culture fit + protected attribute)
    text = "The applicant is female. While she is qualified, we are concerned she won't be a good culture fit."
    # Hiring context -> flags domain pattern
    v_hiring = checker.check(text, "hiring", "sess-1", "agent-1", "path")
    assert len(v_hiring) >= 1
    assert any(x.metadata.get("check_type") == "domain_specific" for x in v_hiring)

    # General context -> does not activate domain hiring patterns (but may flag general cooccurrence)
    v_gen = checker.check(text, "general", "sess-1", "agent-1", "path")
    assert not any(x.metadata.get("check_type") == "domain_specific" for x in v_gen)

    # Lending domain pattern (zip code + rejected)
    lending_text = "We reviewed the property location zip code and decided the loan application must be rejected."
    v_lending = checker.check(lending_text, "lending", "sess-1", "agent-1", "path")
    assert len(v_lending) >= 1
    assert any(x.metadata.get("check_type") == "domain_specific" for x in v_lending)


def test_counterfactual_inconsistency() -> None:
    checker = BiasChecker()

    # Similar inputs (Jaccard > 0.7) but vastly different output sizes (< 30% length ratio)
    trace_event = {
        "session_id": "session-uuid",
        "agent_name": "evaluator",
        "calls": [
            {
                "args_preview": "Evaluate resume of applicant John Smith with 10 years experience",
                "result_preview": "John Smith is rejected because of cultural fit concerns.",  # 56 chars
            },
            {
                "args_preview": "Evaluate resume of applicant Jane Smith with 10 years experience",
                "result_preview": (
                    "Jane Smith is approved. She has excellent technical expertise, "
                    "outstanding communication skills, deep understanding of cloud architecture, "
                    "a history of leadership, and is recommended for the principal engineer role "
                    "immediately."
                ),  # 241 chars
            },
        ],
    }

    violations = checker.check_trace_event(trace_event, "general")
    assert len(violations) >= 1
    assert any(v.violation_type == ViolationType.FAIRNESS_VIOLATION for v in violations)
