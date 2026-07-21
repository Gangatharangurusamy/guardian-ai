# GUARDIAN Phase 4

This document summarizes the changes introduced in Phase 4.

## What was added
1. **FastAPI Backend (`guardian/api/`)**: REST endpoints for traces, ethics flags, and recovery actions. Exposes Prometheus metrics and a health check.
2. **WebSocket Streaming (`guardian/api/ws.py`)**: Live broadcasting of trace events to the dashboard without monkey-patching Phase 1 files.
3. **Web Dashboard (`guardian/dashboard/`)**: A dark-theme, responsive CSS Grid UI. Includes live updating charts, stat cards, and paginated tables.
4. **Compliance Exporter (`guardian/compliance/`)**: Maps GUARDIAN events to EU AI Act risk levels and the OWASP LLM Top 10, generating comprehensive JSON reports.
5. **Docker & Deploy (`Dockerfile`, `docker-compose.yml`, `scripts/deploy.sh`)**: Ready for production deployment on EC2.
6. **PyPI Config**: Updated `pyproject.toml` with `api`, `recovery`, `dev`, and `all` optional dependency groups.

## Installation

```bash
# End users:
pip install guardian-ai[all]

# Contributors (exact pinned versions):
pip install -r requirements-lock.txt
```

## API Reference Summary

- `GET /health` - System health
- `GET /metrics` - Prometheus metrics
- `GET /api/v1/traces` - List traces (supports `agent_name`, `limit`, `offset`)
- `GET /api/v1/traces/failures` - Recent failures
- `GET /api/v1/traces/{id}/compliance` - Download EU AI Act + OWASP JSON
- `GET /api/v1/ethics/flags` - List ethics flags
- `GET /api/v1/ethics/summary` - Aggregate ethics stats
- `GET /api/v1/recovery/summary` - Aggregate recovery stats
- `WS  /ws/traces` - Live event stream

## Dashboard Guide
Start the server: `uvicorn guardian.api.main:app --reload`
- **`/dashboard`**: High-level overview, live charts, and 10 most recent events.
- **`/dashboard/traces`**: Full paginated trace history with filters and Compliance download buttons.
- **`/dashboard/ethics`**: Detailed ethics violation logs with full evidence inspection.
