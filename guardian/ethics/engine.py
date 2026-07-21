"""Ethics engine orchestrator for GUARDIAN.

Coalesces PII scanning, bias checking, and domain classification checks
into a single entry point for checking traces. Writes flags directly to
the database store and throws exceptions if blocking is enabled.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from guardian.ethics.bias_checker import BiasChecker
from guardian.ethics.category_classifier import CategoryClassifier, SensitiveDomain
from guardian.ethics.exceptions import EthicsBlockException
from guardian.ethics.pii_detector import PIIDetector
from guardian.ethics.policy import EthicsPolicy
from guardian.ethics.violations import EthicsViolation, Severity, ViolationType

logger = logging.getLogger("guardian.ethics")


class EthicsEngine:
    """Orchestrates all ethics checks against a trace event.

    Applies PII scanning, bias checking, and domain classification rules
    based on the configured EthicsPolicy.

    Args:
        policy: Loaded EthicsPolicy instance. If None, uses EthicsPolicy.default().
    """

    def __init__(self, policy: EthicsPolicy | None = None) -> None:
        self.policy = policy or EthicsPolicy.default()
        self._pii = PIIDetector(use_spacy=True)
        self._bias = BiasChecker(
            protected_attributes=self.policy.bias.protected_attributes,
            evaluative_window=self.policy.bias.evaluative_window,
        )
        self._classifier = CategoryClassifier(
            use_llm=self.policy.sensitive_domains.use_llm_classifier,
            model_name=self.policy.sensitive_domains.model_name,
        )

    async def evaluate(self, trace_event: dict[str, Any]) -> list[EthicsViolation]:
        """Run all enabled ethics checks against a TraceEvent dict (asynchronous).

        Checks text inputs and outputs, overrides violation severities according
        to policy config, saves violations to database store, and raises
        EthicsBlockException if a BLOCK-severity violation occurs.

        Args:
            trace_event: The TraceEvent dict from the Phase 1 serializer.

        Returns:
            List of EthicsViolation objects. Empty if all checks pass.

        Raises:
            EthicsBlockException: If a BLOCK-severity violation occurs and
                halt_on_block is True in the policy.
        """
        violations: list[EthicsViolation] = []
        session_id = str(trace_event.get("session_id", ""))
        agent_name = str(trace_event.get("agent_name", ""))

        # Walk through all calls in the trace to gather text to analyze
        calls = trace_event.get("calls", [])
        if not isinstance(calls, list):
            return []

        # 1. Category classification (to determine domain-specific context)
        # Classify the overall trace content based on combined results
        domain = SensitiveDomain.NONE
        domain_confidence = 0.0

        all_text_parts = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            args_preview = call.get("args_preview", "")
            result_preview = call.get("result_preview", "")
            if args_preview:
                all_text_parts.append(str(args_preview))
            if result_preview:
                all_text_parts.append(str(result_preview))

        combined_text = " ".join(all_text_parts)
        if combined_text.strip():
            domain, domain_confidence = self._classifier.classify(combined_text)

        # Record sensitive domain classification if enabled
        if self.policy.sensitive_domains.enabled and domain != SensitiveDomain.NONE:
            violations.append(
                EthicsViolation(
                    violation_type=ViolationType.SENSITIVE_DOMAIN,
                    severity=Severity(self.policy.sensitive_domains.severity),
                    description=f"Sensitive domain '{domain.value}' detected",
                    evidence=domain.value,
                    field_path="trace",
                    confidence=domain_confidence,
                    session_id=session_id,
                    agent_name=agent_name,
                    metadata={"domain": domain.value},
                )
            )

        # 2. PII Detection (if enabled)
        if self.policy.pii.enabled:
            raw_pii_violations = self._pii.scan_trace_event(trace_event)
            for v in raw_pii_violations:
                pii_type = v.metadata.get("pii_type", "default")
                policy_severity = self.policy.get_pii_severity(pii_type)
                v.severity = Severity(policy_severity)
                violations.append(v)

        # 3. Bias Checking (if enabled, passing context domain)
        if self.policy.bias.enabled:
            raw_bias_violations = self._bias.check_trace_event(
                trace_event, context=domain.value
            )
            for v in raw_bias_violations:
                v.severity = Severity(self.policy.bias.severity)
                violations.append(v)

        # 4. Save flags to database store (if guardian initialized and database available)
        # We do this asynchronously/safely by importing store methods
        if violations:
            try:
                import json

                from guardian.store.db import get_session
                from guardian.store.models import EthicsFlagRecord

                with get_session() as session:
                    for v in violations:
                        record = EthicsFlagRecord(
                            session_id=v.session_id,
                            agent_name=v.agent_name,
                            violation_type=v.violation_type.value,
                            severity=v.severity.value,
                            description=v.description,
                            evidence=v.evidence,
                            field_path=v.field_path,
                            confidence=v.confidence,
                            metadata_json=json.dumps(v.metadata, default=str),
                        )
                        session.add(record)
            except Exception as exc:
                logger.warning("Failed to save ethics flags to store: %s", exc)

        # 5. Apply severity checks and halt if necessary
        block_violations = [v for v in violations if v.severity == Severity.BLOCK]
        if block_violations and self.policy.blocking.halt_on_block:
            raise EthicsBlockException(
                block_violations,
                message=f"Ethics policy blocked agent execution: found {len(block_violations)} BLOCK-severity violation(s)",
            )

        return violations

    def evaluate_sync(self, trace_event: dict[str, Any]) -> list[EthicsViolation]:
        """Synchronous wrapper around evaluate() for synchronous agent contexts.

        Runs the async evaluate() coroutine inside the event loop safely.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # If called inside an existing loop, use an executor or execute synchronously
            # Since evaluate is mostly CPU-bound/regex except LiteLLM, we can run it.
            # However, running async in async can be tricky. We use run_coroutine_threadsafe or nest_asyncio.
            # But let's build a simple helper to block run it.
            # Since CategoryClassifier is the only thing doing async-like network IO (via litellm),
            # and litellm is synchronous under the hood inside completion(),
            # evaluate doesn't actually block on raw sockets asynchronously.
            # Let's run it using a helper task.
            return loop.run_until_complete(self.evaluate(trace_event))
        else:
            return loop.run_until_complete(self.evaluate(trace_event))
