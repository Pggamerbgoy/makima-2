"""
Makima v7.1 — Agent Orchestrator

Owns the 11 core agent instances. Manages agent lifecycle:
  init → idle → running → cancelled

Rules:
  - One active interactive agent at a time
  - Background agents run in daemon threads
  - CommanderAgent dispatches subtasks to leaf agents
  - Emits agent_started / agent_done / agent_error WS events
  - Agent guardrails enforced per task: wall-time (via asyncio.wait_for,
    since only the orchestrator can interrupt an agent from the outside)
    and tool-call count (enforced inside BaseAgent._use_tool(), raised as
    GuardrailExceeded and caught here).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .agent_guardrails import GuardrailExceeded
from .coordination import CoordinationHub

logger = logging.getLogger("makima.agent_orchestrator")


class AgentState(str, Enum):
    INIT = "init"
    IDLE = "idle"
    RUNNING = "running"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class AgentInstance:
    """Runtime state for a single agent."""
    name: str
    agent: Any  # The actual agent object
    state: AgentState = AgentState.INIT
    current_task_id: Optional[str] = None
    started_at: Optional[float] = None
    tokens_used: int = 0
    tool_calls: int = 0


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
    is_partial: bool = False  # True if guardrail cut it short


class AgentOrchestrator:
    """
    Manages 11 core agent instances and dispatches tasks to them.
    Enforces one active interactive agent at a time.
    """
    
    def __init__(self, ai_handler, memory, tool_registry=None,
                 guardrails=None, ws_broadcast=None, config: dict = None):
        self.ai_handler = ai_handler
        self.memory = memory
        self.tool_registry = tool_registry
        self.guardrails = guardrails
        self.ws_broadcast = ws_broadcast
        self.config = config or {}
        
        # Agent instances
        self.agents: dict[str, AgentInstance] = {}
        
        # Active interactive task tracking
        self._active_interactive: Optional[str] = None
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._interactive_idle = asyncio.Event()
        self._interactive_idle.set()
        self._interactive_state_lock = asyncio.Lock()
        self._active_interactive_refcount: dict[str, int] = {}
        self.subtask_semaphore = asyncio.Semaphore(4)
        self.agent_locks: dict[str, asyncio.Lock] = {}

        # Task cancellation propagation (F3): per-task asyncio.Event tokens +
        # parent→child registry so cancelling a parent task immediately cancels
        # every descendant (commander subtasks, ecosystem DAG workers).
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._parent_children: dict[str, set[str]] = {}

        # Resumable guardrail drafts — task_id -> {agent, partial, ts}
        # Persisted to JSON so partial work survives restarts.
        self._drafts: dict[str, dict] = {}
        self._drafts_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "drafts.json",
        )

        # Elite Coordination Ecosystem
        self.coordination = CoordinationHub(
            max_entries_per_task=self.config.get("blackboard_max_entries", 500),
            max_queue_depth=self.config.get("event_bus_max_queue", 100),
            max_consultations_per_task=self.config.get("max_consultations_per_task", 3),
            ai_handler=self.ai_handler,
        )
        self.coordination.set_orchestrator(self)

        self._init_agents()
        self._load_drafts()
    
    def _init_agents(self) -> None:
        """Initialize all 12 core agent instances."""
        # [MIGRATION]: commander_agent deprecated in favor of EcosystemHub but restored for suite tests
        from .agents.research_agent import ResearchAgent
        from .agents.code_agent import CodeAgent
        from .agents.creative_agent import CreativeAgent
        from .agents.memory_agent import MemoryAgent
        from .agents.system_agent import SystemAgent
        from .agents.messaging_agent import MessagingAgent
        from .agents.media_agent import MediaAgent
        from .agents.browser_agent import BrowserAgent
        from .agents.voice_agent import VoiceAgent
        from .agents.automation_agent import AutomationAgent
        from .agents.document_agent import DocumentAgent
        from .agents.data_analyst_agent import DataAnalystAgent
        from .agents.security_agent import SecurityAgent
        from .agents.devops_agent import DevOpsAgent
        from .agents.elite_ecosystem import EcosystemAgent

        agent_classes = {
            "commander_agent": EcosystemAgent,
            "research_agent": ResearchAgent,
            "code_agent": CodeAgent,
            "creative_agent": CreativeAgent,
            "memory_agent": MemoryAgent,
            "system_agent": SystemAgent,
            "messaging_agent": MessagingAgent,
            "media_agent": MediaAgent,
            "browser_agent": BrowserAgent,
            "voice_agent": VoiceAgent,
            "automation_agent": AutomationAgent,
            "document_agent": DocumentAgent,
            "data_analyst_agent": DataAnalystAgent,
            "security_agent": SecurityAgent,
            "devops_agent": DevOpsAgent,
        }
        
        for name, cls in agent_classes.items():
            try:
                agent = cls(
                    ai_handler=self.ai_handler,
                    memory=self.memory,
                    tool_registry=self.tool_registry,
                    ws_broadcast=self.ws_broadcast,
                    orchestrator=self,  # For Commander to dispatch subtasks
                    guardrails=self.guardrails,
                    coordination=self.coordination,  # Elite coordination ecosystem
                )
                self.agents[name] = AgentInstance(name=name, agent=agent, state=AgentState.IDLE)
                self.agent_locks[name] = asyncio.Lock()
                logger.info(f"Agent initialized: {name}")

                # Register agent capability profile in the registry
                try:
                    from .coordination import AgentProfile
                    import yaml
                    from pathlib import Path
                    
                    profile_name = name.replace("_agent", "")
                    desc = getattr(agent, "DESCRIPTION", name)
                    caps = getattr(agent, "CAPABILITIES", [])
                    tools = getattr(agent, "AGENT_TOOLS", [])
                    tags = getattr(agent, "TAGS", [])
                    
                    yaml_path = Path(__file__).resolve().parents[2] / "configs" / "agent_profiles.yaml"
                    if yaml_path.exists():
                        try:
                            with open(yaml_path, "r", encoding="utf-8") as f:
                                profiles_data = yaml.safe_load(f) or {}
                                agent_yaml = profiles_data.get("agents", {}).get(profile_name)
                                if agent_yaml:
                                    desc = agent_yaml.get("description", desc)
                                    caps = agent_yaml.get("capabilities", caps)
                                    tools = agent_yaml.get("tools", tools)
                                    tags = agent_yaml.get("tags", tags)
                        except Exception as ey:
                            logger.warning(f"Failed to read agent_profiles.yaml for {profile_name}: {ey}")

                    profile = AgentProfile(
                        name=profile_name,
                        description=desc,
                        capabilities=caps,
                        tools=tools,
                        tags=tags,
                    )
                    # Schedule registration (can't await in sync init)
                    asyncio.create_task(self.coordination.on_agent_register(profile))
                except Exception as e:
                    logger.debug(f"Capability registration skipped for {name}: {e}")

            except Exception as e:
                logger.error(f"Failed to initialize agent {name}: {e}")
                self.agents[name] = AgentInstance(name=name, agent=None, state=AgentState.ERROR)
    
    async def dispatch(self, task_id: str, agent_name: str, message: str,
                        context: dict[str, Any] = None, entities: dict = None,
                        is_background: bool = False) -> Optional[TaskResult]:
        """
        Dispatch a task to a specific agent.
        
        Args:
            task_id: Unique task correlation ID
            agent_name: Name of the agent to dispatch to
            message: User message / subtask description
            context: Rich context (memory, screen, clipboard, etc.)
            entities: Extracted entities from intent classification
            is_background: If True, run in background (don't block interactive).
                Also determines which guardrail limit tier applies —
                interactive tasks get the (typically more generous)
                interactive_limits, background tasks get background_limits.
        
        Returns:
            TaskResult on completion, None if backgrounded
        """
        if agent_name not in self.agents:
            logger.error(f"Unknown agent: {agent_name}")
            return TaskResult(
                task_id=task_id,
                agent_name=agent_name,
                success=False,
                error=f"Unknown agent: {agent_name}",
            )
        
        agent_instance = self.agents[agent_name]
        
        if agent_instance.agent is None:
            return TaskResult(
                task_id=task_id,
                agent_name=agent_name,
                success=False,
                error=f"Agent {agent_name} failed to initialize",
            )

        # F3: cancellation propagation — register this dispatch as a child of
        # the parent task (commander subtasks / ecosystem DAG workers), and
        # bail immediately if the parent was already cancelled.
        parent_id = (context or {}).get("_parent_task_id")
        if parent_id:
            if self._is_task_cancelled(parent_id):
                logger.info(f"Task {task_id} skipped — parent {parent_id} already cancelled")
                return TaskResult(
                    task_id=task_id,
                    agent_name=agent_name,
                    success=False,
                    error="Cancelled before dispatch (parent task cancelled)",
                )
            self.register_cancel_child(parent_id, task_id)
        
        owns_interactive_slot = False
        if not is_background:
            # Check bypass first (e.g. CommanderAgent dispatching subtasks)
            async with self._interactive_state_lock:
                if self._active_interactive == task_id:
                    # Log safety net: Warn if this bypass is hit unexpectedly
                    logger.debug(f"Bypassing interactive lock for nested task_id: {task_id}")
                    self._active_interactive_refcount[task_id] = self._active_interactive_refcount.get(task_id, 0) + 1
                    owns_interactive_slot = True
            
            if not owns_interactive_slot:
                while True:
                    if self._active_interactive is not None:
                        logger.debug(f"Task {task_id} waiting for interactive lock (held by {self._active_interactive})")
                    await self._interactive_idle.wait()
                    async with self._interactive_state_lock:
                        if self._active_interactive is None:
                            self._active_interactive = task_id
                            self._active_interactive_refcount[task_id] = 1
                            self._interactive_idle.clear()
                            owns_interactive_slot = True
                            break
                        if self._active_interactive == task_id:
                            # Edge case: slipped in while another bypassed
                            logger.debug(f"Late bypass for task_id: {task_id}")
                            self._active_interactive_refcount[task_id] = self._active_interactive_refcount.get(task_id, 0) + 1
                            owns_interactive_slot = True
                            break
        
        # Emit agent_started event
        if self.ws_broadcast:
            from . import ws_protocol
            await self.ws_broadcast(
                ws_protocol.build_agent_started(task_id, agent_name)
            )
        
        # Set agent state
        agent_instance.state = AgentState.RUNNING
        agent_instance.current_task_id = task_id
        agent_instance.started_at = time.time()
        agent_instance.tokens_used = 0
        agent_instance.tool_calls = 0
        
        # Tell the agent which guardrail tier applies to THIS run, so its
        # own _use_tool() guardrail check enforces the right limit.
        is_interactive = not is_background
        if hasattr(agent_instance.agent, "set_execution_mode"):
            agent_instance.agent.set_execution_mode(is_interactive)
        
        if is_background:
            # Run in background
            bg_task = asyncio.create_task(
                self._run_agent(agent_instance, task_id, message, context, entities, is_interactive)
            )
            self._background_tasks[task_id] = bg_task
            
            # KAMI-12 FIX: Remove task reference on completion to prevent memory leak
            def _cleanup_bg_task(fut: asyncio.Future) -> None:
                self._background_tasks.pop(task_id, None)
            
            bg_task.add_done_callback(_cleanup_bg_task)
            return None
        else:
            try:
                return await self._run_agent(
                    agent_instance, task_id, message, context, entities, is_interactive
                )
            finally:
                if owns_interactive_slot:
                    async with self._interactive_state_lock:
                        if self._active_interactive == task_id:
                            self._active_interactive_refcount[task_id] -= 1
                            if self._active_interactive_refcount[task_id] <= 0:
                                self._active_interactive = None
                                self._interactive_idle.set()
                                self._active_interactive_refcount.pop(task_id, None)
    
    async def _run_agent(self, agent_instance: AgentInstance, task_id: str,
                          message: str, context: dict = None,
                          entities: dict = None, is_interactive: bool = False) -> TaskResult:
        """Run an agent with guardrail enforcement."""
        start_time = time.time()

        # Elite: Notify coordination hub of task start
        try:
            await self.coordination.on_task_start(task_id, agent_instance.name)
        except Exception as e:
            logger.debug(f"Coordination on_task_start failed: {e}")

        # Wall-time limit: this is the only guardrail that MUST be enforced
        # from the outside (via asyncio.wait_for), since an agent can't
        # forcibly interrupt its own in-flight LLM call or tool call.
        # Tool-call count is enforced from the inside (BaseAgent._use_tool),
        # since it's checked at natural boundaries between calls.
        limits = self._get_limits(is_interactive, agent_instance.name)

        try:
            # F3: race the agent execution against the per-task cancellation
            # token. When the token fires (cancel_task), cancel the in-flight
            # execute immediately so the whole await chain unwinds instead of
            # waiting for the agent's cooperative _cancelled checks.
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
            cancel_watcher = asyncio.ensure_future(self._get_cancel_event(task_id).wait())
            done, pending = await asyncio.wait(
                {exec_task, cancel_watcher}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
            if cancel_watcher in done:
                # Cancellation token fired → propagate immediately. Set the
                # cooperative flag too (clean partial result), then raise
                # CancelledError so the existing cancellation path below runs.
                try:
                    if hasattr(agent_instance.agent, "cancel"):
                        await agent_instance.agent.cancel()
                except Exception as e:
                    logger.warning(f"Error calling agent.cancel() for {agent_instance.name}: {e}")
                exec_task.cancel()
                try:
                    await exec_task
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    logger.warning(f"Error waiting for exec_task cancellation in {agent_instance.name}: {e}")
                raise asyncio.CancelledError()
            result_text = exec_task.result()

            duration = time.time() - start_time
            agent_instance.state = AgentState.IDLE
            agent_instance.current_task_id = None

            # Pull the agent's REAL usage stats (previously this read
            # agent_instance.tokens_used/.tool_calls, which were reset to 0
            # at dispatch time and never updated again — always reported 0).
            stats = agent_instance.agent.get_execution_stats()
            agent_instance.tokens_used = stats["tokens_used"]
            agent_instance.tool_calls = stats["tool_calls"]

            result = TaskResult(
                task_id=task_id,
                agent_name=agent_instance.name,
                success=True,
                result=result_text,
                tokens_used=agent_instance.tokens_used,
                tool_calls=agent_instance.tool_calls,
                duration_s=duration,
            )

            # Elite: Notify coordination hub of task completion
            try:
                await self.coordination.on_task_complete(task_id, agent_instance.name, True, duration)
            except Exception as e:
                logger.debug(f"Coordination on_task_complete failed: {e}")

            # Emit agent_done
            if self.ws_broadcast:
                from . import ws_protocol
                await self.ws_broadcast(
                    ws_protocol.build_agent_done(task_id, agent_instance.name, str(result_text)[:200])
                )

            return result
            
        except asyncio.TimeoutError:
            # Guardrail: wall time exceeded
            duration = time.time() - start_time
            logger.warning(f"Agent {agent_instance.name} timed out after {duration:.1f}s")

            # Elite: Notify coordination hub
            try:
                await self.coordination.on_task_complete(task_id, agent_instance.name, False, duration)
            except Exception as e:
                logger.warning(f"Error in on_task_complete for {agent_instance.name}: {e}")

            return await self._handle_guardrail_hit(
                agent_instance, task_id, duration,
                reason="wall_time_exceeded",
                limit_value=limits["max_wall_time_s"],
            )

        except GuardrailExceeded as e:
            # Guardrail: tool-call limit exceeded (raised from inside the
            # agent's _use_tool(), so this is a clean self-cancellation —
            # not an error).
            duration = time.time() - start_time
            logger.warning(
                f"Agent {agent_instance.name} hit guardrail: {e.reason} "
                f"after {duration:.1f}s"
            )

            # Elite: Notify coordination hub
            try:
                await self.coordination.on_task_complete(task_id, agent_instance.name, False, duration)
            except Exception as e:
                logger.warning(f"Error in on_task_complete for {agent_instance.name}: {e}")

            return await self._handle_guardrail_hit(
                agent_instance, task_id, duration,
                reason=e.reason,
                limit_value=limits["max_tool_calls"],
            )

        except asyncio.CancelledError:
            duration = time.time() - start_time
            logger.warning(f"Agent {agent_instance.name} cancelled after {duration:.1f}s")

            agent_instance.state = AgentState.IDLE
            agent_instance.current_task_id = None

            # Elite: Notify coordination hub
            try:
                await self.coordination.on_task_cancelled(task_id, agent_instance.name)
            except Exception as e:
                logger.warning(f"Error in on_task_cancelled for {agent_instance.name}: {e}")

            # Emit cancellation WS event
            if self.ws_broadcast:
                from . import ws_protocol
                await self.ws_broadcast(ws_protocol.WSMessage(
                    v=ws_protocol.PROTOCOL_VERSION,
                    type=ws_protocol.ServerMessageType.AGENT_ERROR,
                    payload={"agent": agent_instance.name, "error": "Task cancelled"},
                    task_id=task_id,
                ))

            # Reraise so asyncio knows the task cancelled correctly
            raise

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Agent {agent_instance.name} error: {e}")

            agent_instance.state = AgentState.IDLE
            agent_instance.current_task_id = None

            # Elite: Notify coordination hub
            try:
                await self.coordination.on_task_complete(task_id, agent_instance.name, False, duration)
            except Exception as e:
                logger.warning(f"Error in on_task_complete for {agent_instance.name}: {e}")
            
            if self.ws_broadcast:
                from . import ws_protocol
                await self.ws_broadcast(ws_protocol.WSMessage(
                    v=ws_protocol.PROTOCOL_VERSION,
                    type=ws_protocol.ServerMessageType.AGENT_ERROR,
                    payload={"agent": agent_instance.name, "error": str(e)},
                    task_id=task_id,
                ))
            
            return TaskResult(
                task_id=task_id,
                agent_name=agent_instance.name,
                success=False,
                error=str(e),
                duration_s=duration,
            )
    
    async def _handle_guardrail_hit(self, agent_instance: AgentInstance, task_id: str,
                                     duration: float, reason: str, limit_value: Any) -> TaskResult:
        """
        Shared handling for any guardrail violation (wall-time or tool-call
        limit): reset agent state, save the partial result as a draft
        (never discard work), emit agent_guardrail_hit, return a TaskResult
        marked is_partial=True.
        """
        agent_instance.state = AgentState.IDLE
        agent_instance.current_task_id = None
        
        # Save partial result — never discard in-progress work
        partial = getattr(agent_instance.agent, "get_partial_result", lambda: "")()
        if partial:
            self._persist_draft(task_id, agent_instance.name, partial)
            # Best-effort also mirror to long-term memory if wired up
            if self.memory and hasattr(self.memory, "save_draft"):
                try:
                    await self.memory.save_draft(task_id, partial)
                except Exception:
                    pass
        
        # Pull whatever real usage stats we can, even on a cut-short run
        try:
            stats = agent_instance.agent.get_execution_stats()
            agent_instance.tokens_used = stats["tokens_used"]
            agent_instance.tool_calls = stats["tool_calls"]
        except Exception:
            pass
        
        if self.ws_broadcast:
            from . import ws_protocol
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
    
    def _get_limits(self, is_interactive: bool, agent_name: str = "") -> dict:
        """
        Get guardrail limits for this run. Delegates to AgentGuardrails
        (the single source of truth for limit values) when available,
        falling back to config-derived defaults only if guardrails wasn't
        supplied (e.g. in isolated unit tests).

        Optionally accepts an agent_name to apply per-agent overrides
        declared in config under agents.<agent_name>.limits.{interactive_limits|background_limits}.
        """
        if self.guardrails is not None:
            limits = dict(self.guardrails.get_limits(is_interactive))
            if agent_name:
                section = self.config.get("agents", {}).get(agent_name, {}).get("limits", {})
                override = section.get("interactive_limits" if is_interactive
                                       else "background_limits", {})
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

    def _persist_draft(self, task_id: str, agent_name: str, partial: str) -> None:
        """Store a guardrail-hit partial result persistently (resumable drafts)."""
        self._drafts[task_id] = {
            "agent": agent_name,
            "partial": partial,
            "ts": time.time(),
        }
        try:
            os.makedirs(os.path.dirname(self._drafts_path), exist_ok=True)
            with open(self._drafts_path, "w", encoding="utf-8") as f:
                json.dump(self._drafts, f)
        except Exception as e:
            logger.warning(f"Could not persist draft for {task_id}: {e}")

    def _load_drafts(self) -> None:
        try:
            with open(self._drafts_path, "r", encoding="utf-8") as f:
                self._drafts = json.load(f)
        except FileNotFoundError:
            self._drafts = {}
        except Exception as e:
            logger.warning(f"Could not load drafts: {e}")
            self._drafts = {}

    async def get_draft(self, task_id: str) -> Optional[str]:
        """Retrieve the saved partial result for a guardrail-interrupted task."""
        self._load_drafts()
        entry = self._drafts.get(task_id)
        return entry.get("partial") if entry else None

    async def resume_task(self, task_id: str, agent_name: str,
                          extra_instruction: str = "") -> Optional[TaskResult]:
        """
        Resume a guardrail-interrupted task from its saved draft. Re-dispatches
        the task with the partial result prepended as context so the agent
        continues instead of restarting from scratch.
        """
        self._load_drafts()
        entry = self._drafts.get(task_id)
        if not entry:
            logger.warning(f"resume_task: no draft found for {task_id}")
            return None
        draft_text = entry.get("partial", "")
        continuation = (
            f"[Previous partial work from guardrail-interrupted run]\n{draft_text}\n"
            f"--- RESUMING ---\n{extra_instruction}".strip()
        )
        return await self.dispatch(
            task_id=task_id,
            agent_name=agent_name or entry.get("agent", ""),
            message=continuation,
        )
    
    async def cancel_task(self, task_id: str) -> None:
        """Cancel a running task and propagate cancellation to all descendants."""
        # 1. Fire the cancellation token — this cascades to every registered
        #    child (commander subtasks, ecosystem DAG workers) immediately.
        self._set_task_cancelled(task_id)

        # 2. Cancel background task
        if task_id in self._background_tasks:
            self._background_tasks[task_id].cancel()
            del self._background_tasks[task_id]
        
        # 3. Reset agent state
        for agent_instance in self.agents.values():
            if agent_instance.current_task_id == task_id:
                agent_instance.state = AgentState.CANCELLED
                agent_instance.current_task_id = None
                
                # Try to get partial result before cancelling
                if hasattr(agent_instance.agent, 'cancel'):
                    await agent_instance.agent.cancel()
        
        logger.info(f"Task {task_id} cancelled in orchestrator")

    def _get_cancel_event(self, task_id: str) -> asyncio.Event:
        """Lazily create the per-task cancellation token."""
        if task_id not in self._cancel_events:
            self._cancel_events[task_id] = asyncio.Event()
        return self._cancel_events[task_id]

    def _is_task_cancelled(self, task_id: str) -> bool:
        ev = self._cancel_events.get(task_id)
        return ev is not None and ev.is_set()

    def register_cancel_child(self, parent_id: str, child_id: str) -> None:
        """Register child_id as a descendant of parent_id for cancel propagation."""
        if parent_id == child_id:
            return  # same token — no self-loop needed
        self._parent_children.setdefault(parent_id, set()).add(child_id)
        # If the parent was already cancelled, propagate immediately.
        if self._is_task_cancelled(parent_id):
            self._set_task_cancelled(child_id)

    def _set_task_cancelled(self, task_id: str, _visited: Optional[set[str]] = None) -> None:
        """Set the token for task_id and recursively for all descendants."""
        if _visited is None:
            _visited = set()
        if task_id in _visited:
            return
        _visited.add(task_id)
        self._get_cancel_event(task_id).set()
        for child in self._parent_children.get(task_id, set()):
            self._set_task_cancelled(child, _visited)
    
    def get_status(self) -> dict[str, dict]:
        """Get status of all agents for health dashboard."""
        return {
            name: {
                "state": inst.state.value,
                "current_task_id": inst.current_task_id,
                "available": inst.agent is not None,
            }
            for name, inst in self.agents.items()
        }

    async def get_agent_registry(self) -> dict:
        """Get dynamic agent capability registry (used by CommanderAgent)."""
        return await self.coordination.registry.get_agent_registry()

    async def get_coordination_stats(self) -> dict:
        """Get health metrics from the entire coordination ecosystem."""
        return await self.coordination.get_stats()

    async def execute_swarm(self, swarm_name: str, task_id: str,
                            message: str, context: dict = None):
        """
        Execute a predefined swarm pattern (e.g., 'research', 'build', 'audit').
        Returns SwarmResult with phase-by-phase outputs and synthesis.
        """
        swarm = self.coordination.create_swarm(swarm_name)
        if not swarm:
            return None
        return await swarm.execute(task_id, message, context or {})
