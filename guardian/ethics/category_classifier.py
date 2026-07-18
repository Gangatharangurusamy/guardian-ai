"""Sensitive domain classifier for GUARDIAN Ethics Engine.

Classifies agent output into high-risk domains (hiring, financial,
healthcare, legal, criminal justice, education, housing) that require
elevated scrutiny under regulations like the EU AI Act.

Uses a two-tier approach:
1. **Keyword classifier** — zero cost, always runs first.
2. **LLM classifier** (via LiteLLM) — optional, only for ambiguous scores.

The LLM tier is fully optional: it requires ``litellm`` to be installed
and at least one provider API key in the ``.env`` file. If unavailable,
keyword-only mode is used silently with no error.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from enum import Enum
from functools import lru_cache
from typing import Any

logger = logging.getLogger("guardian.ethics")


# ---------------------------------------------------------------------------
# Domain enum
# ---------------------------------------------------------------------------


class SensitiveDomain(str, Enum):
    """High-risk domains requiring elevated scrutiny."""

    HIRING = "hiring"
    FINANCIAL = "financial"
    HEALTHCARE = "healthcare"
    LEGAL = "legal"
    CRIMINAL_JUSTICE = "criminal_justice"
    EDUCATION = "education"
    HOUSING = "housing"
    NONE = "none"


# ---------------------------------------------------------------------------
# Domain keyword vocabularies — Tier 1
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[SensitiveDomain, list[str]] = {
    SensitiveDomain.HIRING: [
        "resume", "candidate", "job", "interview", "applicant",
        "hire", "hiring", "role", "position", "qualification",
        "recruitment", "recruiter", "employment", "vacancy",
        "shortlist", "cover letter", "cv", "screening",
    ],
    SensitiveDomain.FINANCIAL: [
        "loan", "credit", "interest rate", "mortgage", "investment",
        "portfolio", "risk score", "approve", "deny", "banking",
        "deposit", "withdrawal", "transaction", "underwriting",
        "debt", "collateral", "amortization", "apr",
    ],
    SensitiveDomain.HEALTHCARE: [
        "diagnosis", "prescription", "treatment", "symptoms",
        "medical", "clinical", "dosage", "therapy", "patient",
        "physician", "hospital", "pharmacy", "disease", "condition",
        "prognosis", "surgery", "medication",
    ],
    SensitiveDomain.LEGAL: [
        "contract", "liability", "lawsuit", "compliance",
        "regulation", "judgment", "statute", "litigation",
        "attorney", "lawyer", "court", "plaintiff", "defendant",
        "arbitration", "injunction", "tort",
    ],
    SensitiveDomain.CRIMINAL_JUSTICE: [
        "arrest", "conviction", "sentence", "parole", "probation",
        "criminal", "felony", "misdemeanor", "incarceration",
        "bail", "indictment", "verdict", "recidivism",
        "prosecution", "defense attorney",
    ],
    SensitiveDomain.EDUCATION: [
        "enrollment", "admission", "student", "academic",
        "curriculum", "grade", "scholarship", "tuition",
        "diploma", "degree", "university", "school",
        "transcript", "gpa", "expulsion",
    ],
    SensitiveDomain.HOUSING: [
        "rental", "tenant", "landlord", "eviction", "lease",
        "housing", "apartment", "mortgage", "property",
        "zoning", "fair housing", "section 8", "hud",
        "discrimination", "accommodation",
    ],
}


# ---------------------------------------------------------------------------
# Keyword scoring
# ---------------------------------------------------------------------------


def _score_keywords(text: str) -> dict[SensitiveDomain, float]:
    """Score text against each domain's keyword vocabulary.

    Args:
        text: The text to classify.

    Returns:
        Dict mapping each domain to a confidence score (0.0 to 1.0).
    """
    tokens = set(re.findall(r"\b\w+\b", text.lower()))
    if not tokens:
        return {d: 0.0 for d in SensitiveDomain if d != SensitiveDomain.NONE}

    scores: dict[SensitiveDomain, float] = {}

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        keyword_set = set(kw.lower() for kw in keywords)
        matches = tokens & keyword_set

        if not matches:
            scores[domain] = 0.0
            continue

        # Score based on absolute number of matches to avoid false negatives in short text.
        # 0.25 per match, meaning 3+ matches confidently identify the domain (>= 0.75).
        raw_score = len(matches) * 0.25

        scores[domain] = round(min(1.0, raw_score), 4)

    return scores


# ---------------------------------------------------------------------------
# LLM classification — Tier 2
# ---------------------------------------------------------------------------

# LRU cache on text hash to avoid repeat LLM calls
@lru_cache(maxsize=256)
def _cached_llm_classify(text_hash: str, text_snippet: str, model_name: str) -> tuple[str, float]:
    """Call LLM via LiteLLM to classify text into a sensitive domain.

    Cached by text hash. Returns ("none", 0.0) on any failure.

    Args:
        text_hash: SHA256 hash of the full text (cache key).
        text_snippet: First 500 chars of text for the LLM prompt.
        model_name: The name of the model to use.

    Returns:
        Tuple of (domain_string, confidence).
    """
    try:
        import litellm  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("LiteLLM not installed, skipping LLM classification.")
        return ("none", 0.0)

    prompt = (
        "Classify this text into exactly one category:\n"
        "hiring | financial | healthcare | legal | criminal_justice | education | housing | none\n\n"
        f"Text: {text_snippet}\n\n"
        'Return only: {"domain": "<category>", "confidence": <0.0-1.0>}'
    )

    try:
        response = litellm.completion(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=50,
        )

        content = response.choices[0].message.content.strip()

        # Parse JSON response
        result = json.loads(content)
        domain = str(result.get("domain", "none")).lower()
        confidence = float(result.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        return (domain, confidence)

    except Exception as exc:
        logger.debug("LLM classification failed: %s", exc)
        return ("none", 0.0)


def _has_any_api_key() -> bool:
    """Check if any supported LLM provider API key exists in environment.

    Returns:
        True if at least one provider key is found.
    """
    import os

    key_names = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "GEMINI_API_KEY",
        "COHERE_API_KEY",
        "AZURE_API_KEY",
        "HUGGINGFACE_API_KEY",
    ]
    return any(os.environ.get(k) for k in key_names)


# ---------------------------------------------------------------------------
# CategoryClassifier class
# ---------------------------------------------------------------------------


class CategoryClassifier:
    """Classifies agent output into sensitive high-risk domains.

    Uses a two-tier approach: keyword scoring (always) followed by
    optional LLM classification (only for ambiguous keyword scores).

    Args:
        use_llm: Whether to attempt LLM classification for ambiguous
            cases. Even if True, LLM is only called when an API key
            exists in the environment and ``litellm`` is installed.
            Falls back silently to keyword-only mode otherwise.
    """

    def __init__(self, use_llm: bool = True, model_name: str = "gpt-4o-mini") -> None:
        self._use_llm = use_llm
        self._model_name = model_name

        # Load .env if python-dotenv is available
        try:
            from dotenv import load_dotenv  # type: ignore[import-untyped]

            load_dotenv()
        except ImportError:
            pass

    def classify(self, text: str) -> tuple[SensitiveDomain, float]:
        """Classify text into a sensitive domain.

        Never raises exceptions — returns ``(SensitiveDomain.NONE, 0.0)``
        on any error.

        Args:
            text: The agent output text to classify.

        Returns:
            Tuple of ``(SensitiveDomain, confidence)``.

        Decision tree::

            keyword score > 0.7     -> return keyword result (no LLM)
            keyword score 0.4-0.7   -> try LLM if available
                                       -> LLM unavailable: return keyword result
            keyword score < 0.4     -> return (NONE, score)
        """
        if not text or not isinstance(text, str):
            return (SensitiveDomain.NONE, 0.0)

        try:
            return self._classify_internal(text)
        except Exception as exc:
            logger.debug("Classification failed: %s", exc)
            return (SensitiveDomain.NONE, 0.0)

    def _classify_internal(self, text: str) -> tuple[SensitiveDomain, float]:
        """Internal classification logic."""
        # Tier 1: Keyword scoring
        scores = _score_keywords(text)

        if not scores:
            return (SensitiveDomain.NONE, 0.0)

        # Find the best scoring domain
        best_domain = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best_domain]

        # High confidence from keywords alone
        if best_score > 0.7:
            return (best_domain, best_score)

        # Low confidence — not a sensitive domain
        if best_score < 0.4:
            return (SensitiveDomain.NONE, best_score)

        # Ambiguous (0.4 - 0.7) — try LLM if available
        if self._use_llm and _has_any_api_key():
            text_snippet = text[:500]
            text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

            llm_domain, llm_confidence = _cached_llm_classify(text_hash, text_snippet, self._model_name)

            # Try to map LLM result to enum
            try:
                domain_enum = SensitiveDomain(llm_domain)
                if llm_confidence > best_score:
                    return (domain_enum, llm_confidence)
            except ValueError:
                # LLM returned an unrecognized domain string
                pass

        # Fall back to keyword result
        if best_score >= 0.4:
            return (best_domain, best_score)

        return (SensitiveDomain.NONE, best_score)
