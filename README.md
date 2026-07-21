# GUARDIAN
**A Responsible Agent Runtime for AI agents.**

GUARDIAN provides observability, ethics enforcement, and auto-remediation for AI agents. It wraps around your existing agent code to trace execution, intercept harmful outputs (e.g. prompt injection, PII leaks), and automatically attempt recovery using a configurable policy.

It includes a built-in FastAPI backend, a real-time dark-theme dashboard, and generates EU AI Act / OWASP LLM Top 10 compliance reports on demand.

## Features
| Feature | Description |
|---|---|
| **Tracing** | Captures start/end times, durations, status, and full tool call history. |
| **Diagnostics** | Evaluates failures using heuristics (e.g. tool loops) or an LLM judge. |
| **Ethics Watchdog** | Intercepts PII leaks, bias, and prompt injection (LOG, WARN, BLOCK). |
| **Auto-Recovery** | Configurable policies (retry, escalate, log_only, human_approval) based on failure type. |
| **Real-time Dashboard** | Monitor traces and ethics flags live via WebSockets. |
| **Compliance Export** | Generate EU AI Act and OWASP Top 10 JSON reports. |

## Quick Start

### Installation

```bash
# End users:
pip install guardian-ai[all]

# Contributors (exact pinned versions):
pip install -r requirements-lock.txt
```

### Usage (3 lines of code)

Just wrap your main agent function with `@watch(agent_name="my_agent")`:

```python
from guardian.sdk import watch

@watch(agent_name="assistant")
def run_agent(prompt: str) -> str:
    # Your existing agent code here...
    return "Agent response"
```

### Dashboard

Start the GUARDIAN backend:
```bash
uvicorn guardian.api.main:app --reload
```
Then open `http://localhost:8000/dashboard` in your browser.

## Architecture

```text
+-------------------+       +--------------------+       +-------------------+
|  Your Agent Code  | <---> |   GUARDIAN @watch  | <---> |  Recovery Engine  |
+-------------------+       +--------------------+       +-------------------+
                                      |
                                      v
                            +--------------------+
                            |    SQLite Store    |
                            +--------------------+
                                      |
                                      v
                            +--------------------+
                            |  FastAPI + WS API  |
                            +--------------------+
                                      |
                                      v
                            +--------------------+
                            |   Web Dashboard    |
                            +--------------------+
```

## Configuration
Configure the recovery policy by editing `config/recovery-policy.yaml`. You can map specific failure types to actions (`retry`, `escalate`, `log_only`, `human_approval`).

Requires `OPENAI_API_KEY` in the environment if your policy uses an OpenAI model for the LLM Diagnoser.

## Compliance
GUARDIAN maps detected events to the OWASP LLM Top 10 and determines an EU AI Act risk level (minimal, limited, high) based on ethics blocks and escalations.
Download reports directly from the Dashboard or via `GET /api/v1/traces/{session_id}/compliance`.

## Contributing
Run tests with `pytest tests/ -v`. Code style is enforced with `ruff check guardian/`.

## License
MIT License.
