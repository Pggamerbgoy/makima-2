"""Makima v7.2 — Elite OfflineQueue

Spec:
- Buffer messages when WebSocket/brain unreachable
- Replay on reconnect
- Cap: 50 messages; FIFO drop oldest beyond cap
- Stale screen context: if queued > 30s, set screen_context=None

This backend module provides a pure in-memory queue (frontend stores
IndexedDB; backend receives replayed items).

Elite v7.2 upgrades:
- Thread-safe via threading.Lock (safe from sync AND async callers)
- Zero-crash resilience: all public methods wrapped in try/except
- Proper type annotations throughout
- Structured logging with %s format for log aggregation
- Monotonic time for staleness checks (immune to NTP clock jumps)
- Thread-safe stats and telemetry
- Safe context mutation (deep copy on enqueue to prevent caller mutation)
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("makima.offline_queue")


@dataclass
class QueuedMessage:
    """A single message buffered in the offline queue."""
    task_id: str
    message: str
    queued_at: float          # monotonic timestamp
    context: Dict[str, Any] = field(default_factory=dict)


class OfflineQueue:
    """Thread-safe, capped, FIFO offline message queue.

    All public methods are safe to call from any thread or async context.
    The queue is bounded by ``cap``; oldest messages are dropped when the
    cap is exceeded.
    """

    def __init__(self, cap: int = 50, stale_screen_s: float = 30.0) -> None:
        if cap < 1:
            raise ValueError("cap must be >= 1")
        if stale_screen_s < 0:
            raise ValueError("stale_screen_s must be >= 0")

        self.cap: int = cap
        self.stale_screen_s: float = stale_screen_s
        self._q: Deque[QueuedMessage] = deque()
        self._lock = threading.Lock()

        # Telemetry
        self._total_enqueued: int = 0
        self._total_dropped: int = 0
        self._total_replayed: int = 0

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def enqueue(
        self,
        task_id: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a message to the queue.

        If the queue exceeds ``cap``, the oldest message(s) are dropped.
        Never raises.
        """
        try:
            ctx: Dict[str, Any] = copy.deepcopy(context) if context else {}
            now = time.monotonic()

            with self._lock:
                self._q.append(
                    QueuedMessage(
                        task_id=task_id,
                        message=message,
                        queued_at=now,
                        context=ctx,
                    )
                )
                self._total_enqueued += 1

                # Evict oldest beyond cap
                dropped = 0
                while len(self._q) > self.cap:
                    self._q.popleft()
                    dropped += 1
                self._total_dropped += dropped

            if dropped:
                logger.warning(
                    "OfflineQueue cap exceeded: dropped %d oldest message(s)",
                    dropped,
                )
        except Exception as e:
            logger.error("OfflineQueue enqueue error: %s", e, exc_info=True)

    def replay_ready(self) -> List[QueuedMessage]:
        """Drain the queue and return all messages, applying staleness rules.

        Messages queued longer than ``stale_screen_s`` have their
        ``screen_context`` set to None and are marked ``offline_queued``.
        Never raises — returns an empty list on error.
        """
        try:
            with self._lock:
                out = list(self._q)
                self._q.clear()
                self._total_replayed += len(out)

            # Apply stale screen rule (outside lock for minimal hold time)
            now = time.monotonic()
            for item in out:
                age = now - item.queued_at
                if age > self.stale_screen_s:
                    item.context["screen_context"] = None
                    item.context["offline_queued"] = True

            return out
        except Exception as e:
            logger.error("OfflineQueue replay_ready error: %s", e, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def pending_count(self) -> int:
        """Return the number of messages currently in the queue.

        Never raises — returns 0 on error.
        """
        try:
            with self._lock:
                return len(self._q)
        except Exception:
            return 0

    def is_empty(self) -> bool:
        """Return True if the queue is empty."""
        return self.pending_count() == 0

    def clear(self) -> int:
        """Clear all pending messages and return the count cleared.

        Never raises — returns 0 on error.
        """
        try:
            with self._lock:
                count = len(self._q)
                self._q.clear()
                self._total_dropped += count
            if count:
                logger.info("OfflineQueue cleared: %d message(s) dropped", count)
            return count
        except Exception as e:
            logger.error("OfflineQueue clear error: %s", e, exc_info=True)
            return 0

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        """Return queue telemetry stats.

        Never raises — returns empty dict on error.
        """
        try:
            with self._lock:
                return {
                    "pending": len(self._q),
                    "total_enqueued": self._total_enqueued,
                    "total_dropped": self._total_dropped,
                    "total_replayed": self._total_replayed,
                    "cap": self.cap,
                }
        except Exception:
            return {}

    def snapshot(self) -> List[QueuedMessage]:
        """Return a shallow copy of the current queue contents (non-destructive).

        Never raises — returns empty list on error.
        """
        try:
            with self._lock:
                return list(self._q)
        except Exception:
            return []
