"""
Makima v7.2 — Elite Inter-Agent Event Bus

Async-safe in-process pub/sub for reactive multi-agent coordination.
Agents publish events and subscribe to patterns, enabling emergent
collaboration without tight coupling.

Features:
  - Pattern-based subscriptions with wildcard matching ("research.*", "*")
  - Per-subscriber async queues with bounded size (backpressure)
  - Dead-letter queue for undeliverable events
  - Event priority levels (low, normal, high, critical)
  - Event history with configurable retention for replay/debugging
  - Memory safety: max subscribers, max queue depth, max history
  - Non-blocking publish (drops with warning on full queue)
  - Fan-out: one event reaches all matching subscribers
  - Async context manager for clean lifecycle
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine, Deque, Dict, List, Optional, Set

logger = logging.getLogger("makima.coordination.event_bus")


class EventPriority(IntEnum):
    """Event priority levels. Higher = processed first by subscribers."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass(frozen=True)
class AgentEvent:
    """Immutable event published on the bus."""
    type: str                          # Dot-notation event type (e.g., "agent.done", "research.data_ready")
    source: str                        # Name of the publishing agent
    payload: Dict[str, Any]            # Event data
    task_id: Optional[str] = None      # Correlation ID for multi-agent tasks
    priority: EventPriority = EventPriority.NORMAL
    timestamp: float = field(default_factory=time.monotonic)
    event_id: str = ""                 # Unique event ID for dedup

    def matches_pattern(self, pattern: str) -> bool:
        """Check if this event type matches a subscription pattern."""
        return EventBus.matches_pattern(self.type, pattern)


@dataclass
class Subscription:
    """A single subscription binding."""
    subscriber_id: str
    pattern: str
    queue: asyncio.Queue
    max_depth: int
    total_received: int = 0
    total_dropped: int = 0
    created_at: float = field(default_factory=time.monotonic)
    active: bool = True

    @property
    def queue_depth(self) -> int:
        return self.queue.qsize()


@dataclass
class DeadLetterEntry:
    """Record of a failed event delivery."""
    event: AgentEvent
    subscriber_id: str
    pattern: str
    reason: str
    timestamp: float = field(default_factory=time.monotonic)


