"""Database engine and session management for GUARDIAN.

Creates the SQLAlchemy engine (defaulting to SQLite, overridable via
GUARDIAN_DB_URL env var for PostgreSQL), provides idempotent table
creation, and a session context manager.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from guardian.store.models import Base

logger = logging.getLogger("guardian")

# Module-level engine and session factory, initialized lazily.
_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine(db_url: str | None = None) -> Engine:
    """Get or create the SQLAlchemy engine.

    Uses the provided URL, or falls back to the GUARDIAN_DB_URL environment
    variable, or defaults to a local SQLite file.

    Args:
        db_url: Optional database URL. If None, reads from GUARDIAN_DB_URL
            env var, then falls back to 'sqlite:///guardian.db'.

    Returns:
        The SQLAlchemy Engine instance.
    """
    global _engine
    if _engine is not None:
        return _engine

    url = db_url or os.environ.get("GUARDIAN_DB_URL", "sqlite:///guardian.db")
    logger.info("GUARDIAN: Creating database engine with URL: %s", url)

    # SQLite-specific: enable WAL mode for better concurrency
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    return _engine


def init_db(db_url: str | None = None) -> Engine:
    """Initialize the database: create the engine and all tables.

    This function is idempotent — safe to call on every startup.
    Tables that already exist are left untouched.

    Args:
        db_url: Optional database URL override.

    Returns:
        The SQLAlchemy Engine instance.
    """
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    logger.info("GUARDIAN: Database tables created/verified.")
    return engine


def _get_session_factory() -> sessionmaker[Session]:
    """Get or create the session factory.

    Returns:
        A sessionmaker bound to the current engine.

    Raises:
        RuntimeError: If the engine has not been initialized yet.
    """
    global _SessionFactory
    if _SessionFactory is None:
        engine = get_engine()
        _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager that yields a database session.

    Commits on clean exit, rolls back on exception, and always closes
    the session.

    Yields:
        A SQLAlchemy Session instance.

    Raises:
        RuntimeError: If the database has not been initialized.
    """
    factory = _get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Reset the module-level engine and session factory.

    Primarily used in tests to ensure a clean state between test runs.
    """
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
