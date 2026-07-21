"""GUARDIAN FastAPI application.

Startup:
  1. Initialises the database (creates tables if needed).
  2. Registers the WebSocket broadcast as the post-write callback in
     guardian.store.writer._post_write_callback (Correction 1 — no monkey-patching).
  3. Starts the WebSocket keepalive task.

Shutdown:
  1. Clears the post-write callback.
  2. Stops the keepalive task.

Routes:
  /health          -- health check
  /metrics         -- Prometheus text format
  /api/v1/traces/* -- trace CRUD
  /api/v1/ethics/* -- ethics flag queries
  /api/v1/recovery/* -- recovery action queries
  /ws/traces       -- live WebSocket stream
  /dashboard       -- dark-theme monitoring UI
  /docs            -- FastAPI Swagger UI (auto)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger("guardian")

# ---------------------------------------------------------------------------
# Path resolution (CWD-independent)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent                       # guardian/api/
_DASHBOARD = _HERE.parent / "dashboard"             # guardian/dashboard/
_STATIC_DIR = _DASHBOARD / "static"
_TEMPLATES_DIR = _DASHBOARD / "templates"

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialise DB and register WebSocket callback."""
    # --- Startup ---
    from guardian.store.db import init_db
    init_db()
    logger.info("GUARDIAN API: database initialised.")

    # Register WebSocket broadcast as post-write callback (Correction 1)
    import guardian.store.writer as _writer

    def _ws_callback(trace_event: dict) -> None:
        """Schedule broadcast on the running event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(manager.broadcast(trace_event))
        except Exception as exc:
            logger.warning("GUARDIAN API: WS callback scheduling failed: %s", exc)

    _writer._post_write_callback = _ws_callback
    logger.info("GUARDIAN API: WebSocket post-write callback registered.")

    await manager.start_keepalive()

    yield  # ← application runs

    # --- Shutdown ---
    _writer._post_write_callback = None
    await manager.stop_keepalive()
    logger.info("GUARDIAN API: shutdown complete.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="GUARDIAN",
    description="Responsible AI Agent Runtime — monitoring, ethics, and compliance API.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
_allowed_origins_env = os.getenv("GUARDIAN_ALLOWED_ORIGINS", "")
_allowed_origins = (
    [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
    if _allowed_origins_env
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# API key middleware (optional)
# ---------------------------------------------------------------------------

_API_KEY = os.getenv("GUARDIAN_API_KEY", "")


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Check X-Guardian-Key header for /api/v1/* routes if GUARDIAN_API_KEY is set."""
    if _API_KEY and request.url.path.startswith("/api/v1/"):
        key = request.headers.get("X-Guardian-Key", "")
        if key != _API_KEY:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing X-Guardian-Key header"},
            )
    return await call_next(request)

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------

from guardian.api.routes.ethics import router as ethics_router  # noqa: E402
from guardian.api.routes.health import router as health_router  # noqa: E402
from guardian.api.routes.recovery import router as recovery_router  # noqa: E402
from guardian.api.routes.traces import router as traces_router  # noqa: E402

app.include_router(health_router)                           # /health, /metrics (no prefix)
app.include_router(traces_router, prefix="/api/v1")         # /api/v1/traces/*
app.include_router(ethics_router, prefix="/api/v1")         # /api/v1/ethics/*
app.include_router(recovery_router, prefix="/api/v1")       # /api/v1/recovery/*

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

from guardian.api.ws import manager, ws_traces_endpoint  # noqa: E402


@app.websocket("/ws/traces")
async def ws_traces(websocket: WebSocket) -> None:
    """Live WebSocket stream of trace events."""
    await ws_traces_endpoint(websocket)

# ---------------------------------------------------------------------------
# Static files and dashboard templates
# ---------------------------------------------------------------------------

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:
    logger.warning("GUARDIAN API: static directory not found at %s", _STATIC_DIR)

if _TEMPLATES_DIR.exists():
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html")

    @app.get("/dashboard/traces", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_traces(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "traces.html")

    @app.get("/dashboard/ethics", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_ethics(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "ethics.html")
else:
    logger.warning("GUARDIAN API: templates directory not found at %s", _TEMPLATES_DIR)
