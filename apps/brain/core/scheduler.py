from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("makima.core.scheduler")

class AgentState(str, Enum):
    """Lifecycle states for an agent instance."""
    INIT = "init"
    IDLE = "idle"
    RUNNING = "running"
    CANCELLED = "cancelled"
    ERROR = "error"
    SUSPENDED = "suspended"


class TaskPreemptionState(str, Enum):
    """Lifecycle states for agent preemption and task execution."""
    IDLE = "idle"
    RUNNING = "running"
    PREEMPTING = "preempting"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


class TaskPriority(int, Enum):
    """
    Task priority tiers for preemptive scheduling.
    Higher numeric value = higher priority.
    """
    BACKGROUND = 0
    INTERACTIVE = 1
    CRITICAL = 2


@dataclass
class AgentInstance:
    """Runtime state for a single agent."""
    name: str
    agent: Any
    state: AgentState = AgentState.INIT
    current_task_id: Optional[str] = None
    started_at: Optional[float] = None
    tokens_used: int = 0
    tool_calls: int = 0


@dataclass
class ExecutionMetrics:
    """Aggregated execution metrics for an agent or domain lane."""
    total_dispatched: int = 0
    total_completed: int = 0
    total_failed: int = 0
    total_timeout: int = 0
    total_guardrail_hits: int = 0
    total_tokens: int = 0
    total_tool_calls: int = 0
    durations_ms: list[float] = None

    def __post_init__(self):
        if self.durations_ms is None:
            self.durations_ms = []

    def record_run(self, duration_s: float, success: bool, tokens: int = 0, tool_calls: int = 0, status: str = "completed"):
        self.total_dispatched += 1
        if status == "timeout":
            self.total_timeout += 1
        elif status == "guardrail":
            self.total_guardrail_hits += 1

        if success:
            self.total_completed += 1
        else:
            self.total_failed += 1

        self.total_tokens += tokens
        self.total_tool_calls += tool_calls

        ms = duration_s * 1000.0
        self.durations_ms.append(ms)
        if len(self.durations_ms) > 100:
            self.durations_ms.pop(0)

    @property
    def p50_ms(self) -> float:
        if not self.durations_ms:
            return 0.0
        sorted_d = sorted(self.durations_ms)
        idx = int(len(sorted_d) * 0.5)
        return round(sorted_d[idx], 2)

    @property
    def p95_ms(self) -> float:
        if not self.durations_ms:
            return 0.0
        sorted_d = sorted(self.durations_ms)
        idx = min(len(sorted_d) - 1, int(len(sorted_d) * 0.95))
        return round(sorted_d[idx], 2)

    @property
    def avg_ms(self) -> float:
        if not self.durations_ms:
            return 0.0
        return round(sum(self.durations_ms) / len(self.durations_ms), 2)

    @property
    def error_rate(self) -> float:
        if self.total_dispatched == 0:
            return 0.0
        return round((self.total_failed / self.total_dispatched) * 100.0, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_dispatched": self.total_dispatched,
            "total_completed": self.total_completed,
            "total_failed": self.total_failed,
            "total_timeout": self.total_timeout,
            "total_guardrail_hits": self.total_guardrail_hits,
            "total_tokens": self.total_tokens,
            "total_tool_calls": self.total_tool_calls,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "avg_ms": self.avg_ms,
            "error_rate_pct": self.error_rate,
        }


class DomainLane(str, Enum):
    """
    Physical & cognitive domain lanes for independent parallel execution.
    Agents in different lanes execute concurrently without global lock contention.
    """
    MEDIA = "media"                  # Spotify, YouTube, Audio playback
    BROWSER = "browser"              # Playwright, Web scraping, Browser automation
    SYSTEM = "system"                # Win32 GUI, process/window management, files
    COGNITIVE = "cognitive"          # Code gen, research, data analysis, docs, memory
    COMMUNICATION = "communication"  # WhatsApp, Telegram, Discord, Email, Alarms
    DEFAULT = "default"              # Unclassified or dynamic custom agents


@dataclass
class TaskResult:
    """Result from an agent execution."""
    task_id: str
    agent_name: str
    success: bool
    result: str = ""
    error: str = ""
    tokens_used: int = 0
    tool_calls: int = 0
    duration_s: float = 0.0
    is_partial: bool = False