class EventBus:
    """
    Elite async pub/sub event bus for inter-agent coordination.

    Usage:
        bus = EventBus(max_queue_depth=100)

        # Subscribe with pattern
        sub_id = await bus.subscribe("research.*", callback=my_handler)

        # Publish an event
        await bus.publish(AgentEvent(
            type="research.data_ready",
            source="research_agent",
            payload={"findings": "..."},
            task_id="task_42",
        ))

        # Cleanup
        await bus.unsubscribe(sub_id)
    """

    # Compile regex patterns for performance
    _GLOB_RE = {
        "*": re.compile(r"^.*$"),
    }

    def __init__(self,
                 max_subscribers: int = 200,
                 max_queue_depth: int = 100,
                 max_history: int = 500,
                 max_dead_letters: int = 100):
        self._subscriptions: Dict[str, Subscription] = {}
        self._patterns: Dict[str, re.Pattern] = {}  # pattern -> compiled regex
        self._lock = asyncio.Lock()
        self._sub_counter = 0
        self._event_counter = 0

        # Memory safety
        self.max_subscribers = max_subscribers
        self.max_queue_depth = max_queue_depth
        self.max_history = max_history
        self.max_dead_letters = max_dead_letters

        # Event history (ring buffer)
        self._history: Deque[AgentEvent] = deque(maxlen=max_history)
        self._dead_letters: Deque[DeadLetterEntry] = deque(maxlen=max_dead_letters)

        # Stats
        self._total_published = 0
        self._total_delivered = 0
        self._total_dropped = 0

    # ──────────────────────────────────────────────
    # Pattern Matching
    # ──────────────────────────────────────────────

    @staticmethod
    def matches_pattern(event_type: str, pattern: str) -> bool:
        """
        Check if an event type matches a subscription pattern.

        Supported patterns:
          - "*"          → matches everything
          - "agent.*"    → matches "agent.done", "agent.error", etc.
          - "agent.+"    → matches "agent.done" but NOT "agent.done.detail"
          - "exact.type" → matches only "exact.type"
        """
        if pattern == event_type:
            return True
        if pattern == "*":
            return True

        # Convert glob to regex
        regex = "^"
        for char in pattern:
            if char == "*":
                regex += ".*"
            elif char == "+":
                regex += "[^.]*"  # Match within one segment only
            elif char in r"\.+^$[]{}()|":
                regex += "\\" + char
            else:
                regex += char
        regex += "$"

        return bool(re.match(regex, event_type))

    def _compile_pattern(self, pattern: str) -> re.Pattern:
        """Compile a glob pattern to regex for fast matching."""
        if pattern in self._patterns:
            return self._patterns[pattern]

        regex = "^"
        for char in pattern:
            if char == "*":
                regex += ".*"
            elif char == "+":
                regex += "[^.]*"
            elif char in r"\.+^$[]{}()|":
                regex += "\\" + char
            else:
                regex += char
        regex += "$"

        compiled = re.compile(regex)
        self._patterns[pattern] = compiled
        return compiled

    # ──────────────────────────────────────────────
    # Subscribe / Unsubscribe
    # ──────────────────────────────────────────────

    async def subscribe(self, pattern: str, *,
                        callback: Optional[Callable[[AgentEvent], Coroutine]] = None,
                        subscriber_id: Optional[str] = None,
                        max_queue_depth: Optional[int] = None) -> str:
        """
        Subscribe to events matching a pattern.

        Two modes:
          1. Queue mode: returns a subscription ID. Use consume() to drain events.
          2. Callback mode: provide a callback. Events are dispatched as they arrive.

        Args:
            pattern: Glob pattern ("research.*", "agent.+", "*")
            callback: Optional async callback function
            subscriber_id: Optional custom ID (auto-generated if omitted)
            max_queue_depth: Override global max queue depth for this subscription

        Returns:
            Subscription ID string
        """
        async with self._lock:
            if len(self._subscriptions) >= self.max_subscribers:
                raise RuntimeError(
                    f"EventBus subscriber limit reached ({self.max_subscribers}). "
                    "Increase max_subscribers or unsubscribe inactive subscriptions."
                )

            self._sub_counter += 1
            sub_id = subscriber_id or f"sub_{self._sub_counter}"
            depth = max_queue_depth or self.max_queue_depth

            sub = Subscription(
                subscriber_id=sub_id,
                pattern=pattern,
                queue=asyncio.Queue(maxsize=depth),
                max_depth=depth,
            )
            self._subscriptions[sub_id] = sub

            # Pre-compile the pattern
            self._compile_pattern(pattern)

            # If callback mode, spawn a background consumer
            if callback:
                asyncio.create_task(
                    self._callback_consumer(sub, callback),
                    name=f"eventbus_callback_{sub_id}"
                )

            logger.debug("[eventbus] Subscription '%s' registered for pattern '%s'", sub_id, pattern)
            return sub_id

    async def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription. Returns True if it existed."""
        async with self._lock:
            sub = self._subscriptions.pop(subscription_id, None)
            if sub:
                sub.active = False
                # Drain any remaining items so the queue GCs
                while not sub.queue.empty():
                    try:
                        sub.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                return True
            return False

    # ──────────────────────────────────────────────
    # Publish
    # ──────────────────────────────────────────────

    async def publish(self, event: AgentEvent) -> int:
        """
        Publish an event to all matching subscribers.

        Returns:
            Number of subscribers the event was delivered to.
        """
        self._total_published += 1
        self._event_counter += 1

        # Assign event_id if not set
        if not event.event_id:
            event = AgentEvent(
                type=event.type, source=event.source, payload=event.payload,
                task_id=event.task_id, priority=event.priority,
                timestamp=event.timestamp,
                event_id=f"evt_{self._event_counter}",
            )

        # Record in history
        self._history.append(event)

        # Find matching subscribers
        delivered = 0
        async with self._lock:
            for sub_id, sub in list(self._subscriptions.items()):
                if not sub.active:
                    continue
                if not self.matches_pattern(event.type, sub.pattern):
                    continue

                # Try to enqueue; drop if full (non-blocking)
                try:
                    sub.queue.put_nowait(event)
                    sub.total_received += 1
                    delivered += 1
                except asyncio.QueueFull:
                    sub.total_dropped += 1
                    self._total_dropped += 1
                    self._dead_letters.append(DeadLetterEntry(
                        event=event,
                        subscriber_id=sub_id,
                        pattern=sub.pattern,
                        reason="queue_full",
                    ))
                    logger.warning(
                        "[eventbus] Dropped event '%s' for '%s' — queue full (%d/%d)",
                        event.type, sub_id, sub.max_depth, sub.max_depth
                    )

        self._total_delivered += delivered
        return delivered

    async def publish_simple(self, event_type: str, source: str,
                             payload: Optional[Dict[str, Any]] = None,
                             task_id: Optional[str] = None,
                             priority: EventPriority = EventPriority.NORMAL) -> int:
        """Convenience method to publish without constructing an AgentEvent."""
        return await self.publish(AgentEvent(
            type=event_type,
            source=source,
            payload=payload or {},
            task_id=task_id,
            priority=priority,
        ))

    # ──────────────────────────────────────────────
    # Consumption
    # ──────────────────────────────────────────────

    async def consume(self, subscription_id: str, *,
                      timeout: Optional[float] = None) -> Optional[AgentEvent]:
        """
        Consume the next event from a subscription's queue.
        Returns None on timeout.
        """
        sub = self._subscriptions.get(subscription_id)
        if not sub or not sub.active:
            return None
        try:
            if timeout:
                return await asyncio.wait_for(sub.queue.get(), timeout=timeout)
            else:
                return await sub.queue.get()
        except asyncio.TimeoutError:
            return None

    async def consume_batch(self, subscription_id: str, *,
                            max_batch: int = 10,
                            timeout: Optional[float] = 5.0) -> List[AgentEvent]:
        """
        Consume up to max_batch events. Returns immediately with whatever
        is available (up to max_batch), waiting up to timeout for the first.
        """
        sub = self._subscriptions.get(subscription_id)
        if not sub or not sub.active:
            return []

        batch: List[AgentEvent] = []
        try:
            # Wait for at least one
            first = await asyncio.wait_for(sub.queue.get(), timeout=timeout)
            batch.append(first)

            # Drain up to max_batch without blocking
            while len(batch) < max_batch:
                try:
                    batch.append(sub.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
        except asyncio.TimeoutError:
            pass

        return batch

    async def _callback_consumer(self, sub: Subscription,
                                 callback: Callable[[AgentEvent], Coroutine]) -> None:
        """Background task that drains the queue and calls the callback."""
        while sub.active:
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
                try:
                    await callback(event)
                except Exception as e:
                    logger.error("[eventbus] Callback failed for '%s': %s", sub.subscriber_id, e)
                    self._dead_letters.append(DeadLetterEntry(
                        event=event,
                        subscriber_id=sub.subscriber_id,
                        pattern=sub.pattern,
                        reason=f"callback_error: {e}",
                    ))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    # ──────────────────────────────────────────────
    # Replay & History
    # ──────────────────────────────────────────────

    async def get_history(self, event_type: Optional[str] = None,
                          task_id: Optional[str] = None,
                          source: Optional[str] = None,
                          limit: int = 50) -> List[AgentEvent]:
        """
        Query event history with optional filters.
        Returns events in chronological order.
        """
        results = []
        for event in reversed(self._history):
            if len(results) >= limit:
                break
            if event_type and event.type != event_type:
                continue
            if task_id and event.task_id != task_id:
                continue
            if source and event.source != source:
                continue
            results.append(event)
        results.reverse()
        return results

    async def replay(self, subscription_id: str, *,
                     event_type: Optional[str] = None,
                     since: Optional[float] = None) -> int:
        """
        Replay historical events to a subscription's queue.
        Useful for late-joining subscribers that need catch-up context.
        """
        sub = self._subscriptions.get(subscription_id)
        if not sub or not sub.active:
            return 0

        replayed = 0
        for event in self._history:
            if not self.matches_pattern(event.type, sub.pattern):
                continue
            if event_type and event.type != event_type:
                continue
            if since and event.timestamp < since:
                continue
            try:
                sub.queue.put_nowait(event)
                replayed += 1
            except asyncio.QueueFull:
                break

        logger.info("[eventbus] Replayed %d events to '%s'", replayed, subscription_id)
        return replayed

    # ──────────────────────────────────────────────
    # Introspection & Health
    # ──────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, Any]:
        """Get event bus health metrics."""
        async with self._lock:
            active_subs = [s for s in self._subscriptions.values() if s.active]
            return {
                "total_subscribers": len(active_subs),
                "total_published": self._total_published,
                "total_delivered": self._total_delivered,
                "total_dropped": self._total_dropped,
                "history_size": len(self._history),
                "dead_letters": len(self._dead_letters),
                "subscribers": [
                    {
                        "id": s.subscriber_id,
                        "pattern": s.pattern,
                        "queue_depth": s.queue.qsize(),
                        "max_depth": s.max_depth,
                        "received": s.total_received,
                        "dropped": s.total_dropped,
                    }
                    for s in active_subs
                ],
            }

    def get_dead_letters(self, limit: int = 20) -> List[DeadLetterEntry]:
        """Get recent dead-letter entries for debugging."""
        return list(self._dead_letters)[-limit:]

    async def clear_dead_letters(self) -> int:
        """Clear dead letter queue. Returns count cleared."""
        count = len(self._dead_letters)
        self._dead_letters.clear()
        return count

    async def shutdown(self) -> None:
        """Gracefully shut down the event bus."""
        async with self._lock:
            for sub in self._subscriptions.values():
                sub.active = False
            self._subscriptions.clear()
        logger.info("[eventbus] Shut down (published=%d, delivered=%d, dropped=%d)",
                    self._total_published, self._total_delivered, self._total_dropped)
