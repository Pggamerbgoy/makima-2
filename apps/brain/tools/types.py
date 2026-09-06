"""
Makima v8.0 — Tool Capability Layer: Core Types

Single source of truth for every tool's 3 parts (Phase 2):
  1. Definition  — name, description, JSON schema (ToolDefinition)
  2. Handler     — async execution logic (Tool.handler)
  3. Policy      — timeout/retry/permission (ToolPolicy)

Plus the standardized ToolResult envelope (Phase 8) and the
ToolContext passed at execution time (Phase 7).

HARD RULE (Phase 1): nothing in this module — or anywhere under
apps/brain/tools/ — may import from apps.brain.agents. Tools receive
everything they need via ToolContext; they never see an Agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

# Permission levels (Phase 7)
ALLOW = "allow"
ASK = "ask"
DENY = "deny"


@dataclass(frozen=True)
class ToolCapability:
    """Generic capability descriptor for tools and parameter operations."""
    domain: str                                 # "system" | "media" | "filesystem" | "window" | "process" | "browser" | "messaging"
    operation: str                              # "open" | "close" | "play" | "pause" | "stop" | "minimize" | "maximize" | "restore" | ...
    target_type: str                            # "app" | "window" | "process" | "file" | "media_player" | "url" | "service"
    state_transition: tuple[str, str] = ("", "") # (from_state, to_state) e.g. ("playing", "paused"), ("visible", "iconic")
    polarity: str = "affirmative"               # "affirmative" | "negative" | "neutral"


@dataclass(frozen=True)
class GroundedSemanticSpec:
    """Structured semantic request specification extracted from natural language."""
    domain: str = ""                            # "system" | "media" | "filesystem" | "window" | "process" | "browser"
    operation: str = ""                         # "open" | "close" | "play" | "pause" | "stop" | "minimize" | "maximize" | ...
    target: str = ""                            # "chrome", "current_media", "file.txt"
    desired_state: str = ""                     # "not_playing", "closed", "iconic", "running", "paused"
    polarity: str = "affirmative"               # "affirmative" | "deny"
    constraints: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ToolDefinition:
    """Part 1 — pure data. No logic, no dependencies."""
    name: str
    description: str
    parameters: dict[str, Any]           # JSON-schema object for handler kwargs
    critical_parameters: tuple[str, ...] = field(default_factory=tuple)  # SAGE-Agent: critical parameter slots
    parameter_domains: dict[str, Any] = field(default_factory=dict)      # SAGE-Agent: categorical vs generative domains

    def to_openai_function(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolPolicy:
    """Part 3 — runtime behavior."""
    timeout_s: float = 30.0
    max_retries: int = 0
    retry_backoff_s: float = 1.0
    retryable_keywords: tuple[str, ...] = field(default_factory=tuple)
    permissions: dict[str, str] = field(default_factory=dict)   # consumer_id -> allow/ask/deny
    default_permission: str = ALLOW


@dataclass
class ToolContext:
    """Execution-time context & unified transactional execution envelope."""
    consumer: str = "unknown"            # agent name / "direct_llm" / "workflow"
    task_id: str = ""
    execution_id: str = ""
    grounded_spec: Optional[GroundedSemanticSpec] = None
    capability: Optional[ToolCapability] = None
    observed_pre_state: dict[str, Any] = field(default_factory=dict)
    preconditions_satisfied: bool = True
    precondition_error: str = ""
    reversibility: str = "none"          # "none" | "snapshot" | "inverse"
    snapshot_id: Optional[str] = None
    compensation_tool: str = ""
    compensation_params: dict[str, Any] = field(default_factory=dict)
    verification_passed: bool = False
    verification_error: str = ""
    verification_status: str = "UNVERIFIABLE"
    verification_confidence: float = 0.0
    observed_post_state: dict[str, Any] = field(default_factory=dict)
    rollback_result: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Phase 8 — the ONLY output shape any tool may return."""
    success: bool
    output: Any = ""
    error: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Any] = field(default_factory=list)

    # ── coercion helpers ─────────────────────────────────────────────
    @classmethod
    def ok(cls, output: Any = "", **metadata: Any) -> ToolResult:
        return cls(success=True, output=output, error=None, metadata=dict(metadata))

    @classmethod
    def fail(cls, code: str, message: str, *, retriable: bool = False,
             output: Any = "", **metadata: Any) -> ToolResult:
        return cls(
            success=False,
            output=output,
            error={"code": code, "message": message, "retriable": retriable},
            metadata=dict(metadata),
        )

    @classmethod
    def coerce(cls, raw: Any, *, tool_name: str = "", latency_ms: float = 0.0) -> ToolResult:
        """Normalize ANY legacy handler return type into a ToolResult."""
        if isinstance(raw, cls):
            if latency_ms and "latency_ms" not in raw.metadata:
                object.__setattr__(
                    raw, "metadata", {**raw.metadata, "latency_ms": latency_ms})
            return raw
        lowered = raw.lower() if isinstance(raw, str) else ""
        error_markers = (
            "error:", "[error", "[tool error", "error executing tool",
            "[timeout]", "timed out", "exception:", "[cancelled]",
            "[nav_failed]", "traceback (", "failed:",
        )
        if lowered.startswith(error_markers):
            return cls.fail(code="handler_error", message=str(raw),
                            retriable=False, tool=tool_name, latency_ms=latency_ms)
        return cls.ok(raw if raw is not None else "", tool=tool_name,
                      latency_ms=latency_ms)


class Tool:
    """The 3 parts bound together. Registry stores/executes this object."""

    def __init__(
        self,
        definition: ToolDefinition,
        handler: Callable[..., Awaitable[Any]],
        policy: Optional[ToolPolicy] = None,
        *,
        category: str = "general",
        agent_hints: Optional[list[str]] = None,
        task_tags: Optional[list[str]] = None,
        priority: int = 5,
        is_destructive: bool = False,
    ) -> None:
        self.definition = definition
        self.handler = handler
        self.policy = policy or ToolPolicy()
        self.category = category
        self.agent_hints = agent_hints or []
        self.task_tags = task_tags or []
        self.priority = priority
        self.is_destructive = is_destructive

        # Telemetry
        self.call_count = 0
        self.failure_count = 0
        self.total_latency_ms = 0.0

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def schema(self) -> dict[str, Any]:
        return self.definition.parameters

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.call_count if self.call_count else 0.0

    def to_openai_function(self) -> dict[str, Any]:
        return self.definition.to_openai_function()

    def permission_for(self, consumer: str) -> str:
        return self.policy.permissions.get(consumer, self.policy.default_permission)
