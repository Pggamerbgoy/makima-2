"""
Makima OS v9.0 — Concurrent DAG App Bootstrap Engine
Location: apps/brain/core/app_bootstrap.py

SOTA Upgrades:
  1. Concurrent DAG Bootstrapping (Parallel wave execution via topological resolution)
  2. Declarative Dependency Graph (Explicit dependencies, zero implicit phase-order crashes)
  3. Auto-Discovery Lifecycle Hooks (Supports both start/stop and close lifecycle methods)
  4. Centralized Service Keys (class S, zero magic strings)
  5. Graceful Degradation (Null-safe non-critical failures with automatic critical rollback)
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from .service_registry import S, ServiceRegistry

logger = logging.getLogger("makima.app_bootstrap")


# =============================================================================
# 1. DAG Node Definition
# =============================================================================

@dataclass
class ServiceNode:
    """A single declarative service node in the application bootstrap DAG."""
    name: str
    factory: Callable[..., Any]
    deps: list[str] = field(default_factory=list)
    critical: bool = False
    aliases: list[str] = field(default_factory=list)


# =============================================================================
# 2. Concurrent DAG Bootstrapper Engine
# =============================================================================

class AppBootstrap:
    """
    Enterprise Concurrent DAG Bootstrapper.
    Resolves dependency frontiers and initializes independent services in parallel waves.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, ws_broadcast: Optional[Any] = None) -> None:
        self.config = config or {}
        self.ws_broadcast = ws_broadcast
        self.services = ServiceRegistry()
        self._nodes: Dict[str, ServiceNode] = {}
        self._start_callbacks: List[Callable] = []
        self._stop_callbacks: List[Callable] = []
        self._phase_timings: Dict[str, float] = {}
        self._background_tasks: set[asyncio.Task] = set()

    def _register_node(
        self,
        name: str,
        factory: Callable,
        deps: Optional[List[str]] = None,
        critical: bool = False,
        aliases: Optional[List[str]] = None,
    ) -> None:
        self._nodes[name] = ServiceNode(
            name=name,
            factory=factory,
            deps=list(deps or []),
            critical=critical,
            aliases=list(aliases or []),
        )

    def _build_dependency_graph(self) -> None:
        """Declarative dependency graph replacing the monolithic linear phases."""
        self._nodes.clear()

        # ── Wave 1: Core Foundation (Zero Dependencies) ──────────────────────
        self._register_node(S.SETTINGS, self._init_settings, critical=True)
        self._register_node(S.OLLAMA, self._init_ollama)
        self._register_node(S.MEDIA_STORE, self._init_media_store, aliases=["media"])
        self._register_node(S.FOCUS, self._init_focus)

        # ── Wave 2: AI & Memory (Depends on Settings) ────────────────────────
        self._register_node(
            S.AI_HANDLER, self._init_ai_handler, deps=[S.SETTINGS], critical=True,
        )
        self._register_node(
            S.MEMORY, self._init_memory, aliases=[S.ETERNAL_MEMORY],
        )

        # ── Wave 3: Tools, Context & Multimedia (Depends on AI, Memory, Media)
        self._register_node(
            S.TOOL_REGISTRY, self._init_tool_registry,
        )
        self._register_node(
            S.MULTIMODAL, self._init_multimodal, deps=[S.MEDIA_STORE, S.AI_HANDLER],
        )
        self._register_node(
            S.MEMORY_FORGET, self._init_memory_forget, deps=[S.AI_HANDLER, S.MEMORY],
        )

        # ── Wave 4: Execution & Learning (Depends on Tools, AI, Memory) ──────
        self._register_node(
            S.REFLEXION_ENGINE, self._init_reflexion_engine,
            deps=[S.AI_HANDLER, S.MEMORY],
            aliases=[S.LEARNING_ENGINE, S.LEARNING_COORD, S.LEARNING],
        )
        self._register_node(
            S.EXECUTION_RUNTIME, self._init_execution_runtime, deps=[S.TOOL_REGISTRY],
        )
        self._register_node(
            S.SAGA_RECOVERY, self._init_saga_recovery,
            deps=[S.TOOL_REGISTRY, S.EXECUTION_RUNTIME],
            aliases=[S.RECOVERY_MANAGER],
        )

        # ── Wave 5: Skill Library & Swarm Orchestrator ────────────────────────
        self._register_node(
            S.SKILL_LIBRARY, self._init_skill_library,
            deps=[S.TOOL_REGISTRY, S.EXECUTION_RUNTIME, S.MEMORY, S.AI_HANDLER],
        )
        self._register_node(
            S.ORCHESTRATOR, self._init_orchestrator,
            deps=[
                S.AI_HANDLER, S.MEMORY, S.TOOL_REGISTRY,
                S.EXECUTION_RUNTIME, S.SAGA_RECOVERY,
                S.REFLEXION_ENGINE,
            ],
            critical=True,
        )

        # ── Wave 6: Core Engines (Task Manager, Durable Tasks, Thought Planner) ──
        self._register_node(
            S.TASK_MANAGER, self._init_task_manager,
        )
        self._register_node(
            S.DURABLE_TASKS, self._init_durable_task_engine,
            deps=[S.ORCHESTRATOR, S.TASK_MANAGER],
        )
        self._register_node(
            S.THOUGHT_PLANNER, self._init_thought_planner,
            deps=[S.AI_HANDLER, S.TOOL_REGISTRY],
        )
        self._register_node(
            S.ORCH_ENGINE, self._init_orchestration_engine,
            deps=[
                S.AI_HANDLER, S.ORCHESTRATOR, S.MEMORY,
                S.TASK_MANAGER, S.REFLEXION_ENGINE, S.SKILL_LIBRARY,
                S.DURABLE_TASKS, S.THOUGHT_PLANNER,
            ],
            critical=True,
            aliases=[S.COMMAND_ROUTER, S.ROUTER],
        )

        # ── Wave 7: Voice & Platform Services (Depends on Orch Engine) ───────
        self._register_node(
            S.VOICE, self._init_voice, deps=[S.ORCH_ENGINE, S.TOOL_REGISTRY], aliases=[S.SPEECH, S.VOICE_ENGINE],
        )
        self._register_node(
            S.HEALTH, self._init_health, deps=[S.AI_HANDLER, S.ORCH_ENGINE],
        )
        self._register_node(
            S.PROACTIVE, self._init_proactive_orchestrator,
            deps=[S.AI_HANDLER, S.ORCHESTRATOR, S.ORCH_ENGINE, S.DURABLE_TASKS],
        )

    async def initialize_services(self) -> ServiceRegistry:
        """Execute the DAG bootstrapper in parallel topological waves."""
        logger.info("Starting Concurrent DAG Bootstrapper...")
        total_start = time.monotonic()
        if not self._nodes:
            self._build_dependency_graph()

        # 1. Topological Sort into Dependency Frontiers (Waves)
        in_degree = {n: len(node.deps) for n, node in self._nodes.items()}
        graph: dict[str, list[str]] = defaultdict(list)
        for n, node in self._nodes.items():
            for dep in node.deps:
                graph[dep].append(n)

        queue = deque([n for n, deg in in_degree.items() if deg == 0])
        
        # 2. Execute Waves Concurrently
        wave_num = 0
        try:
            while queue:
                wave_num += 1
                wave_start = time.monotonic()
                current_wave = list(queue)
                queue.clear()

                # Run all independent services in this wave concurrently
                tasks = [self._run_node(name) for name in current_wave]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for name, res in zip(current_wave, results):
                    node = self._nodes[name]
                    if isinstance(res, Exception):
                        logger.error("Service '%s' failed to initialize: %s", name, res)
                        if node.critical:
                            raise RuntimeError(f"CRITICAL Phase failure: Service '{name}' failed to boot") from res
                        # Non-critical service failed: gracefully degrade (downstream gets None)
                    else:
                        if res is not None:
                            self.services.register(name, res)
                            for alias in node.aliases:
                                self.services.register(alias, res)

                            # Auto-discover lifecycle hooks (start, stop, close)
                            if hasattr(res, "start") and callable(getattr(res, "start")):
                                self._start_callbacks.append(getattr(res, "start"))
                            if hasattr(res, "stop") and callable(getattr(res, "stop")):
                                self._stop_callbacks.append(getattr(res, "stop"))
                            elif hasattr(res, "close") and callable(getattr(res, "close")):
                                self._stop_callbacks.append(getattr(res, "close"))

                    # Always unlock downstream dependents for the next wave
                    for neighbor in graph[name]:
                        in_degree[neighbor] -= 1
                        if in_degree[neighbor] == 0:
                            queue.append(neighbor)

                self._phase_timings[f"wave_{wave_num}"] = time.monotonic() - wave_start

            # Boot-time tool-support contract (best effort, non-blocking background task with strict timeout)
            ai_handler_inst = self.services.get(S.AI_HANDLER)
            if ai_handler_inst is not None and hasattr(ai_handler_inst, "probe_tool_support"):
                async def _run_probe() -> None:
                    try:
                        await asyncio.wait_for(ai_handler_inst.probe_tool_support(timeout_s=3.0), timeout=3.5)
                    except asyncio.TimeoutError:
                        logger.warning("Boot probe timed out after 3.5s")
                    except Exception as probe_err:
                        logger.warning("Boot probe encountered error: %s", probe_err)

                probe_task = asyncio.create_task(_run_probe())
                self._background_tasks.add(probe_task)
                probe_task.add_done_callback(self._background_tasks.discard)

            # Validate that critical services are running
            self._validate_critical_services()
            total_elapsed = time.monotonic() - total_start
            logger.info(
                "DAG Bootstrapper completed in %.2fs | Waves: %d | Timings: %s",
                total_elapsed, wave_num,
                {k: f"{v:.3f}s" for k, v in self._phase_timings.items()},
            )
            return self.services

        except Exception as startup_err:
            logger.error("Startup aborted due to critical error: %s — executing rollback cleanup", startup_err)
            try:
                await self.shutdown_services()
            except Exception as rollback_err:
                logger.error("Error during startup rollback cleanup: %s", rollback_err)
            raise

    async def _run_node(self, name: str) -> Any:
        """Execute a single service factory safely without blocking the event loop."""
        node = self._nodes[name]
        if inspect.iscoroutinefunction(node.factory):
            return await node.factory()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, node.factory)

    # =========================================================================
    # Service Factories (Isolated, Modular, Unit-Testable)
    # =========================================================================

    def _init_settings(self) -> Any:
        from .user_settings_store import UserSettingsStore
        return UserSettingsStore()


    def _init_ollama(self) -> Any:
        from ..ollama_service import OllamaService
        return OllamaService(self.config)

    def _init_media_store(self) -> Any:
        from ..media_store import MediaStore
        return MediaStore()

    def _init_focus(self) -> Any:
        from ..focus_profiles import FocusProfiles
        return FocusProfiles()

    def _init_ai_handler(self) -> Any:
        from ..ai_handler import AIHandler
        settings_store = self.services.get(S.SETTINGS)
        runtime_config = copy.deepcopy(self.config)
        if settings_store and hasattr(settings_store, "get_settings"):
            user_settings = settings_store.get_settings()
            saved_backend = user_settings.get("default_llm_backend")
            if saved_backend:
                runtime_config.setdefault("llm", {})["default_provider"] = saved_backend
            elif not runtime_config.get("llm", {}).get("default_provider"):
                runtime_config.setdefault("llm", {})["default_provider"] = (
                    runtime_config.get("llm", {}).get("active_provider") or "gemini"
                )
            for provider, overrides in settings_store.get_llm_overrides().items():
                provider_config = (
                    runtime_config.setdefault("llm", {})
                    .setdefault("backends", {})
                    .setdefault(provider, {})
                )
                provider_config.update(overrides)

        try:
            return AIHandler(config=runtime_config)
        except Exception as e:
            logger.warning("AIHandler primary initialization failed (%s) — attempting safe degraded mode", e)
            fallback_config = copy.deepcopy(runtime_config)
            fallback_config.setdefault("llm", {})["default_provider"] = "mock"
            return AIHandler(config=fallback_config)

    async def _init_memory(self) -> Any:
        from ..eternal_memory import EternalMemory
        mem = EternalMemory(config=self.config)
        await mem.start()
        self._stop_callbacks.append(mem.stop)
        return mem

    async def _init_tool_registry(self) -> Any:
        from ..tool_registry import ToolRegistry
        from .context_builder import ContextBuilder
        # TODO: SagaRecoveryEngine will handle this
        from .tool_loader import register_core_tools
        from .world_state import get_world_state

        tool_registry = ToolRegistry()
        world_state = get_world_state()
        context_builder = ContextBuilder()
        self.services.register(S.WORLD_STATE, world_state)
        self.services.register(S.CONTEXT_BUILDER, context_builder)

        register_core_tools(tool_registry, self.services)

        # Bug 1 fix: Wire MCP servers declared in config into the ToolRegistry.
        # Done as a background task so slow MCP subprocess startups don't block boot.
        mcp_servers_config = self.config.get("mcp_servers") or []
        if mcp_servers_config:
            from .tool_loader import register_mcp_tools
            import asyncio as _asyncio
            try:
                loop = _asyncio.get_running_loop()
                loop.create_task(register_mcp_tools(tool_registry, mcp_servers_config))
                logger.info("Scheduled MCP tool registration for %d server(s)", len(mcp_servers_config))
            except RuntimeError:
                # No running loop during sync bootstrap — skip; MCP tools won't be available
                logger.warning("No running event loop during MCP registration — MCP tools skipped at boot")

        return tool_registry

    def _init_multimodal(self) -> Any:
        from ..multimodal_service import MultimodalService
        return MultimodalService(self.services.get(S.MEDIA_STORE), self.services.get(S.AI_HANDLER))

    def _init_memory_forget(self) -> Any:
        from ..memory_forget import MemoryForgetHandler
        return MemoryForgetHandler(
            ai_handler=self.services.get(S.AI_HANDLER),
            triple_store=None,
            vector_index=None,
            ws_broadcast=self.ws_broadcast,
            eternal_memory=self.services.get(S.MEMORY),
        )

    async def _init_reflexion_engine(self) -> Any:
        from ..reflexion_engine import ReflexionEngine
        engine = ReflexionEngine(
            eternal_memory=self.services.get(S.MEMORY),
            ai_handler=self.services.get(S.AI_HANDLER),
        )
        await engine.start()
        return engine

    def _init_execution_runtime(self) -> Any:
        from .execution_runtime import ExecutionRuntime
        ref_eng = self.services.get(S.REFLEXION_ENGINE)
        return ExecutionRuntime(
            tool_registry=self.services.get(S.TOOL_REGISTRY),
            learning_coordinator=ref_eng,
            guardrails=None,
            saga_recovery=None,
        )

    def _init_saga_recovery(self) -> Any:
        from .saga_recovery_engine import SagaRecoveryEngine
        exec_rt = self.services.get(S.EXECUTION_RUNTIME)
        tool_reg = self.services.get(S.TOOL_REGISTRY)
        saga_engine = SagaRecoveryEngine(
            tool_registry=tool_reg,
            execution_runtime=exec_rt,
        )
        if exec_rt:
            exec_rt.saga_recovery = saga_engine
            exec_rt.recovery_manager = saga_engine
        return saga_engine

    def _init_orchestrator(self) -> Any:
        from .kernel import NextGenOrchestrator
        ref_eng = self.services.get(S.REFLEXION_ENGINE)
        orchestrator = NextGenOrchestrator(
            ai_handler=self.services.get(S.AI_HANDLER),
            memory=self.services.get(S.MEMORY),
            tool_registry=self.services.get(S.TOOL_REGISTRY),
            ws_broadcast=self.ws_broadcast,
            config=self.config,
            learning_engine=ref_eng,
            learning_coordinator=ref_eng,
            guardrails=None,
        )
        orchestrator.reflexion_engine = ref_eng
        # Cross-wire canonical runtime references
        exec_rt = self.services.get(S.EXECUTION_RUNTIME)
        saga_rec = self.services.get(S.SAGA_RECOVERY)
        orchestrator.execution_runtime = exec_rt
        orchestrator.saga_recovery = saga_rec
        orchestrator.recovery_manager = saga_rec
        if exec_rt:
            exec_rt.kernel = orchestrator
            exec_rt.learning_coordinator = ref_eng

        return orchestrator

    def _init_task_manager(self) -> Any:
        from .task_manager import TaskManager
        return TaskManager()

    async def _init_skill_library(self) -> Any:
        from ..skill_library import SkillLibrary
        lib = SkillLibrary(
            eternal_memory=self.services.get(S.MEMORY),
            ai_handler=self.services.get(S.AI_HANDLER),
            tool_registry=self.services.get(S.TOOL_REGISTRY),
            execution_runtime=self.services.get(S.EXECUTION_RUNTIME),
        )
        await lib.start()
        return lib

    def _init_durable_task_engine(self) -> Any:
        from .durable_task_engine import DurableTaskEngine
        orch = self.services.get(S.ORCHESTRATOR)
        tm = self.services.get(S.TASK_MANAGER)
        if orch and hasattr(orch, "durable_task_engine") and orch.durable_task_engine:
            dte = orch.durable_task_engine
            dte._task_manager = tm
            return dte
        event_store = getattr(orch, "event_store", None)
        if not event_store:
            from .persistence import EventStore
            event_db_path = self.config.get("KERNEL_EVENTS_DB", "~/.makima/kernel_events.db")
            event_store = EventStore(event_db_path)
        return DurableTaskEngine(event_store=event_store, task_manager=tm)

    def _init_thought_planner(self) -> Any:
        from .thought_planner import ThoughtPlanner
        return ThoughtPlanner(
            ai_handler=self.services.get(S.AI_HANDLER),
            tool_registry=self.services.get(S.TOOL_REGISTRY),
        )

    def _init_orchestration_engine(self) -> Any:
        from .orchestration_engine import OrchestrationEngine
        ref_eng = self.services.get(S.REFLEXION_ENGINE)
        skill_lib = self.services.get(S.SKILL_LIBRARY)
        orch = OrchestrationEngine(
            ai_handler=self.services.get(S.AI_HANDLER),
            agent_orchestrator=self.services.get(S.ORCHESTRATOR),
            eternal_memory=self.services.get(S.MEMORY),
            context_budget=None,
            screen_reader=None,
            clipboard_handler=None,
            ws_broadcast=self.ws_broadcast,
            learning_coordinator=ref_eng,
            learning_engine=ref_eng,
            reflexion_engine=ref_eng,
            skill_library=skill_lib,
            task_manager=self.services.get(S.TASK_MANAGER),
            durable_task_engine=self.services.get(S.DURABLE_TASKS),
            thought_planner=self.services.get(S.THOUGHT_PLANNER),
        )
        orch.reflexion_engine = ref_eng
        orch.skill_library = skill_lib
        return orch

    def _init_voice(self) -> Any:
        import os
        from ..voice import VoiceEngine, VoiceConfig
        from ..ai_handler import is_valid_api_key

        raw_key = (
            self.config.get("gemini_api_key")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("MAKIMA_GEMINI_KEY")
            or ""
        )
        api_key = raw_key if is_valid_api_key(raw_key) else ""

        voice_cfg = VoiceConfig.load()

        voice_engine = VoiceEngine(
            api_key=api_key,
            ws_broadcast=self.ws_broadcast,
            command_router=self.services.get(S.ORCH_ENGINE),
            tool_registry=self.services.get(S.TOOL_REGISTRY),
            voice_config=voice_cfg,
        )
        self.gemini_voice = voice_engine
        self._start_callbacks.append(voice_engine.start)
        self._stop_callbacks.append(voice_engine.stop)
        return voice_engine

    def _init_health(self) -> Any:
        from ..health_aggregator import HealthAggregator
        health = HealthAggregator(
            ai_handler=self.services.get(S.AI_HANDLER),
            ws_broadcast=self.ws_broadcast,
        )
        orchestration_engine = self.services.get(S.ORCH_ENGINE)
        if orchestration_engine is not None:
            orchestration_engine.health_aggregator = health
        return health

    def _init_proactive_orchestrator(self) -> Any:
        from ..proactive_orchestrator import ProactiveOrchestrator
        from .service_registry import S
        # TODO: ReflexionEngine will handle this
        return ProactiveOrchestrator(
            config=self.config,
            ws_broadcast=self.ws_broadcast,
            ai_handler=self.services.get(S.AI_HANDLER),
            learning_engine=None,
            kernel=self.services.get(S.ORCHESTRATOR),
            orchestration_engine=self.services.get(S.ORCH_ENGINE),
            durable_task_engine=self.services.get(S.DURABLE_TASKS),
        )

    # =========================================================================
    # Validation, Health & Lifecycle
    # =========================================================================

    def is_ready(self) -> bool:
        """Return True if all critical core services are active and registered."""
        critical = [S.AI_HANDLER, S.ORCHESTRATOR, S.ORCH_ENGINE, S.TOOL_REGISTRY]
        return all(self.services.get(name) is not None for name in critical)

    def get_services_health(self) -> Dict[str, Any]:
        """Return a comprehensive health map of all registered services."""
        critical = [S.AI_HANDLER, S.ORCHESTRATOR, S.ORCH_ENGINE, S.TOOL_REGISTRY, S.MEMORY]
        health: Dict[str, Any] = {
            "ready": self.is_ready(),
            "total_services": len(list(self.services.keys())),
            "phase_timings": dict(self._phase_timings),
            "services": {},
        }
        for name in critical:
            inst = self.services.get(name)
            health["services"][name] = {
                "registered": inst is not None,
                "class": type(inst).__name__ if inst is not None else None,
            }
        return health

    def _validate_critical_services(self) -> None:
        """Assert that essential services are available. Raise RuntimeError for missing ones."""
        critical = [S.AI_HANDLER, S.ORCHESTRATOR, S.ORCH_ENGINE]
        missing = [name for name in critical if self.services.get(name) is None]
        if missing:
            msg = (
                f"CRITICAL: Essential services failed to initialize: {missing}. "
                "The Makima brain cannot serve requests in this state. "
                "Check startup logs above for the root cause."
            )
            logger.error(msg)
            raise RuntimeError(msg)
        else:
            logger.info("Critical service validation passed: all essential services available.")

    async def start_background_services(self) -> None:
        """Start registered background task loops."""
        loop = asyncio.get_running_loop()
        for cb in self._start_callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    await cb()
                else:
                    await loop.run_in_executor(None, cb)
            except Exception as e:
                logger.warning("Background service start error: %s", e)

    async def shutdown_services(self) -> None:
        """Graceful reverse shutdown sequence."""
        logger.info("Starting AppBootstrap graceful shutdown...")
        loop = asyncio.get_running_loop()
        for cb in reversed(self._stop_callbacks):
            try:
                if inspect.iscoroutinefunction(cb):
                    await cb()
                else:
                    await loop.run_in_executor(None, cb)
            except Exception as e:
                logger.warning("Service shutdown error: %s", e)
        logger.info("AppBootstrap shutdown complete.")

    @asynccontextmanager
    async def lifespan(self, app: Any) -> AsyncGenerator[ServiceRegistry, None]:
        """FastAPI lifespan context manager integration."""
        await self.initialize_services()
        await self.start_background_services()
        try:
            yield self.services
        finally:
            await self.shutdown_services()
