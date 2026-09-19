"""
Makima Core Engine Package — v9.0 Next-Gen Architecture
Location: apps/brain/core/__init__.py
"""

from .app_bootstrap import AppBootstrap
from .context_builder import ContextBuilder
from .contracts import (
    Action,
    ActionExecutionContext,
    ExecutionResult,
    Task,
)
from .execution_runtime import ExecutionRuntime
from .orchestration_engine import OrchestrationEngine
from .service_registry import S, ServiceRegistry
from .task_manager import TaskManager
from .tool_loader import register_core_tools
from .world_state import WorldStateService, get_world_state

__version__ = "9.0.0"

__all__ = [
    "Task",
    "Action",
    "ExecutionResult",
    "ActionExecutionContext",
    "TaskManager",
    "ContextBuilder",
    "ExecutionRuntime",
    "WorldStateService",
    "get_world_state",
    "OrchestrationEngine",
    "AppBootstrap",
    "ServiceRegistry",
    "S",
    "register_core_tools",
]
