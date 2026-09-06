"""
Makima OS v9.0 — Core Domain Contracts
Canonical typed contracts for Request -> Task -> Action -> Execution.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# Generic media/play vocabulary for upstream routing + timeout domains.
MEDIA_KEYWORDS: frozenset[str] = frozenset({
    "media", "music", "audio", "play", "track", "song", "songs",
    "spotify", "youtube", "soundcloud", "jio", "saavn", "cast",
})


# ─────────────────────────────────────────────────────────────────────────────
# 1. Request Contract
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class UserRequest:
    """Represents what the user or client submitted to the system."""
    request_id: str
    conversation_id: str
    raw_message: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    client_channel: str = "websocket"  # "websocket", "voice", "cli", "internal"
    received_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "raw_message": self.raw_message,
            "attachments": self.attachments,
            "client_channel": self.client_channel,
            "received_at": self.received_at,
            "metadata": self.metadata,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Task Contract
# ─────────────────────────────────────────────────────────────────────────────
class TaskState(str, Enum):
    """Canonical task state machine states."""
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPENSATING = "COMPENSATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class Task:
    """Represents a stateful outcome Makima is trying to accomplish."""
    task_id: str
    request_id: str
    conversation_id: str
    goal: str
    intent: str = "general"
    grounded_slots: dict[str, Any] = field(default_factory=dict)
    state: TaskState = TaskState.PENDING
    parent_task_id: Optional[str] = None
    subtask_ids: list[str] = field(default_factory=list)
    assigned_capability: Optional[str] = None
    timeout_s: float = 60.0
    retry_budget: int = 2
    retries_used: int = 0
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    final_result: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.state in (TaskState.PENDING, TaskState.PLANNING, TaskState.RUNNING, TaskState.PAUSED, TaskState.COMPENSATING)

    def is_terminal(self) -> bool:
        return self.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "goal": self.goal,
            "intent": self.intent,
            "grounded_slots": self.grounded_slots,
            "state": self.state.value if isinstance(self.state, TaskState) else str(self.state),
            "parent_task_id": self.parent_task_id,
            "subtask_ids": self.subtask_ids,
            "assigned_capability": self.assigned_capability,
            "timeout_s": self.timeout_s,
            "retry_budget": self.retry_budget,
            "retries_used": self.retries_used,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "final_result": self.final_result,
            "metadata": self.metadata,
        }

    def to_agent_task(self, raw_message: str = "") -> "AgentTask":
        """Convert this stateful Task to an immutable typed AgentTask contract."""
        return AgentTask.from_task(self, raw_message=raw_message)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Action Contract
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Action:
    """Represents one concrete capability invocation."""
    action_id: str
    task_id: str
    capability_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "LOW"             # "LOW", "MEDIUM", "CRITICAL"
    is_reversible: bool = True
    preconditions: tuple[str, ...] = field(default_factory=tuple)
    expected_state: dict[str, Any] = field(default_factory=dict)
    compensation_action: Optional[dict[str, Any]] = None

    @property
    def tool_name(self) -> str:
        return self.capability_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "task_id": self.task_id,
            "capability_name": self.capability_name,
            "parameters": self.parameters,
            "risk_level": self.risk_level,
            "is_reversible": self.is_reversible,
            "preconditions": list(self.preconditions),
            "expected_state": self.expected_state,
            "compensation_action": self.compensation_action,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. Execution Result Contract & Execution Context
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ExecutionResult:
    """Represents physical execution result + verification + timing."""
    execution_id: str
    action_id: str
    task_id: str
    pre_state_snapshot: dict[str, Any] = field(default_factory=dict)
    tool_output: Any = None
    post_state_snapshot: dict[str, Any] = field(default_factory=dict)
    is_verified: bool = False
    evidence_tier: str = "UNVERIFIABLE"  # "DIRECT_OS_PROBE", "INVARIANT_MATCH", "UNVERIFIABLE"
    duration_ms: float = 0.0
    error: Optional[str] = None
    rollback_performed: bool = False
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Determines if the action executed successfully (verified or unverifiable without error)."""
        return self.error is None and self.evidence_tier not in ("VERIFIED_FAILURE", "TIMEOUT", "FAILED", "PERMISSION_DENIED")

    @property
    def created_files(self) -> list[str]:
        """Convenience property for any created or modified file paths in artifacts."""
        files = self.artifacts.get("files") or self.artifacts.get("created_files") or []
        return [str(f) for f in files] if isinstance(files, list) else ([str(files)] if files else [])

    @property
    def extracted_urls(self) -> list[str]:
        """Convenience property for any extracted or navigated URLs in artifacts."""
        urls = self.artifacts.get("urls") or self.artifacts.get("extracted_urls") or []
        return [str(u) for u in urls] if isinstance(urls, list) else ([str(urls)] if urls else [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "action_id": self.action_id,
            "task_id": self.task_id,
            "pre_state_snapshot": self.pre_state_snapshot,
            "tool_output": self.tool_output,
            "post_state_snapshot": self.post_state_snapshot,
            "is_verified": self.is_verified,
            "is_success": self.is_success,
            "evidence_tier": self.evidence_tier,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "rollback_performed": self.rollback_performed,
            "artifacts": self.artifacts,
            "metadata": self.metadata,
        }


@dataclass
class ActionExecutionContext:
    """
    Unified transactional execution context.
    Provides complete backward compatibility with ToolContext fields.
    """
    consumer: str = "unknown"
    task_id: str = ""
    execution_id: str = ""
    conversation_id: str = ""
    grounded_spec: Any = None
    capability: Any = None
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
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Aliases
    @property
    def pre_state(self) -> dict[str, Any]:
        return self.observed_pre_state

    @property
    def post_state(self) -> dict[str, Any]:
        return self.observed_post_state


# ToolContext alias for backward compatibility
ToolContext = ActionExecutionContext


# ─────────────────────────────────────────────────────────────────────────────
# 4.5. Action Execution Memory Contract
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ExecutionRecord:
    """Canonical structured record of an executed action/tool for conversation memory."""
    timestamp: float = field(default_factory=time.time)
    conversation_id: str = ""
    task_id: str = ""
    agent: str = "direct_runtime"
    strategy: str = "direct"             # "direct" | "agent" | "conversational"
    tool_name: str = ""
    domain: str = "general"              # "media" | "system" | "browser" | "document" | "general"
    requested_target: Optional[str] = None
    resolved_target: Optional[str] = None
    parameters: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    status: str = "SUCCESS"              # "SUCCESS" | "FAILED" | "CANCELLED" | "PARTIAL" | "VERIFICATION_FAILED"
    verified: bool = False
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Backward compatibility helpers for dict-like access
    def get(self, key: str, default: Any = None) -> Any:
        if key == "message":
            return self.requested_target or self.resolved_target or self.result_summary
        if key == "ok":
            return self.status == "SUCCESS" and self.verified
        if hasattr(self, key):
            val = getattr(self, key)
            return val if val is not None else default
        return self.metadata.get(key, default)

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None and key not in ("error", "requested_target", "resolved_target"):
            raise KeyError(key)
        return val

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "strategy": self.strategy,
            "tool_name": self.tool_name,
            "domain": self.domain,
            "requested_target": self.requested_target,
            "resolved_target": self.resolved_target,
            "parameters": self.parameters,
            "result_summary": self.result_summary,
            "status": self.status,
            "verified": self.verified,
            "error": self.error,
            "metadata": self.metadata,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 5. Agent Contract (Domain Agent Task, Request & Response)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AgentTask:
    """Canonical typed immutable task representation for domain agents."""
    task_id: str
    conversation_id: str
    goal: str
    domain: str = "general"
    operation: str = ""
    target_entity: Optional[str] = None
    parameters: dict[str, Any] = field(default_factory=dict)
    negative_constraints: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    context_artifacts: dict[str, Any] = field(default_factory=dict)
    expected_outcome: Optional[str] = None
    raw_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_task(cls, task: Task, raw_message: str = "") -> AgentTask:
        slots = task.grounded_slots or {}
        neg_c = slots.get("negative_constraints") or slots.get("constraints") or []
        if isinstance(neg_c, (list, set, tuple)):
            neg_tuple = tuple(str(x) for x in neg_c)
        elif isinstance(neg_c, str):
            neg_tuple = (neg_c,)
        else:
            neg_tuple = ()

        deps = slots.get("dependencies") or []
        if isinstance(deps, (list, set, tuple)):
            deps_tuple = tuple(str(x) for x in deps)
        elif isinstance(deps, str):
            deps_tuple = (deps,)
        else:
            deps_tuple = ()

        return cls(
            task_id=task.task_id,
            conversation_id=task.conversation_id,
            goal=task.goal,
            domain=slots.get("domain") or task.intent or "general",
            operation=slots.get("operation") or slots.get("action") or "",
            target_entity=slots.get("target_entity") or slots.get("target") or None,
            parameters=dict(slots.get("parameters") or slots),
            negative_constraints=neg_tuple,
            dependencies=deps_tuple,
            context_artifacts=dict(slots.get("context_artifacts") or {}),
            expected_outcome=slots.get("expected_outcome") or None,
            raw_message=raw_message or task.goal,
            metadata=dict(task.metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "goal": self.goal,
            "domain": self.domain,
            "operation": self.operation,
            "target_entity": self.target_entity,
            "parameters": self.parameters,
            "negative_constraints": list(self.negative_constraints),
            "dependencies": list(self.dependencies),
            "context_artifacts": self.context_artifacts,
            "expected_outcome": self.expected_outcome,
            "raw_message": self.raw_message,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class AgentRequest:
    """Canonical invocation request passed to domain agents."""
    task: AgentTask
    execution_mode: str = "auto"          # "auto", "fast_path", "react", "pipeline"
    wall_clock_timeout_s: float = 60.0
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResponse:
    """Canonical execution response returned by domain agents."""
    success: bool
    final_output: Any
    actions_executed: list[ExecutionResult] = field(default_factory=list)
    artifacts_created: dict[str, str] = field(default_factory=dict)
    unresolved_ambiguities: list[str] = field(default_factory=list)
    error_message: Optional[str] = None


class GuardrailExceeded(Exception):
    """Raised when an agent execution exceeds execution constraints."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

