"""SQLAlchemy 2.0 ORM models for GUARDIAN trace storage.

Defines the database schema for persisting TraceEvent records.
Uses modern DeclarativeBase style for forward compatibility.

Future tables (Phase 2/3):
    - EthicsFlagRecord: Stores ethics/bias/PII violation flags per trace.
    - RecoveryActionRecord: Stores auto-recovery actions taken by the Diagnosis Agent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, Text, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all GUARDIAN ORM models."""

    pass


class TraceEventRecord(Base):
    """Persisted record of a single agent trace event.

    Stores the serialized trace produced by the SDK interceptor, including
    the full call history as a JSON string. Indexed on session_id and
    agent_name for efficient lookups and filtering.

    Attributes:
        id: Auto-incrementing primary key.
        session_id: UUID4 string identifying the trace session.
        agent_name: Human-readable name of the traced agent.
        started_at: UTC timestamp when the agent run began.
        ended_at: UTC timestamp when the agent run completed.
        duration_ms: Total execution time in milliseconds.
        status: Final status — 'success', 'error', or 'retried'.
        calls_json: JSON-serialized list of call records.
        metadata_json: JSON-serialized metadata dict.
        created_at: UTC timestamp when this record was persisted.
    """

    __tablename__ = "trace_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    calls_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Composite index for common query pattern: filter by agent + status
    __table_args__ = (
        Index("ix_trace_events_agent_status", "agent_name", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<TraceEventRecord(id={self.id}, session_id='{self.session_id}', "
            f"agent='{self.agent_name}', status='{self.status}')>"
        )


class EthicsFlagRecord(Base):
    """Persisted record of a single ethics/bias/PII violation flag.

    Stores details of an EthicsViolation detected by the Ethics Engine during
    an agent run. Correlated with the main trace event via session_id.

    Attributes:
        id: Auto-incrementing primary key.
        session_id: UUID4 string correlating with the trace event.
        agent_name: Name of the agent that produced the violation.
        violation_type: Type of violation (pii_detected, bias_detected, sensitive_domain, fairness_violation).
        severity: Severity level (log, warn, block).
        description: Human-readable description.
        evidence: Masked text fragment triggering the violation.
        field_path: Path in the trace where the violation occurred.
        confidence: Confidence score of the detection (0.0 to 1.0).
        metadata_json: JSON-serialized extra metadata.
        detected_at: Timestamp when this violation was recorded.
    """

    __tablename__ = "ethics_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    violation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    field_path: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<EthicsFlagRecord(id={self.id}, session_id='{self.session_id}', "
            f"agent='{self.agent_name}', violation_type='{self.violation_type}', severity='{self.severity}')>"
        )

