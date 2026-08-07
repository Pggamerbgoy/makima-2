"""
Makima v7.2 — Elite Shared Blackboard

Async-safe, per-task isolated shared workspace for multi-agent coordination.
Agents write partial results, discovered facts, or artifacts during execution.
The Commander reads the blackboard instead of injecting raw dependency results.

Features:
  - Per-task namespace isolation (agents from different tasks can't collide)
  - Entry versioning with agent attribution and monotonic timestamps
  - TTL auto-expiry with lazy cleanup on read
  - Size limits (max entries per task, max value size) to prevent memory bloat
  - Snapshot/restore for rollback on failed plans
  - Merge strategies: last-writer-wins (default), append, union-set
  - Async-safe with per-key locking to minimize contention
  - Read-your-writes consistency within a single agent's execution
  - Cross-agent visibility: writes are immediately visible to other agents
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("makima.coordination.blackboard")


class MergeStrategy(str, Enum):
    """How to resolve concurrent writes to the same key."""
    OVERWRITE = "overwrite"  # Last-writer-wins (default)
    APPEND = "append"        # Append new value to a list
    UNION = "union"          # Merge as sets


@dataclass
class BlackboardEntry:
    """A single versioned entry in the blackboard."""
    key: str
    value: Any
    agent: str               # Which agent wrote this
    version: int = 1
    created_at: float = 0.0  # monotonic timestamp
    updated_at: float = 0.0
    ttl_seconds: Optional[float] = None
    merge_strategy: MergeStrategy = MergeStrategy.OVERWRITE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        return (time.monotonic() - self.updated_at) > self.ttl_seconds

    def touch(self) -> None:
        """Update the timestamp without changing the value."""
        self.updated_at = time.monotonic()


@dataclass
class BlackboardSnapshot:
    """Immutable snapshot of a task's blackboard state for rollback."""
    task_id: str
    entries: Dict[str, BlackboardEntry]
    taken_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "entries": {k: {"key": e.key, "value": e.value, "agent": e.agent,
                            "version": e.version} for k, e in self.entries.items()},
            "taken_at": self.taken_at,
        }


