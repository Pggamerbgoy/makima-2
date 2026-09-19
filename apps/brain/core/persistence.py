from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional, Union

logger = logging.getLogger("makima.core.persistence")


@dataclass
class KernelEvent:
    """A single immutable event from the kernel event log."""
    id: int
    timestamp: float
    event_type: str
    task_id: Optional[str]
    agent_name: Optional[str]
    payload: dict


class EventStore:
    """
    Append-only event store backed by SQLite in WAL mode with in-memory hot cache
    and background compaction + checkpointing.

    Every state transition in the kernel is recorded as an immutable row.
    On startup, replaying rows reconstructs the full kernel state.
    WAL mode allows concurrent reads during writes, and NORMAL synchronous
    gives durability without the performance cliff of FULL.
    """

    def __init__(self, db_path: str, hot_cache_size: int = 500) -> None:
        self._db_path = os.path.expanduser(db_path)
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS kernel_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  REAL    NOT NULL,
                event_type TEXT    NOT NULL,
                task_id    TEXT,
                agent_name TEXT,
                payload    TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_task
            ON kernel_events(task_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_type
            ON kernel_events(event_type)
        """)
        self._conn.commit()
        self._write_lock: Optional[asyncio.Lock] = None
        self._hot_cache: deque[KernelEvent] = deque(maxlen=hot_cache_size)

    def _get_lock(self) -> asyncio.Lock:
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        return self._write_lock

    def append_sync(
        self,
        event_type: str,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> int:
        """Insert an event synchronously (for use before event loop starts or in executor)."""
        now = time.time()
        p_dict = payload or {}
        try:
            cur = self._conn.execute(
                "INSERT INTO kernel_events "
                "(timestamp, event_type, task_id, agent_name, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, event_type, task_id, agent_name, json.dumps(p_dict, default=str)),
            )
            self._conn.commit()
            last_id = cur.lastrowid or 0
            ev = KernelEvent(
                id=last_id,
                timestamp=now,
                event_type=event_type,
                task_id=task_id,
                agent_name=agent_name,
                payload=p_dict,
            )
            self._hot_cache.append(ev)
            return last_id
        except Exception as exc:
            logger.debug("Sync event append failed (%s): %s", event_type, exc)
            return 0

    def replay_all_sync(self) -> list[KernelEvent]:
        """Fetch all events in insertion order (synchronous)."""
        try:
            rows = self._conn.execute(
                "SELECT id, timestamp, event_type, task_id, agent_name, payload "
                "FROM kernel_events ORDER BY id"
            ).fetchall()
            events = [
                KernelEvent(
                    id=r[0], timestamp=r[1], event_type=r[2],
                    task_id=r[3], agent_name=r[4],
                    payload=json.loads(r[5]) if r[5] else {},
                )
                for r in rows
            ]
            for ev in events[-self._hot_cache.maxlen:]:
                self._hot_cache.append(ev)
            return events
        except Exception as exc:
            logger.warning("Event replay failed: %s", exc)
            return []

    async def append(
        self,
        event_type: str,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> int:
        """Insert an event asynchronously (runs SQLite in executor)."""
        async with self._get_lock():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self.append_sync, event_type, task_id, agent_name, payload,
            )

    async def replay_all(self) -> list[KernelEvent]:
        """Fetch all events asynchronously."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.replay_all_sync)

    async def get_task_events(self, task_id: str) -> list[KernelEvent]:
        """Fetch all events for a specific task (checks hot cache first, then DB)."""
        # Fast path: check hot cache
        cached = [ev for ev in self._hot_cache if ev.task_id == task_id]
        if cached:
            return list(cached)

        def _fetch() -> list[KernelEvent]:
            rows = self._conn.execute(
                "SELECT id, timestamp, event_type, task_id, agent_name, payload "
                "FROM kernel_events WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
            return [
                KernelEvent(
                    id=r[0], timestamp=r[1], event_type=r[2],
                    task_id=r[3], agent_name=r[4],
                    payload=json.loads(r[5]) if r[5] else {},
                )
                for r in rows
            ]
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch)

    async def get_latest_partial(self, task_id: str) -> Optional[str]:
        """Return the most recent partial text for a task (if any)."""
        events = await self.get_task_events(task_id)
        for ev in reversed(events):
            if ev.event_type == "task_partial":
                return ev.payload.get("partial_text")
        return None

    def get_failure_patterns_sync(
        self,
        lookback_seconds: float = 3600.0,
        min_failures: int = 2,
    ) -> dict[str, dict[str, Any]]:
        """
        Query and aggregate SAGA failure incidents from the last lookback_seconds.
        Identifies recurring failure patterns per tool (e.g. repeated TIMEOUTs or INVALID_PARAMETERS)
        to enable runtime adaptive recovery and intelligent backoff.
        """
        cutoff_time = time.time() - float(lookback_seconds)
        try:
            rows = self._conn.execute(
                "SELECT timestamp, task_id, agent_name, payload "
                "FROM kernel_events WHERE event_type = 'saga_incident' AND timestamp >= ? "
                "ORDER BY id DESC",
                (cutoff_time,),
            ).fetchall()
        except Exception as exc:
            logger.debug("Failed to query saga failure patterns: %s", exc)
            return {}

        tool_stats: dict[str, dict[str, Any]] = {}
        for r in rows:
            ts = r[0]
            task_id = r[1]
            agent_or_tool = r[2] or ""
            payload = {}
            if r[3]:
                try:
                    payload = json.loads(r[3])
                except Exception:
                    pass

            tool_name = payload.get("tool_name") or agent_or_tool or "unknown_tool"
            evidence_tier = payload.get("evidence_tier", "UNKNOWN")
            error_msg = str(payload.get("error", ""))

            if tool_name not in tool_stats:
                tool_stats[tool_name] = {
                    "tool_name": tool_name,
                    "failure_count": 0,
                    "evidence_tiers": {},
                    "recent_errors": [],
                    "last_failed_at": ts,
                    "last_task_id": task_id,
                }

            stats = tool_stats[tool_name]
            stats["failure_count"] += 1
            stats["evidence_tiers"][evidence_tier] = stats["evidence_tiers"].get(evidence_tier, 0) + 1
            if error_msg and len(stats["recent_errors"]) < 3:
                stats["recent_errors"].append(error_msg[:120])
            if ts > stats["last_failed_at"]:
                stats["last_failed_at"] = ts
                stats["last_task_id"] = task_id

        patterns: dict[str, dict[str, Any]] = {}
        for t_name, stats in tool_stats.items():
            if stats["failure_count"] >= min_failures:
                tiers = stats["evidence_tiers"]
                dominant_issue = max(tiers.keys(), key=lambda k: tiers[k]) if tiers else "UNKNOWN"
                stats["dominant_issue"] = dominant_issue

                if dominant_issue == "TIMEOUT":
                    scale = 2.0 if stats["failure_count"] >= 3 else 1.5
                    stats["recommended_timeout_scale"] = scale
                    stats["recommended_action"] = f"Extend timeout by {scale}x"
                elif dominant_issue == "INVALID_PARAMETERS":
                    stats["recommended_timeout_scale"] = 1.0
                    stats["recommended_action"] = "Validate required parameter slots before calling"
                else:
                    stats["recommended_timeout_scale"] = 1.0
                    stats["recommended_action"] = "Review tool execution logs"

                patterns[t_name] = stats

        return patterns

    async def get_failure_patterns(
        self,
        lookback_seconds: float = 3600.0,
        min_failures: int = 2,
    ) -> dict[str, dict[str, Any]]:
        """Asynchronously query SAGA failure patterns."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_failure_patterns_sync, lookback_seconds, min_failures)

    def compact_events_sync(self, keep_recent: int = 2000) -> dict[str, int]:
        """
        Prune historical terminal events to prevent DB bloat, keeping the most recent events
        and running a WAL checkpoint truncate.
        """
        try:
            total_count = self._conn.execute("SELECT COUNT(*) FROM kernel_events").fetchone()[0]
            if total_count <= keep_recent:
                # Perform light checkpoint without deletion
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                return {"pruned": 0, "total_remaining": total_count}

            # Find the ID cutoff
            cutoff_row = self._conn.execute(
                "SELECT id FROM kernel_events ORDER BY id DESC LIMIT 1 OFFSET ?",
                (keep_recent,),
            ).fetchone()
            if not cutoff_row:
                return {"pruned": 0, "total_remaining": total_count}

            cutoff_id = cutoff_row[0]
            # Delete old events up to cutoff_id that are terminal (avoid deleting active tasks)
            cur = self._conn.execute(
                "DELETE FROM kernel_events WHERE id <= ? AND event_type NOT IN ('kernel_init', 'agent_initialized')",
                (cutoff_id,),
            )
            pruned = cur.rowcount
            self._conn.commit()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            remaining = self._conn.execute("SELECT COUNT(*) FROM kernel_events").fetchone()[0]
            logger.info("EventStore compacted: pruned %d events, %d remaining", pruned, remaining)
            return {"pruned": pruned, "total_remaining": remaining}
        except Exception as exc:
            logger.warning("EventStore compaction failed: %s", exc)
            return {"pruned": 0, "total_remaining": 0, "error": str(exc)}

    async def compact_events(self, keep_recent: int = 2000) -> dict[str, int]:
        """Asynchronously compact events and checkpoint SQLite WAL."""
        async with self._get_lock():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self.compact_events_sync, keep_recent)

    def get_storage_stats(self) -> dict[str, Any]:
        """Return event store diagnostics (row count, db size, hot cache count)."""
        try:
            count = self._conn.execute("SELECT COUNT(*) FROM kernel_events").fetchone()[0]
            size_bytes = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0
            wal_path = f"{self._db_path}-wal"
            wal_bytes = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
            return {
                "total_events": count,
                "db_size_kb": round(size_bytes / 1024.0, 2),
                "wal_size_kb": round(wal_bytes / 1024.0, 2),
                "hot_cache_items": len(self._hot_cache),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def get_recent_events_sync(
        self,
        event_type: Union[str, int, None] = None,
        limit: int = 50,
    ) -> list[KernelEvent]:
        """
        Fetch recent events, optionally filtered by event_type.
        Supports get_recent_events_sync("task_outcome", limit=20),
        get_recent_events_sync(limit=50), and positional limit calls.
        """
        actual_type: Optional[str] = None
        actual_limit: int = limit

        if isinstance(event_type, int):
            actual_limit = event_type
            actual_type = None
        elif isinstance(event_type, str):
            actual_type = event_type.strip() or None

        try:
            if actual_type:
                rows = self._conn.execute(
                    "SELECT id, timestamp, event_type, task_id, agent_name, payload "
                    "FROM kernel_events WHERE event_type = ? ORDER BY id DESC LIMIT ?",
                    (actual_type, int(actual_limit)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, timestamp, event_type, task_id, agent_name, payload "
                    "FROM kernel_events ORDER BY id DESC LIMIT ?",
                    (int(actual_limit),),
                ).fetchall()

            return [
                KernelEvent(
                    id=r[0], timestamp=r[1], event_type=r[2],
                    task_id=r[3], agent_name=r[4],
                    payload=json.loads(r[5]) if r[5] else {},
                )
                for r in rows
            ]
        except Exception as exc:
            logger.warning("Failed to fetch recent events: %s", exc)
            return []

    async def get_recent_events(
        self,
        event_type: Union[str, int, None] = None,
        limit: int = 50,
    ) -> list[KernelEvent]:
        """Asynchronously fetch recent events (optionally filtered by event_type)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_recent_events_sync, event_type, limit)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

