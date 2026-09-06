"""
Makima OS — Agent Task Context Model
Location: apps/brain/core/task_context.py

Structured context object for delegated tasks supporting parent-child tracing,
delegation depth enforcement, execution constraints, and state propagation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentTaskContext:
    """
    Structured execution context for tasks executed directly or delegated to agents.
    
    Guarantees:
      1. Parent-child task traceability for multi-agent DAGs and sub-delegations.
      2. Strictly bounded delegation depth to prevent recursive agent loops.
      3. Propagation of world state, permissions, negative constraints, and prior results.
    """
    task_id: str
    user_request: str
    parent_task_id: Optional[str] = None
    execution_strategy: str = "direct"  # direct | agent | multi_agent | conversational
    delegation_depth: int = 0
    max_delegation_depth: int = 3
    relevant_state: dict[str, Any] = field(default_factory=dict)
    available_tools: list[str] = field(default_factory=list)
    previous_results: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    priority: str = "normal"  # critical | high | normal | low | background
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def can_delegate(self) -> bool:
        """Check whether this task is permitted to sub-delegate to another agent."""
        return self.delegation_depth < self.max_delegation_depth

    def create_child_context(
        self,
        child_task_id: str,
        subtask_instruction: str,
        execution_strategy: str = "agent",
        additional_state: Optional[dict[str, Any]] = None,
        constraints: Optional[list[str]] = None,
    ) -> AgentTaskContext:
        """
        Create a child context for a sub-delegated task with incremented depth.
        Raises RecursionError if max delegation depth is exceeded.
        """
        if not self.can_delegate():
            raise RecursionError(
                f"Delegation depth limit reached ({self.delegation_depth}/{self.max_delegation_depth}) "
                f"for task '{self.task_id}'. Further agent delegation is forbidden."
            )

        merged_state = dict(self.relevant_state)
        if additional_state:
            merged_state.update(additional_state)

        merged_constraints = list(self.constraints)
        if constraints:
            for c in constraints:
                if c not in merged_constraints:
                    merged_constraints.append(c)

        return AgentTaskContext(
            task_id=child_task_id,
            parent_task_id=self.task_id,
            user_request=subtask_instruction,
            execution_strategy=execution_strategy,
            delegation_depth=self.delegation_depth + 1,
            max_delegation_depth=self.max_delegation_depth,
            relevant_state=merged_state,
            available_tools=list(self.available_tools),
            previous_results=dict(self.previous_results),
            constraints=merged_constraints,
            priority=self.priority,
            metadata={
                **self.metadata,
                "root_task_id": self.metadata.get("root_task_id", self.task_id),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize context to a plain dictionary for transport and logging."""
        return {
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "user_request": self.user_request,
            "execution_strategy": self.execution_strategy,
            "delegation_depth": self.delegation_depth,
            "max_delegation_depth": self.max_delegation_depth,
            "relevant_state": self.relevant_state,
            "available_tools": self.available_tools,
            "previous_results": self.previous_results,
            "constraints": self.constraints,
            "priority": self.priority,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentTaskContext:
        """Construct an AgentTaskContext from a dictionary."""
        return cls(
            task_id=str(data.get("task_id", "")),
            user_request=str(data.get("user_request", "")),
            parent_task_id=data.get("parent_task_id"),
            execution_strategy=str(data.get("execution_strategy", "direct")),
            delegation_depth=int(data.get("delegation_depth", 0)),
            max_delegation_depth=int(data.get("max_delegation_depth", 3)),
            relevant_state=dict(data.get("relevant_state", {}) or {}),
            available_tools=list(data.get("available_tools", []) or []),
            previous_results=dict(data.get("previous_results", {}) or {}),
            constraints=list(data.get("constraints", []) or []),
            priority=str(data.get("priority", "normal")),
            metadata=dict(data.get("metadata", {}) or {}),
            created_at=float(data.get("created_at", time.time())),
        )
