"""
Makima OS — Typed Service Registry Container
Location: apps/brain/core/service_registry.py

Strongly-typed dependency injection container.
Replaces untyped global `_modules` dictionary anti-pattern with full IDE autocompletion & type safety.
"""

from typing import Any, Dict


class S:
    """
    Centralized registry of canonical service keys in Makima OS.
    Eliminates magic strings and typo-induced runtime errors across DI injection.
    """
    SETTINGS = "settings_store"
    AI_HANDLER = "ai_handler"
    GUARDRAILS = "guardrails"
    OLLAMA = "ollama"
    MEMORY = "memory"
    ETERNAL_MEMORY = "eternal_memory"
    TOOL_REGISTRY = "tool_registry"
    CAPABILITY_MESH = "capability_mesh"
    WORLD_STATE = "world_state"
    CONTEXT_BUILDER = "context_builder"
    SAGA_RECOVERY = "saga_recovery"
    RECOVERY_MANAGER = "recovery_manager"
    EXECUTION_RUNTIME = "execution_runtime"
    CLIPBOARD = "clipboard_handler"
    SCREEN_READER = "screen_reader"
    REFLEXION_ENGINE = "reflexion_engine"
    SKILL_LIBRARY = "skill_library"
    LEARNING_ENGINE = "learning_engine"
    LEARNING_COORD = "learning_coordinator"
    LEARNING = "learning"
    ORCHESTRATOR = "orchestrator"
    TASK_MANAGER = "task_manager"
    ORCH_ENGINE = "orchestration_engine"
    COMMAND_ROUTER = "command_router"
    ROUTER = "router"
    VOICE = "voice"
    SPEECH = "speech"
    VOICE_ENGINE = "voice_engine"
    WATCHDOG = "watchdog"
    DISK_GUARD = "disk_guard"
    HEALTH = "health"
    GHOST_WATCHDOG = "ghost_watchdog"
    PROACTIVE = "proactive_orchestrator"
    FOCUS = "focus_profiles"
    MEMORY_FORGET = "memory_forget"
    MEDIA_STORE = "media_store"
    MULTIMODAL = "multimodal"
    DURABLE_TASKS = "durable_task_engine"
    THOUGHT_PLANNER = "thought_planner"


class ServiceRegistry:
    """
    Strongly-typed dependency injection container for Makima modules.
    Exposes explicit properties for core services while maintaining backward-compatible dict access.
    """

    def __init__(self) -> None:
        self._services: Dict[str, Any] = {}

    def register(self, name: str, instance: Any) -> None:
        """Register a named service instance."""
        self._services[name] = instance

    def get(self, name: str, default: Any = None) -> Any:
        """Retrieve a service instance by name (dict-compatible)."""
        return self._services.get(name, default)

    def update(self, services: Dict[str, Any]) -> None:
        """Batch update services (legacy dict compatibility)."""
        self._services.update(services)

    def __getitem__(self, item: str) -> Any:
        return self._services[item]

    def __setitem__(self, key: str, value: Any) -> None:
        self._services[key] = value

    def __contains__(self, item: str) -> bool:
        return item in self._services

    def items(self):
        return self._services.items()

    def keys(self):
        return self._services.keys()

    def values(self):
        return self._services.values()

    def __iter__(self):
        return iter(self._services)

    @property
    def ai_handler(self) -> Any:
        return self._services.get("ai_handler")

    @property
    def orchestration_engine(self) -> Any:
        return self._services.get("orchestration_engine") or self._services.get("command_router")

    @property
    def learning_coordinator(self) -> Any:
        return self._services.get("reflexion_engine") or self._services.get("learning_coordinator")

    @property
    def reflexion_engine(self) -> Any:
        return self._services.get("reflexion_engine")

    @property
    def skill_library(self) -> Any:
        return self._services.get("skill_library")

    @property
    def learning_engine(self) -> Any:
        return self._services.get("reflexion_engine")

    @property
    def memory(self) -> Any:
        return self._services.get("memory") or self._services.get("eternal_memory")

    @property
    def tool_registry(self) -> Any:
        return self._services.get("tool_registry")

    @property
    def guardrails(self) -> Any:
        return self._services.get("guardrails")

    @property
    def saga_recovery(self) -> Any:
        return self._services.get("saga_recovery") or self._services.get("recovery_manager")

    @property
    def durable_task_engine(self) -> Any:
        return self._services.get("durable_task_engine")

    @property
    def thought_planner(self) -> Any:
        return self._services.get("thought_planner")