class SharedBlackboard:
    """
    Elite async-safe shared workspace for multi-agent coordination.

    Usage:
        bb = SharedBlackboard(max_entries_per_task=500, max_value_chars=50000)
        await bb.write(task_id, "research.findings", data, agent="research_agent")
        result = await bb.read(task_id, "research.findings")
        snapshot = await bb.snapshot(task_id)
        # ... on failure ...
        await bb.restore(snapshot)
    """

    def __init__(self, max_entries_per_task: int = 500,
                 max_value_chars: int = 50_000,
                 max_snapshots_per_task: int = 5,
                 default_ttl: Optional[float] = None):
        self._namespaces: Dict[str, OrderedDict[str, BlackboardEntry]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}        # per-task lock
        self._key_locks: Dict[str, asyncio.Lock] = {}     # per-key lock (fine-grained)
        self._snapshots: Dict[str, List[BlackboardSnapshot]] = {}
        self._subscribers: Dict[str, List[Callable]] = {} # key_pattern -> callbacks
        self._sub_lock = asyncio.Lock()

        self.max_entries_per_task = max_entries_per_task
        self.max_value_chars = max_value_chars
        self.max_snapshots_per_task = max_snapshots_per_task
        self.default_ttl = default_ttl

    # ──────────────────────────────────────────────
    # Internal Helpers
    # ──────────────────────────────────────────────

    async def _get_task_lock(self, task_id: str) -> asyncio.Lock:
        if task_id not in self._locks:
            self._locks[task_id] = asyncio.Lock()
        return self._locks[task_id]

    async def _get_key_lock(self, ns_key: str) -> asyncio.Lock:
        if ns_key not in self._key_locks:
            self._key_locks[ns_key] = asyncio.Lock()
        return self._key_locks[ns_key]

    def _ensure_namespace(self, task_id: str) -> OrderedDict:
        if task_id not in self._namespaces:
            self._namespaces[task_id] = OrderedDict()
        return self._namespaces[task_id]

    def _ns_key(self, task_id: str, key: str) -> str:
        return f"{task_id}:{key}"

    def _evict_expired(self, ns: OrderedDict) -> int:
        """Lazy eviction: remove expired entries on access."""
        expired = [k for k, v in ns.items() if v.is_expired()]
        for k in expired:
            del ns[k]
        return len(expired)

    async def _notify_subscribers(self, task_id: str, key: str, value: Any, agent: str) -> None:
        """Notify any subscribers watching this key pattern."""
        async with self._sub_lock:
            for pattern, callbacks in self._subscribers.items():
                if self._matches_pattern(key, pattern):
                    for cb in callbacks:
                        try:
                            result = cb(task_id=task_id, key=key, value=value, agent=agent)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.warning("[blackboard] Subscriber callback failed: %s", e)

    @staticmethod
    def _matches_pattern(key: str, pattern: str) -> bool:
        """Simple glob-style pattern matching: 'research.*' matches 'research.findings'."""
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return key.startswith(prefix + ".") or key == prefix
        return key == pattern

    # ──────────────────────────────────────────────
    # Core Read/Write API
    # ──────────────────────────────────────────────

    async def write(self, task_id: str, key: str, value: Any, *,
                    agent: str = "unknown",
                    ttl_seconds: Optional[float] = None,
                    merge_strategy: MergeStrategy = MergeStrategy.OVERWRITE,
                    metadata: Optional[Dict[str, Any]] = None) -> BlackboardEntry:
        """
        Write a value to the blackboard with agent attribution and optional TTL.

        Args:
            task_id: Task namespace for isolation
            key: Dot-notation key (e.g., "research.findings", "code.ast")
            value: Any serializable value
            agent: Name of the writing agent
            ttl_seconds: Optional TTL; None = no expiry (or default_ttl)
            merge_strategy: How to handle concurrent writes to same key
            metadata: Optional metadata dict for tracing

        Returns:
            The written BlackboardEntry (with version info)

        Raises:
            ValueError: If value exceeds max_value_chars
        """
        value_str = str(value)
        if len(value_str) > self.max_value_chars:
            raise ValueError(
                f"Value size {len(value_str)} exceeds max {self.max_value_chars} chars. "
                f"Compress or chunk the data before writing to blackboard."
            )

        effective_ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        ns = self._ensure_namespace(task_id)
        ns_key = self._ns_key(task_id, key)

        # Fine-grained per-key lock for minimal contention
        key_lock = await self._get_key_lock(ns_key)
        async with key_lock:
            self._evict_expired(ns)

            # Enforce entry limit per task
            if len(ns) >= self.max_entries_per_task and key not in ns:
                # Evict oldest entry (LRU) to make room
                oldest_key = next(iter(ns))
                logger.warning("[blackboard] Entry limit reached, evicting oldest: %s", oldest_key)
                del ns[oldest_key]

            now = time.monotonic()

            if key in ns and merge_strategy != MergeStrategy.OVERWRITE:
                existing = ns[key]
                if merge_strategy == MergeStrategy.APPEND:
                    current = existing.value if isinstance(existing.value, list) else [existing.value]
                    current.append(value)
                    merged_value = current
                elif merge_strategy == MergeStrategy.UNION:
                    current = set(existing.value) if isinstance(existing.value, (set, list)) else {existing.value}
                    new_set = set(value) if isinstance(value, (set, list)) else {value}
                    merged_value = current | new_set
                else:
                    merged_value = value

                existing.value = merged_value
                existing.version += 1
                existing.updated_at = now
                existing.agent = agent
                if metadata:
                    existing.metadata.update(metadata)
                entry = existing
            else:
                entry = BlackboardEntry(
                    key=key,
                    value=value,
                    agent=agent,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    ttl_seconds=effective_ttl,
                    merge_strategy=merge_strategy,
                    metadata=metadata or {},
                )
                ns[key] = entry

            # Move to end (most recently used)
            ns.move_to_end(key)

        # Notify subscribers outside the lock to prevent deadlock
        await self._notify_subscribers(task_id, key, entry.value, agent)

        logger.debug("[blackboard] Wrote %s v%d by %s (task=%s)", key, entry.version, agent, task_id)
        return entry

    async def read(self, task_id: str, key: str, *,
                   default: Any = None,
                   agent: Optional[str] = None) -> Any:
        """
        Read a value from the blackboard.

        Args:
            task_id: Task namespace
            key: Dot-notation key
            default: Value to return if key not found or expired
            agent: Optional — if set, records a read-access in metadata

        Returns:
            The stored value, or default if not found/expired
        """
        ns = self._ensure_namespace(task_id)

        if key not in ns:
            return default

        entry = ns[key]

        # Lazy expiry check
        if entry.is_expired():
            logger.debug("[blackboard] Key %s expired, removing", key)
            del ns[key]
            return default

        # Record read access
        entry.touch()
        ns.move_to_end(key)

        if agent and "read_by" not in entry.metadata:
            entry.metadata["read_by"] = []
        if agent:
            entry.metadata["read_by"].append(agent)

        return entry.value

    async def read_entry(self, task_id: str, key: str) -> Optional[BlackboardEntry]:
        """Read the full BlackboardEntry (with version, agent, timestamps)."""
        ns = self._ensure_namespace(task_id)
        entry = ns.get(key)
        if entry and not entry.is_expired():
            entry.touch()
            return entry
        if entry and entry.is_expired():
            del ns[key]
        return None

    async def read_all(self, task_id: str, *,
                       prefix: Optional[str] = None,
                       agent: Optional[str] = None) -> Dict[str, Any]:
        """
        Read all entries in a task's namespace, optionally filtered by prefix.

        Args:
            task_id: Task namespace
            prefix: Optional key prefix filter (e.g., "research." returns only research keys)
            agent: Optional — records read-access

        Returns:
            Dict of key -> value for all non-expired entries
        """
        ns = self._ensure_namespace(task_id)
        self._evict_expired(ns)

        result = {}
        for key, entry in ns.items():
            if prefix and not key.startswith(prefix):
                continue
            entry.touch()
            result[key] = entry.value
            if agent:
                entry.metadata.setdefault("read_by", []).append(agent)

        return result

    async def delete(self, task_id: str, key: str) -> bool:
        """Delete a single entry. Returns True if it existed."""
        ns = self._ensure_namespace(task_id)
        if key in ns:
            del ns[key]
            return True
        return False

    async def clear_task(self, task_id: str) -> int:
        """Clear all entries for a task. Returns count of cleared entries."""
        ns = self._namespaces.pop(task_id, {})
        count = len(ns)
        self._snapshots.pop(task_id, None)
        logger.info("[blackboard] Cleared %d entries for task %s", count, task_id)
        return count

    # ──────────────────────────────────────────────
    # Snapshot & Restore (Rollback)
    # ──────────────────────────────────────────────

    async def snapshot(self, task_id: str) -> BlackboardSnapshot:
        """
        Take an immutable snapshot of the task's blackboard state.
        Used for rollback when a plan fails partway through.
        """
        ns = self._ensure_namespace(task_id)
        self._evict_expired(ns)

        # Deep copy entries for immutability
        entries_copy = {
            k: BlackboardEntry(
                key=e.key, value=e.value, agent=e.agent,
                version=e.version, created_at=e.created_at,
                updated_at=e.updated_at, ttl_seconds=e.ttl_seconds,
                merge_strategy=e.merge_strategy, metadata=dict(e.metadata),
            )
            for k, e in ns.items()
        }

        snap = BlackboardSnapshot(task_id=task_id, entries=entries_copy)

        # Enforce snapshot limit
        if task_id not in self._snapshots:
            self._snapshots[task_id] = []
        snaps = self._snapshots[task_id]
        if len(snaps) >= self.max_snapshots_per_task:
            snaps.pop(0)  # Drop oldest
        snaps.append(snap)

        logger.debug("[blackboard] Snapshot taken for task %s (%d entries)", task_id, len(entries_copy))
        return snap

    async def restore(self, snapshot: BlackboardSnapshot) -> None:
        """
        Restore a task's blackboard to a previous snapshot state.
        All entries written after the snapshot are lost.
        """
        task_id = snapshot.task_id
        ns = self._ensure_namespace(task_id)
        ns.clear()

        for k, e in snapshot.entries.items():
            ns[k] = BlackboardEntry(
                key=e.key, value=e.value, agent=e.agent,
                version=e.version, created_at=e.created_at,
                updated_at=time.monotonic(),  # Reset TTL clock
                ttl_seconds=e.ttl_seconds,
                merge_strategy=e.merge_strategy, metadata=dict(e.metadata),
            )

        logger.info("[blackboard] Restored task %s to snapshot (%d entries)", task_id, len(ns))

    # ──────────────────────────────────────────────
    # Subscription / Watch API
    # ──────────────────────────────────────────────

    async def subscribe(self, pattern: str, callback: Callable) -> Callable:
        """
        Subscribe to writes matching a key pattern.
        Returns an unsubscribe function.

        Patterns:
          - "*" matches all keys
          - "research.*" matches "research.findings", "research.sources"
          - "code.ast" matches exact key only
        """
        async with self._sub_lock:
            if pattern not in self._subscribers:
                self._subscribers[pattern] = []
            self._subscribers[pattern].append(callback)

        def unsubscribe():
            try:
                self._subscribers[pattern].remove(callback)
            except ValueError:
                pass

        return unsubscribe

    # ──────────────────────────────────────────────
    # Introspection & Health
    # ──────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, Any]:
        """Get blackboard health metrics."""
        total_entries = sum(len(ns) for ns in self._namespaces.values())
        total_tasks = len(self._namespaces)
        total_snapshots = sum(len(s) for s in self._snapshots.values())

        return {
            "total_tasks": total_tasks,
            "total_entries": total_entries,
            "total_snapshots": total_snapshots,
            "max_entries_per_task": self.max_entries_per_task,
            "max_value_chars": self.max_value_chars,
            "active_subscribers": sum(len(cbs) for cbs in self._subscribers.values()),
            "tasks": {
                tid: {"entries": len(ns), "snapshots": len(self._snapshots.get(tid, []))}
                for tid, ns in self._namespaces.items()
            },
        }

    async def get_agent_contributions(self, task_id: str) -> Dict[str, List[str]]:
        """Get which agents contributed what keys to a task's blackboard."""
        ns = self._ensure_namespace(task_id)
        contributions: Dict[str, List[str]] = {}
        for key, entry in ns.items():
            contributions.setdefault(entry.agent, []).append(key)
        return contributions
