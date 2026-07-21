"""Trace writer for GUARDIAN store.

Provides both async (queue-based with background flush) and synchronous
write paths for persisting trace events to the database.

The async TraceWriter batches writes to avoid per-call DB overhead:
traces are queued and flushed every 500ms or when the queue reaches
50 items, whichever comes first.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from guardian.store.db import get_session
from guardian.store.models import TraceEventRecord

logger = logging.getLogger("guardian")

# Flush configuration
_FLUSH_INTERVAL_SECONDS: float = 0.5
_FLUSH_BATCH_SIZE: int = 50

# Optional post-write callback — set by the API server at startup to enable
# live WebSocket streaming. If set, called with each trace dict after a
# successful DB write. Failures are logged as warnings and never crash the writer.
_post_write_callback: Callable[[dict[str, Any]], None] | None = None


def _trace_to_record(trace_event: dict[str, Any]) -> TraceEventRecord:
    """Convert a TraceEvent dict into a TraceEventRecord ORM instance.

    Args:
        trace_event: The serialized TraceEvent dict from the SDK serializer.

    Returns:
        A TraceEventRecord ready for database insertion.
    """
    # Parse ISO datetime strings back to datetime objects
    started_at = _parse_datetime(trace_event.get("started_at", ""))
    ended_at = _parse_datetime(trace_event.get("ended_at", ""))

    return TraceEventRecord(
        session_id=str(trace_event.get("session_id", "")),
        agent_name=str(trace_event.get("agent_name", "")),
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=int(trace_event.get("duration_ms", 0)),
        status=str(trace_event.get("status", "success")),
        calls_json=json.dumps(trace_event.get("calls", []), default=str),
        metadata_json=json.dumps(trace_event.get("metadata", {}), default=str),
    )


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime string, with fallback to now().

    Args:
        value: ISO 8601 formatted datetime string.

    Returns:
        Parsed datetime, or current UTC time if parsing fails.
    """
    if not value or value == "<unserializable>":
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


class TraceWriter:
    """Async trace writer with in-memory queue and background flush.

    Batches trace events and flushes them to the database periodically
    to avoid hammering the DB with one transaction per agent call.

    Usage::

        writer = TraceWriter()
        await writer.start()
        await writer.write(trace_event)
        # ... later ...
        await writer.stop()

    Attributes:
        _queue: Async queue holding pending trace events.
        _flush_task: Background asyncio task performing periodic flushes.
        _running: Whether the background flush task is active.
    """

    def __init__(self) -> None:
        """Initialize the TraceWriter with an empty queue."""
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._flush_task: asyncio.Task[None] | None = None
        self._running: bool = False

    async def start(self) -> None:
        """Start the background flush task.

        Idempotent — calling start() when already running is a no-op.
        """
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.debug("GUARDIAN TraceWriter: Background flush task started.")

    async def stop(self) -> None:
        """Stop the background flush task and flush remaining items.

        Drains the queue before stopping to avoid losing pending traces.
        """
        self._running = False
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        # Final flush of any remaining items
        await self._flush_queue()
        logger.debug("GUARDIAN TraceWriter: Stopped and flushed remaining traces.")

    async def write(self, trace_event: dict[str, Any]) -> None:
        """Queue a trace event for async persistence.

        If the background flush task isn't running, starts it automatically.
        Never raises — DB or queue errors are logged and the trace is dropped.

        Args:
            trace_event: The serialized TraceEvent dict to persist.
        """
        try:
            if not self._running:
                await self.start()
            await self._queue.put(trace_event)

            # Trigger immediate flush if batch size reached
            if self._queue.qsize() >= _FLUSH_BATCH_SIZE:
                await self._flush_queue()
        except Exception as exc:
            logger.warning("GUARDIAN TraceWriter: Failed to queue trace: %s", exc)

    async def _flush_loop(self) -> None:
        """Background loop that flushes the queue every FLUSH_INTERVAL_SECONDS.

        Runs until stop() is called or the task is cancelled.
        """
        try:
            while self._running:
                await asyncio.sleep(_FLUSH_INTERVAL_SECONDS)
                await self._flush_queue()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("GUARDIAN TraceWriter: Flush loop error: %s", exc)

    async def _flush_queue(self) -> None:
        """Drain all items from the queue and write them to the DB in one batch.

        After a successful flush, calls _post_write_callback (if registered)
        for each trace to enable live WebSocket streaming. Callback failures
        are logged as warnings and never crash the flush loop.
        """
        batch: list[dict[str, Any]] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                batch.append(item)
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        try:
            with get_session() as session:
                for trace_event in batch:
                    record = _trace_to_record(trace_event)
                    session.add(record)
            logger.debug(
                "GUARDIAN TraceWriter: Flushed %d trace(s) to database.", len(batch)
            )
            # Fire post-write callback for each trace (e.g. WebSocket broadcast)
            if _post_write_callback is not None:
                for trace_event in batch:
                    try:
                        _post_write_callback(trace_event)
                    except Exception as cb_exc:
                        logger.warning(
                            "GUARDIAN TraceWriter: post-write callback failed: %s", cb_exc
                        )
        except Exception as exc:
            logger.warning(
                "GUARDIAN TraceWriter: DB write failed for %d trace(s): %s",
                len(batch),
                exc,
            )


def write_sync(trace_event: dict[str, Any]) -> None:
    """Synchronous convenience function to write a single trace event.

    Writes directly to the database without queuing. Suitable for simple
    scripts and tests that don't need async batching.

    After a successful write, calls _post_write_callback if registered.
    Callback failures are logged as warnings and never crash the write.

    Never raises — DB errors are logged and the trace is dropped.

    Args:
        trace_event: The serialized TraceEvent dict to persist.
    """
    try:
        record = _trace_to_record(trace_event)
        with get_session() as session:
            session.add(record)
        logger.debug("GUARDIAN: Wrote trace %s synchronously.", trace_event.get("session_id"))
        # Fire post-write callback (e.g. WebSocket broadcast)
        if _post_write_callback is not None:
            try:
                _post_write_callback(trace_event)
            except Exception as cb_exc:
                logger.warning(
                    "GUARDIAN TraceWriter: post-write callback failed: %s", cb_exc
                )
    except Exception as exc:
        logger.warning("GUARDIAN: Sync write failed: %s", exc)
