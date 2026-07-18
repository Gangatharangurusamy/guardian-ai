"""Tests for sensitive domain classifier module."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch
import pytest
from guardian.ethics.category_classifier import CategoryClassifier, SensitiveDomain


def test_keyword_classification() -> None:
    classifier = CategoryClassifier(use_llm=False)

    # Hiring keywords
    hiring_text = "We screened the resume, interviewed the candidate, and decided to hire them for the developer role."
    domain, confidence = classifier.classify(hiring_text)
    assert domain == SensitiveDomain.HIRING
    assert confidence > 0.7

    # Financial keywords
    finance_text = "The borrower requested a mortgage loan for property development. We analyzed credit score and approved the mortgage."
    domain, confidence = classifier.classify(finance_text)
    assert domain == SensitiveDomain.FINANCIAL
    assert confidence > 0.7

    # Clean text (not high risk)
    clean_text = "The weather today is sunny and mild. I think we should go for a walk in the park."
    domain, confidence = classifier.classify(clean_text)
    assert domain == SensitiveDomain.NONE
    assert confidence < 0.4


def test_ambiguous_text_no_key_fallback() -> None:
    classifier = CategoryClassifier(use_llm=True)

    # Ambiguous text containing only a couple keywords (score between 0.4 and 0.7)
    ambiguous_text = "Let's review the job description for the new role."
    
    # Force _has_any_api_key to return False to test fallback to keyword result
    with patch("guardian.ethics.category_classifier._has_any_api_key", return_value=False):
        domain, confidence = classifier.classify(ambiguous_text)
        # Should fall back to keyword classification and not raise any errors
        assert isinstance(domain, SensitiveDomain)


@patch("guardian.ethics.category_classifier._has_any_api_key", return_value=True)
def test_llm_classification_called_on_ambiguity(mock_has_key: MagicMock) -> None:
    # Ambiguous text to trigger Tier 2
    ambiguous_text = "Let's review the job description for the new role."

    # Mock litellm completion call
    mock_completion_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"domain": "hiring", "confidence": 0.85}'
    mock_completion_response.choices = [mock_choice]

    # Create dummy litellm module in sys.modules if not installed
    # so the import works inside tests
    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = mock_completion_response

    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        classifier = CategoryClassifier(use_llm=True, model_name="claude-3-haiku-20240307")
        domain, confidence = classifier.classify(ambiguous_text)

        # Verify it mapped the LLM result correctly
        assert domain == SensitiveDomain.HIRING
        assert confidence == 0.85
        assert mock_litellm.completion.called
        
        # Verify the model parameter was correctly passed
        mock_litellm.completion.assert_called_once()
        called_args, called_kwargs = mock_litellm.completion.call_args
        assert called_kwargs.get("model") == "claude-3-haiku-20240307"



@patch("guardian.ethics.category_classifier._has_any_api_key", return_value=True)
def test_llm_not_called_on_unambiguous(mock_has_key: MagicMock) -> None:
    # Unambiguous hiring text (score > 0.7 from keywords)
    unambiguous_text = (
        "candidate resume candidate job interview applicant hire role position qualification "
        "recruitment recruiter employment vacancy shortlist cv cv cv cv screening"
    )

    mock_litellm = MagicMock()
    with patch.dict(sys.modules, {"litellm": mock_litellm}):
        classifier = CategoryClassifier(use_llm=True)
        domain, confidence = classifier.classify(unambiguous_text)

        # Should classify via keyword and skip LLM call
        assert domain == SensitiveDomain.HIRING
        assert confidence > 0.7
        assert not mock_litellm.completion.called
