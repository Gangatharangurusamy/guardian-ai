#!/usr/bin/env python3
"""GUARDIAN Phase 1 — Basic Usage Example.

Demonstrates:
1. Initializing GUARDIAN with a local SQLite database
2. Decorating agent functions with @guardian.watch()
3. Capturing successful calls, failed calls, and nested calls
4. Querying stored traces with list_traces() and get_recent_failures()

Run:
    python examples/basic_usage.py
"""

import json
import random
import time

import guardian
from guardian.store.reader import get_recent_failures, list_traces


# ── 1. Initialize GUARDIAN ────────────────────────────────────
# This sets up the SQLite database and wires @watch to auto-persist traces.
# Without this call, traces would only be logged to stdout.
guardian.init()

print("=" * 60)
print("  GUARDIAN Phase 1 — Basic Usage Demo")
print(f"  Version: {guardian.__version__}")
print("=" * 60)
print()


# ── 2. Define agent functions ─────────────────────────────────


@guardian.watch("research_agent")
def research_agent(query: str) -> dict:
    """Simulates a research agent that searches and summarizes."""
    # Simulate some work
    time.sleep(0.1)
    return {
        "query": query,
        "results": [
            {"title": "Result 1", "relevance": 0.95},
            {"title": "Result 2", "relevance": 0.87},
        ],
        "summary": f"Found 2 relevant results for '{query}'.",
    }


@guardian.watch("math_agent")
def math_agent(expression: str) -> float:
    """Simulates a math agent that evaluates expressions."""
    time.sleep(0.05)
    # Simple eval for demo (never do this in production!)
    return eval(expression)  # noqa: S307


@guardian.watch("unreliable_agent")
def unreliable_agent(task: str) -> str:
    """Simulates an agent that sometimes fails."""
    time.sleep(0.05)
    if random.random() < 0.5:
        raise ConnectionError(f"Failed to connect to LLM service for task: {task}")
    return f"Completed: {task}"


@guardian.watch("orchestrator_agent")
def orchestrator(query: str) -> dict:
    """A higher-level agent that calls other agents (nested tracing)."""
    research = research_agent(query)
    calculation = math_agent("2 + 2")
    return {
        "research": research,
        "calculation": calculation,
        "combined": f"Research found {len(research['results'])} results, 2+2={calculation}",
    }


# -- 3. Run the agents ----------------------------------------

print("> Running research_agent...")
result = research_agent("quantum computing breakthroughs 2025")
print(f"  Result: {result['summary']}")
print()

print("> Running math_agent...")
result = math_agent("42 * 3 + 7")
print(f"  Result: {result}")
print()

print("> Running orchestrator (nested calls)...")
result = orchestrator("AI safety")
print(f"  Result: {result['combined']}")
print()

print("> Running unreliable_agent (may fail)...")
for i in range(4):
    try:
        result = unreliable_agent(f"task_{i}")
        print(f"  Task {i}: {result}")
    except ConnectionError as e:
        print(f"  Task {i}: FAILED - {e}")
print()


# -- 4. Query stored traces -----------------------------------

print("=" * 60)
print("  Stored Traces")
print("=" * 60)
print()

all_traces = list_traces()
print(f"Total traces stored: {len(all_traces)}")
print()

for trace in all_traces:
    status_icon = "[OK]" if trace["status"] == "success" else "[ERROR]"
    print(
        f"  {status_icon} [{trace['agent_name']}] "
        f"session={trace['session_id'][:8]}... "
        f"status={trace['status']} "
        f"duration={trace['duration_ms']}ms "
        f"calls={len(trace['calls'])}"
    )

print()

# -- 5. Query failures ----------------------------------------

failures = get_recent_failures()
print(f"Recent failures: {len(failures)}")
for f in failures:
    print(
        f"  [ERROR] [{f['agent_name']}] "
        f"session={f['session_id'][:8]}... "
        f"error={f['calls'][0]['error'] if f['calls'] else 'unknown'}"
    )

print()

# -- 6. Show a full trace as JSON ------------------------------

if all_traces:
    print("=" * 60)
    print("  Full Trace (first entry)")
    print("=" * 60)
    print(json.dumps(all_traces[0], indent=2, default=str))

print()
print("Guardian Phase 1 demo complete!")
