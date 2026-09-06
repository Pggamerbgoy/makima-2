"""
Makima Core Engine Package — v9.0 Next-Gen Architecture
Location: apps/brain/core/__init__.py

Canonical Subsystems:
- Domain Contracts: UserRequest, Task, TaskState, Action, ExecutionResult, ActionExecutionContext
- Task Management: TaskManager
- Context Assembly: ContextBuilder
- Physical Execution: ExecutionRuntime
- Capability Mesh: CapabilityMesh, Capability, AgentDescriptor
- World State: WorldStateService, get_world_state
- Verification & Recovery: # TODO: SagaRecoveryEngine will handle this
- Agent Adapters: AgentCapabilityAdapter
- Orchestration & DAG: DAGEngine, DAGPlan, DAGNode, OrchestrationEngine, NextGenOrchestrator
- Voice & Streaming: VoiceEngine
- Service Bootstrap: AppBootstrap, ServiceRegistry, register_core_tools
"""

from .app_bootstrap import AppBootstrap, ServiceNode
from .context_builder import ContextBuilder
from .contracts import (
    Action,
    ActionExecutionContext,
    ExecutionResult,
    MEDIA_KEYWORDS,
    Task,
    TaskState,
    ToolContext,
    UserRequest,
)
from .execution_runtime import ExecutionRuntime
from .kernel import NextGenOrchestrator
from .orchestration_engine import OrchestrationEngine
from .saga_recovery_engine import SagaRecoveryEngine, RecoveryResult, SagaRollbackReport
from .service_registry import S, ServiceRegistry
from .task_manager import TaskManager
from .tool_loader import register_core_tools
from .world_state import WorldStateService, get_world_state

__version__ = "9.0.0"

__all__ = [
    "UserRequest",
    "Task",
    "TaskState",
    "Action",
    "ExecutionResult",
    "ActionExecutionContext",
    "ToolContext",
    "MEDIA_KEYWORDS",
    "TaskManager",
    "ContextBuilder",
    "ExecutionRuntime",
    "WorldStateService",
    "get_world_state",
    "OrchestrationEngine",
    "NextGenOrchestrator",
    "AppBootstrap",
    "ServiceNode",
    "ServiceRegistry",
    "S",
    "SagaRecoveryEngine",
    "RecoveryResult",
    "SagaRollbackReport",
    "register_core_tools",
]

