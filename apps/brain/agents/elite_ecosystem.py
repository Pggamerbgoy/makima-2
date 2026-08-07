"""
Makima v7.2 — Elite Advanced Agent Ecosystem
=============================================

A production-grade, highly-coordinated multi-agent ecosystem providing:

  a) Shared Reactive State Memory — pub/sub on state changes so agents
     instantly react to each other's outputs, context shifts, and signals.

  b) Asynchronous Inter-Agent Message Bus — direct task handoff, broadcast,
     fallback delegation chains, and strict priority queues.

  c) Zero-Crash Self-Healing Execution — per-agent timeouts, circuit breakers
     with half-open recovery, automatic retries with jittered backoff, and
     graceful fallback delegation when an agent stalls or fails.

  d) Coordinator Agent + Worker Swarm — a live coordinated execution cycle
     where a Coordinator decomposes tasks, dispatches to capability-matched
     workers, monitors health, and synthesizes final results.

Integration:
  - Plugs into BaseAgent via the `coordination` parameter.
  - Compatible with CommandRouter priority levels and Intent schema.
  - Compatible with CommanderAgent DAG execution for multi-step tasks.
  - Uses ws_broadcast for real-time WebSocket UI updates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Deque,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

logger = logging.getLogger("makima.ecosystem")

# ─────────────────────────────────────────────────────────────────────────────
# TYPE ALIASES & PROTOCOLS
# ─────────────────────────────────────────────────────────────────────────────

StateKey = str
StateValue = Any
SubscriptionId = str
AgentName = str
TaskId = str

StateCallback = Callable[[StateKey, StateValue, StateValue | None], Awaitable[None]]
"""Callback signature: (key, new_value, old_value) -> coroutine."""


@runtime_checkable
class AgentExecutor(Protocol):
    """Structural protocol that any agent must satisfy to join the ecosystem."""

    AGENT_NAME: str

    async def execute(
        self,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
    ) -> str: ...


# ─────────────────────────────────────────────────────────────────────────────
# (a) SHARED REACTIVE STATE MEMORY
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StateEntry:
    """A single versioned state entry."""

    key: StateKey
    value: StateValue
    version: int
    updated_at: float
    ttl: float | None  # seconds; None means immortal


@dataclass(frozen=True, slots=True)
class StateChangeEvent:
    """Emitted on every state mutation."""

    key: StateKey
    old_value: StateValue | None
    new_value: StateValue
    version: int
    timestamp: float
    source_agent: AgentName | None


class SharedReactiveState:
    """
    Thread-safe, async-aware reactive state store.

    Features:
      - Versioned keys (monotonically increasing).
      - Pub/sub: agents subscribe to key patterns and get instant callbacks.
      - TTL-based auto-expiry with a background reaper.
      - Snapshot and bulk-read for debugging / introspection.
    """

    def __init__(self, reaper_interval: float = 30.0) -> None:
        self._store: dict[StateKey, StateEntry] = {}
        self._subscriptions: dict[SubscriptionId, tuple[str, StateCallback]] = {}
        self._lock = asyncio.Lock()
        self._reaper_interval = reaper_interval
        self._reaper_task: asyncio.Task[None] | None = None
        self._event_log: Deque[StateChangeEvent] = deque(maxlen=5000)

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch the TTL reaper background task."""
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(
                self._reaper_loop(), name="state-reaper"
            )
            logger.info("[SharedState] Reaper started (interval=%.1fs)", self._reaper_interval)

    async def stop(self) -> None:
        """Cancel the reaper and drain pending callbacks."""
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
            logger.info("[SharedState] Reaper stopped")

    # ── read ───────────────────────────────────────────────────────────────

    async def get(self, key: StateKey) -> StateValue | None:
        """Return current value or None if missing / expired."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.ttl is not None and (time.time() - entry.updated_at) > entry.ttl:
                del self._store[key]
                return None
            return entry.value

    async def get_entry(self, key: StateKey) -> StateEntry | None:
        """Return the full entry (with version / metadata) or None."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.ttl is not None and (time.time() - entry.updated_at) > entry.ttl:
                del self._store[key]
                return None
            return entry

    async def snapshot(self) -> dict[StateKey, StateValue]:
        """Return a shallow copy of all live key-value pairs."""
        async with self._lock:
            now = time.time()
            result: dict[StateKey, StateValue] = {}
            expired_keys: list[StateKey] = []
            for k, entry in self._store.items():
                if entry.ttl is not None and (now - entry.updated_at) > entry.ttl:
                    expired_keys.append(k)
                else:
                    result[k] = entry.value
            for k in expired_keys:
                del self._store[k]
            return result

    async def keys(self, prefix: str = "") -> list[StateKey]:
        """Return all live keys, optionally filtered by prefix."""
        async with self._lock:
            now = time.time()
            live: list[StateKey] = []
            expired: list[StateKey] = []
            for k, entry in self._store.items():
                if entry.ttl is not None and (now - entry.updated_at) > entry.ttl:
                    expired.append(k)
                elif k.startswith(prefix):
                    live.append(k)
            for k in expired:
                del self._store[k]
            return live

    # ── write ──────────────────────────────────────────────────────────────

    async def set(
        self,
        key: StateKey,
        value: StateValue,
        *,
        ttl: float | None = None,
        source_agent: AgentName | None = None,
    ) -> int:
        """
        Set a key to a new value. Returns the new version number.
        Fires all matching subscriber callbacks asynchronously.
        """
        async with self._lock:
            old_entry = self._store.get(key)
            # Check if old entry is expired before treating as "old"
            old_value: StateValue | None = None
            old_version = 0
            if old_entry is not None:
                if old_entry.ttl is not None and (time.time() - old_entry.updated_at) > old_entry.ttl:
                    old_entry = None
                else:
                    old_value = old_entry.value
                    old_version = old_entry.version

            new_version = old_version + 1
            now = time.time()
            new_entry = StateEntry(
                key=key,
                value=value,
                version=new_version,
                updated_at=now,
                ttl=ttl,
            )
            self._store[key] = new_entry

            event = StateChangeEvent(
                key=key,
                old_value=old_value,
                new_value=value,
                version=new_version,
                timestamp=now,
                source_agent=source_agent,
            )
            self._event_log.append(event)

            # Collect matching subscribers while still under lock
            matching_callbacks: list[StateCallback] = []
            for _sub_id, (pattern, callback) in self._subscriptions.items():
                if self._pattern_matches(pattern, key):
                    matching_callbacks.append(callback)

        # Fire callbacks outside the lock to avoid deadlock
        for cb in matching_callbacks:
            try:
                asyncio.create_task(
                    self._safe_callback(cb, key, value, old_value),
                    name=f"state-cb-{key}",
                )
            except RuntimeError:
                # No running event loop — fire synchronously as a fallback
                logger.warning("[SharedState] No event loop; callback deferred for key=%s", key)

        logger.debug("[SharedState] SET %s v%d (agent=%s)", key, new_version, source_agent)
        return new_version

    async def delete(self, key: StateKey, *, source_agent: AgentName | None = None) -> bool:
        """Delete a key. Returns True if it existed."""
        async with self._lock:
            entry = self._store.pop(key, None)
            if entry is None:
                return False

            event = StateChangeEvent(
                key=key,
                old_value=entry.value,
                new_value=None,
                version=entry.version + 1,
                timestamp=time.time(),
                source_agent=source_agent,
            )
            self._event_log.append(event)

            matching_callbacks: list[StateCallback] = []
            for _sub_id, (pattern, callback) in self._subscriptions.items():
                if self._pattern_matches(pattern, key):
                    matching_callbacks.append(callback)

        for cb in matching_callbacks:
            try:
                asyncio.create_task(
                    self._safe_callback(cb, key, None, entry.value),
                    name=f"state-cb-del-{key}",
                )
            except RuntimeError:
                pass

        return True

    async def compare_and_swap(
        self,
        key: StateKey,
        expected_version: int,
        new_value: StateValue,
        *,
        ttl: float | None = None,
        source_agent: AgentName | None = None,
    ) -> bool:
        """Atomic CAS: only update if current version matches expected."""
        matching_callbacks: list[StateCallback] = []
        async with self._lock:
            entry = self._store.get(key)
            if entry is None or entry.version != expected_version:
                return False

            old_value = entry.value
            new_version = entry.version + 1
            now = time.time()
            new_entry = StateEntry(
                key=key,
                value=new_value,
                version=new_version,
                updated_at=now,
                ttl=ttl,
            )
            self._store[key] = new_entry

            event = StateChangeEvent(
                key=key,
                old_value=old_value,
                new_value=new_value,
                version=new_version,
                timestamp=now,
                source_agent=source_agent,
            )
            self._event_log.append(event)

            for _sub_id, (pattern, callback) in self._subscriptions.items():
                if self._pattern_matches(pattern, key):
                    matching_callbacks.append(callback)

        for cb in matching_callbacks:
            try:
                asyncio.create_task(
                    self._safe_callback(cb, key, new_value, old_value),
                    name=f"state-cb-{key}",
                )
            except RuntimeError:
                pass

        logger.debug("[SharedState] CAS %s v%d -> v%d (agent=%s)", key, expected_version, new_version, source_agent)
        return True

    # ── pub/sub ────────────────────────────────────────────────────────────

    async def subscribe(
        self,
        pattern: str,
        callback: StateCallback,
    ) -> SubscriptionId:
        """
        Subscribe to state changes matching a pattern.

        Pattern syntax:
          - Exact match: "agent:code:status"
          - Prefix wildcard: "agent:*" matches any key starting with "agent:"
          - Global wildcard: "*" matches everything

        Returns a subscription ID for later unsubscription.
        """
        sub_id = uuid.uuid4().hex[:12]
        async with self._lock:
            self._subscriptions[sub_id] = (pattern, callback)
        logger.debug("[SharedState] Subscription %s for pattern '%s'", sub_id, pattern)
        return sub_id

    async def unsubscribe(self, sub_id: SubscriptionId) -> bool:
        """Remove a subscription. Returns True if it existed."""
        async with self._lock:
            return self._subscriptions.pop(sub_id, None) is not None

    async def recent_events(self, count: int = 50) -> list[StateChangeEvent]:
        """Return the most recent state change events."""
        async with self._lock:
            return list(self._event_log)[-count:]

    # ── internals ──────────────────────────────────────────────────────────

    @staticmethod
    def _pattern_matches(pattern: str, key: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return key.startswith(pattern[:-1])
        return pattern == key

    @staticmethod
    async def _safe_callback(
        cb: StateCallback,
        key: StateKey,
        new_val: StateValue,
        old_val: StateValue | None,
    ) -> None:
        try:
            await cb(key, new_val, old_val)
        except Exception:
            logger.exception("[SharedState] Subscriber callback error for key=%s", key)

    async def _reaper_loop(self) -> None:
        """Periodically purge expired entries."""
        while True:
            try:
                await asyncio.sleep(self._reaper_interval)
                purged = 0
                async with self._lock:
                    now = time.time()
                    expired = [
                        k
                        for k, entry in self._store.items()
                        if entry.ttl is not None and (now - entry.updated_at) > entry.ttl
                    ]
                    for k in expired:
                        del self._store[k]
                        purged += 1
                if purged:
                    logger.debug("[SharedState] Reaper purged %d expired entries", purged)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[SharedState] Reaper error")


# ─────────────────────────────────────────────────────────────────────────────
# (b) ASYNCHRONOUS INTER-AGENT MESSAGE BUS
# ─────────────────────────────────────────────────────────────────────────────


class MessagePriority(int, Enum):
    """Bus message priority. Higher value = processed first."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class MessageType(str, Enum):
    """Bus message types."""

    TASK = "task"               # Direct task assignment
    RESULT = "result"           # Result from a worker
    BROADCAST = "broadcast"     # Fan-out to all agents
    HANDOFF = "handoff"         # Direct agent-to-agent handoff
    HEARTBEAT = "heartbeat"     # Health signal
    CANCEL = "cancel"           # Cancellation signal
    FALLBACK = "fallback"       # Fallback delegation
    ACK = "ack"                 # Acknowledgment signal
    DELEGATE = "delegate"       # Subtask delegation


@dataclass(slots=True)
class BusMessage:
    """A single message on the inter-agent bus."""

    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    msg_type: MessageType = MessageType.TASK
    priority: MessagePriority = MessagePriority.NORMAL
    sender: AgentName = ""
    recipient: AgentName = ""       # empty = broadcast / untargeted
    payload: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    correlation_id: str = ""        # links request/response pairs
    created_at: float = field(default_factory=time.time)
    ttl: float = 120.0              # seconds before auto-expiry
    retry_count: int = 0
    max_retries: int = 3
    fallback_chain: list[AgentName] = field(default_factory=list)

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl

    def __lt__(self, other: BusMessage) -> bool:
        if self.priority != other.priority:
            return self.priority > other.priority  # higher priority first
        return self.created_at < other.created_at   # earlier first


class InterAgentMessageBus:
    """
    High-throughput async message bus with:
      - Per-agent mailbox queues (priority-sorted).
      - Broadcast channels.
      - Direct handoff with acknowledgement.
      - Fallback delegation chains.
      - Dead-letter queue for undeliverable messages.
    """

    def __init__(
        self,
        max_queue_size: int = 10_000,
        dead_letter_max: int = 1_000,
    ) -> None:
        self._mailboxes: dict[AgentName, asyncio.PriorityQueue[BusMessage]] = {}
        self._broadcast_subscribers: dict[str, list[AgentName]] = defaultdict(list)
        self._registered_agents: set[AgentName] = set()
        self._max_queue_size = max_queue_size
        self._dead_letter: Deque[BusMessage] = deque(maxlen=dead_letter_max)
        self._lock = asyncio.Lock()
        self._message_log: Deque[BusMessage] = deque(maxlen=10_000)
        self._pending_acks: dict[str, asyncio.Event] = {}
        self._dispatch_tasks: dict[AgentName, asyncio.Task[None]] = {}

    # ── agent registration ─────────────────────────────────────────────────

    async def register_agent(self, agent_name: AgentName) -> None:
        """Register an agent and create its mailbox."""
        async with self._lock:
            self._registered_agents.add(agent_name)
            if agent_name not in self._mailboxes:
                self._mailboxes[agent_name] = asyncio.PriorityQueue(
                    maxsize=self._max_queue_size
                )
            logger.info("[MessageBus] Agent registered: %s", agent_name)

    async def unregister_agent(self, agent_name: AgentName) -> None:
        """Unregister an agent and clean up its mailbox."""
        async with self._lock:
            self._registered_agents.discard(agent_name)
            self._mailboxes.pop(agent_name, None)
            task = self._dispatch_tasks.pop(agent_name, None)
            if task and not task.done():
                task.cancel()
            for channel_agents in self._broadcast_subscribers.values():
                if agent_name in channel_agents:
                    channel_agents.remove(agent_name)
            logger.info("[MessageBus] Agent unregistered: %s", agent_name)

    async def register_broadcast_listener(
        self, agent_name: AgentName, channel: str = "global"
    ) -> None:
        """Subscribe an agent to a broadcast channel."""
        async with self._lock:
            if agent_name not in self._broadcast_subscribers[channel]:
                self._broadcast_subscribers[channel].append(agent_name)
            logger.debug("[MessageBus] %s listening on channel '%s'", agent_name, channel)

    # ── send / dispatch ────────────────────────────────────────────────────

    async def send(self, message: BusMessage) -> bool:
        """
        Route a message to its destination.
        Returns True if successfully enqueued.
        """
        if message.is_expired():
            logger.warning("[MessageBus] Message %s expired before send", message.msg_id)
            self._dead_letter.append(message)
            return False

        self._message_log.append(message)

        # KAMI-11 FIX: Intercept RESULT/ACK messages to trigger pending ACKs
        if message.msg_type in (MessageType.RESULT, MessageType.ACK, MessageType.DELEGATE) and message.correlation_id:
            ack_event = self._pending_acks.get(message.correlation_id)
            if ack_event:
                ack_event.set()

        if message.msg_type == MessageType.BROADCAST:
            return await self._broadcast(message)

        if message.recipient:
            return await self._direct_send(message)

        # Untargeted: try to find a handler via fallback chain
        if message.fallback_chain:
            return await self._fallback_dispatch(message)

        logger.warning("[MessageBus] Untargeted message %s with no fallback chain", message.msg_id)
        self._dead_letter.append(message)
        return False

    async def send_task(
        self,
        sender: AgentName,
        recipient: AgentName,
        task_id: str,
        payload: dict[str, Any],
        *,
        priority: MessagePriority = MessagePriority.NORMAL,
        fallback_chain: list[AgentName] | None = None,
        ttl: float = 120.0,
    ) -> str:
        """Convenience: send a TASK message and return the message ID."""
        msg = BusMessage(
            msg_type=MessageType.TASK,
            priority=priority,
            sender=sender,
            recipient=recipient,
            payload=payload,
            task_id=task_id,
            ttl=ttl,
            fallback_chain=fallback_chain or [],
        )
        await self.send(msg)
        return msg.msg_id

    async def send_result(
        self,
        sender: AgentName,
        recipient: AgentName,
        task_id: str,
        result: str,
        correlation_id: str = "",
    ) -> str:
        """Convenience: send a RESULT message back to the requester."""
        msg = BusMessage(
            msg_type=MessageType.RESULT,
            priority=MessagePriority.HIGH,
            sender=sender,
            recipient=recipient,
            payload={"result": result},
            task_id=task_id,
            correlation_id=correlation_id,
        )
        await self.send(msg)
        return msg.msg_id

    async def broadcast(
        self,
        sender: AgentName,
        payload: dict[str, Any],
        channel: str = "global",
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> int:
        """Broadcast a message. Returns the number of recipients reached."""
        msg = BusMessage(
            msg_type=MessageType.BROADCAST,
            priority=priority,
            sender=sender,
            payload={**payload, "_channel": channel},
        )
        await self.send(msg)
        async with self._lock:
            return len(self._broadcast_subscribers.get(channel, []))

    async def request_with_ack(
        self,
        sender: AgentName,
        recipient: AgentName,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
        priority: MessagePriority = MessagePriority.HIGH,
    ) -> BusMessage | None:
        """Send a message and wait for an acknowledgement/result."""
        ack_event = asyncio.Event()
        msg = BusMessage(
            msg_type=MessageType.TASK,
            priority=priority,
            sender=sender,
            recipient=recipient,
            payload=payload,
            task_id=uuid.uuid4().hex[:12],
        )
        self._pending_acks[msg.msg_id] = ack_event

        delivered = await self.send(msg)
        if not delivered:
            self._pending_acks.pop(msg.msg_id, None)
            return None

        try:
            await asyncio.wait_for(ack_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "[MessageBus] ACK timeout for msg %s → %s", msg.msg_id, recipient
            )
            self._pending_acks.pop(msg.msg_id, None)
            return None

        # Retrieve the response from the mailbox
        result_msg = await self.receive(sender, timeout=1.0)
        self._pending_acks.pop(msg.msg_id, None)
        return result_msg

    async def receive(
        self, agent_name: AgentName, timeout: float = 5.0
    ) -> BusMessage | None:
        """Receive the next message from an agent's mailbox."""
        async with self._lock:
            queue = self._mailboxes.get(agent_name)
        if queue is None:
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def cancel_task(self, sender: AgentName, task_id: str) -> None:
        """Broadcast a cancellation signal for a specific task."""
        cancel_msg = BusMessage(
            msg_type=MessageType.CANCEL,
            priority=MessagePriority.CRITICAL,
            sender=sender,
            payload={"cancelled_task_id": task_id},
            task_id=task_id,
        )
        await self._broadcast(cancel_msg)

    # ── introspection ──────────────────────────────────────────────────────

    async def queue_depth(self, agent_name: AgentName) -> int:
        """Return the current queue depth for an agent."""
        async with self._lock:
            queue = self._mailboxes.get(agent_name)
        return queue.qsize() if queue else 0

    async def all_queue_depths(self) -> dict[AgentName, int]:
        """Return queue depths for all registered agents."""
        async with self._lock:
            return {name: q.qsize() for name, q in self._mailboxes.items()}

    async def registered_agents(self) -> set[AgentName]:
        async with self._lock:
            return set(self._registered_agents)

    async def dead_letter_count(self) -> int:
        return len(self._dead_letter)

    async def drain_dead_letters(self) -> list[BusMessage]:
        """Drain and return all dead-letter messages."""
        messages = list(self._dead_letter)
        self._dead_letter.clear()
        return messages

    # ── DLQ auto-retry ─────────────────────────────────────────────────────

    def start_dlq_requeue(self, interval: float = 300.0) -> None:
        """Background task: re-queue dead-lettered messages until max_retries."""
        if getattr(self, "_dlq_task", None) and not self._dlq_task.done():
            return
        self._dlq_interval = interval

        async def _requeue_loop() -> None:
            while True:
                try:
                    await asyncio.sleep(self._dlq_interval)
                    messages = await self.drain_dead_letters()
                    for msg in messages:
                        if msg.retry_count >= msg.max_retries:
                            logger.warning(
                                "[MessageBus] Dropping msg %s — max retries (%d) exceeded",
                                msg.msg_id, msg.max_retries,
                            )
                            continue
                        msg.retry_count += 1
                        logger.info(
                            "[MessageBus] DLQ requeue attempt %d/%d for msg %s → %s",
                            msg.retry_count, msg.max_retries, msg.msg_id, msg.recipient,
                        )
                        await self.send(msg)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("[MessageBus] DLQ requeue cycle failed: %s", e)

        self._dlq_task = asyncio.create_task(_requeue_loop())
        logger.info("[MessageBus] DLQ auto-retry started (interval=%.0fs)", interval)

    def stop_dlq_requeue(self) -> None:
        """Cancel the DLQ requeue background task."""
        task = getattr(self, "_dlq_task", None)
        if task and not task.done():
            task.cancel()
            logger.info("[MessageBus] DLQ auto-retry stopped")

    # ── internal routing ───────────────────────────────────────────────────

    async def _direct_send(self, message: BusMessage) -> bool:
        """Deliver a message to a specific agent's mailbox."""
        async with self._lock:
            queue = self._mailboxes.get(message.recipient)
            is_registered = message.recipient in self._registered_agents

        if queue is None or not is_registered:
            # Try fallback chain if recipient is unavailable
            if message.fallback_chain:
                return await self._fallback_dispatch(message)
            logger.warning(
                "[MessageBus] Recipient '%s' not registered; dead-lettering msg %s",
                message.recipient,
                message.msg_id,
            )
            self._dead_letter.append(message)
            return False

        try:
            queue.put_nowait(message)
            logger.debug(
                "[MessageBus] Delivered %s → %s (type=%s, pri=%s)",
                message.msg_id,
                message.recipient,
                message.msg_type.value,
                message.priority.name,
            )
            return True
        except asyncio.QueueFull:
            logger.error(
                "[MessageBus] Queue full for '%s'; dead-lettering msg %s",
                message.recipient,
                message.msg_id,
            )
            self._dead_letter.append(message)
            return False

    async def _broadcast(self, message: BusMessage) -> bool:
        """Fan-out a message to all agents on the broadcast channel."""
        channel = message.payload.get("_channel", "global")
        async with self._lock:
            targets = list(self._broadcast_subscribers.get(channel, []))
            # Also send to all registered agents if global
            if channel == "global":
                targets = list(self._registered_agents)

        delivered = 0
        for agent_name in targets:
            if agent_name == message.sender:
                continue  # Don't echo to sender
            copy = BusMessage(
                msg_type=message.msg_type,
                priority=message.priority,
                sender=message.sender,
                recipient=agent_name,
                payload=dict(message.payload),
                task_id=message.task_id,
                correlation_id=message.correlation_id,
                ttl=message.ttl,
            )
            if await self._direct_send(copy):
                delivered += 1

        logger.debug(
            "[MessageBus] Broadcast from %s on '%s': %d/%d delivered",
            message.sender,
            channel,
            delivered,
            len(targets),
        )
        return delivered > 0

    async def _fallback_dispatch(self, message: BusMessage) -> bool:
        """Try each agent in the fallback chain until one accepts."""
        remaining_chain = [
            a for a in message.fallback_chain if a != message.recipient
        ]
        for fallback_agent in remaining_chain:
            fallback_msg = BusMessage(
                msg_type=MessageType.FALLBACK,
                priority=message.priority,
                sender=message.sender,
                recipient=fallback_agent,
                payload={
                    **message.payload,
                    "_original_recipient": message.recipient,
                    "_fallback_reason": "primary_unavailable",
                },
                task_id=message.task_id,
                correlation_id=message.correlation_id,
                ttl=message.ttl,
                retry_count=message.retry_count,
                max_retries=message.max_retries,
                fallback_chain=[a for a in remaining_chain if a != fallback_agent],
            )
            if await self._direct_send(fallback_msg):
                logger.info(
                    "[MessageBus] Fallback: %s → %s (task=%s)",
                    message.recipient,
                    fallback_agent,
                    message.task_id,
                )
                return True

        logger.error(
            "[MessageBus] All fallbacks exhausted for task %s; dead-lettering",
            message.task_id,
        )
        self._dead_letter.append(message)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# (c) ZERO-CRASH SELF-HEALING EXECUTION
# ─────────────────────────────────────────────────────────────────────────────


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"         # Normal operation
    OPEN = "open"             # Failing; reject calls
    HALF_OPEN = "half_open"   # Testing recovery


@dataclass(slots=True)
class CircuitBreakerState:
    """Per-agent circuit breaker tracking."""

    agent_name: AgentName
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_successes: int = 2


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is open and rejecting calls."""

    def __init__(self, agent_name: str, retry_after: float) -> None:
        self.agent_name = agent_name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit open for '{agent_name}'; retry after {retry_after:.1f}s"
        )


class AgentExecutionTimeoutError(Exception):
    """Raised when an agent exceeds its execution time limit."""

    def __init__(self, agent_name: str, timeout: float) -> None:
        self.agent_name = agent_name
        self.timeout = timeout
        super().__init__(f"Agent '{agent_name}' timed out after {timeout:.1f}s")


class AgentExecutionError(Exception):
    """Wraps any exception from agent execution."""

    def __init__(self, agent_name: str, original: Exception) -> None:
        self.agent_name = agent_name
        self.original = original
        super().__init__(f"Agent '{agent_name}' failed: {original}")


class SelfHealingExecutor:
    """
    Zero-crash execution wrapper providing:

      - Per-agent timeouts (configurable).
      - Circuit breakers with CLOSED → OPEN → HALF_OPEN recovery.
      - Automatic retries with jittered exponential backoff.
      - Fallback delegation to alternate agents.
      - Health monitoring and automatic state publishing.
    """

    def __init__(
        self,
        state: SharedReactiveState,
        bus: InterAgentMessageBus,
        *,
        default_timeout: float = 60.0,
        default_retries: int = 2,
        backoff_base: float = 1.0,
        backoff_max: float = 15.0,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0,
        ws_broadcast: Any = None,
    ) -> None:
        self._state = state
        self._bus = bus
        self._ws_broadcast = ws_broadcast
        self._default_timeout = default_timeout
        self._default_retries = default_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        # Optional cancellation hook (F3): callable(task_id) -> asyncio.Event.
        # When set, worker execution races this token so cancel_task cascades
        # into ecosystem DAG workers immediately.
        self.cancel_event_provider: Any = None
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_recovery_timeout = circuit_recovery_timeout

        self._circuits: dict[AgentName, CircuitBreakerState] = {}
        self._agents: dict[AgentName, AgentExecutor] = {}
        self._fallback_chains: dict[AgentName, list[AgentName]] = {}
        self._agent_timeouts: dict[AgentName, float] = {}
        self._lock = asyncio.Lock()
        self._execution_log: Deque[dict[str, Any]] = deque(maxlen=5_000)

    # ── agent management ───────────────────────────────────────────────────

    async def register_agent(
        self,
        agent: AgentExecutor,
        *,
        timeout: float | None = None,
        fallback_chain: list[AgentName] | None = None,
    ) -> None:
        """Register an agent with the self-healing executor."""
        name = agent.AGENT_NAME
        async with self._lock:
            self._agents[name] = agent
            self._circuits[name] = CircuitBreakerState(
                agent_name=name,
                failure_threshold=self._circuit_failure_threshold,
                recovery_timeout=self._circuit_recovery_timeout,
            )
            if timeout is not None:
                self._agent_timeouts[name] = timeout
            if fallback_chain:
                self._fallback_chains[name] = list(fallback_chain)

        await self._bus.register_agent(name)
        await self._state.set(
            f"agent:{name}:health",
            {"status": "healthy", "circuit": CircuitState.CLOSED.value},
            source_agent="self_healing",
        )
        logger.info("[SelfHealing] Agent registered: %s", name)

    async def unregister_agent(self, agent_name: AgentName) -> None:
        """Remove an agent from the executor."""
        async with self._lock:
            self._agents.pop(agent_name, None)
            self._circuits.pop(agent_name, None)
            self._fallback_chains.pop(agent_name, None)
            self._agent_timeouts.pop(agent_name, None)
        await self._bus.unregister_agent(agent_name)
        await self._state.delete(f"agent:{agent_name}:health", source_agent="self_healing")

    async def set_fallback_chain(
        self, agent_name: AgentName, chain: list[AgentName]
    ) -> None:
        """Define a fallback chain for an agent."""
        async with self._lock:
            self._fallback_chains[agent_name] = list(chain)

    # ── execution ──────────────────────────────────────────────────────────

    async def execute(
        self,
        agent_name: AgentName,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> str:
        """
        Execute a task on an named agent with full self-healing:
          1. Check circuit breaker.
          2. Execute with timeout.
          3. On failure: retry with backoff.
          4. On circuit open: delegate to fallback chain.
          5. Publish health state throughout.
        """
        effective_timeout = timeout or self._agent_timeouts.get(agent_name, self._default_timeout)
        effective_retries = retries if retries is not None else self._default_retries

        async with self._lock:
            agent = self._agents.get(agent_name)
            circuit = self._circuits.get(agent_name)
            fallback_chain = list(self._fallback_chains.get(agent_name, []))

        if agent is None:
            logger.error("[SelfHealing] Agent '%s' not registered", agent_name)
            return f"[Error] Agent '{agent_name}' is not available."

        # Circuit breaker gate
        if circuit:
            self._check_circuit(circuit)

        # Attempt execution with retries
        last_error: Exception | None = None
        for attempt in range(effective_retries + 1):
            if circuit and circuit.state == CircuitState.OPEN:
                break

            try:
                result = await self._execute_with_timeout(
                    agent, task_id, message, context, entities, effective_timeout
                )
                await self._record_success(agent_name, circuit)
                self._log_execution(agent_name, task_id, attempt, success=True)
                return result

            except asyncio.TimeoutError as exc:
                last_error = AgentExecutionTimeoutError(agent_name, effective_timeout)
                logger.warning(
                    "[SelfHealing] Timeout on %s (attempt %d/%d)",
                    agent_name,
                    attempt + 1,
                    effective_retries + 1,
                )
            except Exception as exc:
                last_error = AgentExecutionError(agent_name, exc)
                logger.warning(
                    "[SelfHealing] Error on %s (attempt %d/%d): %s",
                    agent_name,
                    attempt + 1,
                    effective_retries + 1,
                    exc,
                )

            await self._record_failure(agent_name, circuit)
            await self._notify(
                f"⚠️ {agent_name} failed (attempt {attempt + 1}/{effective_retries + 1}), healing..."
            )

            # Jittered exponential backoff before retry
            if attempt < effective_retries:
                backoff = min(
                    self._backoff_base * (2 ** attempt) + random.uniform(0, 0.5),
                    self._backoff_max,
                )
                logger.debug("[SelfHealing] Backoff %.2fs before retry", backoff)
                await asyncio.sleep(backoff)

        # All retries exhausted — try fallback chain
        if fallback_chain:
            logger.info(
                "[SelfHealing] Falling back from %s through chain: %s",
                agent_name,
                fallback_chain,
            )
            await self._notify(f"🔄 {agent_name} still failing — falling back to {', '.join(fallback_chain)}")
            for fallback_agent_name in fallback_chain:
                try:
                    result = await self.execute(
                        fallback_agent_name,
                        task_id,
                        message,
                        context,
                        entities,
                        timeout=timeout,
                        retries=0,  # Don't cascade retries
                    )
                    await self._state.set(
                        f"agent:{agent_name}:last_fallback",
                        {
                            "fallback_to": fallback_agent_name,
                            "task_id": task_id,
                            "timestamp": time.time(),
                        },
                        ttl=300.0,
                        source_agent="self_healing",
                    )
                    return result
                except Exception as fb_exc:
                    logger.warning(
                        "[SelfHealing] Fallback %s also failed: %s",
                        fallback_agent_name,
                        fb_exc,
                    )
                    continue

        # Complete failure
        error_msg = str(last_error) if last_error else "Unknown error"
        self._log_execution(agent_name, task_id, effective_retries, success=False, error=error_msg)
        await self._notify(f"❌ {agent_name} failed after {effective_retries + 1} attempts: {error_msg}")

        await self._state.set(
            f"agent:{agent_name}:last_error",
            {"error": error_msg, "task_id": task_id, "timestamp": time.time()},
            ttl=600.0,
            source_agent="self_healing",
        )
        return f"[Self-Healing] Agent '{agent_name}' failed after {effective_retries + 1} attempts: {error_msg}"

    # ── health monitoring ──────────────────────────────────────────────────

    async def _notify(self, text: str) -> None:
        """Broadcast a human-readable healing status to the UI (no-op if no sink)."""
        if not self._ws_broadcast:
            return
        try:
            from .. import ws_protocol
            await self._ws_broadcast(ws_protocol.build_ai_chunk("self_healing", text, is_final=False))
        except Exception:
            pass

    async def get_health_report(self) -> dict[AgentName, dict[str, Any]]:
        """Return a comprehensive health report for all agents."""
        async with self._lock:
            report: dict[AgentName, dict[str, Any]] = {}
            for name, circuit in self._circuits.items():
                report[name] = {
                    "circuit_state": circuit.state.value,
                    "failure_count": circuit.failure_count,
                    "success_count": circuit.success_count,
                    "last_failure_time": circuit.last_failure_time,
                    "is_healthy": circuit.state != CircuitState.OPEN,
                }
        return report

    async def get_agent(self, agent_name: AgentName) -> AgentExecutor | None:
        """Retrieve a registered agent by name."""
        async with self._lock:
            return self._agents.get(agent_name)

    async def registered_agents(self) -> list[AgentName]:
        async with self._lock:
            return list(self._agents.keys())

    # ── internal helpers ───────────────────────────────────────────────────

    def _check_circuit(self, circuit: CircuitBreakerState) -> None:
        """Evaluate circuit state and raise if open."""
        if circuit.state == CircuitState.CLOSED:
            return

        if circuit.state == CircuitState.OPEN:
            elapsed = time.time() - circuit.last_failure_time
            if elapsed >= circuit.recovery_timeout:
                circuit.state = CircuitState.HALF_OPEN
                circuit.last_state_change = time.time()
                logger.info(
                    "[CircuitBreaker] %s: OPEN → HALF_OPEN (testing recovery)",
                    circuit.agent_name,
                )
            else:
                retry_after = circuit.recovery_timeout - elapsed
                raise CircuitBreakerOpenError(circuit.agent_name, retry_after)

        # HALF_OPEN: allow the call through (test probe)

    async def _record_success(
        self, agent_name: AgentName, circuit: CircuitBreakerState | None
    ) -> None:
        if circuit is None:
            return
        circuit.success_count += 1
        if circuit.state == CircuitState.HALF_OPEN:
            if circuit.success_count >= 2:  # half_open_max_successes
                circuit.state = CircuitState.CLOSED
                circuit.failure_count = 0
                circuit.last_state_change = time.time()
                logger.info(
                    "[CircuitBreaker] %s: HALF_OPEN → CLOSED (recovered!)",
                    agent_name,
                )
        else:
            # Reset failure count on success in CLOSED state
            circuit.failure_count = max(0, circuit.failure_count - 1)

        await self._state.set(
            f"agent:{agent_name}:health",
            {"status": "healthy", "circuit": circuit.state.value, "successes": circuit.success_count},
            source_agent="self_healing",
        )

    async def _record_failure(
        self, agent_name: AgentName, circuit: CircuitBreakerState | None
    ) -> None:
        if circuit is None:
            return
        circuit.failure_count += 1
        circuit.last_failure_time = time.time()

        if circuit.state == CircuitState.HALF_OPEN:
            circuit.state = CircuitState.OPEN
            circuit.last_state_change = time.time()
            logger.warning(
                "[CircuitBreaker] %s: HALF_OPEN → OPEN (recovery failed)",
                agent_name,
            )
        elif circuit.failure_count >= circuit.failure_threshold:
            circuit.state = CircuitState.OPEN
            circuit.last_state_change = time.time()
            logger.warning(
                "[CircuitBreaker] %s: CLOSED → OPEN (%d failures)",
                agent_name,
                circuit.failure_count,
            )

        await self._state.set(
            f"agent:{agent_name}:health",
            {
                "status": "degraded" if circuit.state == CircuitState.OPEN else "unhealthy",
                "circuit": circuit.state.value,
                "failures": circuit.failure_count,
            },
            source_agent="self_healing",
        )

    async def _execute_with_timeout(
        self,
        agent: AgentExecutor,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
        timeout: float,
    ) -> str:
        """Run agent.execute() with a strict timeout."""
        try:
            # F3: race the worker against its cancellation token so a
            # cancel_task on a parent task immediately kills this subtask.
            if self.cancel_event_provider is not None:
                exec_task = asyncio.ensure_future(
                    asyncio.wait_for(agent.execute(task_id, message, context, entities), timeout=timeout)
                )
                cancel_ev = self.cancel_event_provider(task_id)
                watcher = asyncio.ensure_future(cancel_ev.wait())
                done, pending = await asyncio.wait(
                    {exec_task, watcher}, return_when=asyncio.FIRST_COMPLETED
                )
                for p in pending:
                    p.cancel()
                if watcher in done:
                    if hasattr(agent, "cancel") and callable(agent.cancel):
                        try:
                            await agent.cancel()
                        except Exception:
                            pass
                    exec_task.cancel()
                    try:
                        await exec_task
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    except Exception:
                        pass
                    raise asyncio.CancelledError()
                return str(exec_task.result())

            result = await asyncio.wait_for(
                agent.execute(task_id, message, context, entities),
                timeout=timeout,
            )
            return str(result)
        except asyncio.TimeoutError:
            # Attempt to cancel the running task if agent supports it
            if hasattr(agent, "cancel") and callable(agent.cancel):
                try:
                    await agent.cancel()
                except Exception:
                    pass
            raise
        except asyncio.CancelledError:
            raise

    def _log_execution(
        self,
        agent_name: AgentName,
        task_id: str,
        attempt: int,
        *,
        success: bool,
        error: str = "",
    ) -> None:
        self._execution_log.append({
            "agent": agent_name,
            "task_id": task_id,
            "attempt": attempt,
            "success": success,
            "error": error,
            "timestamp": time.time(),
        })


# ─────────────────────────────────────────────────────────────────────────────
# (d) COORDINATOR AGENT + WORKER SWARM
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class WorkerDescriptor:
    """Describes a worker in the swarm."""

    agent_name: AgentName
    capabilities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    max_concurrent: int = 3
    current_load: int = 0
    total_completed: int = 0
    total_failed: int = 0
    avg_latency_ms: float = 0.0

    @property
    def available_slots(self) -> int:
        return max(0, self.max_concurrent - self.current_load)

    @property
    def load_ratio(self) -> float:
        if self.max_concurrent == 0:
            return 1.0
        return self.current_load / self.max_concurrent


@dataclass(slots=True)
class SwarmTask:
    """A task tracked by the worker swarm."""

    task_id: str
    message: str
    context: dict[str, Any]
    entities: dict[str, Any]
    assigned_worker: AgentName = ""
    status: str = "pending"   # pending | assigned | running | completed | failed
    result: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    error: str = ""


class WorkerSwarm:
    """
    A pool of capability-tagged workers with:
      - Capability-based task matching.
      - Load-balanced assignment (least-loaded-first).
      - Concurrent execution tracking.
      - Dynamic worker registration/deregistration.
    """

    def __init__(self, executor: SelfHealingExecutor) -> None:
        self._executor = executor
        self._workers: dict[AgentName, WorkerDescriptor] = {}
        self._active_tasks: dict[str, SwarmTask] = {}
        self._completed_tasks: Deque[SwarmTask] = deque(maxlen=2_000)
        self._lock = asyncio.Lock()

    async def add_worker(
        self,
        agent_name: AgentName,
        capabilities: list[str] | None = None,
        tags: list[str] | None = None,
        max_concurrent: int = 3,
    ) -> None:
        """Register a worker in the swarm."""
        async with self._lock:
            self._workers[agent_name] = WorkerDescriptor(
                agent_name=agent_name,
                capabilities=capabilities or [],
                tags=tags or [],
                max_concurrent=max_concurrent,
            )
        logger.info("[Swarm] Worker added: %s (caps=%s)", agent_name, capabilities)

    async def remove_worker(self, agent_name: AgentName) -> None:
        """Remove a worker from the swarm."""
        async with self._lock:
            self._workers.pop(agent_name, None)
        logger.info("[Swarm] Worker removed: %s", agent_name)

    async def find_best_worker(
        self,
        required_capabilities: list[str] | None = None,
        required_tags: list[str] | None = None,
    ) -> AgentName | None:
        """
        Find the best available worker for a task using capability matching
        and least-loaded-first selection.
        """
        logger.info("[Swarm] find_best_worker search: required_caps=%s, required_tags=%s. Registered workers: %s", 
                    required_capabilities, required_tags, 
                    {name: {"caps": w.capabilities, "tags": w.tags, "slots": w.available_slots} for name, w in self._workers.items()})
        async with self._lock:
            candidates: list[WorkerDescriptor] = []
            for w in self._workers.values():
                # Capability filter
                if required_capabilities:
                    if not any(cap in w.capabilities for cap in required_capabilities):
                        continue
                # Tag filter
                if required_tags:
                    if not any(tag in w.tags for tag in required_tags):
                        continue
                # Availability filter
                if w.available_slots > 0:
                    candidates.append(w)

            if not candidates:
                return None

            # Least-loaded-first, then by avg latency
            candidates.sort(key=lambda w: (w.load_ratio, w.avg_latency_ms))
            return candidates[0].agent_name

    async def assign_task(
        self,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
        *,
        preferred_worker: AgentName | None = None,
        required_capabilities: list[str] | None = None,
        required_tags: list[str] | None = None,
    ) -> SwarmTask:
        """Assign and execute a task on the best available worker."""
        # Find worker
        worker_name = preferred_worker
        if worker_name is None:
            worker_name = await self.find_best_worker(required_capabilities, required_tags)

        swarm_task = SwarmTask(
            task_id=task_id,
            message=message,
            context=context,
            entities=entities,
        )

        if worker_name is None:
            swarm_task.status = "failed"
            swarm_task.error = "No available workers"
            swarm_task.completed_at = time.time()
            self._completed_tasks.append(swarm_task)
            logger.error("[Swarm] No workers available for task %s", task_id)
            return swarm_task

        swarm_task.assigned_worker = worker_name
        swarm_task.status = "running"

        # Update load
        async with self._lock:
            worker = self._workers.get(worker_name)
            if worker:
                worker.current_load += 1

        self._active_tasks[task_id] = swarm_task
        start_time = time.monotonic()

        try:
            result = await self._executor.execute(
                worker_name, task_id, message, context, entities
            )
            swarm_task.result = result
            swarm_task.status = "completed"
            swarm_task.completed_at = time.time()

            async with self._lock:
                w = self._workers.get(worker_name)
                if w:
                    w.current_load = max(0, w.current_load - 1)
                    w.total_completed += 1
                    latency = (swarm_task.completed_at - start_time) * 1000
                    # Exponential moving average
                    w.avg_latency_ms = (
                        w.avg_latency_ms * 0.8 + latency * 0.2
                        if w.avg_latency_ms > 0
                        else latency
                    )

        except Exception as exc:
            swarm_task.status = "failed"
            swarm_task.error = str(exc)
            swarm_task.completed_at = time.time()

            async with self._lock:
                w = self._workers.get(worker_name)
                if w:
                    w.current_load = max(0, w.current_load - 1)
                    w.total_failed += 1

        self._active_tasks.pop(task_id, None)
        self._completed_tasks.append(swarm_task)
        return swarm_task

    async def get_swarm_status(self) -> dict[str, Any]:
        """Return a comprehensive status snapshot of the swarm."""
        async with self._lock:
            workers_info = {}
            for name, w in self._workers.items():
                workers_info[name] = {
                    "capabilities": w.capabilities,
                    "load": f"{w.current_load}/{w.max_concurrent}",
                    "load_ratio": round(w.load_ratio, 2),
                    "completed": w.total_completed,
                    "failed": w.total_failed,
                    "avg_latency_ms": round(w.avg_latency_ms, 1),
                }
            return {
                "total_workers": len(self._workers),
                "active_tasks": len(self._active_tasks),
                "completed_tasks": len(self._completed_tasks),
                "workers": workers_info,
            }

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": t.task_id,
                "worker": t.assigned_worker,
                "status": t.status,
                "elapsed": round(time.time() - t.created_at, 2),
            }
            for t in self._active_tasks.values()
        ]


class EliteCoordinator:
    """
    The Coordinator Agent — the strategic brain of the worker swarm.

    Lifecycle for every incoming complex task:
      1. DECOMPOSE: Break the task into subtasks with capability requirements.
      2. PLAN: Order subtasks by dependencies (DAG).
      3. DISPATCH: Assign subtasks to the best workers via the swarm.
      4. MONITOR: Track execution via shared state and the message bus.
      5. SYNTHESIZE: Collect results and produce a unified final response.
      6. HEAL: If any subtask fails, trigger self-healing and retry/fallback.

    This integrates directly with BaseAgent via the `coordination` parameter
    and with CommandRouter via the MULTI_STEP intent path.
    """

    COORDINATOR_NAME: AgentName = "elite_coordinator"

    def __init__(
        self,
        state: SharedReactiveState,
        bus: InterAgentMessageBus,
        executor: SelfHealingExecutor,
        swarm: WorkerSwarm,
        *,
        ai_handler: Any = None,
        ws_broadcast: Any = None,
    ) -> None:
        self._state = state
        self._bus = bus
        self._executor = executor
        self._swarm = swarm
        self._ai_handler = ai_handler
        self._ws_broadcast = ws_broadcast
        self._active_plans: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        # Optional cancellation hooks (F3): cancel_event_provider(task_id)->
        # asyncio.Event and register_child(parent_id, child_id). When set, DAG
        # subtasks register under the root task and each worker races the token.
        self.cancel_event_provider: Any = None
        self.register_child: Any = None
        self._coordination_sub_id: SubscriptionId | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize the coordinator: start state, bus, and subscribe."""
        await self._state.start()
        await self._bus.register_agent(self.COORDINATOR_NAME)
        await self._bus.register_broadcast_listener(self.COORDINATOR_NAME, "coordinator")

        # Subscribe to agent health changes for reactive re-planning
        self._coordination_sub_id = await self._state.subscribe(
            "agent:*:health",
            self._on_agent_health_change,
        )

        await self._state.set(
            f"agent:{self.COORDINATOR_NAME}:health",
            {"status": "healthy", "role": "coordinator"},
            source_agent=self.COORDINATOR_NAME,
        )
        logger.info("[Coordinator] Started and listening")

    async def stop(self) -> None:
        """Shut down the coordinator gracefully."""
        if self._coordination_sub_id:
            await self._state.unsubscribe(self._coordination_sub_id)
        await self._bus.unregister_agent(self.COORDINATOR_NAME)
        await self._state.stop()
        logger.info("[Coordinator] Stopped")

    # ── main execution entry point ─────────────────────────────────────────

    async def execute_task(
        self,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
    ) -> str:
        """
        Full coordination cycle: decompose → plan → dispatch → synthesize.
        Compatible with BaseAgent.execute() signature.
        """
        plan_id = f"plan_{task_id}"
        logger.info("[Coordinator] Executing task %s: %s", task_id, message[:100])

        await self._state.set(
            f"plan:{plan_id}:status",
            "decomposing",
            source_agent=self.COORDINATOR_NAME,
        )

        # Broadcast that we're starting
        await self._bus.broadcast(
            self.COORDINATOR_NAME,
            {"event": "plan_started", "plan_id": plan_id, "task_id": task_id},
            channel="coordinator",
        )

        try:
            # Step 1: Decompose into subtasks
            subtasks = await self._decompose(message, context, entities)
            if not subtasks:
                # Simple task — execute directly on best worker
                return await self._execute_single(task_id, message, context, entities)

            await self._state.set(
                f"plan:{plan_id}:status",
                "executing",
                source_agent=self.COORDINATOR_NAME,
            )
            await self._state.set(
                f"plan:{plan_id}:subtasks",
                len(subtasks),
                source_agent=self.COORDINATOR_NAME,
            )

            # Step 2 & 3: Dispatch subtasks respecting dependencies
            results = await self._execute_dag(plan_id, subtasks, context, entities)

            # Step 4: Synthesize
            await self._state.set(
                f"plan:{plan_id}:status",
                "synthesizing",
                source_agent=self.COORDINATOR_NAME,
            )
            final = await self._synthesize(message, results)

            # HITL fallback: if any subtask failed, ask the user instead of failing silently
            failed = [k for k, v in results.items() if str(v).startswith("[Failed]")]
            if failed:
                ask = ("\n\n⚠️ **I got stuck on some steps and need your help:** "
                       f"{', '.join(failed)}. Please provide more detail "
                       "(e.g. a direct URL, the correct file path, or clarification) and I will retry.")
                final = f"{final}\n{ask}"
                await self._stream_partial(plan_id, "ask_user", ask)

            await self._state.set(
                f"plan:{plan_id}:status",
                "completed",
                source_agent=self.COORDINATOR_NAME,
            )

            # Broadcast completion
            await self._bus.broadcast(
                self.COORDINATOR_NAME,
                {
                    "event": "plan_completed",
                    "plan_id": plan_id,
                    "subtasks": len(subtasks),
                    "result_preview": final[:200],
                },
                channel="coordinator",
            )

            return final

        except Exception as exc:
            logger.exception("[Coordinator] Task %s failed: %s", task_id, exc)
            await self._state.set(
                f"plan:{plan_id}:status",
                "failed",
                source_agent=self.COORDINATOR_NAME,
            )
            ask = (f"[Coordinator] Execution failed: {exc}\n\n"
                   "⚠️ **I got stuck and need your help:** please provide more detail "
                   "(e.g. a direct URL, the correct file path, or clarification) and I will retry.")
            await self._stream_partial(plan_id, "ask_user", ask)
            return ask

    # ── decomposition ──────────────────────────────────────────────────────

    async def _decompose(
        self,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Decompose a complex task into subtasks.
        Uses the AI handler if available; otherwise applies heuristic decomposition.
        """
        if self._ai_handler is not None:
            return await self._ai_decompose(message, context, entities)
        return self._heuristic_decompose(message, context, entities)

    async def _ai_decompose(
        self,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Use the LLM to intelligently decompose a task."""
        available_workers = await self._swarm.get_swarm_status()
        worker_summary = json.dumps(
            {
                name: info.get("capabilities", [])
                for name, info in available_workers.get("workers", {}).items()
            },
            indent=2,
        )

        prompt = f"""You are a task decomposition engine for an AI assistant.
Given a user request, break it into atomic subtasks that can each be handled by a single agent.

Available workers and capabilities:
{worker_summary}

User request: {message}

Respond with a JSON array of subtasks:
[
  {{
    "subtask_id": "st_1",
    "instruction": "specific actionable instruction",
    "required_capabilities": ["capability1"],
    "dependencies": [],
    "priority": "normal"
  }}
]

If the request is simple and only needs one agent, return a single-element array.
If the request cannot be decomposed further, return an empty array []."""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await self._ai_handler.generate(messages, task="decompose", require_json=True)
            text = getattr(response, "text", str(response))
            parsed = json.loads(text) if isinstance(text, str) else text

            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and isinstance(parsed.get("subtasks"), list):
                return parsed["subtasks"]
            return []
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            logger.warning("[Coordinator] AI decomposition failed, using heuristic: %s", exc)
            return self._heuristic_decompose(message, context, entities)

    def _heuristic_decompose(
        self,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Rule-based decomposition for common multi-step patterns."""
        msg_lower = message.lower()
        subtasks: list[dict[str, Any]] = []
        st_idx = 1

        # Pattern: search + code
        if any(kw in msg_lower for kw in ["search", "find", "look up", "research"]):
            search_topic = message
            for prefix in ["search for", "find", "look up", "research"]:
                if msg_lower.startswith(prefix):
                    search_topic = message[len(prefix):].strip()
                    break
            subtasks.append({
                "subtask_id": f"st_{st_idx}",
                "instruction": f"Research and find information about: {search_topic}",
                "required_capabilities": ["web_search"],
                "dependencies": [],
                "priority": "high",
            })
            st_idx += 1

        if any(kw in msg_lower for kw in ["code", "write a script", "program", "function", "implement"]):
            subtasks.append({
                "subtask_id": f"st_{st_idx}",
                "instruction": f"Write code based on the research: {message}",
                "required_capabilities": ["code_generation"],
                "dependencies": [f"st_{st_idx - 1}"] if st_idx > 1 else [],
                "priority": "normal",
            })
            st_idx += 1

        # Pattern: system + automation
        if any(kw in msg_lower for kw in ["open", "launch", "close", "kill process", "system"]):
            subtasks.append({
                "subtask_id": f"st_{st_idx}",
                "instruction": f"System operation: {message}",
                "required_capabilities": ["system_control"],
                "dependencies": [],
                "priority": "critical",
            })
            st_idx += 1

        # Pattern: create document
        if any(kw in msg_lower for kw in ["create a document", "write a report", "make a spreadsheet", "generate pdf"]):
            subtasks.append({
                "subtask_id": f"st_{st_idx}",
                "instruction": f"Create document: {message}",
                "required_capabilities": ["document_creation"],
                "dependencies": [],
                "priority": "normal",
            })
            st_idx += 1

        return subtasks

    # ── DAG execution ──────────────────────────────────────────────────────

    async def _execute_dag(
        self,
        plan_id: str,
        subtasks: list[dict[str, Any]],
        context: dict[str, Any],
        entities: dict[str, Any],
    ) -> dict[str, str]:
        """
        Execute subtasks as a DAG, respecting dependencies.
        Independent subtasks run in parallel; dependent ones wait.
        """
        results: dict[str, str] = {}
        completed_ids: set[str] = set()
        pending = list(subtasks)
        max_iterations = len(subtasks) * 3  # Safety guard against infinite loops

        iteration = 0
        while pending:
            if iteration >= max_iterations:
                logger.warning(
                    "[Coordinator] _execute_dag hit max_iterations (%d); clearing %d pending tasks",
                    max_iterations, len(pending),
                )
                pending.clear()
                break
            iteration += 1

            # Find all subtasks whose dependencies are satisfied
            ready: list[dict[str, Any]] = []
            still_pending: list[dict[str, Any]] = []

            for st in pending:
                deps = st.get("dependencies", [])
                if all(dep_id in completed_ids for dep_id in deps):
                    ready.append(st)
                else:
                    still_pending.append(st)

            if not ready:
                # Deadlock detection: mark remaining as failed
                logger.error("[Coordinator] DAG deadlock detected; %d subtasks stuck", len(still_pending))
                for st in still_pending:
                    results[st.get("subtask_id", "unknown")] = "[Failed] Dependency deadlock"
                    completed_ids.add(st.get("subtask_id", "unknown"))
                break

            # Execute all ready subtasks concurrently
            coros = []
            for st in ready:
                st_id = st.get("subtask_id", f"st_auto_{iteration}")
                instruction = st.get("instruction", "")
                required_caps = st.get("required_capabilities", [])
                priority_str = st.get("priority", "normal")

                # Inject previous results into context
                enriched_context = {
                    **context,
                    "_plan_id": plan_id,
                    "_subtask_id": st_id,
                    "_previous_results": dict(results),
                }

                coros.append(
                    self._dispatch_subtask(
                        st_id, instruction, enriched_context, entities, required_caps, priority_str
                    )
                )

            batch_results = await asyncio.gather(*coros, return_exceptions=True)

            for st, result in zip(ready, batch_results):
                st_id = st.get("subtask_id", "unknown")
                if isinstance(result, Exception):
                    results[st_id] = f"[Failed] {result}"
                    logger.error("[Coordinator] Subtask %s failed: %s", st_id, result)
                else:
                    results[st_id] = str(result)
                completed_ids.add(st_id)

                # Publish subtask result to shared state
                await self._state.set(
                    f"plan:{plan_id}:subtask:{st_id}:result",
                    results[st_id][:500],  # Truncate for state store
                    ttl=300.0,
                    source_agent=self.COORDINATOR_NAME,
                )

                # Stream partial result to the UI as soon as it lands
                await self._stream_partial(plan_id, st_id, results[st_id])

            pending = still_pending

        return results

    async def _dispatch_subtask(
        self,
        subtask_id: str,
        instruction: str,
        context: dict[str, Any],
        entities: dict[str, Any],
        required_capabilities: list[str],
        priority_str: str,
    ) -> str:
        """Dispatch a single subtask to the best worker via the swarm."""
        root_id = context.get("_root_task_id") or context.get("_plan_id", "plan")
        task_id = f"{root_id}_{subtask_id}"

        # F3: register this derived subtask id under the root task so a parent
        # cancel cascades to the worker's cancellation token immediately.
        if self.register_child is not None:
            root_id = context.get("_root_task_id")
            if root_id:
                try:
                    self.register_child(root_id, task_id)
                except Exception:
                    pass

        logger.debug(
            "[Coordinator] Dispatching %s (caps=%s): %s",
            subtask_id,
            required_capabilities,
            instruction[:80],
        )

        swarm_task = await self._swarm.assign_task(
            task_id=task_id,
            message=instruction,
            context=context,
            entities=entities,
            required_capabilities=required_capabilities if required_capabilities else None,
        )

        if swarm_task.status == "failed":
            raise RuntimeError(
                f"Subtask {subtask_id} failed on worker {swarm_task.assigned_worker}: "
                f"{swarm_task.error}"
            )

        return swarm_task.result

    async def _stream_partial(self, plan_id: str, subtask_id: str, result: str) -> None:
        """Stream a completed subtask's result to the UI as soon as it lands."""
        if not self._ws_broadcast:
            return
        try:
            from .. import ws_protocol
            task_id = plan_id[5:] if plan_id.startswith("plan_") else plan_id
            preview = str(result)[:300]
            if str(result).startswith("[Failed]"):
                chunk = ws_protocol.build_ai_chunk(
                    task_id, f"❌ Subtask {subtask_id} failed: {preview}", is_final=False
                )
            else:
                chunk = ws_protocol.build_ai_chunk(
                    task_id, f"✅ Subtask {subtask_id} complete:\n{preview}", is_final=False
                )
            await self._ws_broadcast(chunk)
        except Exception:
            pass

    async def _execute_single(
        self,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
    ) -> str:
        """Execute a simple task on the best available worker."""
        swarm_task = await self._swarm.assign_task(
            task_id=task_id,
            message=message,
            context=context,
            entities=entities,
        )
        if getattr(swarm_task, "status", "") == "failed":
            raise RuntimeError(
                f"Worker failed: {getattr(swarm_task, 'error', 'unknown error')}"
            )
        return swarm_task.result

    # ── synthesis ──────────────────────────────────────────────────────────

    async def _synthesize(
        self,
        original_message: str,
        results: dict[str, str],
    ) -> str:
        """Combine subtask results into a coherent final response."""
        if len(results) == 1:
            return next(iter(results.values()))

        if self._ai_handler is not None:
            return await self._ai_synthesize(original_message, results)

        # Fallback: concatenate results
        parts: list[str] = []
        for st_id, result in sorted(results.items()):
            if result.startswith("[Failed]"):
                parts.append(f"⚠️ {st_id}: {result}")
            else:
                parts.append(f"✅ {st_id}: {result[:300]}")
        return "\n\n".join(parts)

    async def _ai_synthesize(
        self,
        original_message: str,
        results: dict[str, str],
    ) -> str:
        """Use the LLM to synthesize a polished final response."""
        results_text = json.dumps(results, indent=2, ensure_ascii=False)
        prompt = f"""You are synthesizing results from multiple AI subtasks.

Original user request: {original_message}

Subtask results:
{results_text}

Produce a single, coherent, well-structured response that addresses the user's original request.
Do not mention subtask IDs or internal mechanics. Be concise but complete.
If any subtask failed, acknowledge it briefly and focus on what was accomplished."""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await self._ai_handler.generate(messages, task="synthesize")
            return getattr(response, "text", str(response))
        except Exception as exc:
            logger.warning("[Coordinator] AI synthesis failed: %s", exc)
            # Fallback: clean string join (no __wrapped__ hack)
            return "\n\n".join(
                f"{'⚠️' if v.startswith('[Failed]') else '✅'} {k}: {v[:300]}"
                for k, v in sorted(results.items())
            )

    # ── reactive handlers ──────────────────────────────────────────────────

    async def _on_agent_health_change(
        self, key: StateKey, new_value: StateValue, old_value: StateValue | None
    ) -> None:
        """React to agent health state changes."""
        if not isinstance(new_value, dict):
            return

        status = new_value.get("status", "")
        circuit = new_value.get("circuit", "")
        agent_name = key.split(":")[1] if ":" in key else "unknown"

        logger.info(
            "[Coordinator] Health change: %s → status=%s, circuit=%s",
            agent_name,
            status,
            circuit,
        )

        if status == "degraded" or circuit == "open":
            # Notify the swarm to deprioritize this worker
            logger.warning(
                "[Coordinator] Agent %s degraded; swarm will deprioritize",
                agent_name,
            )
            await self._bus.broadcast(
                self.COORDINATOR_NAME,
                {
                    "event": "agent_degraded",
                    "agent": agent_name,
                    "status": status,
                },
                channel="coordinator",
                priority=MessagePriority.HIGH,
            )

        elif status == "healthy" and old_value:
            old_dict = old_value if isinstance(old_value, dict) else {}
            if old_dict.get("status") in ("degraded", "unhealthy"):
                logger.info("[Coordinator] Agent %s recovered!", agent_name)
                await self._bus.broadcast(
                    self.COORDINATOR_NAME,
                    {"event": "agent_recovered", "agent": agent_name},
                    channel="coordinator",
                )


# ─────────────────────────────────────────────────────────────────────────────
# ECOSYSTEM HUB — SINGLE ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────


class EcosystemHub:
    """
    The single entry point that wires together all ecosystem components:
      - SharedReactiveState
      - InterAgentMessageBus
      - SelfHealingExecutor
      - WorkerSwarm
      - EliteCoordinator

    Usage:
        hub = EcosystemHub(ai_handler=my_ai, ws_broadcast=my_ws_fn)
        await hub.start()
        await hub.register_worker(my_code_agent, capabilities=["code_generation"])
        result = await hub.execute(task_id, message, context, entities)
        await hub.stop()
    """

    def __init__(
        self,
        *,
        ai_handler: Any = None,
        ws_broadcast: Any = None,
        default_timeout: float = 60.0,
        default_retries: int = 2,
        cancel_token_provider: Any = None,
        register_child: Any = None,
    ) -> None:
        self.state = SharedReactiveState()
        self.bus = InterAgentMessageBus()
        self.executor = SelfHealingExecutor(
            self.state,
            self.bus,
            default_timeout=default_timeout,
            default_retries=default_retries,
            ws_broadcast=ws_broadcast,
        )
        self.swarm = WorkerSwarm(self.executor)
        self.coordinator = EliteCoordinator(
            self.state,
            self.bus,
            self.executor,
            self.swarm,
            ai_handler=ai_handler,
            ws_broadcast=ws_broadcast,
        )
        self._ai_handler = ai_handler
        self._ws_broadcast = ws_broadcast
        self._started = False
        # F3: thread cancellation callbacks down to workers / DAG subtasks.
        self.executor.cancel_event_provider = cancel_token_provider
        self.coordinator.cancel_event_provider = cancel_token_provider
        self.coordinator.register_child = register_child

    async def start(self) -> None:
        """Boot up all ecosystem components."""
        if self._started:
            return
        await self.coordinator.start()
        self.bus.start_dlq_requeue()
        self._started = True
        logger.info("[EcosystemHub] All systems online")

    async def stop(self) -> None:
        """Gracefully shut down all components."""
        if not self._started:
            return
        self.bus.stop_dlq_requeue()
        await self.coordinator.stop()
        self._started = False
        logger.info("[EcosystemHub] All systems offline")

    async def register_worker(
        self,
        agent: AgentExecutor,
        *,
        capabilities: list[str] | None = None,
        tags: list[str] | None = None,
        max_concurrent: int = 3,
        timeout: float | None = None,
        fallback_chain: list[str] | None = None,
    ) -> None:
        """Register an agent as a worker in the ecosystem."""
        import yaml
        from pathlib import Path
        
        agent_name = getattr(agent, "AGENT_NAME", "unknown")

        # Prevent recursive dispatch: skip if agent is the commander itself
        if agent_name == "commander_agent":
            logger.debug("[EcosystemHub] Skipping recursive registration of commander_agent")
            return

        yaml_caps = None
        yaml_tags = None
        
        yaml_path = Path(__file__).resolve().parents[2] / "configs" / "agent_profiles.yaml"
        if yaml_path.exists():
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    profiles_data = yaml.safe_load(f) or {}
                    agent_yaml = profiles_data.get("agents", {}).get(agent_name)
                    if agent_yaml:
                        yaml_caps = agent_yaml.get("capabilities")
                        yaml_tags = agent_yaml.get("tags")
            except Exception as ey:
                logger.warning(f"Failed to read agent_profiles.yaml in register_worker: {ey}")

        if capabilities is None:
            if yaml_caps is not None:
                capabilities = list(yaml_caps)
            elif hasattr(agent, "CAPABILITIES"):
                capabilities = list(agent.CAPABILITIES) if agent.CAPABILITIES else []
            else:
                capabilities = []
            
        # Ensure the agent's name is always one of its capabilities so direct routing works
        if agent_name not in capabilities:
            capabilities.append(agent_name)
            
        if tags is None:
            if yaml_tags is not None:
                tags = list(yaml_tags)
            elif hasattr(agent, "TAGS"):
                tags = agent.TAGS or []
            else:
                tags = []

        # Cross-agent blackboard bridge: let agents publish/read shared state
        try:
            setattr(agent, "_shared_state", self.state)
        except Exception:
            pass

        await self.executor.register_agent(
            agent, timeout=timeout, fallback_chain=fallback_chain
        )
        await self.swarm.add_worker(
            agent.AGENT_NAME,
            capabilities=capabilities,
            tags=tags,
            max_concurrent=max_concurrent,
        )

        # Publish capability registry to shared state
        await self.state.set(
            f"registry:{agent.AGENT_NAME}",
            {
                "capabilities": capabilities or [],
                "tags": tags or [],
                "max_concurrent": max_concurrent,
            },
            source_agent="ecosystem_hub",
        )
        logger.info(
            "[EcosystemHub] Worker registered: %s (caps=%s, tags=%s)",
            agent.AGENT_NAME,
            capabilities,
            tags,
        )

    async def unregister_worker(self, agent_name: AgentName) -> None:
        """Remove a worker from the ecosystem."""
        await self.executor.unregister_agent(agent_name)
        await self.swarm.remove_worker(agent_name)
        await self.state.delete(f"registry:{agent_name}", source_agent="ecosystem_hub")

    async def execute(
        self,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
    ) -> str:
        """
        Execute a task through the full ecosystem pipeline.
        This is the primary entry point for the CommandRouter.
        """
        return await self.coordinator.execute_task(task_id, message, context, entities)

    async def execute_direct(
        self,
        agent_name: AgentName,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> str:
        """
        Execute a task directly on a specific agent (bypasses coordinator decomposition).
        Useful for CommandRouter single-intent dispatch.
        """
        return await self.executor.execute(
            agent_name, task_id, message, context, entities, timeout=timeout
        )

    async def get_status(self) -> dict[str, Any]:
        """Return a comprehensive status of the entire ecosystem."""
        swarm_status = await self.swarm.get_swarm_status()
        health_report = await self.executor.get_health_report()
        state_snapshot = await self.state.snapshot()
        queue_depths = await self.bus.all_queue_depths()
        dead_letters = await self.bus.dead_letter_count()

        return {
            "ecosystem": {
                "started": self._started,
                "agents_registered": len(health_report),
            },
            "swarm": swarm_status,
            "health": health_report,
            "message_bus": {
                "queue_depths": queue_depths,
                "dead_letter_count": dead_letters,
            },
            "state_keys": len(state_snapshot),
        }

    async def send_message(
        self,
        sender: AgentName,
        recipient: AgentName,
        payload: dict[str, Any],
        *,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> str:
        """Send a direct inter-agent message via the bus."""
        return await self.bus.send_task(
            sender=sender,
            recipient=recipient,
            task_id=uuid.uuid4().hex[:12],
            payload=payload,
            priority=priority,
        )

    async def broadcast(
        self,
        sender: AgentName,
        payload: dict[str, Any],
        channel: str = "global",
    ) -> int:
        """Broadcast a message to all agents on a channel."""
        return await self.bus.broadcast(sender, payload, channel=channel)


from .base_agent import BaseAgent


class EcosystemAgent(BaseAgent):
    """
    Production-Grade Elite Ecosystem Agent.
    Replaces legacy CommanderAgent with full Coordinator + WorkerSwarm + SelfHealing pipeline.
    """
    AGENT_NAME = "commander_agent"
    DESCRIPTION = "Elite Ecosystem Coordinator — multi-agent DAG execution, swarm load balancing, and self-healing"
    CAPABILITIES = ["dag_decomposition", "multi_agent_orchestration", "workflow_synthesis", "self_healing"]
    AGENT_TOOLS = ["subtask_dispatch", "dag_execution", "swarm_load_balance"]
    TAGS = ["commander", "orchestrator", "swarm", "dag", "ecosystem"]

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        if self.coordination and hasattr(self.coordination, "execute"):
            res = await self.coordination.execute(task_id, message, context, entities)
            if res:
                return res
        # Lazily build the hub ONCE and reuse it across calls — building a
        # fresh EcosystemHub + re-registering every worker per execution was
        # excessive per-call overhead (each register_worker attaches shared
        # state / spawns coordination wiring).
        hub = getattr(self, "_ecosystem_hub", None)
        if hub is None:
            # Double-checked locking for safe concurrent lazy initialization
            lock = getattr(self, "_hub_lock", None)
            if lock is None:
                lock = asyncio.Lock()
                self._hub_lock = lock
            async with lock:
                hub = getattr(self, "_ecosystem_hub", None)
                if hub is None:
                    # F3: wire cancellation callbacks so cancel_task(parent) propagates
                    # into ecosystem DAG subtasks immediately.
                    _tok_provider = None
                    _reg_child = None
                    if self.orchestrator is not None:
                        _tok_provider = self.orchestrator._get_cancel_event
                        _reg_child = self.orchestrator.register_cancel_child
                    hub = EcosystemHub(
                        ai_handler=self.ai_handler,
                        ws_broadcast=self.ws_broadcast,
                        cancel_token_provider=_tok_provider,
                        register_child=_reg_child,
                    )
                    if self.orchestrator and hasattr(self.orchestrator, "agents"):
                        for name, inst in self.orchestrator.agents.items():
                            if inst.agent != self:
                                await hub.register_worker(inst.agent)
                    self._ecosystem_hub = hub
        await hub.start()
        # Stamp root task id so derived subtask ids register under it.
        if isinstance(context, dict):
            context["_root_task_id"] = task_id
        return await hub.execute(task_id, message, context, entities)
