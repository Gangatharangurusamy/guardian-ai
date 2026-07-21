"""Tests for GET /health and GET /metrics endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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
def client():
    """TestClient with lifespan for the GUARDIAN FastAPI app."""
    from guardian.api.main import app
    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_db_connected(self, client):
        data = resp = client.get("/health").json()
        assert data["db_connected"] is True

    def test_health_response_shape(self, client):
        data = client.get("/health").json()
        assert "status" in data
        assert "version" in data
        assert "db_connected" in data
        assert data["status"] == "healthy"


class TestMetricsEndpoint:
    def test_metrics_returns_200(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_contains_guardian_traces_total(self, client):
        resp = client.get("/metrics")
        assert "guardian_traces_total" in resp.text
