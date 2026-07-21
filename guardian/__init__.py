"""GUARDIAN — A Responsible Agent Runtime for AI Agents.

GUARDIAN is an open-source framework that traces, diagnoses, and
auto-remediates AI agent failures. Add one decorator to your agent
function and get structured traces, ethics checks, and audit logging.

Quick Start::

    import guardian

    # Optional: wire traces to the database
    guardian.init()

    @guardian.watch("my_agent")
    def run_agent(query: str) -> str:
        return "response"

    result = run_agent("hello")

    # Query stored traces
    from guardian.store import list_traces
    traces = list_traces()
"""

from __future__ import annotations

__version__ = "0.1.0"

import logging

from guardian.sdk.decorator import _set_default_on_trace, watch
from guardian.store.db import init_db
from guardian.store.writer import write_sync

logger = logging.getLogger("guardian")

# Track whether init() has been called
_initialized: bool = False


def init(db_url: str | None = None) -> None:
    """Initialize GUARDIAN: set up the database and wire trace storage.

    Call this once at application startup to enable automatic persistence
    of traces to the database. If never called, the ``@watch()`` decorator
    still works but only logs traces (no database dependency).

    Args:
        db_url: Optional database URL override. Defaults to the
            ``GUARDIAN_DB_URL`` environment variable, then to
            ``sqlite:///guardian.db``.

    Example::

        import guardian

        # Use default SQLite
        guardian.init()

        # Or specify PostgreSQL
        guardian.init(db_url="postgresql://user:pass@host/guardian")
    """
    global _initialized

    # Initialize the database engine and create tables
    init_db(db_url)

    # Wire the decorator's default callback to the synchronous writer.
    # We use write_sync here because the decorator may be called from
    # both sync and async contexts, and write_sync is safe in both.
    # The async TraceWriter can be used explicitly for high-throughput
    # scenarios via on_trace=writer.write.
    _set_default_on_trace(write_sync)

    _initialized = True
    logger.info("GUARDIAN v%s initialized. Traces will be stored to database.", __version__)


def is_initialized() -> bool:
    """Check whether guardian.init() has been called.

    Returns:
        True if init() has been called successfully.
    """
    return _initialized


__all__ = [
    "__version__",
    "init",
    "is_initialized",
    "watch",
]
