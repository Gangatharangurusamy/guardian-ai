# GUARDIAN Phase 3 — Diagnosis Agent + Recovery Engine

Phase 3 adds autonomous failure diagnosis and self-healing recovery to GUARDIAN. After every agent run, GUARDIAN reads the captured `TraceEvent`, detects failure patterns without any LLM, optionally sends them to an LLM for root-cause analysis, and executes a configured recovery action.

---

## What Phase 3 Built

| Component | Description |
|---|---|
| `guardian/watchdog/` | **FailureDetector** — pure-Python heuristics. **Diagnoser** — LLM-powered root cause analysis. |
| `guardian/recovery/` | **RecoveryEngine** — orchestrates actions. **CircuitBreaker** — per-provider failure tracking. **ApprovalGate** — human-in-the-loop. |
| `guardian/store/` | `RecoveryActionRecord` table + reader functions. |
| `guardian/sdk/decorator.py` | `recovery_policy=` parameter on `@guardian.watch`. |

---

## Quick Start

Add `recovery_policy=` to any `@guardian.watch` decorator:

```python
import guardian

guardian.init()

@guardian.watch(
    "my_agent",
    policy="config/ethics-policy.yaml",           # Phase 2 (optional)
    recovery_policy="config/recovery-policy.yaml", # Phase 3 (new)
)
async def my_agent(query: str) -> str:
    return await call_llm(query)
```

That's it. GUARDIAN will automatically:
1. Detect failure patterns after each run
2. Send the trace to your configured LLM for diagnosis
3. Execute the appropriate recovery action

---

## Configuration

Edit `config/recovery-policy.yaml`:

```yaml
diagnosis:
  enabled: true
  model: gpt-4o          # Change to any LiteLLM-supported model
  max_trace_chars: 8000

failure_detection:
  loop_threshold: 3
  timeout_ms: 30000
  error_repeat_threshold: 2

recovery:
  mode: human_in_loop    # See Recovery Modes below

  actions:
    tool_loop:       switch_model
    timeout:         retry
    repeated_error:  escalate
    ethics_block:    escalate
    confidence_drop: switch_model
    unknown:         escalate

  fallback_model: claude-3-5-sonnet-20241022
```

---

## Recovery Modes

### `human_in_loop` (default — safest)

Pauses the agent and prints a formatted approval prompt to the terminal:

```
═══════════════════════════════════════════════════════════
  GUARDIAN — Recovery Approval Required
═══════════════════════════════════════════════════════════
  Agent:      my_agent
  Session:    3f2a1b9c-...
  Failure:    The search tool was called 4 times with identical results
  Action:     Switch to fallback model 'claude-3-5-sonnet-20241022' and retry
  Confidence: 95%
───────────────────────────────────────────────────────────
  Approve? [y/n] (timeout in 300s):
```

- `y` or `yes` → action executes
- anything else → rejected, agent stays paused
- no input within `timeout_seconds` → auto-escalates

### `notify_then_retry`

Logs the recovery action to the GUARDIAN logger, then executes it automatically. No human input required.

### `auto_retry`

Executes recovery silently. User sees only the final result. Use in high-throughput automated pipelines.

---

## Setting the Diagnosis Model

Change `diagnosis.model` in `config/recovery-policy.yaml` and add the matching key to `.env`:

| Provider | YAML model string | .env key |
|---|---|---|
| OpenAI | `gpt-4o` | `OPENAI_API_KEY=sk-...` |
| Anthropic | `claude-3-5-sonnet-20241022` | `ANTHROPIC_API_KEY=sk-ant-...` |
| Groq | `groq/llama3-70b-8192` | `GROQ_API_KEY=gsk_...` |
| Mistral | `mistral/mistral-large` | `MISTRAL_API_KEY=...` |

> **Note**: If no API key is found, GUARDIAN raises `RuntimeError` during diagnosis. The decorator catches this and logs a warning — your agent is **never crashed** by diagnosis failures.

---

## Failure Detection (No LLM Required)

The `FailureDetector` runs before any LLM call and uses pure-Python heuristics:

| Failure Type | Detection Logic |
|---|---|
| `tool_loop` | Same function called 3+ times with identical/empty results |
| `timeout` | Total or per-call duration exceeds threshold |
| `repeated_error` | Same error type raised 2+ times |
| `confidence_drop` | Result preview shrinks strictly on each call (hallucination proxy) |
| `ethics_block` | `EthicsBlockException` raised (Phase 2 integration) |

---

## Circuit Breaker

Prevents GUARDIAN from hammering a failing LLM provider:

```yaml
circuit_breaker:
  enabled: true
  failure_threshold: 5      # 5 failures within 60s = OPEN
  window_seconds: 60
  recovery_timeout_s: 120   # try again after 2 minutes
```

When a provider's circuit is OPEN, GUARDIAN automatically switches to `fallback_model` for the next recovery attempt.

---

## 2-Strike Ethics Rule

If the same ethics violation type is seen **twice in the same session**, GUARDIAN skips normal recovery and **escalates immediately** — regardless of what `actions.ethics_block` is set to. This prevents retrying requests that keep hitting the same ethical guardrail.

---

## Querying Recovery Records

```python
from guardian.store.reader import (
    get_recovery_actions,
    get_recent_recovery_actions,
    get_recovery_actions_by_agent,
)

# All recoveries for a specific session
records = get_recovery_actions("3f2a1b9c-...")

# Most recent 50 recovery attempts (across all agents)
recent = get_recent_recovery_actions(limit=50)

# Recovery history for a specific agent
agent_history = get_recovery_actions_by_agent("my_agent", limit=20)
```

---

## Running the Example

```bash
# Requires an LLM API key in .env
python examples/recovery_usage.py
```

This runs three agents demonstrating:
1. **Tool loop** — `looping_agent` calls the same search tool 4 times
2. **Timeout simulation** — `slow_agent` runs slowly
3. **Repeated error** — `error_agent` raises `ConnectionError`

---

## Running Tests

```bash
# All Phase 1 + 2 + 3 tests (should be ~88+ tests)
pytest tests/ -v

# Phase 3 tests only
pytest tests/test_detector.py tests/test_diagnoser.py \
       tests/test_circuit_breaker.py tests/test_approval.py \
       tests/test_recovery_engine.py -v
```

---

## Architecture

```
@guardian.watch(recovery_policy="config/recovery-policy.yaml")
    ↓
Agent runs → TraceEvent captured by Phase 1 SDK
    ↓
_should_run_watchdog() — skip if success and no retries
    ↓
FailureDetector.detect() — pure Python, no LLM
    ↓ (if signals found)
Diagnoser.diagnose() — sends trace + signals to LLM via LiteLLM
    ↓
RecoveryEngine.recover()
  ├── 2-strike ethics check
  ├── Circuit breaker check
  ├── ApprovalGate (if human_in_loop mode)
  ├── Execute action (retry / switch_model / pause / escalate / log_only)
  ├── Update circuit breaker
  └── Write RecoveryActionRecord to SQLite
    ↓
RecoveryOutcome returned (agent never crashes from watchdog errors)
```

---

## What Phase 4 Will Add

- FastAPI server exposing recovery records via REST API
- Web dashboard for viewing failures and approvals
- Webhook approval gate (real-time Slack/Teams integration)
- Slack/email escalation notifications
- Docker deployment

---

*Phase 3 of GUARDIAN — Diagnosis Agent + Recovery Engine. See PHASE1_README.md and PHASE2_README.md for earlier phases.*
