from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import pkgutil
import importlib
import functools
import typing
import uuid
from collections import deque
from .contracts import MEDIA_KEYWORDS, GuardrailExceeded
from dataclasses import dataclass
from typing import Any, Optional

from apps.brain.core.persistence import EventStore
from apps.brain.core.scheduler import (
    AgentState,
    TaskPriority,
    AgentInstance,
    TaskResult,
    DomainLane,
    ExecutionMetrics,
    TaskPreemptionState,
)
from apps.brain.agents.base_agent import BaseAgent

logger = logging.getLogger("makima.nextgen_orchestrator")

# Maximum delegation hops to prevent infinite recursion between agents.
_MAX_DELEGATION_DEPTH: int = 3


# =============================================================================
# Actor-Model Inter-Agent Delegation (SOTA Upgrade)
# =============================================================================

@dataclass
class AgentDelegationRequest:
    """
    Structured Actor-Model message for type-safe inter-agent delegation.

    An agent's ``execute()`` can return this instead of a plain string to hand
    off control to another agent. The kernel unpacks it in ``_run_agent`` and
    re-dispatches — with a depth counter to prevent infinite chains.

    Attributes:
        target_agent: Registry key of the agent to delegate to (e.g. ``"code_agent"``).
        payload:      The message or structured dict to pass to the target agent.
        reason:       Human-readable reason for the delegation (logged, not executed).
    """
    target_agent: str
    payload: dict[str, Any] | str
    reason: str = "Dynamic delegation based on agent capability resolution."


# =============================================================================
# SOTA Adaptive Circuit Breaker (Google SRE Standard)
# =============================================================================

class _CircuitState(str):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing — fast-reject all tasks
    HALF_OPEN = "half_open" # Recovery probe — allow one task through


