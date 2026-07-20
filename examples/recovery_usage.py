"""GUARDIAN Phase 3 — Recovery Usage Examples.

Demonstrates the Diagnosis Agent + Recovery Engine with three failure scenarios:
1. Tool loop — agent calls the same tool repeatedly with no progress
2. Timeout — agent exceeds the configured duration threshold
3. Repeated error — same exception raised multiple times

Run with:
    python examples/recovery_usage.py

Note: This example uses auto_retry mode to avoid blocking on a CLI prompt.
Change mode to human_in_loop in config/recovery-policy.yaml to see
the approval gate in action.

Requires an LLM API key in .env (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.).
If no key is available, the watchdog will detect failures but diagnosis
will raise a RuntimeError (caught by the decorator and logged as a warning).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# Ensure project root is on path when running directly
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

import guardian
from guardian.store.reader import get_recent_recovery_actions

# Load API keys from .env
load_dotenv()

# Show GUARDIAN logs
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s",
)

# Initialize GUARDIAN (creates/verifies database tables)
guardian.init()


# ---------------------------------------------------------------------------
# Example 1: Agent stuck in a tool loop
# ---------------------------------------------------------------------------

# Use a temporary auto_retry policy override for the example
# (so we don't block on a CLI prompt)
_POLICY = "config/recovery-policy.yaml"


async def _fake_search(query: str) -> str:
    """Simulate a search tool that always returns the same result."""
    return "No results found"


@guardian.watch(
    "looping_agent",
    recovery_policy=_POLICY,
)
async def looping_agent(query: str) -> str:
    """Agent that gets stuck calling the same tool 4 times with no progress."""
    result = ""
    for _ in range(4):
        result = await _fake_search(query)
    return result


# ---------------------------------------------------------------------------
# Example 2: Agent that times out
# ---------------------------------------------------------------------------

@guardian.watch(
    "slow_agent",
    recovery_policy=_POLICY,
)
async def slow_agent(query: str) -> str:
    """Agent that simulates a very slow execution (500ms — below CLI timeout,
    above detector threshold when timeout_ms is set low for the example).

    To see timeout detection, lower timeout_ms in recovery-policy.yaml.
    """
    await asyncio.sleep(0.5)
    return "finally done"


# ---------------------------------------------------------------------------
# Example 3: Agent that raises a repeated error
# ---------------------------------------------------------------------------

_error_count: dict[str, int] = {}


@guardian.watch(
    "error_agent",
    recovery_policy=_POLICY,
)
async def error_agent(query: str) -> str:
    """Agent that raises ConnectionError to trigger repeated error detection."""
    raise ConnectionError("LLM provider unreachable")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def main() -> None:
    print("\n" + "="*60)
    print("  GUARDIAN Phase 3 — Recovery Engine Demo")
    print("="*60 + "\n")

    # --- Example 1: Tool Loop ---
    print("[ Example 1 ] Running looping_agent...")
    try:
        result = await looping_agent("search for something")
        print(f"  Result: {result!r}")
    except Exception as e:
        print(f"  Agent raised: {type(e).__name__}: {e}")

    await asyncio.sleep(0.2)

    # --- Example 2: Slow Agent ---
    print("\n[ Example 2 ] Running slow_agent...")
    try:
        result = await slow_agent("slow query")
        print(f"  Result: {result!r}")
    except Exception as e:
        print(f"  Agent raised: {type(e).__name__}: {e}")

    await asyncio.sleep(0.2)

    # --- Example 3: Error Agent ---
    print("\n[ Example 3 ] Running error_agent (will raise)...")
    try:
        result = await error_agent("error query")
        print(f"  Result: {result!r}")
    except Exception as e:
        print(f"  Agent raised: {type(e).__name__}: {e}")

    await asyncio.sleep(0.2)

    # --- Query stored recovery records ---
    print("\n" + "="*60)
    print("  Recovery Records from SQLite")
    print("="*60)
    records = get_recent_recovery_actions(limit=10)
    if records:
        for r in records:
            print(
                f"  [{r['recovered_at'][:19]}] "
                f"agent={r['agent_name']} "
                f"failure={r['failure_type']} "
                f"action={r['action_taken']} "
                f"success={r['success']}"
            )
    else:
        print("  No recovery records found.")
        print("  (Watchdog only runs when a failure is detected or retry_count > 0)")

    print("\nDone.\n")


if __name__ == "__main__":
    asyncio.run(main())
