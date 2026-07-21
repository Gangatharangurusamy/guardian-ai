"""Tests for GET /api/v1/ethics/* endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _seed_flag(session_id: str, severity: str, violation_type: str):
    from datetime import datetime, timezone
    from guardian.store.db import get_session
    from guardian.store.models import EthicsFlagRecord
    with get_session() as sess:
        sess.add(EthicsFlagRecord(
            session_id=session_id,
            agent_name="ethics_agent",
            violation_type=violation_type,
            severity=severity,
            description=f"{violation_type} detected",
            evidence="evidence text",
            field_path="output.content",
            confidence=0.9,
            metadata_json="{}",
            detected_at=datetime.now(timezone.utc),
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
    _seed_flag("eth-001", severity="warn",  violation_type="pii_leak")
    _seed_flag("eth-002", severity="block", violation_type="ethics_violation")
    _seed_flag("eth-003", severity="log",   violation_type="bias_detected")


class TestEthicsFlags:
    def test_returns_list(self, client):
        resp = client.get("/api/v1/ethics/flags")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_severity_filter_block(self, client):
        resp = client.get("/api/v1/ethics/flags?severity=block")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert all(f["severity"] == "block" for f in data)


class TestEthicsSummary:
    def test_summary_structure(self, client):
        resp = client.get("/api/v1/ethics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "by_severity" in data
        assert "by_type" in data
        assert "recent_24h" in data

    def test_summary_counts(self, client):
        resp = client.get("/api/v1/ethics/summary").json()
        assert resp["total"] >= 3
        assert resp["by_severity"].get("warn", 0) >= 1
        assert resp["by_severity"].get("block", 0) >= 1
