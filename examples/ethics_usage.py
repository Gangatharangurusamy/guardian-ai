"""Runnable example demonstrating GUARDIAN Phase 2 Ethics Engine checks."""

from __future__ import annotations

import asyncio
import os
import sys
from dotenv import load_dotenv

# Ensure guardian package is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import guardian
from guardian.ethics.exceptions import EthicsBlockException
from guardian.store import init_db, get_recent_ethics_flags

load_dotenv()
guardian.init()

# ---------------------------------------------------------------------------
# Example Agents
# ---------------------------------------------------------------------------


# Example 1: Customer support agent that leaks email (WARN) and SSN (BLOCK)
# We configure this to use the default policy file config/ethics-policy.yaml
@guardian.watch("customer_support_agent", policy="config/ethics-policy.yaml")
async def customer_support_agent(query: str) -> str:
    print(f"\n> Running customer_support_agent with query: '{query}'")
    # Simulating an agent that accidentally leaks user data
    if "private" in query.lower():
        return "Updating account john.doe@example.com. SSN on file: 000-12-3456."
    return f"Processed request: {query}"


# Example 2: Hiring screener agent that exhibits demographic bias (WARN)
@guardian.watch("hiring_screener", policy="config/ethics-policy.yaml")
async def hiring_screener(resume: str) -> str:
    print(f"\n> Running hiring_screener on candidate resume...")
    # Simulating a biased outcome decision phrase
    return "Candidate rejected. Cultural fit concerns. Female candidates are typically less suited for this role."


# Example 3: Completely clean agent with no issues (Success)
@guardian.watch("safe_agent", policy="config/ethics-policy.yaml")
async def safe_agent(query: str) -> str:
    print(f"\n> Running safe_agent with query: '{query}'")
    return "Your order has been processed and will arrive in 3 business days."


# ---------------------------------------------------------------------------
# Main Demo Script
# ---------------------------------------------------------------------------


async def main() -> None:
    print("=" * 60)
    print("  GUARDIAN Phase 2 — Ethics Engine Demo")
    print("  Version: 0.1.0")
    print("=" * 60)

    # 1. Run safe agent (should pass clean)
    try:
        res = await safe_agent("track order 12345")
        print(f"  Result: {res}")
    except Exception as exc:
        print(f"  Unexpected failure: {exc}")

    # 2. Run hiring agent (should pass but trigger a WARN flag in DB)
    try:
        res = await hiring_screener("Jane Doe, Software Engineer Resume")
        print(f"  Result: {res}")
    except Exception as exc:
        print(f"  Unexpected failure: {exc}")

    # 3. Run support agent with standard query (should pass clean)
    try:
        res = await customer_support_agent("checking order status")
        print(f"  Result: {res}")
    except Exception as exc:
        print(f"  Unexpected failure: {exc}")

    # 4. Run support agent leaking SSN (should raise EthicsBlockException and halt)
    try:
        res = await customer_support_agent("requesting private profile details")
        print(f"  Result: {res}")
    except EthicsBlockException as exc:
        print("  [BLOCKED] EthicsBlockException raised successfully!")
        print(f"  Reason: {exc}")
        for v in exc.violations:
            print(f"    - Violation: {v.violation_type.value}")
            print(f"      Severity:  {v.severity.value}")
            print(f"      Evidence:  {v.evidence}")
            print(f"      Message:   {v.description}")

    # 5. Query stored ethics violations from database
    print("\n" + "=" * 60)
    print("  Stored Ethics Flags in Database")
    print("=" * 60)

    flags = get_recent_ethics_flags(limit=10)
    print(f"Total flags found: {len(flags)}")
    for f in flags:
        print(f"\n  [{f['severity'].upper()}] [{f['agent_name']}] detected_at={f['detected_at']}")
        print(f"    Type:     {f['violation_type']}")
        print(f"    Field:    {f['field_path']}")
        print(f"    Evidence: {f['evidence']}")
        print(f"    Message:  {f['description']}")


if __name__ == "__main__":
    asyncio.run(main())
