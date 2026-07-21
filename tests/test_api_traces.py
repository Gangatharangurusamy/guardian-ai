"""Tests for GET /api/v1/traces/* endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ── Shared DB seed helpers ────────────────────────────────────────────────────

def _seed_trace(session_id: str, agent_name: str = "test_agent", status: str = "success"):
    from datetime import datetime, timezone
    from guardian.store.db import get_session
    from guardian.store.models import TraceEventRecord
    with get_session() as sess:
        sess.add(TraceEventRecord(
            session_id=session_id,
            agent_name=agent_name,
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            duration_ms=42,
            status=status,
            calls_json="[]",
            metadata_json="{}",
        ))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    """Use an isolated in-memory SQLite database for each test."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    import guardian.store.db as db_module

    db_module.reset_engine()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_module._engine = engine
    db_module._SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    
    from guardian.store.models import Base
    Base.metadata.create_all(engine)
    monkeypatch.setenv("GUARDIAN_DB_URL", "sqlite:///:memory:")
    yield
    db_module.reset_engine()


@pytest.fixture
def client(setup_test_db):
    """Start TestClient with lifespan (initialises DB) for all tests in module."""
    from guardian.api.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def seed_db(client):
    """Seed the DB once before each test runs."""
    _seed_trace("trace-aaa", agent_name="alpha_agent", status="success")
    _seed_trace("trace-bbb", agent_name="beta_agent",  status="error")
    _seed_trace("trace-ccc", agent_name="alpha_agent", status="success")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestListTraces:
    def test_returns_list(self, client):
        resp = client.get("/api/v1/traces")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_filter_by_agent_name(self, client):
        resp = client.get("/api/v1/traces?agent_name=alpha_agent")
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["agent_name"] == "alpha_agent" for t in data)
        assert len(data) >= 2

    def test_offset_pagination(self, client):
        all_resp  = client.get("/api/v1/traces?limit=100").json()
        page1     = client.get("/api/v1/traces?limit=2&offset=0").json()
        page2     = client.get("/api/v1/traces?limit=2&offset=2").json()
        # Pages must not overlap
        ids1 = {t["session_id"] for t in page1}
        ids2 = {t["session_id"] for t in page2}
        assert ids1.isdisjoint(ids2)


class TestGetTrace:
    def test_returns_correct_trace(self, client):
        resp = client.get("/api/v1/traces/trace-aaa")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "trace-aaa"
        assert data["agent_name"] == "alpha_agent"

    def test_returns_404_for_unknown(self, client):
        resp = client.get("/api/v1/traces/does-not-exist-xyz")
        assert resp.status_code == 404


class TestGetFailures:
    def test_returns_only_failures(self, client):
        resp = client.get("/api/v1/traces/failures")
        assert resp.status_code == 200
        data = resp.json()
        # All returned traces must have non-success status
        for t in data:
            assert t["status"] != "success"
