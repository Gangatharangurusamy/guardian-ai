# GUARDIAN — Phase 1: SDK Interceptor + Trace Store

> **GUARDIAN** is a Responsible Agent Runtime — it watches, diagnoses, and auto-remediates AI agent behavior. Phase 1 provides the foundational tracing and storage layer.

## What's in Phase 1

| Module | Purpose |
|--------|---------|
| **SDK Interceptor** (`guardian/sdk/`) | `@guardian.watch()` decorator that traces any agent function (sync/async) with zero code changes |
| **Trace Store** (`guardian/store/`) | SQLite-backed persistence with query functions for trace retrieval |

## Quick Start

### 1. Install

```bash
# Clone the repository
git clone <repo-url>
cd guardian

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

### 2. Run the Example

```bash
python examples/basic_usage.py
```

This will:
- Initialize a local SQLite database (`guardian.db`)
- Run several simulated agent functions (including one that fails)
- Display all captured traces and failures

### 3. Use in Your Own Code

```python
import guardian

# Initialize once at startup (enables DB storage)
guardian.init()

# Decorate any agent function
@guardian.watch("my_agent")
def my_agent(prompt: str) -> str:
    # Your existing agent code — unchanged
    return call_llm(prompt)

# Call normally — tracing happens automatically
result = my_agent("What is the meaning of life?")

# Query stored traces
from guardian.store import list_traces, get_recent_failures

all_traces = list_traces()
failures = get_recent_failures()
```

### Without Database (Logging Only)

If you skip `guardian.init()`, the decorator still works — traces are logged via Python's `logging` module but not persisted:

```python
import guardian

@guardian.watch("my_agent")
def my_agent(prompt: str) -> str:
    return "response"

# Traces logged to stdout at INFO level
my_agent("hello")
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run just decorator tests
pytest tests/test_decorator.py -v

# Run just store tests
pytest tests/test_store.py -v
```

## Project Structure

```
guardian/
├── __init__.py          # Package root — exposes watch() and init()
├── sdk/
│   ├── __init__.py
│   ├── decorator.py     # @watch() decorator factory
│   ├── context.py       # TraceContext + ContextVar isolation
│   ├── capture.py       # CapturedCall dataclass + helpers
│   └── serializer.py    # Trace → JSON serialization
└── store/
    ├── __init__.py
    ├── models.py         # SQLAlchemy ORM models
    ├── db.py             # Engine + session management
    ├── writer.py         # Async queue writer + sync writer
    └── reader.py         # Query functions
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `GUARDIAN_DB_URL` | `sqlite:///guardian.db` | Database connection URL. Set to a PostgreSQL URL for production use. |

## Trace Event Schema

Every traced call produces a structured event:

```json
{
  "session_id": "uuid4",
  "agent_name": "my_agent",
  "started_at": "2025-01-01T00:00:00+00:00",
  "ended_at": "2025-01-01T00:00:01+00:00",
  "duration_ms": 1000.0,
  "status": "success",
  "calls": [
    {
      "function": "my_agent",
      "args_preview": "('prompt',)",
      "result_preview": "'response'",
      "duration_ms": 1000.0,
      "error": null,
      "retry_count": 0,
      "estimated_tokens": 42
    }
  ],
  "metadata": {}
}
```

## What's Next

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | SDK Interceptor + Trace Store | ✅ **This phase** |
| Phase 2 | Ethics Engine (PII, bias, YAML policy) | 🔲 Planned |
| Phase 3 | Diagnosis Agent + Recovery Engine | 🔲 Planned |
| Phase 4 | FastAPI, Dashboard, Compliance, Docker | 🔲 Planned |

## License

MIT
