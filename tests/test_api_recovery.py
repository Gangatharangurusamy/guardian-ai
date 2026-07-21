"""Tests for GET /api/v1/recovery/* and POST /api/v1/recovery/approve/*."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _seed_recovery(session_id: str, action: str, failure_type: str, success: bool = True):
    from datetime import datetime, timezone
    from guardian.store.db import get_session
    from guardian.store.models import RecoveryActionRecord
    with get_session() as sess:
        sess.add(RecoveryActionRecord(
            session_id=session_id,
            agent_name="recovery_agent",
            failure_type=failure_type,
            action_taken=action,
            success=success,
            retries_attempted=1,
            metadata_json="{}",
            recovered_at=datetime.now(timezone.utc),
        ))


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
    from guardian.api.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def seed_db(client):
    _seed_recovery("rec-001", action="retry",     failure_type="tool_loop",    success=True)
    _seed_recovery("rec-002", action="log_only",  failure_type="ethics_block", success=True)
    _seed_recovery("rec-003", action="escalate",  failure_type="tool_loop",    success=False)


class TestRecoveryActions:
    def test_returns_list(self, client):
        resp = client.get("/api/v1/recovery/actions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 3


class TestRecoverySummary:
    def test_summary_success_rate(self, client):
        resp = client.get("/api/v1/recovery/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "success_rate" in data
        assert "by_action" in data
        assert "by_failure_type" in data
        # 2 out of 3 seeded as success
        assert data["total"] >= 3
        assert 0.0 <= data["success_rate"] <= 1.0


class TestRecoveryApprove:
    def test_approve_returns_approved(self, client):
        resp = client.post("/api/v1/recovery/approve/rec-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["session_id"] == "rec-001"