class AdaptiveCircuitBreaker:
    """
    SOTA Per-Agent Adaptive Circuit Breaker (Google SRE Standard).

    Upgrades:
      1. Rolling Sliding-Window (N=10) error-rate evaluation (>50% error rate over >=4 calls).
      2. Consecutive Failure Fast-Trip (threshold=3).
      3. Latency Spike Tripping (p95 latency > 25.0s over >=4 calls).
      4. Exponential Backoff Scaling (60s -> 120s -> 240s -> max 600s).
      5. Single-Probe Isolation in HALF_OPEN state with auto-recovery or trip escalation.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_window_s: float = 60.0,
        max_recovery_window_s: float = 600.0,
        failure_rate_threshold: float = 0.5,
        latency_threshold_s: float = 25.0,
        window_size: int = 10,
    ) -> None:
        self._state: str = _CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._failure_threshold: int = failure_threshold
        self._base_recovery_window_s: float = recovery_window_s
        self._current_recovery_window_s: float = recovery_window_s
        self._max_recovery_window_s: float = max_recovery_window_s
        self._failure_rate_threshold: float = failure_rate_threshold
        self._latency_threshold_s: float = latency_threshold_s
        self._opened_at: float = 0.0
        self._probe_in_flight: bool = False
        self._trip_count: int = 0

        # Rolling sliding window of recent calls: dict(success, duration_s, ts, error)
        self._window: deque[dict[str, Any]] = deque(maxlen=window_size)

    @property
    def state(self) -> str:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def _recovery_window_s(self) -> float:
        return self._current_recovery_window_s

    def is_open(self) -> bool:
        """Returns True if the circuit should reject the next task immediately."""
        if self._state == _CircuitState.OPEN:
            elapsed = time.time() - self._opened_at
            if elapsed >= self._current_recovery_window_s:
                self._state = _CircuitState.HALF_OPEN
                self._probe_in_flight = False
                logger.info("[CircuitBreaker] Transitioning to HALF_OPEN after %.1fs recovery window", elapsed)
                return False
            return True
        return False

    def allow_probe(self) -> bool:
        """In HALF_OPEN state, allow exactly one probe. Returns True if probe allowed."""
        if self._state == _CircuitState.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def record_success(self, duration_s: float = 0.0) -> None:
        """Record a successful execution — resets failure count and closes the circuit."""
        self._window.append({
            "success": True,
            "duration_s": duration_s,
            "ts": time.time(),
            "error": None,
        })
        self._consecutive_failures = 0
        self._probe_in_flight = False

        if self._state != _CircuitState.CLOSED:
            logger.info(
                "[CircuitBreaker] Probe succeeded — closing circuit (trip_count reset from %d to 0)",
                self._trip_count,
            )
            self._trip_count = 0
            self._current_recovery_window_s = self._base_recovery_window_s
            self._state = _CircuitState.CLOSED

    def record_failure(self, duration_s: float = 0.0, error_type: str = "execution_error") -> None:
        """Record a failed execution — may open the circuit with exponential backoff."""
        self._window.append({
            "success": False,
            "duration_s": duration_s,
            "ts": time.time(),
            "error": error_type,
        })
        self._probe_in_flight = False
        self._consecutive_failures += 1

        should_trip = False
        trip_reason = ""

        # Condition 1: Consecutive failures threshold
        if self._consecutive_failures >= self._failure_threshold:
            should_trip = True
            trip_reason = f"{self._consecutive_failures} consecutive failures"

        # Condition 2: Rolling window error rate (min 4 samples)
        elif len(self._window) >= 4:
            fails = sum(1 for call in self._window if not call["success"])
            rate = fails / len(self._window)
            if rate >= self._failure_rate_threshold:
                should_trip = True
                trip_reason = f"error rate {rate:.1%} exceeds {self._failure_rate_threshold:.1%}"

            # Condition 3: Latency degradation trip
            if not should_trip:
                durations = [call["duration_s"] for call in self._window if call.get("duration_s")]
                if len(durations) >= 4:
                    sorted_d = sorted(durations)
                    p95 = sorted_d[min(len(sorted_d) - 1, int(len(sorted_d) * 0.95))]
                    if p95 >= self._latency_threshold_s:
                        should_trip = True
                        trip_reason = f"p95 latency ({p95:.1f}s) exceeded {self._latency_threshold_s:.1f}s threshold"

        if should_trip:
            self._state = _CircuitState.OPEN
            self._opened_at = time.time()
            self._trip_count += 1
            # Exponential backoff: base * (2^(trip_count-1)) capped at max
            self._current_recovery_window_s = min(
                self._max_recovery_window_s,
                self._base_recovery_window_s * (2 ** (self._trip_count - 1)),
            )
            logger.warning(
                "[CircuitBreaker] Agent circuit OPENED (%s). Trip count: %d. Recovery window: %.0fs",
                trip_reason, self._trip_count, self._current_recovery_window_s,
            )

    def get_status(self) -> dict[str, Any]:
        fails = sum(1 for call in self._window if not call["success"])
        rate = (fails / len(self._window)) if self._window else 0.0
        durations = [call["duration_s"] for call in self._window if call.get("duration_s")]
        avg_lat = (sum(durations) / len(durations)) if durations else 0.0

        return {
            "state": self._state,
            "consecutive_failures": self._consecutive_failures,
            "threshold": self._failure_threshold,
            "recovery_window_s": self._current_recovery_window_s,
            "trip_count": self._trip_count,
            "window_samples": len(self._window),
            "window_error_rate_pct": round(rate * 100.0, 1),
            "window_avg_latency_s": round(avg_lat, 2),
        }


# Aliasing for backward compatibility
AgentCircuitBreaker = AdaptiveCircuitBreaker


# =============================================================================
# Media Agent (Native Playback & Audio Control Wrapper)
# =============================================================================

class MediaAgent(BaseAgent):
    """
    Media, music, and video playback control agent for Makima OS.
    Controls YouTube and Spotify Web Player, seek, volume, track navigation, and ad-skipping.
    """
    AGENT_NAME = "media"
    REGISTRY_KEY = "media_agent"
    DESCRIPTION = (
        "Music and video playback control on YouTube and Spotify Web Player, "
        "including play, pause, resume, toggle, seek, next/previous track, volume management, and ad-skipping."
    )
    SYSTEM_PROMPT = (
        "You are Makima's Media Agent. You control music and video playback on YouTube and Spotify Web Player.\n"
        "Use the appropriate tools (media_play, media_pause, media_resume, media_toggle, media_next, media_previous, "
        "media_seek, media_set_volume, media_get_state, media_skip_ad) to handle user playback and volume requests directly."
    )
    AGENT_TOOLS = [
        "media_play", "media_pause", "media_resume", "media_toggle",
        "media_next", "media_previous", "media_seek", "media_set_volume",
        "media_get_state", "media_skip_ad"
    ]
    CAPABILITIES = [
        "media_control", "music_playback", "volume_management",
        "spotify_control", "youtube_control", "transcript_fetching"
    ]
    TAGS = ["media", "music", "youtube", "spotify", "sound", "playback", "audio"]
    DOMAIN_LANE = "media"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    async def execute(
        self,
        task_id: str,
        message: str,
        context: dict[str, Any],
        entities: Optional[dict[str, Any]] = None,
    ) -> str:
        session_id = context.get("conversation_id") or task_id
        try:
            final_out = await self.run_sdk_execution(
                task_id=session_id,
                message=message,
                context=context,
                max_turns=6,
                task_type="fast_chat",
                input_guardrails=[],
                output_guardrails=[],
            )
            self._partial_result = final_out
            return final_out
        except Exception as e:
            logger.error("[MediaAgent] SDK execution error: %s", e)
            return f"Media task complete nahi hua: {e}"


# =============================================================================
# Dynamic Agent Discovery (Microkernel)
# =============================================================================

def discover_agent_classes() -> dict[str, type]:
    """
    Scan ``apps/brain/agents/`` for every concrete ``BaseAgent`` subclass.
    """
    try:
        agents_pkg = importlib.import_module("apps.brain.agents")
    except ImportError:
        logger.error("Cannot import apps.brain.agents package for discovery")
        return {}

    discovered: dict[str, type] = {}

    for _importer, modname, _ispkg in pkgutil.iter_modules(agents_pkg.__path__):
        if modname in ("base_agent", "__init__"):
            continue

        fqn = f"apps.brain.agents.{modname}"
        try:
            module = importlib.import_module(fqn)
        except Exception as exc:
            logger.warning("Agent module import failed (%s): %s", fqn, exc)
            continue

        for attr_name in dir(module):
            try:
                attr = getattr(module, attr_name)
            except Exception:
                continue

            if not isinstance(attr, type):
                continue

            if attr.__name__ == "BaseAgent":
                continue

            mro_names = [base.__name__ for base in attr.__mro__]
            if "BaseAgent" not in mro_names:
                continue

            if getattr(attr, "ENABLED", True) is False:
                continue

            if not getattr(attr, "AGENT_NAME", None):
                continue

            # Derive the orchestrator registry key
            registry_key = getattr(attr, "REGISTRY_KEY", None)
            if registry_key:
                key = registry_key
            elif modname.endswith("_agent"):
                key = modname
            else:
                name = attr.AGENT_NAME
                key = name if name.endswith("_agent") else f"{name}_agent"

            if key in discovered:
                logger.debug(
                    "Agent key '%s' already mapped to %s — skipping duplicate %s",
                    key, discovered[key].__name__, attr.__name__,
                )
                continue

            discovered[key] = attr
            logger.info("Discovered agent plugin: %s → %s.%s", key, fqn, attr_name)

    # Ensure MediaAgent is always discovered and registered
    if "media_agent" not in discovered:
        discovered["media_agent"] = MediaAgent
        logger.info("Discovered agent plugin: media_agent → apps.brain.core.kernel.MediaAgent")

    return discovered


# =============================================================================
# Multi-Lane Domain Concurrency Scheduler
# =============================================================================

class InteractiveSlotManager:
    """
    Single-lane generation-counted lease manager for agent preemption.
    """

    def __init__(self, preemption_timeout_s: float = 3.0, lane_name: str = "default") -> None:
        self.lane_name = lane_name
        self._lock = asyncio.Lock()
        self._current_lease_id: int = 0
        self._holder_task_id: Optional[str] = None
        self._state: TaskPreemptionState = TaskPreemptionState.IDLE
        self._preemption_timeout_s: float = preemption_timeout_s
        self._idle_event = asyncio.Event()
        self._idle_event.set()

    @property
    def current_lease_id(self) -> int:
        return self._current_lease_id

    @property
    def holder_task_id(self) -> Optional[str]:
        return self._holder_task_id

    @property
    def state(self) -> TaskPreemptionState:
        return self._state

    @property
    def is_idle(self) -> bool:
        return self._holder_task_id is None and self._idle_event.is_set()

    async def acquire_slot(self, task_id: str, priority_level: int = 1) -> int:
        """
        Acquire slot in this lane. If priority_level >= 2 (CRITICAL), preempt current holder.
        Returns a unique monotonically increasing lease_id.
        """
        while True:
            should_wait_preemption = False
            async with self._lock:
                if self._holder_task_id == task_id:
                    return self._current_lease_id

                if self._holder_task_id is None:
                    self._current_lease_id += 1
                    self._holder_task_id = task_id
                    self._state = TaskPreemptionState.RUNNING
                    self._idle_event.clear()
                    return self._current_lease_id

                if priority_level >= 2:
                    self._state = TaskPreemptionState.PREEMPTING
                    self._idle_event.clear()
                    should_wait_preemption = True
                else:
                    should_wait_preemption = False

            if should_wait_preemption:
                try:
                    await asyncio.wait_for(
                        self._idle_event.wait(), timeout=self._preemption_timeout_s
                    )
                except asyncio.TimeoutError:
                    pass

                async with self._lock:
                    if self._holder_task_id == task_id:
                        return self._current_lease_id
                    if self._holder_task_id is None or priority_level >= 2:
                        self._current_lease_id += 1
                        self._holder_task_id = task_id
                        self._state = TaskPreemptionState.RUNNING
                        self._idle_event.clear()
                        return self._current_lease_id
            else:
                slot_wait_timeout = self._preemption_timeout_s * 3
                try:
                    await asyncio.wait_for(
                        self._idle_event.wait(), timeout=slot_wait_timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[Lane:%s] INTERACTIVE slot wait timed out (%.1fs) for task %s — degraded acquisition.",
                        self.lane_name, slot_wait_timeout, task_id,
                    )
                    async with self._lock:
                        if self._holder_task_id is not None and self._holder_task_id != task_id:
                            self._current_lease_id += 1
                            self._holder_task_id = task_id
                            self._state = TaskPreemptionState.RUNNING
                            self._idle_event.clear()
                            return self._current_lease_id
                    continue

    async def release_slot(self, task_id: str, lease_id: int) -> bool:
        """
        Release slot ONLY if lease_id matches active lease and holder matches task_id.
        """
        async with self._lock:
            if self._holder_task_id == task_id and (lease_id == 0 or self._current_lease_id == lease_id):
                self._holder_task_id = None
                self._state = TaskPreemptionState.IDLE
                self._idle_event.set()
                return True
            return False


class DomainLaneSlotManager:
    """
    Multi-Lane Domain Concurrency Manager.

    Partitions execution into isolated physical/cognitive domain lanes (MEDIA, BROWSER, SYSTEM,
    COGNITIVE, COMMUNICATION, DEFAULT). Enables concurrent foreground execution across distinct domains
    without lock contention, while maintaining strict mutual exclusion within the same domain lane.
    """

    def __init__(self, preemption_timeout_s: float = 3.0) -> None:
        self._preemption_timeout_s = preemption_timeout_s
        self._lanes: dict[DomainLane, InteractiveSlotManager] = {
            lane: InteractiveSlotManager(preemption_timeout_s=preemption_timeout_s, lane_name=lane.value)
            for lane in DomainLane
        }

    @staticmethod
    def resolve_agent_lane(
        agent_name: str,
        agent_instance: Optional[Any] = None,
    ) -> DomainLane:
        """Resolve an agent to its canonical DomainLane.

        SOTA Upgrade: checks the agent class's ``DOMAIN_LANE`` attribute first
        (zero-hardcoded affordance routing). Falls back to the name-matching
        table for agents that have not yet declared ``DOMAIN_LANE``.

        Args:
            agent_name:     Registry key string (e.g. ``"media_agent"``).
            agent_instance: Optional ``AgentInstance`` dataclass.  When supplied
                            the method inspects the underlying agent class for a
                            ``DOMAIN_LANE`` attribute before falling back to the
                            name table, ensuring new agents self-classify without
                            requiring a kernel change.
        """
        # ── Step 1: Class-attribute fast-path (SOTA affordance routing) ────────
        if agent_instance is not None:
            agent_obj = getattr(agent_instance, "agent", agent_instance)
            if agent_obj is not None:
                declared = (
                    getattr(agent_obj, "DOMAIN_LANE", None)
                    or getattr(agent_obj, "LANE", None)
                )
                if declared is not None:
                    if isinstance(declared, DomainLane):
                        return declared
                    try:
                        return DomainLane(declared)
                    except ValueError:
                        logger.debug(
                            "Agent '%s' has invalid DOMAIN_LANE %r — falling back to name table",
                            agent_name, declared,
                        )

        # ── Step 2: Name-matching fallback (backward compat) ───────────────────
        name = agent_name.lower().replace("_agent", "").strip()
        if name in MEDIA_KEYWORDS:
            return DomainLane.MEDIA
        elif name in ("browser", "web", "scraper", "playwright"):
            return DomainLane.BROWSER
        elif name in ("system", "devops", "security", "os", "filesystem"):
            return DomainLane.SYSTEM
        elif name in ("code", "research", "data_analyst", "data", "document", "creative", "memory", "voice"):
            return DomainLane.COGNITIVE
        elif name in ("messaging", "automation", "whatsapp", "telegram", "discord", "email", "notification"):
            return DomainLane.COMMUNICATION
        return DomainLane.DEFAULT


    def get_lane(self, lane: DomainLane | str) -> InteractiveSlotManager:
        if isinstance(lane, str):
            try:
                lane = DomainLane(lane)
            except ValueError:
                lane = DomainLane.DEFAULT
        return self._lanes[lane]

    async def acquire_lane_slot(
        self, task_id: str, priority_level: int = 1, domain_lane: DomainLane = DomainLane.DEFAULT,
    ) -> int:
        """Acquire a generation-counted slot lease in the target domain lane."""
        lane_mgr = self.get_lane(domain_lane)
        return await lane_mgr.acquire_slot(task_id, priority_level)

    async def release_lane_slot(
        self, task_id: str, lease_id: int, domain_lane: DomainLane = DomainLane.DEFAULT,
    ) -> bool:
        """Release slot lease in the target domain lane."""
        lane_mgr = self.get_lane(domain_lane)
        return await lane_mgr.release_slot(task_id, lease_id)

    async def force_release_all_for_task(self, task_id: str) -> list[DomainLane]:
        """Immediate release of all domain lane leases held by a task upon cancellation."""
        released: list[DomainLane] = []
        for lane, mgr in self._lanes.items():
            if mgr.holder_task_id == task_id:
                await mgr.release_slot(task_id, mgr.current_lease_id)
                released.append(lane)
        return released

    # ── Backward Compatibility API for legacy InteractiveSlotManager callers ──
    @property
    def current_lease_id(self) -> int:
        return self._lanes[DomainLane.DEFAULT].current_lease_id

    @property
    def holder_task_id(self) -> Optional[str]:
        return self._lanes[DomainLane.DEFAULT].holder_task_id

    @property
    def state(self) -> TaskPreemptionState:
        return self._lanes[DomainLane.DEFAULT].state

    @property
    def is_idle(self) -> bool:
        return all(lane.is_idle for lane in self._lanes.values())

    @property
    def _idle_event(self) -> asyncio.Event:
        return self._lanes[DomainLane.DEFAULT]._idle_event

    async def acquire_slot(self, task_id: str, priority_level: int = 1) -> int:
        return await self.acquire_lane_slot(task_id, priority_level, DomainLane.DEFAULT)

    async def release_slot(self, task_id: str, lease_id: int) -> bool:
        released = False
        for lane in self._lanes.values():
            if lane.holder_task_id == task_id:
                if await lane.release_slot(task_id, lease_id):
                    released = True
        return released

    def get_lane_status(self) -> dict[str, dict[str, Any]]:
        return {
            lane.value: {
                "state": mgr.state.value,
                "holder_task_id": mgr.holder_task_id,
                "current_lease_id": mgr.current_lease_id,
                "is_idle": mgr.is_idle,
            }
            for lane, mgr in self._lanes.items()
        }


# =============================================================================
# Kernel Telemetry & Observability Engine
# =============================================================================

class KernelTelemetryCollector:
    """
    Live Telemetry Collector for Makima OS.
    Maintains rolling latencies (p50, p95, avg), throughput, error rates,
    and domain lane utilization.
    """

    def __init__(self) -> None:
        self._boot_time: float = time.time()
        self._agent_metrics: dict[str, ExecutionMetrics] = {}
        self._lane_metrics: dict[str, ExecutionMetrics] = {
            lane.value: ExecutionMetrics() for lane in DomainLane
        }
        self._total_recoveries: int = 0
        self._recovery_history: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    def get_agent_metrics(self, agent_name: str) -> ExecutionMetrics:
        if agent_name not in self._agent_metrics:
            self._agent_metrics[agent_name] = ExecutionMetrics()
        return self._agent_metrics[agent_name]

    def record_execution(
        self,
        agent_name: str,
        domain_lane: DomainLane,
        duration_s: float,
        success: bool,
        tokens: int = 0,
        tool_calls: int = 0,
        status: str = "completed",
    ) -> None:
        # Record agent metrics
        am = self.get_agent_metrics(agent_name)
        am.record_run(duration_s, success, tokens, tool_calls, status)

        # Record domain lane metrics
        lm = self._lane_metrics.get(domain_lane.value)
        if lm:
            lm.record_run(duration_s, success, tokens, tool_calls, status)

    def record_recovery(self, source_agent: str, target_agent: str, success: bool = True) -> None:
        self._total_recoveries += 1
        if len(self._recovery_history) >= 200:
            self._recovery_history.pop(0)
        self._recovery_history.append({
            "source_agent": source_agent,
            "target_agent": target_agent,
            "success": success,
            "timestamp": time.time(),
        })

    def get_telemetry_snapshot(
        self,
        lane_manager: Optional[DomainLaneSlotManager] = None,
        circuit_breakers: Optional[dict[str, AdaptiveCircuitBreaker]] = None,
        event_store: Optional[EventStore] = None,
    ) -> dict[str, Any]:
        uptime_s = round(time.time() - self._boot_time, 1)

        total_dispatched = sum(m.total_dispatched for m in self._agent_metrics.values())
        total_completed = sum(m.total_completed for m in self._agent_metrics.values())
        total_failed = sum(m.total_failed for m in self._agent_metrics.values())
        total_tokens = sum(m.total_tokens for m in self._agent_metrics.values())
        total_tool_calls = sum(m.total_tool_calls for m in self._agent_metrics.values())

        agents_data = {
            name: metrics.to_dict() for name, metrics in self._agent_metrics.items()
        }
        lanes_data = {
            name: metrics.to_dict() for name, metrics in self._lane_metrics.items()
        }

        circuits_data = {}
        if circuit_breakers:
            circuits_data = {name: cb.get_status() for name, cb in circuit_breakers.items()}

        lane_occupancy = lane_manager.get_lane_status() if lane_manager else {}
        storage_stats = event_store.get_storage_stats() if event_store else {}

        return {
            "uptime_s": uptime_s,
            "summary": {
                "total_dispatched": total_dispatched,
                "total_completed": total_completed,
                "total_failed": total_failed,
                "total_recoveries": self._total_recoveries,
                "total_tokens": total_tokens,
                "total_tool_calls": total_tool_calls,
                "global_error_rate_pct": round((total_failed / total_dispatched * 100.0), 1) if total_dispatched else 0.0,
            },
            "agents": agents_data,
            "lanes": lanes_data,
            "lane_occupancy": lane_occupancy,
            "circuit_breakers": circuits_data,
            "storage": storage_stats,
        }


# =============================================================================
# Next-Gen Orchestrator (Kernel)
# =============================================================================

class NextGenOrchestrator:
    """
    Next-Gen Kernel Orchestrator — drop-in replacement for AgentOrchestrator.

    SOTA Upgrades:
      1. **Multi-Lane Domain Concurrency** — independent domain scheduling (MEDIA, BROWSER, SYSTEM, COGNITIVE, COMMUNICATION)
      2. **Adaptive Sliding-Window Circuit Breakers** — SRE standard with exponential backoff & latency tripping
      3. **Real-time Observability & Telemetry API** — p50/p95 tracking, lane utilization, and WS streaming
      4. **Sub-5ms Cascade Task Cancellation** — instant domain lease revocation and async future abortion
      5. **Event Sourcing with WAL Auto-Compaction** — SQLite WAL log with hot ring buffer and background compaction
    """

    def __init__(
        self,
        ai_handler: Any,
        memory: Any,
        tool_registry: Any = None,
        guardrails: Any = None,
        ws_broadcast: Any = None,
        config: Optional[dict] = None,
        learning_engine: Any = None,
        learning_coordinator: Any = None,
        reflexion_engine: Any = None,
    ) -> None:
        self.ai_handler = ai_handler
        self.memory = memory
        self.tool_registry = tool_registry
        self.guardrails = guardrails
        self.ws_broadcast = ws_broadcast
        self.config = config or {}
        self.reflexion_engine = reflexion_engine or learning_engine
        self.learning_engine = self.reflexion_engine
        self.learning_coordinator = learning_coordinator or self.reflexion_engine

        # ── Agent instances ─────────────────────────────────────────────────
        self.agents: dict[str, AgentInstance] = {}
        self.agent_locks: dict[str, asyncio.Lock] = {}

        # ── Preemption system ───────────────────────────────────────────────
        self._preemption_timeout_s: float = float(self.config.get("preemption_timeout_s", 3.0))

        # ── Multi-Lane Domain Slot Manager ──────────────────────────────────
        self._lane_manager = DomainLaneSlotManager(
            preemption_timeout_s=self._preemption_timeout_s
        )
        self._slot_manager = self._lane_manager  # Backward compatibility alias
        self._task_leases: dict[str, dict[DomainLane, int]] = {}
        self._active_interactive_refcount: dict[str, int] = {}
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._background_tasks_lock = asyncio.Lock()
        self._interactive_state_lock = asyncio.Lock()

        # ── Kernel Telemetry & Observability ────────────────────────────────
        self._telemetry = KernelTelemetryCollector()

        # ── Cancellation propagation ────────────────────────────────────────
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._parent_children: dict[str, set[str]] = {}

        # ── Event store ─────────────────────────────────────────────────────
        event_db_path = self.config.get(
            "KERNEL_EVENTS_DB", "~/.makima/kernel_events.db"
        )
        self._event_store = EventStore(event_db_path)

        # ── Durable Task Engine (Module 4) ──────────────────────────────────
        from apps.brain.core.durable_task_engine import DurableTaskEngine
        self._durable_task_engine = DurableTaskEngine(self._event_store)

        # ── Drafts ──────────────────────────────────────────────────────────
        self._drafts: dict[str, dict] = {}

        # ── Coordination ────────────────────────────────────────────────────
        self.coordination = None

        # ── Subtask concurrency limiter ─────────────────────────────────────
        self.subtask_semaphore = asyncio.Semaphore(4)

        # ── Background task limiter ─────────────────────────────────────────
        self._max_background_tasks: int = self.config.get("max_background_tasks", 20)

        # ── Per-agent adaptive circuit breakers ─────────────────────────────
        _cb_failure_threshold: int = int(self.config.get("circuit_breaker_failure_threshold", 3))
        _cb_recovery_window_s: float = float(self.config.get("circuit_breaker_recovery_window_s", 60.0))
        self._circuit_breakers: dict[str, AdaptiveCircuitBreaker] = {}
        self._cb_failure_threshold = _cb_failure_threshold
        self._cb_recovery_window_s = _cb_recovery_window_s

        # ── Boot synchronization event ──────────────────────────────────────
        self._boot_complete = asyncio.Event()

        # ── Boot sequence ───────────────────────────────────────────────────
        self._init_agents()
        self._replay_events_sync()
        self._boot_complete.set()

    async def async_boot(self) -> None:
        """
        Asynchronously (re-)initialize agents and replay event store without blocking
        the main thread. Concurrency safety: all asyncio.Lock/Event primitives are created
        on the main event loop thread.
        """
        self._boot_complete.clear()
        loop = asyncio.get_running_loop()
        discovered = await loop.run_in_executor(None, discover_agent_classes)

        for name, cls in discovered.items():
            if name not in self.agents or self.agents[name].agent is None:
                try:
                    agent = cls(
                        ai_handler=self.ai_handler,
                        memory=self.memory,
                        tool_registry=self.tool_registry,
                        ws_broadcast=self.ws_broadcast,
                        orchestrator=self,
                        guardrails=self.guardrails,
                        coordination=None,
                        learning_coordinator=self.learning_coordinator,
                        learning_engine=self.reflexion_engine,
                    )
                    try:
                        agent._learning_coordinator = self.learning_coordinator
                        agent._learning_engine = self.reflexion_engine
                        agent.reflexion_engine = self.reflexion_engine
                        # Cognition persistence: agents log plans/thoughts into
                        # the SAME EventStore the kernel already uses (no new
                        # storage layer — reuse, don't duplicate).
                        agent.event_store = self._event_store
                    except Exception:
                        pass

                    self._register_agent_tools(agent, name)
                    self.agents[name] = AgentInstance(name=name, agent=agent, state=AgentState.IDLE)
                    if name not in self.agent_locks:
                        self.agent_locks[name] = asyncio.Lock()
                    if name not in self._circuit_breakers:
                        self._circuit_breakers[name] = AdaptiveCircuitBreaker(
                            failure_threshold=self._cb_failure_threshold,
                            recovery_window_s=self._cb_recovery_window_s,
                        )
                    await self._event_store.append("agent_initialized", agent_name=name)
                    logger.info("Agent initialized (async_boot): %s", name)
                except Exception as exc:
                    logger.error("Failed to initialize agent %s in async_boot: %s", name, exc, exc_info=True)
                    self.agents[name] = AgentInstance(name=name, agent=None, state=AgentState.ERROR)

        await loop.run_in_executor(None, self._replay_events_sync)
        self._boot_complete.set()
        logger.info("NextGenOrchestrator async boot sequence complete.")

    # =========================================================================
    # Agent Discovery & Initialization
    # =========================================================================

    def _init_agents(self) -> None:
        """
        Dynamically discover and instantiate all BaseAgent subclasses.
        Each discovered agent is constructed with standard dependencies and registered.
        """
        discovered = discover_agent_classes()

        if not discovered:
            logger.error("Dynamic agent discovery found ZERO agents — check apps/brain/agents/")

        for name, cls in discovered.items():
            try:
                agent = cls(
                    ai_handler=self.ai_handler,
                    memory=self.memory,
                    tool_registry=self.tool_registry,
                    ws_broadcast=self.ws_broadcast,
                    orchestrator=self,
                    guardrails=self.guardrails,
                    coordination=None,
                    learning_coordinator=self.learning_coordinator,
                    learning_engine=self.reflexion_engine,
                )
                try:
                    agent._learning_coordinator = self.learning_coordinator
                    agent._learning_engine = self.reflexion_engine
                    agent.reflexion_engine = self.reflexion_engine
                    agent.event_store = self._event_store
                except Exception:
                    pass

                self._register_agent_tools(agent, name)

                self.agents[name] = AgentInstance(
                    name=name, agent=agent, state=AgentState.IDLE,
                )
                self.agent_locks[name] = asyncio.Lock()
                self._circuit_breakers[name] = AdaptiveCircuitBreaker(
                    failure_threshold=self._cb_failure_threshold,
                    recovery_window_s=self._cb_recovery_window_s,
                )
                logger.info("Agent initialized (dynamic): %s", name)

                self._event_store.append_sync(
                    "agent_initialized", agent_name=name,
                )

            except Exception as exc:
                logger.error("Failed to initialize agent %s: %s", name, exc, exc_info=True)
                self.agents[name] = AgentInstance(
                    name=name, agent=None, state=AgentState.ERROR,
                )
                self._event_store.append_sync(
                    "agent_init_failed", agent_name=name,
                    payload={"error": str(exc)},
                )

    # =========================================================================
    # Agent Tool Auto-Registration
    # =========================================================================

    def _register_agent_tools(self, agent: Any, agent_name: str) -> None:
        """Auto-register agent-local tools into the global ToolRegistry."""
        if not self.tool_registry:
            logger.warning("No tool_registry available — skipping tool registration for %s", agent_name)
            return

        registered_count = 0

        # Pattern 2: _TOOL_MAP
        tool_map = getattr(agent, "_TOOL_MAP", None)
        if tool_map and isinstance(tool_map, dict):
            for tool_name, handler in tool_map.items():
                if self.tool_registry.has_tool(tool_name):
                    continue
                actual_handler = handler.func if isinstance(handler, functools.partial) else handler
                if not inspect.iscoroutinefunction(actual_handler):
                    continue

                schema = self._build_schema_from_handler(handler)
                doc = inspect.getdoc(actual_handler) or inspect.getdoc(handler)
                if not doc and hasattr(agent, f"_tool_{tool_name}"):
                    doc = inspect.getdoc(getattr(agent, f"_tool_{tool_name}"))
                desc = doc.strip().split("\n\n")[0].replace("\n", " ").strip() if (doc and doc.strip()) else f"Agent tool: {tool_name} (from {agent_name})"

                self.tool_registry.register_tool(
                    name=tool_name,
                    description=desc,
                    func=handler,
                    schema=schema,
                    category=agent_name,
                    agent_hints=[agent_name],
                    task_tags=[agent_name],
                    priority=3,
                    is_destructive=getattr(handler, "_is_destructive", False),
                )
                registered_count += 1

        # Pattern 3: _tool_* convention methods
        for attr_name in dir(agent):
            if not attr_name.startswith("_tool_"):
                continue
            tool_name = attr_name[6:]
            if not tool_name or self.tool_registry.has_tool(tool_name):
                continue
            handler = getattr(agent, attr_name, None)
            actual_handler = handler.func if isinstance(handler, functools.partial) else handler
            if not inspect.iscoroutinefunction(actual_handler):
                continue

            schema = self._build_schema_from_handler(handler)
            doc = inspect.getdoc(actual_handler) or inspect.getdoc(handler)
            desc = doc.strip().split("\n\n")[0].replace("\n", " ").strip() if (doc and doc.strip()) else f"Auto-discovered tool: {tool_name} from {agent_name}"

            self.tool_registry.register_tool(
                name=tool_name,
                description=desc,
                func=handler,
                schema=schema,
                category=agent_name,
                agent_hints=[agent_name],
                task_tags=[agent_name],
                priority=5,
            )
            registered_count += 1

        # Pattern 4: _tool_registry
        tool_defs = getattr(agent, "_tool_registry", None)
        if tool_defs and isinstance(tool_defs, list):
            for d in tool_defs:
                err = self._validate_tool_entry(d, agent_name)
                if err or self.tool_registry.has_tool(d["name"]):
                    continue
                try:
                    self.tool_registry.register_tool(
                        name=d["name"],
                        description=d.get("description", d["name"]),
                        func=d["func"],
                        schema=d.get("schema", {"type": "object", "properties": {}}),
                        category=d.get("category", agent_name),
                        agent_hints=d.get("agent_hints", [agent_name]),
                        task_tags=d.get("task_tags", [agent_name]),
                        priority=d.get("priority", 5),
                        is_destructive=bool(d.get("is_destructive", False)),
                        parallel_safe=bool(d.get("parallel_safe", True)),
                        timeout_s=float(d.get("timeout_s", 30.0)),
                    )
                    registered_count += 1
                except Exception as e:
                    logger.error("[ToolRegistration] Agent '%s' — failed tool '%s': %s", agent_name, d.get("name"), e)

        # Pattern 1: AGENT_TOOLS
        agent_tools_list = getattr(agent, "AGENT_TOOLS", []) or []
        norm_name = agent_name.replace("_agent", "")
        for tool_name in agent_tools_list:
            tool_obj = self.tool_registry.get_tool(tool_name)
            if tool_obj:
                if agent_name not in tool_obj.agent_hints:
                    tool_obj.agent_hints.append(agent_name)
                if norm_name not in tool_obj.agent_hints:
                    tool_obj.agent_hints.append(norm_name)

        if registered_count:
            if hasattr(self.tool_registry, "_manifest_cache"):
                self.tool_registry._manifest_cache.clear()
            logger.info("Agent '%s': registered %d local tools into global registry", agent_name, registered_count)

    @staticmethod
    def _validate_tool_entry(d: Any, agent_name: str) -> Optional[str]:
        if not isinstance(d, dict):
            return f"entry is not a dict (got {type(d).__name__})"
        name = d.get("name")
        if not isinstance(name, str) or not name.strip():
            return f"'name' must be a non-empty string (got {name!r})"
        if "func" not in d or d["func"] is None:
            return f"'func' missing or None for '{name}'"
        func = d["func"]
        if inspect.iscoroutine(func):
            return f"'func' for '{name}' is a coroutine instance, not a function."
        if not callable(func):
            return f"'func' for '{name}' is not callable"
        if not inspect.iscoroutinefunction(func):
            return f"'func' for '{name}' is not an async function"
        return None

    @staticmethod
    def _build_schema_from_handler(handler: Any) -> dict:
        try:
            sig = inspect.signature(handler)
        except (ValueError, TypeError):
            return {"type": "object", "properties": {}}

        properties = {}
        required = []
        type_map = {int: "integer", float: "number", bool: "boolean", str: "string", list: "array", dict: "object"}

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls") or param.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                continue
            ann = param.annotation
            origin = typing.get_origin(ann) if ann != inspect.Parameter.empty else None
            args = typing.get_args(ann) if ann != inspect.Parameter.empty else ()

            if origin is typing.Union or (hasattr(typing, "UnionType") and origin is getattr(typing, "UnionType", None)):
                non_none_args = [a for a in args if a is not type(None)]
                if non_none_args:
                    ann = non_none_args[0]
                    origin = typing.get_origin(ann)

            target_type = origin or ann
            prop_def: dict[str, Any] = {"type": "string"}

            if origin is typing.Literal or (getattr(typing, "Literal", None) and origin is typing.Literal):
                prop_def = {"type": "string", "enum": list(args)}
            elif target_type in (typing.List, list, tuple, set, typing.Tuple, typing.Set, typing.Sequence) or (origin in (typing.List, list, tuple, set, typing.Tuple, typing.Set, typing.Sequence)):
                prop_def = {"type": "array"}
            elif target_type in (typing.Dict, dict, typing.Mapping) or (origin in (typing.Dict, dict, typing.Mapping)) or target_type in (typing.Any, Any):
                prop_def = {"type": "object"}
            elif target_type in type_map:
                prop_def = {"type": type_map[target_type]}

            properties[param_name] = prop_def
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        schema: dict = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    # =========================================================================
    # Event Replay — Crash Recovery
    # =========================================================================

    def _replay_events_sync(self) -> None:
        """Reconstruct kernel state by replaying the event log."""
        events = self._event_store.replay_all_sync()
        recovered_drafts: dict[str, dict] = {}
        running_tasks: set[str] = set()

        for ev in events:
            et = ev.event_type
            tid = ev.task_id
            if et in ("task_dispatched", "task_running"):
                if tid:
                    running_tasks.add(tid)
            elif et in ("task_completed", "task_error", "task_cancelled", "task_timeout", "task_guardrail_hit", "task_durable_completed", "task_durable_failed"):
                if tid:
                    running_tasks.discard(tid)
            elif et == "task_partial" and tid:
                recovered_drafts[tid] = {
                    "agent": ev.agent_name or "",
                    "partial": ev.payload.get("partial_text", ""),
                    "ts": ev.timestamp,
                }

        self._drafts = recovered_drafts

        resumable_tasks = 0
        for tid in running_tasks:
            checkpoint = None
            if hasattr(self, "_durable_task_engine") and self._durable_task_engine:
                try:
                    checkpoint = self._durable_task_engine.get_checkpoint_sync(tid)
                except Exception as _cpe:
                    logger.debug("[Kernel] Error checking checkpoint for task %s: %s", tid, _cpe)

            if checkpoint:
                logger.info("Crash recovery: task %s checkpointed (status=%s), will resume", tid, checkpoint.status)
                resumable_tasks += 1
                self._event_store.append_sync(
                    "task_checkpoint_resumed",
                    task_id=tid,
                    payload={"checkpoint_id": checkpoint.checkpoint_id, "status": checkpoint.status},
                )
            else:
                logger.warning("Task %s lost — no checkpoint found", tid)
                self._event_store.append_sync(
                    "task_error", task_id=tid,
                    payload={"error": "Crash recovery: task was running at shutdown without checkpoint"},
                )

        logger.info(
            "Event replay complete: %d events, %d drafts recovered, %d crashed tasks (%d checkpointed for resume)",
            len(events), len(recovered_drafts), len(running_tasks), resumable_tasks,
        )
        self._event_store.append_sync("kernel_init")

    # =========================================================================
    # Dispatch — Multi-Lane Entry Point
    # =========================================================================

    async def execute_agent(
        self,
        agent_name: str,
        message: str,
        context: Optional[dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> str:
        """Execute a task on an agent and return its string result or raise on failure."""
        tid = task_id or f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        res = await self.dispatch(tid, agent_name, message, context=context)
        if res is None:
            return f"Agent '{agent_name}' completed with no result."
        if not getattr(res, "success", True):
            err_msg = getattr(res, "error", "") or f"Agent '{agent_name}' execution failed"
            raise RuntimeError(err_msg)
        return str(getattr(res, "result", "") or "")

    async def dispatch(
        self,
        task_id: str,
        agent_name: str,
        message: str,
        context: Optional[dict[str, Any]] = None,
        entities: Optional[dict] = None,
        is_background: bool = False,
        priority: Optional[TaskPriority] = None,
    ) -> Optional[TaskResult]:
        """Dispatch a task to a specific agent within its isolated domain lane."""
        # 1. Ensure boot sequence is complete
        if not self._boot_complete.is_set():
            await self._boot_complete.wait()

        if agent_name not in self.agents:
            alias_map = {
                "ecosystem_agent": "commander_agent",
                "ecosystem": "commander_agent",
                "commander": "commander_agent",
                "orchestrator_agent": "commander_agent",
            }
            resolved = alias_map.get(agent_name)
            if resolved and resolved in self.agents:
                agent_name = resolved
            elif f"{agent_name}_agent" in self.agents:
                agent_name = f"{agent_name}_agent"
            elif agent_name.endswith("_agent") and agent_name[:-6] in self.agents:
                agent_name = agent_name[:-6]

        if agent_name not in self.agents:
            logger.error("Unknown agent: %s", agent_name)
            return TaskResult(
                task_id=task_id, agent_name=agent_name,
                success=False, error=f"Unknown agent: {agent_name}",
            )

        # ── Delegation Depth & Loop Guard ──────────────────────────────────
        _depth = 0
        _max_depth = 3
        if isinstance(context, dict):
            _task_ctx = context.get("task_context")
            if _task_ctx and hasattr(_task_ctx, "delegation_depth"):
                _depth = getattr(_task_ctx, "delegation_depth", 0)
                _max_depth = getattr(_task_ctx, "max_delegation_depth", 3)
            else:
                _depth = context.get("delegation_depth", 0)
                _max_depth = context.get("max_delegation_depth", 3)

        if _depth > _max_depth:
            logger.warning("Delegation depth exceeded (%d/%d) for task %s on %s", _depth, _max_depth, task_id, agent_name)
            return TaskResult(
                task_id=task_id, agent_name=agent_name,
                success=False, error=f"Max delegation depth exceeded ({_depth}/{_max_depth})",
            )

        agent_instance = self.agents[agent_name]
        if agent_instance.agent is None:
            return TaskResult(
                task_id=task_id, agent_name=agent_name,
                success=False, error=f"Agent {agent_name} failed to initialize",
            )

        # ── Adaptive Circuit Breaker check ──────────────────────────────────
        _breaker = self._circuit_breakers.get(agent_name)
        if _breaker is not None:
            if _breaker.is_open():
                status = _breaker.get_status()
                # SOTA Upgrade: Semantic fallback — check if agent declared a FALLBACK_AGENT
                _fallback_name: Optional[str] = None
                if agent_instance.agent is not None:
                    _fallback_name = getattr(agent_instance.agent, "FALLBACK_AGENT", None)

                if (
                    _fallback_name
                    and _fallback_name != agent_name
                    and _fallback_name in self.agents
                    and self.agents[_fallback_name].agent is not None
                ):
                    _fb_breaker = self._circuit_breakers.get(_fallback_name)
                    _fb_open = _fb_breaker is not None and _fb_breaker.is_open()
                    if not _fb_open:
                        logger.warning(
                            "[CircuitBreaker] Agent '%s' OPEN — auto-degrading to FALLBACK_AGENT '%s' "
                            "(window_error_rate=%.1f%%, trip=%d)",
                            agent_name, _fallback_name,
                            status.get("window_error_rate_pct", 100.0),
                            status.get("trip_count", 1),
                        )
                        self._telemetry.record_recovery(agent_name, _fallback_name, success=True)
                        return await self.dispatch(
                            task_id, _fallback_name, message, context, entities,
                            is_background, priority,
                        )

                logger.warning(
                    "[CircuitBreaker] Agent '%s' circuit is OPEN — fast-rejecting task %s "
                    "(window_error_rate=%.1f%%, trip=%d)",
                    agent_name, task_id,
                    status.get("window_error_rate_pct", 100.0),
                    status.get("trip_count", 1),
                )
                return TaskResult(
                    task_id=task_id, agent_name=agent_name,
                    success=False,
                    error=f"Agent '{agent_name}' is temporarily unavailable (adaptive circuit breaker open). "
                          f"It will auto-recover in ~{int(_breaker._recovery_window_s)}s.",
                )
            elif _breaker.state == _CircuitState.HALF_OPEN and not _breaker.allow_probe():
                logger.info(
                    "[CircuitBreaker] Agent '%s' in HALF_OPEN — probe already in flight, rejecting task %s.",
                    agent_name, task_id,
                )
                return TaskResult(
                    task_id=task_id, agent_name=agent_name,
                    success=False,
                    error=f"Agent '{agent_name}' is currently probing recovery. Please try again shortly.",
                )

        if priority is None:
            priority = TaskPriority.BACKGROUND if is_background else TaskPriority.INTERACTIVE

        # Cancel propagation
        parent_id = (context or {}).get("_parent_task_id")
        if parent_id:
            if self._is_task_cancelled(parent_id):
                logger.info("Task %s skipped — parent %s already cancelled", task_id, parent_id)
                return TaskResult(
                    task_id=task_id, agent_name=agent_name,
                    success=False, error="Cancelled before dispatch (parent task cancelled)",
                )
            self.register_cancel_child(parent_id, task_id)

        # ── Domain Lane Slot Acquisition ────────────────────────────────────
        domain_lane = self._lane_manager.resolve_agent_lane(agent_name, agent_instance=agent_instance)
        owns_slot = False
        lease_id = 0

        if not is_background and priority != TaskPriority.BACKGROUND:
            owns_slot, lease_id = await self._acquire_domain_slot(
                task_id, priority, domain_lane, parent_id=parent_id,
            )

        # Record dispatch event
        await self._event_store.append(
            "task_dispatched", task_id=task_id, agent_name=agent_name,
            payload={
                "message_preview": message[:200],
                "is_background": is_background,
                "priority": priority.value if hasattr(priority, "value") else int(priority),
                "domain_lane": domain_lane.value,
            },
        )

        # Emit agent_started WS event
        if self.ws_broadcast:
            try:
                from .. import ws_protocol
                await self.ws_broadcast(
                    ws_protocol.build_agent_started(task_id, agent_name)
                )
            except Exception as e:
                logger.debug("WS broadcast agent_started failed: %s", e)

        # Set agent state
        agent_instance.state = AgentState.RUNNING
        agent_instance.current_task_id = task_id
        agent_instance.started_at = time.time()
        agent_instance.tokens_used = 0
        agent_instance.tool_calls = 0

        is_interactive = not is_background
        if hasattr(agent_instance.agent, "set_execution_mode"):
            agent_instance.agent.set_execution_mode(is_interactive)

        # Execute
        if is_background:
            async with self._background_tasks_lock:
                if len(self._background_tasks) >= self._max_background_tasks:
                    logger.warning("Background task limit reached (%d) — rejecting %s",
                                   self._max_background_tasks, task_id)
                    if owns_slot:
                        await self._release_domain_slot(task_id, domain_lane, lease_id)
                    return TaskResult(
                        task_id=task_id, agent_name=agent_name,
                        success=False, error="Background task limit reached",
                    )

            bg_task = asyncio.create_task(
                self._run_agent(
                    agent_instance, task_id, message, context, entities, is_interactive, domain_lane,
                )
            )
            async with self._background_tasks_lock:
                self._background_tasks[task_id] = bg_task

            async def _async_cleanup(fut: asyncio.Future):
                crashed = False
                try:
                    if not fut.cancelled():
                        exc = fut.exception()
                        if exc:
                            logger.error("[Kernel] Background agent task %s (%s) crashed: %s", task_id, agent_name, exc)
                            crashed = True
                        else:
                            res = fut.result()
                            if isinstance(res, TaskResult) and not res.success:
                                logger.error("[Kernel] Background task %s (%s) failed: %s", task_id, agent_name, res.error)
                except Exception as _cb_err:
                    logger.warning("[Kernel] Error reading background task result for %s: %s", task_id, _cb_err)
                finally:
                    if agent_instance.current_task_id == task_id:
                        agent_instance.state = AgentState.IDLE
                        agent_instance.current_task_id = None
                    if crashed:
                        _cb = self._circuit_breakers.get(agent_name)
                        if _cb is not None:
                            _cb.record_failure(error_type="task_crash")
                    async with self._background_tasks_lock:
                        self._background_tasks.pop(task_id, None)

            def _on_bg_done(f: asyncio.Future) -> None:
                clean_task = asyncio.create_task(_async_cleanup(f))
                clean_key = f"cleanup_{task_id}"
                self._background_tasks[clean_key] = clean_task
                clean_task.add_done_callback(lambda _: self._background_tasks.pop(clean_key, None))

            bg_task.add_done_callback(_on_bg_done)
            return None

        else:
            try:
                return await self._run_agent(
                    agent_instance, task_id, message, context, entities, is_interactive, domain_lane,
                )
            finally:
                if owns_slot:
                    await self._release_domain_slot(task_id, domain_lane, lease_id)

    # =========================================================================
    # Multi-Lane Domain Preemptive Slot Management
    # =========================================================================

    @property
    def slot_manager(self) -> DomainLaneSlotManager:
        return self._lane_manager

    @property
    def _active_interactive(self) -> Optional[str]:
        return self._lane_manager.holder_task_id

    @_active_interactive.setter
    def _active_interactive(self, value: Optional[str]) -> None:
        pass

    @property
    def _interactive_idle(self) -> asyncio.Event:
        return self._lane_manager._idle_event

    async def _acquire_domain_slot(
        self, task_id: str, priority: TaskPriority | int, domain_lane: DomainLane, parent_id: Optional[str] = None,
    ) -> tuple[bool, int]:
        """Acquire a generation-counted lease in the agent's domain lane."""
        priority_val = int(priority) if isinstance(priority, (TaskPriority, int)) else 1
        lane_mgr = self._lane_manager.get_lane(domain_lane)

        # If parent task already holds the slot in this lane, child shares parent lease without blocking!
        if parent_id and lane_mgr.holder_task_id == parent_id:
            return False, lane_mgr.current_lease_id

        if priority_val >= 2:
            old_task = lane_mgr.holder_task_id
            if old_task is not None and old_task != task_id:
                logger.info(
                    "CRITICAL task %s preempting lane [%s] holder %s",
                    task_id, domain_lane.value, old_task,
                )
                self._set_task_cancelled(old_task)
                for inst in self.agents.values():
                    if inst.current_task_id == old_task and inst.agent is not None:
                        if hasattr(inst.agent, "cancel"):
                            try:
                                await inst.agent.cancel()
                            except Exception as exc:
                                logger.warning("agent.cancel() failed during preemption: %s", exc)

                await self._event_store.append(
                    "task_preempted", task_id=old_task,
                    payload={"by_task_id": task_id, "reason": "CRITICAL priority", "lane": domain_lane.value},
                )

        lease_id = await self._lane_manager.acquire_lane_slot(task_id, priority_val, domain_lane)
        async with self._interactive_state_lock:
            if task_id not in self._task_leases:
                self._task_leases[task_id] = {}
            self._task_leases[task_id][domain_lane] = lease_id
            self._active_interactive_refcount[task_id] = (
                self._active_interactive_refcount.get(task_id, 0) + 1
            )
        return True, lease_id

    async def _release_domain_slot(
        self, task_id: str, domain_lane: DomainLane, lease_id: int,
    ) -> bool:
        """Release domain lane lease held by task_id."""
        async with self._interactive_state_lock:
            refcount = self._active_interactive_refcount.get(task_id, 1) - 1
            if refcount <= 0:
                self._active_interactive_refcount.pop(task_id, None)
                lane_leases = self._task_leases.pop(task_id, {})
                lid = lane_leases.get(domain_lane, lease_id)
                return await self._lane_manager.release_lane_slot(task_id, lid, domain_lane)
            else:
                self._active_interactive_refcount[task_id] = refcount
                return True

    # =========================================================================
    # Agent Execution Runtime
    # =========================================================================

    async def _run_agent(
        self,
        agent_instance: AgentInstance,
        task_id: str,
        message: str,
        context: Optional[dict] = None,
        entities: Optional[dict] = None,
        is_interactive: bool = False,
        domain_lane: DomainLane = DomainLane.DEFAULT,
    ) -> TaskResult:
        """Run an agent with telemetry, guardrails, and event sourcing."""
        start_time = time.time()
        context = dict(context or {})
        await self._inject_learning_context(agent_instance.name, message, context)

        # Resolve or synthesize canonical AgentTask contract
        agent_task: Optional[Any] = context.get("agent_task")
        if agent_task is None and hasattr(self, "task_manager") and self.task_manager:
            try:
                agent_task = await self.task_manager.get_agent_task(task_id, raw_message=message)
            except Exception as _tm_err:
                logger.debug("TaskManager get_agent_task warning: %s", _tm_err)

        if agent_task is None:
            try:
                from .contracts import AgentTask
                slots = dict(context.get("grounded_slots") or entities or {})
                neg_c = slots.get("negative_constraints") or slots.get("constraints") or []
                neg_tuple = tuple(str(x) for x in neg_c) if isinstance(neg_c, (list, set, tuple)) else ((neg_c,) if isinstance(neg_c, str) else ())
                deps = slots.get("dependencies") or []
                deps_tuple = tuple(str(x) for x in deps) if isinstance(deps, (list, set, tuple)) else ((deps,) if isinstance(deps, str) else ())

                agent_task = AgentTask(
                    task_id=task_id,
                    conversation_id=str(context.get("conversation_id", task_id)),
                    goal=message,
                    domain=getattr(agent_instance, "domain", agent_instance.name),
                    operation=str(slots.get("action") or slots.get("operation") or ""),
                    target_entity=str(slots.get("target") or slots.get("target_entity") or "") or None,
                    parameters=slots,
                    negative_constraints=neg_tuple,
                    dependencies=deps_tuple,
                    context_artifacts=dict(slots.get("context_artifacts") or {}),
                    expected_outcome=slots.get("expected_outcome") or None,
                    raw_message=message,
                )
            except Exception as _at_err:
                logger.debug("AgentTask fallback synthesis warning: %s", _at_err)

        if agent_task is not None:
            context["agent_task"] = agent_task

        await self._event_store.append(
            "task_running", task_id=task_id, agent_name=agent_instance.name,
            payload={"domain_lane": domain_lane.value},
        )

        limits = self._get_limits(is_interactive, agent_instance.name)

        try:
            if hasattr(agent_instance.agent, "set_execution_task"):
                agent_instance.agent.set_execution_task(agent_task or task_id)
            exec_task = asyncio.ensure_future(
                asyncio.wait_for(
                    agent_instance.agent.execute(
                        task_id=task_id,
                        message=message,
                        context=context or {},
                        entities=entities or {},
                    ),
                    timeout=limits["max_wall_time_s"],
                )
            )
            cancel_watcher = asyncio.ensure_future(
                self._get_cancel_event(task_id).wait()
            )
            done, pending = await asyncio.wait(
                {exec_task, cancel_watcher},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()

            if cancel_watcher in done:
                try:
                    if hasattr(agent_instance.agent, "cancel"):
                        await agent_instance.agent.cancel()
                except Exception as exc:
                    logger.warning("agent.cancel() error for %s: %s", agent_instance.name, exc)
                exec_task.cancel()
                try:
                    await exec_task
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    pass
                raise asyncio.CancelledError()

            result_text = exec_task.result()

            # ── SOTA Actor-Model Inter-Agent Delegation Unpack ──────────────────
            if isinstance(result_text, AgentDelegationRequest):
                target = result_text.target_agent
                payload = result_text.payload
                delegation_depth = int((context or {}).get("_delegation_depth", 0)) + 1
                delegation_chain = list((context or {}).get("_delegation_chain", [])) + [agent_instance.name]
                if delegation_depth > _MAX_DELEGATION_DEPTH or target in delegation_chain:
                    logger.error(
                        "[%s] Delegation depth limit or circular cycle detected (depth=%d, max=%d, chain=%s) for target '%s'",
                        agent_instance.name, delegation_depth, _MAX_DELEGATION_DEPTH, delegation_chain, target,
                    )
                    result_text = f"Delegation cycle or max depth ({_MAX_DELEGATION_DEPTH}) exceeded when delegating to {target} (chain: {' -> '.join(delegation_chain)})."
                elif target in self.agents:
                    logger.info(
                        "[%s] Actor-Model delegation to %s (depth=%d, chain=%s, reason=%s)",
                        agent_instance.name, target, delegation_depth, delegation_chain, result_text.reason,
                    )
                    sub_context = dict(context or {})
                    sub_context["_delegation_depth"] = delegation_depth
                    sub_context["_delegation_chain"] = delegation_chain
                    sub_context["_parent_task_id"] = task_id
                    sub_task_id = f"{task_id}_del_{delegation_depth}_{uuid.uuid4().hex[:6]}"
                    sub_msg = payload if isinstance(payload, str) else json.dumps(payload, default=str)
                    sub_res = await self.dispatch(
                        task_id=sub_task_id,
                        agent_name=target,
                        message=sub_msg,
                        context=sub_context,
                        entities=entities,
                        is_background=False,
                        priority=TaskPriority.INTERACTIVE,
                    )
                    if sub_res and sub_res.success:
                        result_text = sub_res.result
                    else:
                        err_msg = sub_res.error if sub_res and sub_res.error else "Delegation task failed."
                        result_text = f"Delegated task to {target}, but encountered an issue: {err_msg}"
                else:
                    result_text = f"Invalid delegation target: {target}"

            # Post-success learning
            await self._learn_from_turn(
                agent_instance.name, task_id, message, result_text, context,
            )

            duration = time.time() - start_time
            agent_instance.state = AgentState.IDLE
            agent_instance.current_task_id = None

            try:
                stats = agent_instance.agent.get_execution_stats()
                agent_instance.tokens_used = stats.get("tokens_used", 0)
                agent_instance.tool_calls = stats.get("tool_calls", 0)
            except (AttributeError, KeyError, TypeError):
                pass

            # Circuit breaker: record success with duration
            _cb = self._circuit_breakers.get(agent_instance.name)
            if _cb is not None:
                _cb.record_success(duration_s=duration)

            # Record Kernel Telemetry
            self._telemetry.record_execution(
                agent_name=agent_instance.name,
                domain_lane=domain_lane,
                duration_s=duration,
                success=True,
                tokens=agent_instance.tokens_used,
                tool_calls=agent_instance.tool_calls,
                status="completed",
            )

            result = TaskResult(
                task_id=task_id,
                agent_name=agent_instance.name,
                success=True,
                result=result_text,
                tokens_used=agent_instance.tokens_used,
                tool_calls=agent_instance.tool_calls,
                duration_s=duration,
            )

            await self._event_store.append(
                "task_completed", task_id=task_id, agent_name=agent_instance.name,
                payload={
                    "result_preview": str(result_text)[:200],
                    "tokens_used": agent_instance.tokens_used,
                    "tool_calls": agent_instance.tool_calls,
                    "duration_s": round(duration, 3),
                    "domain_lane": domain_lane.value,
                },
            )

            if self.ws_broadcast:
                try:
                    from .. import ws_protocol
                    out_text = str(result_text or "").strip() or "Task completed successfully."
                    await self.ws_broadcast(
                        ws_protocol.build_ai_chunk(task_id, out_text, is_final=True)
                    )
                    agent_done_payload = ws_protocol.build_agent_done(
                        task_id, agent_instance.name, out_text[:200],
                    )
                    try:
                        if hasattr(agent_done_payload, "payload") and isinstance(agent_done_payload.payload, dict):
                            agent_done_payload.payload["tokens_used"] = agent_instance.tokens_used
                            agent_done_payload.payload["tool_calls"] = agent_instance.tool_calls
                            agent_done_payload.payload["duration_s"] = round(duration, 3)
                    except Exception:
                        pass
                    await self.ws_broadcast(agent_done_payload)
                except Exception as _ws_err:
                    logger.debug("WS broadcast completion failed: %s", _ws_err)

            return result

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            logger.warning("Agent %s timed out after %.1fs", agent_instance.name, duration)
            _cb = self._circuit_breakers.get(agent_instance.name)
            if _cb is not None:
                _cb.record_failure(duration_s=duration, error_type="timeout")

            self._telemetry.record_execution(
                agent_name=agent_instance.name,
                domain_lane=domain_lane,
                duration_s=duration,
                success=False,
                status="timeout",
            )

            await self._learn_from_failure(
                agent_instance.name, task_id, message, "wall_time_exceeded", context,
            )
            await self._event_store.append(
                "task_timeout", task_id=task_id, agent_name=agent_instance.name,
                payload={"limit": limits["max_wall_time_s"], "duration_s": round(duration, 3)},
            )
            return await self._handle_guardrail_hit(
                agent_instance, task_id, duration,
                reason="wall_time_exceeded",
                limit_value=limits["max_wall_time_s"],
                domain_lane=domain_lane,
            )

        except GuardrailExceeded as exc:
            duration = time.time() - start_time
            logger.warning("Agent %s hit guardrail: %s after %.1fs", agent_instance.name, exc.reason, duration)
            self._telemetry.record_execution(
                agent_name=agent_instance.name,
                domain_lane=domain_lane,
                duration_s=duration,
                success=False,
                status="guardrail",
            )
            await self._learn_from_failure(
                agent_instance.name, task_id, message, f"guardrail:{exc.reason}", context,
            )
            await self._event_store.append(
                "task_guardrail_hit", task_id=task_id, agent_name=agent_instance.name,
                payload={"reason": exc.reason, "duration_s": round(duration, 3)},
            )
            return await self._handle_guardrail_hit(
                agent_instance, task_id, duration,
                reason=exc.reason,
                limit_value=limits.get("max_tool_calls", 20),
                domain_lane=domain_lane,
            )

        except asyncio.CancelledError:
            duration = time.time() - start_time
            logger.warning("Agent %s cancelled after %.1fs", agent_instance.name, duration)
            agent_instance.state = AgentState.IDLE
            agent_instance.current_task_id = None
            await self._event_store.append(
                "task_cancelled", task_id=task_id, agent_name=agent_instance.name,
                payload={"duration_s": round(duration, 3), "domain_lane": domain_lane.value},
            )
            if self.ws_broadcast:
                try:
                    from .. import ws_protocol
                    await self.ws_broadcast(
                        ws_protocol.build_ai_chunk(task_id, "[Task cancelled by user]", is_final=True)
                    )
                    await self.ws_broadcast(ws_protocol.WSMessage(
                        v=ws_protocol.PROTOCOL_VERSION,
                        type=ws_protocol.ServerMessageType.AGENT_ERROR,
                        payload={"agent": agent_instance.name, "error": "Task cancelled"},
                        task_id=task_id,
                    ))
                except Exception:
                    pass
            raise

        except Exception as exc:
            duration = time.time() - start_time
            logger.error("Agent %s error: %s", agent_instance.name, exc, exc_info=True)
            _cb = self._circuit_breakers.get(agent_instance.name)
            if _cb is not None:
                _cb.record_failure(duration_s=duration, error_type=str(exc))

            self._telemetry.record_execution(
                agent_name=agent_instance.name,
                domain_lane=domain_lane,
                duration_s=duration,
                success=False,
                status="error",
            )

            await self._learn_from_failure(
                agent_instance.name, task_id, message, str(exc), context,
            )
            agent_instance.state = AgentState.IDLE
            agent_instance.current_task_id = None
            await self._event_store.append(
                "task_error", task_id=task_id, agent_name=agent_instance.name,
                payload={"error": str(exc), "duration_s": round(duration, 3), "domain_lane": domain_lane.value},
            )
            if self.ws_broadcast:
                try:
                    from .. import ws_protocol
                    err_msg = f"I encountered an issue completing that task: {str(exc)}"
                    await self.ws_broadcast(
                        ws_protocol.build_ai_chunk(task_id, err_msg, is_final=True)
                    )
                    await self.ws_broadcast(ws_protocol.WSMessage(
                        v=ws_protocol.PROTOCOL_VERSION,
                        type=ws_protocol.ServerMessageType.AGENT_ERROR,
                        payload={"agent": agent_instance.name, "error": str(exc)},
                        task_id=task_id,
                    ))
                except Exception:
                    pass
            return TaskResult(
                task_id=task_id,
                agent_name=agent_instance.name,
                success=False,
                error=str(exc),
                duration_s=duration,
            )
        finally:
            if hasattr(self, "_cancel_events"):
                self._cancel_events.pop(task_id, None)
            if hasattr(self, "_parent_children"):
                self._parent_children.pop(task_id, None)

    # =========================================================================
    # Guardrail Hit Handler
    # =========================================================================

    async def _handle_guardrail_hit(
        self,
        agent_instance: AgentInstance,
        task_id: str,
        duration: float,
        reason: str,
        limit_value: Any,
        domain_lane: DomainLane = DomainLane.DEFAULT,
    ) -> TaskResult:
        agent_instance.state = AgentState.IDLE
        agent_instance.current_task_id = None

        partial = getattr(agent_instance.agent, "get_partial_result", lambda: "")()
        if partial:
            await self._event_store.append(
                "task_partial", task_id=task_id, agent_name=agent_instance.name,
                payload={"partial_text": partial, "reason": reason, "domain_lane": domain_lane.value},
            )
            self._drafts[task_id] = {
                "agent": agent_instance.name,
                "partial": partial,
                "ts": time.time(),
            }
            if self.memory and hasattr(self.memory, "save_draft"):
                try:
                    await self.memory.save_draft(task_id, partial)
                except Exception:
                    pass

        try:
            stats = agent_instance.agent.get_execution_stats()
            agent_instance.tokens_used = stats.get("tokens_used", 0)
            agent_instance.tool_calls = stats.get("tool_calls", 0)
        except (AttributeError, KeyError, TypeError):
            pass

        if self.ws_broadcast:
            try:
                from .. import ws_protocol
                terminal_text = str(partial).strip() if partial else f"Task stopped: {reason} limit reached ({limit_value})."
                await self.ws_broadcast(
                    ws_protocol.build_ai_chunk(task_id, terminal_text, is_final=True)
                )
                await self.ws_broadcast(ws_protocol.WSMessage(
                    v=ws_protocol.PROTOCOL_VERSION,
                    type=ws_protocol.ServerMessageType.AGENT_GUARDRAIL_HIT,
                    payload={
                        "agent": agent_instance.name,
                        "reason": reason,
                        "limit": limit_value,
                    },
                    task_id=task_id,
                ))
            except Exception:
                pass

        return TaskResult(
            task_id=task_id,
            agent_name=agent_instance.name,
            success=False,
            result=partial,
            error=f"Agent guardrail hit: {reason}",
            tokens_used=agent_instance.tokens_used,
            tool_calls=agent_instance.tool_calls,
            duration_s=duration,
            is_partial=True,
        )

    # =========================================================================
    # Sub-5ms Instant Task Cancellation
    # =========================================================================

    async def cancel_task(self, task_id: str) -> None:
        """Cancel a running task, immediately release domain lane leases, and propagate."""
        self._set_task_cancelled(task_id)

        # 1. Immediately revoke and release all domain lane leases held by this task
        released_lanes = await self._lane_manager.force_release_all_for_task(task_id)
        if released_lanes:
            logger.info("Task %s cancellation: instantly freed domain lanes: %s", task_id, [l.value for l in released_lanes])

        # 2. Cancel in-flight background tasks
        async with self._background_tasks_lock:
            if task_id in self._background_tasks:
                self._background_tasks[task_id].cancel()
                del self._background_tasks[task_id]

        # 3. Cancel active agent instances
        for agent_instance in self.agents.values():
            if agent_instance.current_task_id == task_id:
                agent_instance.state = AgentState.CANCELLED
                agent_instance.current_task_id = None
                if hasattr(agent_instance.agent, "cancel"):
                    try:
                        await agent_instance.agent.cancel()
                    except Exception as exc:
                        logger.warning("agent.cancel() failed for %s: %s", agent_instance.name, exc)

        await self._event_store.append(
            "task_cancelled", task_id=task_id,
            payload={"source": "explicit_cancel"},
        )
        if self.ws_broadcast:
            try:
                from .. import ws_protocol
                await self.ws_broadcast(
                    ws_protocol.build_ai_chunk(task_id, "[Task cancelled by user]", is_final=True)
                )
            except Exception:
                pass
        logger.info("Task %s cancelled in NextGen kernel", task_id)

    # =========================================================================
    # Telemetry, Resolution & Status
    # =========================================================================

    def get_kernel_telemetry(self) -> dict[str, Any]:
        """Return rich live telemetry snapshot (p50/p95, lanes, storage, error rates)."""
        return self._telemetry.get_telemetry_snapshot(
            lane_manager=self._lane_manager,
            circuit_breakers=self._circuit_breakers,
            event_store=self._event_store,
        )

    def record_recovery(self, source_agent: str, target_agent: str, success: bool = True) -> None:
        """Record an autonomous LLM dynamic recovery event in kernel telemetry."""
        self._telemetry.record_recovery(source_agent, target_agent, success)

    def get_agent(self, agent_name: str) -> Optional[Any]:
        if agent_name in self.agents:
            inst = self.agents[agent_name]
            return getattr(inst, "agent", inst)
        alias_map = {
            "ecosystem_agent": "commander_agent",
            "ecosystem": "commander_agent",
            "commander": "commander_agent",
            "orchestrator_agent": "commander_agent",
        }
        resolved = alias_map.get(agent_name)
        if resolved and resolved in self.agents:
            inst = self.agents[resolved]
            return getattr(inst, "agent", inst)
        return None

    def has_agent(self, agent_name: str) -> bool:
        return self.get_agent(agent_name) is not None

    def get_agent_tools_manifest(self, include_degraded: bool = False) -> list[dict]:
        """Construct OpenAI function tool specifications for active agents.

        If include_degraded is False, circuit-broken OPEN agents are excluded.
        If include_degraded is True, they are included with a DEGRADED notice.
        """
        manifest: list[dict] = []
        for name, inst in self.agents.items():
            if inst.agent is None:
                continue
            cb = self._circuit_breakers.get(name)
            is_open = bool(cb and cb.is_open())
            if is_open and not include_degraded:
                continue

            agent_obj = getattr(inst, "agent", inst)
            desc = (
                getattr(agent_obj, "DESCRIPTION", None)
                or getattr(agent_obj, "__doc__", None)
                or f"Delegate specialized task to {name}."
            )
            desc = str(desc).strip()
            if is_open and include_degraded:
                desc += " [DEGRADED: temporarily unavailable due to repeated errors — avoid calling unless necessary]"
            tool_name = name if name.startswith("call_") else f"call_{name}"
            manifest.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "instruction": {
                                "type": "string",
                                "description": "Specific instruction/task for the agent",
                            },
                            "entities": {
                                "type": "object",
                                "description": "Optional extracted slots/parameters like app name, query, file path, volume level, etc.",
                            },
                        },
                        "required": ["instruction"],
                    },
                },
            })
        return manifest

    async def execute_direct_tool(
        self,
        tool_name: str,
        params: Optional[dict[str, Any]] = None,
        task_id: str = "direct_task",
        context: Optional[dict[str, Any]] = None,
    ) -> TaskResult:
        """
        Directly execute an MCP or system tool via ExecutionRuntime / ToolRegistry
        without spinning up a worker agent.
        """
        t0 = time.perf_counter()
        clean_tool_name = tool_name.replace("call_", "").strip() if tool_name else ""
        if not self.tool_registry or not (self.tool_registry.has_tool(clean_tool_name) or self.tool_registry.has_tool(tool_name)):
            return TaskResult(
                task_id=task_id,
                agent_name="direct_runtime",
                success=False,
                error=f"Tool '{tool_name}' is not registered in ToolRegistry",
            )

        target_name = clean_tool_name if self.tool_registry.has_tool(clean_tool_name) else tool_name
        effective_params = dict(params or {})

        # Broadcast tool call started
        if self.ws_broadcast:
            try:
                from ..ws_protocol import build_tool_call_started
                await self.ws_broadcast(build_tool_call_started(task_id, target_name, effective_params, agent="direct_runtime"))
            except Exception:
                pass

        try:
            from .contracts import Action, ActionExecutionContext
            from .execution_runtime import ExecutionRuntime
            runtime = getattr(self, "execution_runtime", None)
            if not runtime:
                runtime = ExecutionRuntime(
                    tool_registry=self.tool_registry,
                    guardrails=self.guardrails,
                    kernel=self,
                )
                self.execution_runtime = runtime

            action = Action(
                action_id=f"act_{target_name}_{time.time_ns()}",
                task_id=task_id,
                capability_name=target_name,
                parameters=effective_params,
            )
            ctx = ActionExecutionContext(task_id=task_id, consumer="direct_runtime")
            exec_res = await runtime.execute_action(action=action, context=ctx)

            duration_ms = (time.perf_counter() - t0) * 1000.0
            is_err = bool(exec_res.error and not exec_res.is_verified)
            res_str = f"[Failed] {exec_res.error}" if is_err else str(exec_res.tool_output or "")

            if self.ws_broadcast:
                try:
                    from ..ws_protocol import build_tool_call_finished
                    await self.ws_broadcast(
                        build_tool_call_finished(
                            task_id,
                            target_name,
                            result=res_str,
                            duration_ms=duration_ms,
                            is_success=not is_err,
                            agent="direct_runtime",
                        )
                    )
                except Exception:
                    pass

            await self._event_store.append(
                "direct_tool_executed",
                task_id=task_id,
                agent_name="direct_runtime",
                payload={
                    "tool_name": target_name,
                    "params": effective_params,
                    "duration_ms": duration_ms,
                    "is_success": not is_err,
                },
            )
            return TaskResult(
                task_id=task_id,
                agent_name="direct_runtime",
                success=not is_err,
                result=res_str,
                error=exec_res.error or "" if is_err else "",
                duration_s=duration_ms / 1000.0,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            logger.error("[Kernel] Direct tool '%s' execution failed: %s", target_name, exc)
            return TaskResult(
                task_id=task_id,
                agent_name="direct_runtime",
                success=False,
                error=str(exc),
                duration_s=duration_ms / 1000.0,
            )

    def get_status(self) -> dict[str, dict]:
        result = {}
        for name, inst in self.agents.items():
            cb = self._circuit_breakers.get(name)
            result[name] = {
                "state": inst.state.value,
                "current_task_id": inst.current_task_id,
                "available": inst.agent is not None,
                "domain_lane": self._lane_manager.resolve_agent_lane(name, agent_instance=inst).value,
                "circuit_breaker": cb.get_status() if cb else None,
            }
        return result

    def get_circuit_status(self) -> dict[str, dict]:
        return {
            name: cb.get_status()
            for name, cb in self._circuit_breakers.items()
        }

    async def get_draft(self, task_id: str) -> Optional[str]:
        entry = self._drafts.get(task_id)
        if entry:
            return entry.get("partial")
        return await self._event_store.get_latest_partial(task_id)

    async def resume_task(
        self,
        task_id: str,
        agent_name: str,
        extra_instruction: str = "",
    ) -> Optional[TaskResult]:
        partial_text = await self.get_draft(task_id)
        if not partial_text:
            logger.warning("resume_task: no draft found for %s", task_id)
            return None

        if not agent_name:
            events = await self._event_store.get_task_events(task_id)
            for ev in reversed(events):
                if ev.agent_name:
                    agent_name = ev.agent_name
                    break

        if not agent_name:
            logger.warning("resume_task: cannot determine agent for %s", task_id)
            return None

        continuation = (
            f"[Previous partial work from guardrail-interrupted run]\n"
            f"{partial_text}\n"
            f"--- RESUMING ---\n{extra_instruction}".strip()
        )
        return await self.dispatch(
            task_id=task_id,
            agent_name=agent_name,
            message=continuation,
        )

    # =========================================================================
    # Guardrail Limits
    # =========================================================================

    def _get_limits(self, is_interactive: bool, agent_name: str = "") -> dict:
        if self.guardrails is not None:
            limits = dict(self.guardrails.get_limits(is_interactive))
            if agent_name:
                section = self.config.get("agents", {}).get(agent_name, {}).get("limits", {})
                override = section.get("interactive_limits" if is_interactive else "background_limits", {})
                if override:
                    limits.update(override)
            return limits

        agents_cfg = self.config.get("agents", {})
        if agent_name:
            section = agents_cfg.get(agent_name, {}).get("limits", {})
            limits = dict(self._resolve_limits_section(section, is_interactive))
        else:
            limits = dict(self._resolve_limits_section(agents_cfg, is_interactive))
        return {
            "max_wall_time_s": limits.get("max_wall_time_s", 300),
            "max_tool_calls": limits.get("max_tool_calls", 20),
        }

    def _resolve_limits_section(self, cfg: dict, is_interactive: bool) -> dict:
        key = "interactive_limits" if is_interactive else "background_limits"
        return cfg.get(key, {})

    # =========================================================================
    # Learning Integration
    # =========================================================================

    async def _inject_learning_context(
        self, agent_name: str, message: str, context: dict[str, Any],
    ) -> None:
        # TODO: ReflexionEngine will handle this
        return

    async def _learn_from_turn(
        self, agent_name: str, task_id: str, message: str,
        result_text: str, context: dict[str, Any],
    ) -> None:
        # TODO: ReflexionEngine will handle this
        return

    async def _learn_from_failure(
        self, agent_name: str, task_id: str, message: str,
        error_text: str, context: dict[str, Any],
    ) -> None:
        lc = getattr(self, "learning_coordinator", None) or getattr(self, "reflexion_engine", None)
        if lc is None:
            return
        try:
            if hasattr(lc, "on_agent_failure"):
                await lc.on_agent_failure(
                    agent_name=agent_name,
                    user_message=str(message or task_id)[:200],
                    error_text=str(error_text or "unknown failure")[:300],
                )
            elif hasattr(lc, "on_tool_failure"):
                await lc.on_tool_failure(
                    agent_name=agent_name,
                    tool_name="execute",
                    user_message=str(message or task_id)[:200],
                    error_text=str(error_text or "unknown failure")[:300],
                )
        except Exception as exc:
            logger.debug("Learning failure signal failed: %s", exc)

    # =========================================================================
    # Cancellation Propagation
    # =========================================================================

    def _get_cancel_event(self, task_id: str) -> asyncio.Event:
        if task_id not in self._cancel_events:
            self._cancel_events[task_id] = asyncio.Event()
        return self._cancel_events[task_id]

    def _is_task_cancelled(self, task_id: str) -> bool:
        ev = self._cancel_events.get(task_id)
        return ev is not None and ev.is_set()

    def register_cancel_child(self, parent_id: str, child_id: str) -> None:
        if parent_id == child_id:
            return
        self._parent_children.setdefault(parent_id, set()).add(child_id)
        if self._is_task_cancelled(parent_id):
            self._set_task_cancelled(child_id)

    def _set_task_cancelled(
        self, task_id: str, _visited: Optional[set[str]] = None,
    ) -> None:
        if _visited is None:
            _visited = set()
        if task_id in _visited:
            return
        _visited.add(task_id)
        self._get_cancel_event(task_id).set()
        for child in self._parent_children.get(task_id, set()):
            self._set_task_cancelled(child, _visited)

    def cleanup_task(self, task_id: str) -> None:
        self._cancel_events.pop(task_id, None)
        self._parent_children.pop(task_id, None)
        self._drafts.pop(task_id, None)

    # =========================================================================
    # Maintenance & Shutdown
    # =========================================================================

    async def compact_event_store(self, keep_recent: int = 2000) -> dict[str, int]:
        """Prune older completed events and checkpoint SQLite WAL."""
        return await self._event_store.compact_events(keep_recent=keep_recent)

    @property
    def event_store(self):
        """Public read access to the kernel event store (observability integrations)."""
        return self._event_store

    @property
    def durable_task_engine(self):
        """Public access to the durable task engine."""
        return getattr(self, "_durable_task_engine", None)

    def close(self) -> None:
        self._event_store.close()
        logger.info("NextGenOrchestrator closed.")


# =============================================================================
# GuardrailExceeded re-export
# =============================================================================

# GuardrailExceeded re-export from contracts
from .contracts import GuardrailExceeded


# Canonical Kernel alias
Kernel = NextGenOrchestrator
