"""
Makima OS — Elite Next-Gen Orchestration Engine v9.0
Location: apps/brain/core/orchestration_engine.py

Modern, high-performance async intent router & task dispatcher.
Replaces monolithic legacy command_router logic while preserving 100% full API & protocol compatibility.

Features:
- Zero circular dependencies (decoupled from main._modules)
- Zero hardcoded agent names — all routing built from live kernel registry at runtime
- OpenAI Agents SDK Native Handoffs & Dynamic is_enabled tool filtering
- Configurable Ollama URL, similarity thresholds, and history window via constructor
- Async intent classification & fast-chat direct LLM response bridge
- Per-conversation follow-up ring buffers & context continuity
- Priority Queue (CRITICAL > INTERACTIVE > BACKGROUND)
- Native integration with LearningCoordinator, PersonalityEngine & EternalMemory
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import pickle
import aiohttp
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Union

try:
    from ..ws_protocol import build_ai_chunk, build_toast_notification
except (ImportError, ValueError):
    from apps.brain.ws_protocol import build_ai_chunk, build_toast_notification

logger = logging.getLogger("orchestration_engine")

# ---------------------------------------------------------------------------
# Default routing config — overridable via OrchestrationEngine constructor
# ---------------------------------------------------------------------------
_DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
_DEFAULT_SCORE_THRESHOLD = 0.85
_DEFAULT_MARGIN_THRESHOLD = 0.06
_DEFAULT_HISTORY_WINDOW = 20      # turns injected into LLM context
_DEFAULT_TOP_K_AGENTS = 12        # max agents passed to LLM tool manifest


class Intent(str, Enum):
    """Recognized intent categories."""
    FAST_CHAT = "fast_chat"
    RESEARCH = "research"
    CODE = "code"
    CREATIVE = "creative"
    MEMORY_QUERY = "memory_query"
    MEMORY_FORGET = "memory_forget"
    SYSTEM_CONTROL = "system_control"
    MESSAGING = "messaging"
    MEDIA = "media"
    BROWSER = "browser"
    VOICE = "voice"
    AUTOMATION = "automation"
    DOCUMENT = "document"
    DATA_ANALYSIS = "data_analysis"
    SECURITY = "security"
    DEVOPS = "devops"
    MULTI_STEP = "multi_step"
    TRIVIAL = "trivial"
    UNKNOWN = "unknown"





@dataclass
class SubIntent:
    """Represents a sub-intent in a compound multi-step command."""
    intent: Intent
    entities: dict[str, Any] = field(default_factory=dict)
    raw_segment: str = ""
    confidence: float = 0.85


@dataclass
class IntentResult:
    """Result of intent classification."""
    intent: Intent
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    sub_intents: list[SubIntent] = field(default_factory=list)
    dependency_dag: dict[str, list[str]] = field(default_factory=dict)


class OrchestrationEngine:
    """
    Elite Next-Gen Orchestration Engine.
    Primary entry point for every user message (text & voice).
    """

    def __init__(
        self,
        ai_handler: Any = None,
        agent_orchestrator: Any = None,
        eternal_memory: Any = None,
        context_budget: Any = None,
        screen_reader: Any = None,
        clipboard_handler: Any = None,
        ws_broadcast: Optional[Callable] = None,
        personality: Any = None,
        entity_extractor: Any = None,
        learning_coordinator: Any = None,
        learning_engine: Any = None,
        reflexion_engine: Any = None,
        skill_library: Any = None,
        health_aggregator: Any = None,
        task_manager: Any = None,
        durable_task_engine: Any = None,
        thought_planner: Any = None,
        # Routing config — overridable, no hardcoded defaults in code
        ollama_url: str = _DEFAULT_OLLAMA_URL,
        router_score_threshold: float = _DEFAULT_SCORE_THRESHOLD,
        router_margin_threshold: float = _DEFAULT_MARGIN_THRESHOLD,
        history_window: int = _DEFAULT_HISTORY_WINDOW,
        top_k_agents: int = _DEFAULT_TOP_K_AGENTS,
    ):
        self._FOLLOWUP_HISTORY_SIZE = 6
        self._history_window = history_window
        self.ai_handler = ai_handler
        self.orchestrator = agent_orchestrator
        self.memory = eternal_memory
        self.context_budget = context_budget
        self.screen_reader = screen_reader
        self.clipboard = clipboard_handler
        self.ws_broadcast = ws_broadcast
        self.entity_extractor = entity_extractor
        self.learning_coordinator = learning_coordinator
        self.reflexion_engine = reflexion_engine or learning_engine
        self.learning_engine = self.reflexion_engine
        self.skill_library = skill_library
        self.health_aggregator = health_aggregator
        self.durable_task_engine = durable_task_engine or getattr(agent_orchestrator, "durable_task_engine", None)
        self.thought_planner = thought_planner

        # resolution and semantic retry ("try karo wapas", "usko next").
        self._conv_actions: dict[str, Any] = {}

        # task_id → clarify question we asked (for merging the user's answer)
        self._pending_clarifies: dict[str, dict[str, str]] = {}


        if task_manager is None:
            from .task_manager import TaskManager
            self.task_manager = TaskManager()
        else:
            self.task_manager = task_manager

        if personality is None:
            try:
                from ..personality import PersonalityEngine
                self.personality = PersonalityEngine()
            except Exception:
                self.personality = None
        else:
            self.personality = personality

        self._followup_ring_buffer: dict[str, list[dict]] = {}
        self._last_task_context: dict[str, dict[str, Any]] = {}
        self._active_tasks: set[str] = set()
        self._cancelled_tasks: set[str] = set()
        self._task_handles: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._cached_triage_agent: Optional[Any] = None
        self._sdk_run_config: Optional[Any] = None

        logger.info(
            "OrchestrationEngine v9.5 active — SDK-first dynamic tool filtering, history_window=%d.",
            history_window,
        )

    def _get_or_create_triage_agent(self) -> Any:
        """Lazily initialize and cache the triage agent graph to avoid recreating 5 specialist agents per turn."""
        if self._cached_triage_agent is None:
            from .sdk_bridge import make_triage_agent
            self._cached_triage_agent = make_triage_agent(
                ai_handler=self.ai_handler,
                orchestrator=self.orchestrator,
                ws_broadcast=self.ws_broadcast,
                personality=self.personality,
            )
        return self._cached_triage_agent

    def _get_sdk_run_config(self) -> Any:
        """Lazily initialize and cache high-performance RunConfig with ToolOutputTrimmer and concurrency controls."""
        if self._sdk_run_config is None:
            from .sdk_bridge import get_default_run_config
            self._sdk_run_config = get_default_run_config()
        return self._sdk_run_config

    def _create_tracked_task(self, coro: Any) -> asyncio.Task:
        """Spawn background task with strong reference tracking to prevent Python GC collection."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task by task_id immediately."""
        if not hasattr(self, "_cancelled_tasks"):
            self._cancelled_tasks = set()
        self._cancelled_tasks.add(task_id)

        if getattr(self, "task_manager", None):
            try:
                await self.task_manager.cancel_task(task_id)
            except Exception as _tm_err:
                logger.debug("TaskManager cancel_task error: %s", _tm_err)

        if getattr(self, "orchestrator", None) and hasattr(self.orchestrator, "cancel_task"):
            try:
                await self.orchestrator.cancel_task(task_id)
            except Exception as _orch_err:
                logger.warning("Orchestrator cancel_task error for %s: %s", task_id, _orch_err)

        handle = getattr(self, "_task_handles", {}).get(task_id)
        if handle and handle is not asyncio.current_task() and not handle.done():
            handle.cancel()

        if self.ws_broadcast:
            try:
                from ..ws_protocol import build_ai_chunk
                await self.ws_broadcast(build_ai_chunk(task_id, "[Task cancelled by user]", is_final=True))
            except Exception:
                pass
        logger.info("Task %s marked for cancellation and cancelled", task_id)
        return True

    def is_task_cancelled(self, task_id: str) -> bool:
        if task_id in getattr(self, "_cancelled_tasks", set()):
            return True
        if getattr(self, "task_manager", None):
            tm = self.task_manager
            if hasattr(tm, "is_cancelled_sync"):
                return tm.is_cancelled_sync(task_id)
            task = getattr(tm, "_tasks", {}).get(task_id)
            if task and getattr(task, "state", None) == "CANCELLED":
                return True
        return False

    async def run_autonomous_goal(
        self,
        task_id: str,
        goal: str,
        conversation_id: str = "default_session",
        context: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Launches a long-running, multi-step goal as a tracked background autonomous loop.
        Immediately returns the task_id while Runner.run_streamed executes in the background,
        streaming progress toasts and updating UI state.
        """
        ctx = dict(context or {})
        ctx["is_autonomous_goal"] = True
        ctx["task_id"] = task_id
        ctx["conversation_id"] = conversation_id
        self._create_tracked_task(
            self._execute_autonomous_goal_loop(task_id, goal, conversation_id, ctx)
        )
        return task_id

    async def _execute_autonomous_goal_loop(
        self,
        task_id: str,
        goal: str,
        conversation_id: str,
        context: dict[str, Any],
    ) -> None:
        """Internal worker executing the autonomous goal loop via OpenAI Agents SDK."""
        logger.info("[OrchestrationEngine] Starting autonomous goal task %s: %s", task_id, goal[:100])
        try:
            if self.ws_broadcast:
                from ..ws_protocol import build_toast_notification
                await self.ws_broadcast(build_toast_notification(
                    task_id,
                    f"Autonomous Goal Started: {goal[:60]}...",
                    level="info",
                    duration_ms=4000,
                ))
            await self.handle_message(task_id, goal, context=context)
        except Exception as exc:
            logger.error("[OrchestrationEngine] Autonomous goal %s failed: %s", task_id, exc, exc_info=True)
            if self.ws_broadcast:
                from ..ws_protocol import build_ai_chunk
                await self.ws_broadcast(build_ai_chunk(
                    task_id,
                    f"Autonomous goal encountered an issue: {exc}",
                    is_final=True,
                ))


    async def close(self) -> None:
        """Gracefully shut down router session and background tasks."""
        if hasattr(self, "router") and self.router and hasattr(self.router, "close"):
            try:
                await self.router.close()
            except Exception as e:
                logger.debug("Error closing OrchestrationEngine router: %s", e)
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        self._background_tasks.clear()

    async def handle_message(self, task_id: str, message: str, context: Optional[dict[str, Any]] = None) -> None:
        """
        Single entry point for incoming user messages with strict raw prompt preservation.
        Modern LLM-First Dynamic Agent Routing architecture with dynamic tool manifest dispatch.
        """
        context = dict(context or {})
        raw_message = message or ""
        clean_msg = raw_message.strip()
        
        passed_conv = context.get("conversation_id")
        if passed_conv and passed_conv != task_id:
            conv_id = passed_conv
            self._active_conversation_id = passed_conv
        elif getattr(self, "_active_conversation_id", None):
            conv_id = self._active_conversation_id
            context["conversation_id"] = conv_id
        else:
            conv_id = "default_session"
            self._active_conversation_id = conv_id
            context["conversation_id"] = conv_id

        if not clean_msg:
            return

        # ── Fast-Path: SkillLibrary (Voyager paper, Wang et al. 2023) ─────────
        if self.skill_library:
            try:
                matched_skill = await self.skill_library.retrieve_matching_skill(
                    clean_msg, threshold=0.88
                )
                if matched_skill:
                    logger.info("SkillLibrary match: %s", matched_skill.name)
                    try:
                        result = await self.skill_library.execute_skill(
                            matched_skill, {}, context
                        )
                        from ..agents.base_agent import BaseAgent
                        is_failed = BaseAgent._tool_failed(result) if hasattr(BaseAgent, "_tool_failed") else False
                        await self.skill_library.record_skill_execution(
                            matched_skill.skill_id, success=(not is_failed)
                        )
                        if not is_failed:
                            if self.ws_broadcast:
                                from ..ws_protocol import build_ai_chunk
                                await self.ws_broadcast(build_ai_chunk(
                                    task_id, str(result or f"Task completed via learned skill '{matched_skill.name}'."), is_final=True
                                ))
                            return result  # skip full agent planning
                    except Exception as exec_err:
                        await self.skill_library.record_skill_execution(
                            matched_skill.skill_id, success=False
                        )
                        logger.debug("SkillLibrary execution error, falling back to agent planning: %s", exec_err)
            except Exception as skill_err:
                logger.debug("SkillLibrary match execution error: %s", skill_err)

        logger.info("[OrchestrationEngine] Inbound task %s (conv=%s): %r", task_id, conv_id, clean_msg[:80])

        # Store raw unaltered user message in context for downstream fidelity
        context["raw_user_message"] = raw_message
        context["original_message"] = raw_message

        self._active_tasks.add(task_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            self._task_handles[task_id] = current_task
            if self.task_manager:
                self.task_manager.bind_coroutine_handle(task_id, current_task)
        start_time = time.time()

        if self.task_manager:
            try:
                await self.task_manager.create_task(
                    request=raw_message,
                    goal=raw_message,
                    task_id=task_id,
                )
                await self.task_manager.start_running(task_id)
            except Exception as _tm_err:
                logger.debug("TaskManager registration error: %s", _tm_err)

        try:
            if self.reflexion_engine:
                try:
                    lessons = await self.reflexion_engine.get_relevant_reflections(
                        clean_msg, top_k=3
                    )
                    if lessons:
                        context["past_failure_lessons"] = lessons
                        logger.info("ReflexionEngine injected %d lessons", len(lessons))
                except Exception as ref_err:
                    logger.debug("ReflexionEngine retrieval error: %s", ref_err)

            if self.memory and hasattr(self.memory, "save_turn"):
                mem_coro = self.memory.save_turn(raw_message, role="user", conversation_id=conv_id)
                mem_task = asyncio.create_task(mem_coro)
                self._background_tasks.add(mem_task)
                mem_task.add_done_callback(self._background_tasks.discard)

            # 2. Multi-turn Short-term Memory: Load from Memory if not present
            if ("history" not in context or not context["history"]) and self.memory and hasattr(self.memory, "get_history"):
                try:
                    turns = await self.memory.get_history(n=50, conversation_id=conv_id)
                    if turns:
                        context["history"] = [
                            {"role": t.get("role", "user"), "content": t.get("message") or t.get("content", "")}
                            for t in turns
                            if (t.get("message") or t.get("content"))
                        ]
                except Exception as hist_err:
                    logger.debug("Failed to retrieve conversation history: %s", hist_err)

            # 3. Eternal Memory Retrieval & Context Injection (Gated & Quality-Filtered)
            _is_pure_action = any(raw_message.lower().strip().startswith(kw) for kw in (
                "open ", "launch ", "kholo ", "close ", "band karo ", "play ", "baja ", "volume ",
                "screenshot ", "photo lo ", "format ", "delete ", "clean ", "calc ", "ping "
            ))
            if not _is_pure_action and self.memory and hasattr(self.memory, "search"):
                try:
                    try:
                        mem_hits = await self.memory.search(raw_message, top_k=3)
                    except TypeError:
                        mem_hits = await self.memory.search(raw_message, k=3)

                    if asyncio.iscoroutine(mem_hits):
                        mem_hits = await mem_hits

                    if mem_hits and isinstance(mem_hits, (list, tuple)):
                        lines = []
                        for h in mem_hits:
                            if isinstance(h, dict):
                                # Reject low-confidence hits (< 0.35)
                                score = float(h.get("relevance_score") or h.get("composite_score") or h.get("cosine_sim") or 0.0)
                                if score < 0.35:
                                    continue
                                text = h.get("message") or h.get("content") or h.get("text") or ""
                            else:
                                text = str(h)
                            text_clean = text.strip()
                            # Filter plumbing lines, query echo, and generic assistant greetings
                            if (
                                text_clean
                                and text_clean.lower() != raw_message.lower().strip()
                                and not text_clean.startswith("⚙ tools")
                                and not text_clean.startswith("[Snapshot:")
                                and not text_clean.startswith("LLMResponse(")
                                and "hello! i'm makima" not in text_clean.lower()
                            ):
                                lines.append(f"- {text_clean}")
                        if lines:
                            formatted_mem = "Relevant context from memory:\n" + "\n".join(lines)
                            context["memory_context"] = formatted_mem
                            context["memory_results"] = [line[2:] for line in lines]
                except Exception as mem_err:
                    logger.debug("[OrchestrationEngine] Eternal memory search failed: %s", mem_err)

            # Ensure task_context is attached
            from .task_context import AgentTaskContext
            if "task_context" not in context:
                context["task_context"] = AgentTaskContext(
                    task_id=task_id,
                    user_request=raw_message,
                    relevant_state=dict(context),
                )

            # Attach recent conversation actions for contextual follow-up & retry routing
            if "last_actions" not in context:
                context["last_actions"] = self._format_last_actions(conv_id)
            if "last_action" not in context:
                context["last_action"] = self._last_action(conv_id)

            # ── SDK Input Guardrail: Block Dangerous OS Commands ──
            from .sdk_bridge import dangerous_command_guard
            try:
                fn = getattr(dangerous_command_guard, "guardrail_function", dangerous_command_guard)
                guard_out = await fn(None, None, raw_message)
                if guard_out and getattr(guard_out, "tripwire_triggered", False):
                    clean_msg = "Yeh command safe nahi hai — block kar diya."
                    logger.warning("[OrchestrationEngine] InputGuardrail blocked dangerous command: %s", raw_message)
                    await self._emit_final_response(
                        task_id,
                        clean_msg,
                        conv_id,
                        user_message=raw_message,
                        feedback_mode="toast",
                    )
                    return
            except Exception as ge:
                logger.debug("[OrchestrationEngine] Guardrail evaluation error: %s", ge)

            # ── Step 4: OpenAI Agents SDK Native Triage & Handoff Routing ──
            from .sdk_bridge import (
                make_triage_agent,
                get_sdk_session,
                InputGuardrailTripwireTriggered,
            )
            from agents import Runner

            session_id = conv_id or context.get("user_session_id") or "makima_main"

            # ── SAGE-Agent Multi-Turn Clarification Resumption ──
            pending_clarify = self._pending_clarifies.pop(session_id, None)
            if pending_clarify:
                now = time.time()
                if now - pending_clarify.get("timestamp", 0) < 600:  # 10 min TTL
                    orig_task = pending_clarify.get("task", "")
                    cand_tool = pending_clarify.get("candidate_tool", "")
                    logger.info("[OrchestrationEngine] SAGE: Resuming pending clarification for session %s (orig='%s', tool='%s')", session_id, orig_task[:40], cand_tool)
                    raw_message = f"{orig_task}\n[User Clarification Detail: {raw_message}]"
                    context["resumed_clarification"] = True
                    context["candidate_tool"] = cand_tool
                    context["partial_parameters"] = pending_clarify.get("partial_params", {})

            context["task_id"] = task_id
            context["conversation_id"] = conv_id
            context["ws_broadcast"] = self.ws_broadcast
            context["raw_message"] = raw_message
            context["message"] = raw_message

            if "foreground_window" not in context:
                try:
                    from .world_state import get_world_state
                    fw = get_world_state().get_foreground_window()
                    if fw:
                        context["foreground_window"] = fw
                except Exception:
                    pass

            triage_success = False
            session = get_sdk_session(session_id)
            try:
                # ── ThoughtPlanner Integration (Module 5: Tree of Thoughts / SAGE-Agent) ──
                if self.thought_planner:
                    try:
                        complexity = await self.thought_planner.classify_complexity(raw_message, context=context)
                        if complexity == "complex":
                            logger.info("[OrchestrationEngine] Complex/Ambiguous task detected — activating ThoughtPlanner search")
                            plan = await self.thought_planner.search(raw_message, context=context, max_depth=3)

                            # ── SAGE Clarification Gate: Halt blind execution if critical params missing ──
                            if plan.is_clarification_required:
                                q_text = plan.clarifying_question or "Please clarify your specific requirements before proceeding."
                                logger.info("[OrchestrationEngine] SAGE Clarification required for session %s: %s", session_id, q_text)
                                self._pending_clarifies[session_id] = {
                                    "task": raw_message,
                                    "task_id": task_id,
                                    "candidate_tool": plan.candidate_tool,
                                    "missing_params": plan.missing_critical_params,
                                    "partial_params": plan.partial_parameters,
                                    "question": q_text,
                                    "timestamp": time.time(),
                                }
                                if self.ws_broadcast:
                                    from ..ws_protocol import build_toast_notification
                                    await self.ws_broadcast(build_toast_notification(
                                        task_id,
                                        "Clarification needed to proceed",
                                        level="info",
                                        duration_ms=3000,
                                    ))
                                if hasattr(self.task_manager, "update_task_status"):
                                    self.task_manager.update_task_status(task_id, "clarification_needed")
                                await self._emit_final_response(
                                    task_id,
                                    q_text,
                                    conv_id,
                                    user_message=raw_message,
                                    feedback_mode="full",
                                )
                                return

                            context["execution_plan"] = plan.to_prompt_string()
                            if self.ws_broadcast:
                                from ..ws_protocol import build_ai_chunk
                                plan_msg = (
                                    f"\n> 🧠 *Deliberative Plan Generated (Confidence: {int(plan.confidence * 100)}%):*\n"
                                    + "\n".join(f"> - {n.thought}" for n in plan.selected_path)
                                    + "\n\n"
                                )
                                await self.ws_broadcast(build_ai_chunk(task_id, plan_msg, is_final=False))
                    except Exception as pe:
                        logger.debug("[OrchestrationEngine] ThoughtPlanner search error: %s", pe)

                triage_agent = self._get_or_create_triage_agent()
                sdk_run_cfg = self._get_sdk_run_config()
                from .sdk_bridge import MakimaRunHooks
                hooks = MakimaRunHooks(ws_broadcast=self.ws_broadcast, task_id=task_id)

                agent_input = raw_message
                env_blocks = []
                if context.get("foreground_window"):
                    env_blocks.append(f"[Active Window: {context['foreground_window']}]")
                if context.get("clipboard"):
                    env_blocks.append(f"[Active Clipboard / Source Data:\n{context['clipboard']}\n]")
                if env_blocks:
                    agent_input = "\n".join(env_blocks) + f"\n\nUser Instruction: {raw_message}"

                run_stream = Runner.run_streamed(
                    triage_agent,
                    agent_input,
                    session=session,
                    context=context,
                    max_turns=25,
                    hooks=hooks,
                    run_config=sdk_run_cfg,
                )
                streamed_chunks: list[str] = []
                async for stream_ev in run_stream.stream_events():
                    if hasattr(stream_ev, "data"):
                        d = stream_ev.data
                        if getattr(d, "type", "") == "response.output_text.delta":
                            tok = getattr(d, "delta", "")
                            if tok and self.ws_broadcast:
                                streamed_chunks.append(tok)
                                from ..ws_protocol import build_ai_chunk
                                await self.ws_broadcast(build_ai_chunk(task_id, tok, is_final=False))
                        elif getattr(d, "type", "") == "response.output_text.done":
                            if streamed_chunks and not streamed_chunks[-1].endswith("\n"):
                                streamed_chunks.append("\n\n")

                    # ── Turn Limit Checkpoint Check at Turn 20 (Module 4) ──
                    if getattr(hooks, "turn_count", 0) >= 20 and self.durable_task_engine:
                        await self.durable_task_engine.checkpoint_task(
                            task_id=task_id,
                            prompt=raw_message,
                            completed_steps=getattr(hooks, "completed_steps", []),
                            remaining_steps=[],
                            context=context,
                            turn_count=hooks.turn_count,
                            status="paused",
                        )
                        checkpoint_msg = "Task checkpointed at turn 20/25. Will resume next session."
                        logger.info("[OrchestrationEngine] %s", checkpoint_msg)
                        if self.ws_broadcast:
                            from ..ws_protocol import build_ai_chunk
                            await self.ws_broadcast(build_ai_chunk(task_id, f"\n\n> 💾 *{checkpoint_msg}*\n", is_final=True))
                        break

                final_out = str(run_stream.final_output or "".join(streamed_chunks)).strip()
                if final_out:
                    if getattr(hooks, "completed_steps", None):
                        context["tool_calls_made"] = [
                            {"tool": s.get("tool"), "agent": s.get("agent"), "result": s.get("result_summary")}
                            for s in hooks.completed_steps
                        ]
                    if self.durable_task_engine:
                        await self.durable_task_engine.mark_completed(task_id, final_result=final_out)
                    await self._emit_final_response(
                        task_id,
                        final_out,
                        conv_id,
                        user_message=raw_message,
                        feedback_mode="full",
                        skip_session_write=True,
                        already_streamed=bool(streamed_chunks),
                        context=context,
                    )
                    triage_success = True
                    return
            except InputGuardrailTripwireTriggered:
                clean_msg = "Yeh command safe nahi hai — block kar diya."
                logger.warning("[OrchestrationEngine] Triage InputGuardrail blocked dangerous command: %s", raw_message)
                await self._emit_final_response(
                    task_id,
                    clean_msg,
                    conv_id,
                    user_message=raw_message,
                    feedback_mode="toast",
                    skip_session_write=True,
                )
                return
            except Exception as triage_exc:
                logger.warning("[OrchestrationEngine] OpenAI Agents SDK Triage failed (%s), proceeding to fallback LLM router", triage_exc)
            finally:
                try:
                    if hasattr(session, "close"):
                        session.close()
                except Exception:
                    pass

            # Fallback path if SDK handoff returned empty or errored
            if not triage_success:
                top_tools = self._get_top_tools_filtered(raw_message, max_tools=25)
                routed = await self._execute_llm_first_turn(task_id, raw_message, conv_id, context, top_tools)
                if not routed:
                    # Fallback to direct conversational response
                    await self._handle_fast_chat(task_id, raw_message, conv_id, context)

        except asyncio.CancelledError:
            logger.info("[OrchestrationEngine] Task %s execution cancelled cleanly", task_id)
            if self.ws_broadcast:
                try:
                    from ..ws_protocol import build_ai_chunk
                    await self.ws_broadcast(build_ai_chunk(task_id, "[Task cancelled by user]", is_final=True))
                except Exception:
                    pass
            return
        except Exception as e:
            logger.error("OrchestrationEngine error processing task %s: %s", task_id, e, exc_info=True)
            await self._emit_final_response(
                task_id,
                "I couldn't complete that request. Please try again.",
                conv_id,
                user_message=raw_message,
            )
        finally:
            if self.task_manager and not self.is_task_cancelled(task_id):
                try:
                    await self.task_manager.complete_task(task_id)
                except Exception:
                    pass
            self._active_tasks.discard(task_id)
            self._cancelled_tasks.discard(task_id)
            self._task_handles.pop(task_id, None)

    async def _emit_final_response(
        self,
        task_id: str,
        text: str,
        conv_id: str,
        user_message: str = "",
        feedback_mode: str = "full",
        agent_name: str = "",
        format: str = "markdown",
        skip_session_write: bool = False,
        already_streamed: bool = False,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        """Stream conversational, toast, or silent response chunks over WebSocket."""
        clean_text = str(text or "").strip()
        # Clean reasoning/thinking tags if leaked into response text
        clean_text = re.sub(r"<(?:thinking|think|thought|reasoning)>.*?</(?:thinking|think|thought|reasoning)>", "", clean_text, flags=re.DOTALL).strip()
        
        # Clean raw JSON tool envelopes (e.g. {"tool": "complete", "reply": "..."} or {"reply": "..."})
        if (clean_text.startswith("{") and clean_text.endswith("}")) or (clean_text.startswith("```json") and clean_text.endswith("```")):
            try:
                stripped = re.sub(r"^```json\s*|\s*```$", "", clean_text).strip()
                parsed = json.loads(stripped)
                if isinstance(parsed, dict) and any(k in parsed for k in ("reply", "message", "response", "content")):
                    extracted = parsed.get("reply") or parsed.get("message") or parsed.get("response") or parsed.get("content")
                    if extracted and isinstance(extracted, str) and extracted.strip():
                        clean_text = extracted.strip()
            except Exception:
                pass

        if not clean_text:
            return

        from ..ws_protocol import build_ai_chunk, build_toast_notification

        mode = (feedback_mode or "full").lower().strip()

        # Smart format detection
        if not format or format == "markdown":
            if "research" in agent_name.lower() or (len(clean_text) > 1500 and ("# " in clean_text or "## " in clean_text)):
                format = "report"

        media = context.get("media") if isinstance(context, dict) else None
        sources = context.get("sources") if isinstance(context, dict) else None

        if mode == "silent":
            logger.info("[OrchestrationEngine] Silent feedback mode for task %s — suppressing UI emission", task_id)
        elif mode == "toast":
            if self.ws_broadcast:
                is_err = "error" in clean_text.lower() or "failed" in clean_text.lower() or "⚠️" in clean_text
                is_media = "🎵" in clean_text or "now playing" in clean_text.lower() or clean_text.lower().startswith("playing")
                if is_media and not is_err:
                    # Strictly strip any fabricated volume, percentages, or extraneous text from media playback toast
                    clean_text = re.sub(r"(?:,\s*|\.\s*|\n\s*)?(?:volume|vol|awaaz|sound)\s*(?:is\s*)?(?:set\s*to\s*)?\d+%?.*$", "", clean_text, flags=re.IGNORECASE).strip()
                    clean_text = re.sub(r"^(?:playing\s+)", "", clean_text, flags=re.IGNORECASE).strip()
                    if not clean_text.startswith("🎵"):
                        clean_text = f"🎵 {clean_text}"
                    if "now playing" not in clean_text.lower():
                        clean_text = f"{clean_text} — Now Playing"
                lvl = "warning" if is_err else ("success" if is_media else "info")
                dur = 4000 if (is_media or is_err) else 3000
                # 1. Toast notification
                await self.ws_broadcast(build_toast_notification(task_id, clean_text, level=lvl, duration_ms=dur))
                # 2. Chat bubble (single line)
                await self.ws_broadcast(build_ai_chunk(task_id, clean_text, is_final=True, agent=agent_name, format=format, media=media, sources=sources))
        else:  # "full", "voice_only", etc.
            if self.ws_broadcast:
                if not already_streamed:
                    chunk_size = 512
                    for i in range(0, len(clean_text), chunk_size):
                        chunk = clean_text[i:i + chunk_size]
                        await self.ws_broadcast(build_ai_chunk(task_id, chunk, is_final=False, agent=agent_name, format=format))
                        await asyncio.sleep(0.002)
                await self.ws_broadcast(build_ai_chunk(task_id, "", is_final=True, agent=agent_name, format=format, media=media, sources=sources))

        if self.memory and hasattr(self.memory, "save_turn") and clean_text:
            mem_coro = self.memory.save_turn(clean_text, role="assistant", conversation_id=conv_id)
            mem_task = asyncio.create_task(mem_coro)
            self._background_tasks.add(mem_task)
            mem_task.add_done_callback(self._background_tasks.discard)

        if user_message:
            self.record_turn(user_message, clean_text, skip_session=skip_session_write)

        # After successful multi-step task completion (3+ tool calls), synthesize into SkillLibrary
        tool_calls_made = []
        if isinstance(context, dict):
            tool_calls_made = context.get("tool_calls_made") or context.get("executed_tools") or []
        if getattr(self, "skill_library", None) and len(tool_calls_made) >= 3:
            syn_task = asyncio.create_task(
                self.skill_library.synthesize_from_trajectory(
                    task=user_message or clean_text,
                    trajectory=tool_calls_made,
                    final_output=clean_text,
                )
            )
            self._background_tasks.add(syn_task)
            syn_task.add_done_callback(self._background_tasks.discard)

    def _should_query_memory(self, message: str, context: dict[str, Any]) -> bool:
        """
        Determines if long-term memory vector database should be searched.
        Accesses memory database ONLY when genuinely needed (zero-redundancy).
        """
        if context.get("force_memory"):
            return True

        clean = (message or "").strip().lower()
        if not clean or len(clean) < 4:
            return False

        # Fast rejection for generic greetings, single-word acknowledgments, and basic chit-chat
        _CHIT_CHAT_EXCLUSIONS = (
            "hi", "hello", "hey", "sup", "kya haal", "kya haal hai", "good morning",
            "good night", "good evening", "ok", "okay", "yes", "no", "haan", "nahi",
            "thanks", "thank you", "shukriya", "bye", "goodbye", "alvida",
        )
        if clean in _CHIT_CHAT_EXCLUSIONS:
            return False

        # Fast rejection for obvious system action / tool execution commands
        _ACTION_PREFIXES = (
            "open ", "launch ", "run ", "close ", "kill ", "play ", "bajao ",
            "chalao ", "set volume", "screenshot ", "search google", "browse ",
        )
        if any(clean.startswith(p) for p in _ACTION_PREFIXES):
            return False

        # High-signal memory trigger indicators (personal recall, temporal references, past facts)
        _MEMORY_TRIGGER_PATTERNS = (
            "remember", "recall", "remind", "yaad hai", "yaad h", "yaad dilao", "yaad dila",
            "pehle", "earlier", "told you", "bataya tha", "my name", "mera naam", "my project",
            "my code", "my pet", "my cat", "my dog", "my secret", "my address", "where do i live",
            "my favorite", "mera favorite", "last time", "pichli baar", "who am i",
            "what was my", "what did i", "what is my", "maine kya", "kya tha mera",
            "favorite language", "my password", "my pin", "credential", "preference",
            "batao mera", "bata mera",
        )
        if any(t in clean for t in _MEMORY_TRIGGER_PATTERNS):
            return True

        # If user asks a question containing first-person possessive pronouns ("my", "mera", "meri", "mine")
        words = set(re.findall(r'[a-z0-9]+', clean))
        possessives = {"my", "mera", "meri", "mere", "mine", "ours", "apna", "apni"}
        question_words = {"what", "who", "where", "which", "when", "kya", "kaun", "kaha", "kab", "kaise"}
        if (words & possessives) and (words & question_words):
            return True

        return False

    def _build_fast_chat_system_prompt(self, context: dict[str, Any]) -> str:
        """
        Construct a lean, lightweight system prompt for pure conversational and informational turns.
        Preserves Makima's rich identity, warmth, Hinglish tone, emotion state, and user memory,
        while completely omitting the heavy 15-agent tools manifest and OS automation directives.
        """
        sys_prompt = "You are Makima, a smart, precise, and helpful personal AI assistant. Reply conversationally and directly."
        if self.personality:
            if hasattr(self.personality, "build_system_prompt"):
                sys_prompt = self.personality.build_system_prompt()
            elif hasattr(self.personality, "system_prompt"):
                sys_prompt = self.personality.system_prompt

        if context.get("memory_results"):
            mem_text = "\n".join(f"- {m}" for m in context["memory_results"] if m)
            if mem_text:
                sys_prompt += f"\n\n[RELEVANT RECALLED MEMORY]\n{mem_text}\nUse this relevant user context to accurately answer personal questions."

        if context.get("behavior_rules"):
            rules_text = "\n".join(f"- {r}" for r in context["behavior_rules"] if r)
            if rules_text:
                sys_prompt += f"\n\n[USER PREFERENCES & RULES]\n{rules_text}"

        if context.get("past_failure_lessons"):
            lessons = context["past_failure_lessons"]
            if isinstance(lessons, list):
                sys_prompt += "\n\n[PAST FAILURE LESSONS — APPLY NOW]\n" + "\n".join(str(l) for l in lessons)
            elif isinstance(lessons, str):
                sys_prompt += f"\n\n[PAST FAILURE LESSONS — APPLY NOW]\n{lessons}"

        return sys_prompt

    def _build_router_system_prompt(self, context: dict[str, Any]) -> str:
        """
        Construct the system prompt for the primary LLM routing & conversational turn.
        Agent directives are generated DYNAMICALLY from the live orchestrator.agents
        registry — no agent names are hardcoded here.
        """
        sys_prompt = "You are Makima, a smart, precise, and helpful personal AI assistant. Reply conversationally and directly. Use markdown formatting where it aids clarity."
        if self.personality:
            if hasattr(self.personality, "build_system_prompt"):
                sys_prompt = self.personality.build_system_prompt()
            elif hasattr(self.personality, "system_prompt"):
                sys_prompt = self.personality.system_prompt

        sys_prompt += (
            "\n\n[CORE ROUTING DIRECTIVES]\n"
            "1. If the user asks to perform ANY action (manage files, search apps, open/close apps, system stats, ping, clipboard, desktop organization), invoke the matching agent tool directly.\n"
            "2. If the user asks about past conversations, preferences, or personal facts, invoke the memory agent tool before answering.\n"
            "3. PERSONAL ACCOUNTS & WEB SERVICES (Gmail, WhatsApp, feeds): NEVER refuse with canned AI disclaimers. Invoke browser tools to navigate or system tools to inspect/launch on desktop.\n"
            "4. LIVE NEWS & REAL-TIME EVENTS: The current year is 2026. Your training cutoff is historical (2024). NEVER answer news, headlines, current events, or live scores from internal memory. You MUST invoke research tools to search live web data.\n"
            "5. HONESTY: NEVER claim an action was completed unless a matching tool execution in this turn confirms it.\n"
        )

        # ── Dynamic agent routing hints from live registry ──────────────────
        # Generated at runtime — no agent names hardcoded. New agents registered
        # in the kernel automatically appear here with no code changes needed.
        orch_agents = getattr(self.orchestrator, "agents", None) if self.orchestrator else None
        if isinstance(orch_agents, dict):
            hints: list[str] = []
            for agent_name, inst in orch_agents.items():
                agent_obj = getattr(inst, "agent", inst)
                if agent_obj is None:
                    continue
                desc = str(getattr(agent_obj, "DESCRIPTION", "") or "").strip()
                caps = list(getattr(agent_obj, "CAPABILITIES", []) or [])
                call_name = f"call_{agent_name}"
                cap_str = ", ".join(caps[:12]) if caps else desc[:120]
                if cap_str:
                    hints.append(f"- {call_name}: {cap_str}")
            if hints:
                sys_prompt += (
                    "\n[AVAILABLE AGENT TOOLS — call the matching tool for any action request]\n"
                    + "\n".join(hints)
                    + "\n"
                )

        if context.get("memory_context"):
            sys_prompt += f"\n\n{context['memory_context']}\nUse this relevant user context to accurately answer questions."
        elif context.get("memory_results"):
            mem_text = "\n".join(f"- {m}" for m in context["memory_results"] if m)
            if mem_text:
                sys_prompt += f"\n\nRelevant context from memory:\n{mem_text}\nUse this relevant user context to accurately answer questions."

        if context.get("behavior_rules"):
            rules_text = "\n".join(f"- {r}" for r in context["behavior_rules"] if r)
            if rules_text:
                sys_prompt += f"\n\n[USER PREFERENCES & RULES]\n{rules_text}"

        if context.get("past_failure_lessons"):
            lessons = context["past_failure_lessons"]
            if isinstance(lessons, list):
                sys_prompt += "\n\n[PAST FAILURE LESSONS — APPLY NOW]\n" + "\n".join(str(l) for l in lessons)
            elif isinstance(lessons, str):
                sys_prompt += f"\n\n[PAST FAILURE LESSONS — APPLY NOW]\n{lessons}"

        return sys_prompt

    _build_system_prompt = _build_router_system_prompt

    def _build_messages_list(self, raw_message: str, sys_prompt: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Construct cleaned message list with context history for the LLM turn."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]

        if context.get("history"):
            for turn in context["history"][-self._history_window :]:
                if isinstance(turn, dict) and "role" in turn and "content" in turn:
                    role = turn["role"]
                    c_text = str(turn["content"] or "").strip()
                    cleaned_lines = [
                        line for line in c_text.splitlines()
                        if not self._is_status_artifact(line)
                    ]
                    cleaned_content = "\n".join(cleaned_lines).strip()
                    if cleaned_content:
                        messages.append({"role": role, "content": cleaned_content})

        user_content: Union[str, list[dict[str, Any]]] = raw_message
        attachment_parts = [
            item.get("content")
            for item in context.get("attachments", [])
            if isinstance(item, dict) and isinstance(item.get("content"), dict)
        ]
        if attachment_parts:
            user_content = [{"type": "text", "text": raw_message}, *attachment_parts]

        messages.append({"role": "user", "content": user_content})
        return messages

    def _get_top_tools_filtered(self, query: str, max_tools: int = 25) -> list[dict[str, Any]]:
        """
        Dynamically filters direct tools and agent tools using category intent rules (OpenAI Agents SDK pattern):
          - media tools -> is_enabled only when query contains 'music/gaana/volume/song/play'
          - browser tools -> is_enabled only when query contains 'browser/open/search/website/url'
          - system tools -> is_enabled when query contains 'open/app/window/screenshot'
          - code tools -> is_enabled when query contains 'code/python/script'
        If query is purely conversational, returns empty list so LLM responds conversationally without tool hallucination.
        """
        all_filtered: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        # 1. Direct tools from ToolRegistry via dynamic filter
        if self.orchestrator and hasattr(self.orchestrator, "tool_registry") and self.orchestrator.tool_registry:
            reg = self.orchestrator.tool_registry
            if hasattr(reg, "get_tools_dynamic"):
                try:
                    res = reg.get_tools_dynamic(query, max_tools=max_tools)
                    matching_metas = []
                    if asyncio.iscoroutine(res):
                        res.close()
                    elif isinstance(res, (list, tuple)):
                        matching_metas = res

                    for m in matching_metas:
                        spec = m.to_openai_function() if hasattr(m, "to_openai_function") else m
                        if isinstance(spec, dict):
                            fn_name = spec.get("function", {}).get("name")
                            if fn_name and fn_name not in seen_names:
                                seen_names.add(fn_name)
                                all_filtered.append(spec)
                except Exception as e:
                    logger.debug("Failed dynamic tool filter: %s", e)

        # 2. Agent tools from Orchestrator via dynamic filter
        if self.orchestrator and hasattr(self.orchestrator, "get_agent_tools_manifest"):
            try:
                manifest = self.orchestrator.get_agent_tools_manifest()
                if asyncio.iscoroutine(manifest):
                    manifest.close()
                    manifest = []
                elif isinstance(manifest, (list, tuple)):
                    from ..tool_registry import is_tool_enabled_for_query
                    for t in manifest:
                        if isinstance(t, dict):
                            fn = t.get("function", {})
                            name = str(fn.get("name") or "").strip()
                            clean_name = name[5:] if name.startswith("call_") else name
                            ag_cat = (
                                "media" if "media" in clean_name else
                                "browser" if "browser" in clean_name else
                                "code" if "code" in clean_name or "devops" in clean_name else
                                "system" if "system" in clean_name or "automation" in clean_name else
                                "general"
                            )
                            if is_tool_enabled_for_query(clean_name, query, category=ag_cat):
                                if name and name not in seen_names:
                                    seen_names.add(name)
                                    all_filtered.append(t)
            except Exception as e:
                logger.debug("Failed to fetch agent tools: %s", e)

        return all_filtered[:max_tools]


    async def _execute_llm_first_turn(
        self,
        task_id: str,
        raw_message: str,
        conv_id: str,
        context: dict[str, Any],
        agent_tools: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        """
        Phase-2 SDK-native LLM First Turn:
        Builds an ephemeral MakimaRouter SDK Agent with the top-N filtered tools
        for this query and runs it via SDK Runner.run().

        Benefits over legacy path:
          - SDK Runner manages the tool-call → result → re-prompt loop natively.
          - No manual tool_call extraction, _execute_single_tool dispatch, or
            Branch A/B switch needed.
          - Guardrails (fabrication_guard) apply at the SDK level.
          - Falls back to conversational fallback if Runner errors or returns empty.
        """
        if not self.ai_handler:
            return False

        # Build filtered tool specs for this specific query
        tools_to_use = (
            agent_tools if (agent_tools is not None and len(agent_tools) > 0)
            else self._get_top_tools_filtered(raw_message, max_tools=25)
        )

        # If no tools matched the query, fall back to conversational fallback generation
        if not tools_to_use:
            return await self._execute_llm_first_turn_fallback(
                task_id, raw_message, conv_id, context
            )

        from .sdk_bridge import make_router_agent, get_sdk_session, InputGuardrailTripwireTriggered
        from agents import Runner

        context["raw_message"] = raw_message
        context["message"] = raw_message
        context["task_id"] = task_id
        context["conversation_id"] = conv_id

        session = get_sdk_session(conv_id)
        try:
            router_agent = make_router_agent(
                ai_handler=self.ai_handler,
                tool_specs=tools_to_use,
                orchestrator=self.orchestrator,
                query=raw_message,
            )

            from .sdk_bridge import MakimaRunHooks
            hooks = MakimaRunHooks(ws_broadcast=self.ws_broadcast, task_id=task_id)

            run_stream = Runner.run_streamed(
                router_agent,
                raw_message,
                session=session,
                context=context,
                max_turns=10,
                hooks=hooks,
                run_config=self._get_sdk_run_config(),
            )
            accumulated_tokens: list[str] = []
            async for ev in run_stream.stream_events():
                if hasattr(ev, "data"):
                    d = ev.data
                    if getattr(d, "type", "") == "response.output_text.delta":
                        delta_t = getattr(d, "delta", "")
                        if delta_t:
                            accumulated_tokens.append(delta_t)
                            if self.ws_broadcast:
                                try:
                                    await self.ws_broadcast(build_ai_chunk(task_id, delta_t, is_final=False))
                                except Exception:
                                    pass
                    elif getattr(d, "type", "") == "response.output_text.done":
                        if accumulated_tokens and not accumulated_tokens[-1].endswith("\n"):
                            accumulated_tokens.append("\n\n")

            final_out = "".join(accumulated_tokens).strip()
            if not final_out:
                final_out = str(getattr(run_stream, "final_output", "") or "").strip()

            if final_out:
                # Scrub any status artifact lines before emitting
                clean_lines = [self._scrub_status_line(l) for l in final_out.splitlines()]
                final_out = "\n".join(clean_lines).strip()

            if final_out:
                if getattr(hooks, "completed_steps", None):
                    context["tool_calls_made"] = [
                        {"tool": s.get("tool"), "agent": s.get("agent"), "result": s.get("result_summary")}
                        for s in hooks.completed_steps
                    ]
                await self._emit_final_response(
                    task_id, final_out, conv_id,
                    user_message=raw_message,
                    feedback_mode="full",
                    already_streamed=True,
                    context=context,
                )
                self.record_turn(raw_message, final_out, context=context)
                if self.memory and hasattr(self.memory, "save_turn"):
                    mem_coro = self.memory.save_turn(final_out, role="assistant", conversation_id=conv_id)
                    mem_task = asyncio.create_task(mem_coro)
                    self._background_tasks.add(mem_task)
                    mem_task.add_done_callback(self._background_tasks.discard)
                return True

            # SDK Runner returned empty — fall back to streamed fallback path
            logger.debug("[llm_first_turn] SDK Router returned empty output; trying fallback path")
            return await self._execute_llm_first_turn_fallback(
                task_id, raw_message, conv_id, context
            )

        except InputGuardrailTripwireTriggered:
            logger.warning("[llm_first_turn] Guardrail blocked message in router turn")
            await self._emit_final_response(
                task_id, "Yeh command safe nahi hai — block kar diya.", conv_id,
                user_message=raw_message, feedback_mode="toast"
            )
            return True
        except asyncio.TimeoutError:
            logger.warning("[llm_first_turn] SDK Router hung >50s — falling back")
            return await self._execute_llm_first_turn_fallback(
                task_id, raw_message, conv_id, context
            )
        except Exception as sdk_err:
            logger.warning("[llm_first_turn] SDK Router error (%s) — falling back", sdk_err)
            return await self._execute_llm_first_turn_fallback(
                task_id, raw_message, conv_id, context
            )
        finally:
            try:
                if hasattr(session, "close"):
                    session.close()
            except Exception:
                pass

    async def _execute_llm_first_turn_fallback(
        self,
        task_id: str,
        raw_message: str,
        conv_id: str,
        context: dict[str, Any],
    ) -> bool:
        """
        Streamed LLM fallback when SDK Router turn is empty or times out.
        Directly streams conversational response through ai_handler.
        """
        if not self.ai_handler or (not hasattr(self.ai_handler, "generate_stream") and not hasattr(self.ai_handler, "generate")):
            return False

        sys_prompt = self._build_router_system_prompt(context)
        messages = self._build_messages_list(raw_message, sys_prompt, context)

        from ..ws_protocol import build_ai_chunk

        accumulated = []
        try:
            if hasattr(self.ai_handler, "generate_stream"):
                async for token in self.ai_handler.generate_stream(messages=messages, task="fast_chat"):
                    if token:
                        accumulated.append(token)
                        if self.ws_broadcast:
                            await self.ws_broadcast(build_ai_chunk(task_id, token, is_final=False))
            elif hasattr(self.ai_handler, "generate"):
                res = await self.ai_handler.generate(messages=messages, task="fast_chat")
                # Support legacy unit-test mocks that inject tool_calls
                tool_calls = getattr(res, "tool_calls", None)
                if tool_calls and self.orchestrator and hasattr(self.orchestrator, "dispatch"):
                    for tc in tool_calls:
                        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                        t_name = str(fn.get("name", "")).replace("call_", "")
                        t_args = fn.get("arguments", {})
                        if isinstance(t_args, str):
                            try:
                                t_args = json.loads(t_args)
                            except Exception:
                                t_args = {}
                        msg_str = str(t_args.get("instruction") or raw_message)
                        await self.orchestrator.dispatch(task_id, t_name, msg_str, context)
                    return True
                text_out = str(getattr(res, "text", "") or "")
                if text_out:
                    accumulated.append(text_out)

            final_text = "".join(accumulated).strip()
            if final_text:
                if self.ws_broadcast:
                    await self.ws_broadcast(build_ai_chunk(task_id, "", is_final=True))
                self.record_turn(raw_message, final_text, context=context)
                if self.memory and hasattr(self.memory, "save_turn"):
                    mem_coro = self.memory.save_turn(final_text, role="assistant", conversation_id=conv_id)
                    mem_task = asyncio.create_task(mem_coro)
                    self._background_tasks.add(mem_task)
                    mem_task.add_done_callback(self._background_tasks.discard)
                return True
        except Exception as e:
            logger.warning("[llm_first_turn_fallback] Error in fallback generation: %s", e)

        return False


    async def classify_intent(self, message: str, context: Optional[dict[str, Any]] = None) -> IntentResult:
        """
        Classify user intent using fast direct URL regex, ai_handler parse, or vector SemanticRouter.
        Modern autonomous execution is managed natively by the OpenAI Agents SDK swarm,
        while this method provides backward-compatible intent detection for legacy callers.
        """
        msg_trimmed = message.strip()
        ctx = context or {}

        # Pure URL navigation detection → browser
        if re.search(r"^(?:https?://|www\.)[a-z0-9-]+\.[a-z]{2,}(?:/[^\s]*)?$", msg_trimmed, re.IGNORECASE):
            return IntentResult(
                intent=Intent.BROWSER,
                confidence=0.98,
                entities={
                    "domain": "browser",
                    "operation": "open",
                    "target": msg_trimmed,
                    "url": msg_trimmed,
                    "polarity": "ALLOW",
                    "desired_state": "loaded",
                },
                sub_intents=[],
                dependency_dag={},
            )

        # Context-aware semantic retry
        if "last_failed_action" in ctx:
            lfa = ctx["last_failed_action"]
            if any(w in msg_trimmed.lower() for w in ("wapas", "retry", "again", "phir")):
                dom = str(lfa.get("domain", "")).lower()
                matched_intent = Intent.MEDIA if dom == "media" else Intent.FAST_CHAT
                return IntentResult(
                    intent=matched_intent,
                    confidence=0.95,
                    entities={"domain": dom, "operation": "retry", "target": lfa.get("target", "")},
                )

        # If ai_handler has mock or custom try_parse_json (e.g. in unit tests)
        if self.ai_handler and hasattr(self.ai_handler, "try_parse_json"):
            try:
                gen_res = await self.ai_handler.generate(messages=[{"role": "user", "content": message}], task="fast_chat")
                raw_t = getattr(gen_res, "text", str(gen_res))
                parsed = self.ai_handler.try_parse_json(raw_t)
                if isinstance(parsed, dict) and "intent" in parsed:
                    int_str = str(parsed.get("intent", "fast_chat")).lower().strip()
                    ent = parsed.get("entities", {})
                    conf = float(parsed.get("confidence", 0.8))
                    try:
                        enum_intent = Intent(int_str)
                    except ValueError:
                        enum_intent = Intent.FAST_CHAT
                    return IntentResult(
                        intent=enum_intent,
                        confidence=conf,
                        entities=ent if isinstance(ent, dict) else {},
                    )
            except Exception:
                pass

        # Vector Semantic Router fallback
        if hasattr(self, "semantic_router") and self.semantic_router:
            try:
                router_intent, router_score = await self.semantic_router.classify_with_score(message)
                if router_intent is not None and router_score >= getattr(self.semantic_router, "_score_threshold", 0.6):
                    return IntentResult(
                        intent=router_intent,
                        confidence=router_score,
                        entities={"domain": router_intent.value},
                        sub_intents=[],
                        dependency_dag={},
                    )
            except Exception as e:
                logger.debug("[orchestrator] SemanticRouter unavailable: %s", e)

        # Default fallback to FAST_CHAT
        return IntentResult(
            intent=Intent.FAST_CHAT,
            confidence=0.75,
            sub_intents=[],
            dependency_dag={},
        )

    def _resolve_gate_agent(self, hint: str) -> Optional[str]:
        """Resolve a free-form LLM hint to an actual registered agent name."""
        if not hint:
            return None
        h = str(hint).strip().lower()
        if h in ("media", "media_agent"):
            return "media_agent"
        if h in ("browser", "browser_agent"):
            return "browser_agent"
        if h in ("system", "system_agent"):
            return "system_agent"
        if h in ("code", "code_agent"):
            return "code_agent"
        if h in ("research", "research_agent"):
            return "research_agent"
        return f"{h}_agent" if not h.endswith("_agent") else h


    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Conversation action memory — referents + semantic retry
    # ------------------------------------------------------------------

    def _remember_action(
        self,
        conv_id: str,
        agent_name: str,
        message: str,
        ok: bool,
        *,
        tool_name: str = "",
        domain: str = "general",
        requested_target: Optional[str] = None,
        resolved_target: Optional[str] = None,
        parameters: Optional[dict[str, Any]] = None,
        result_summary: str = "",
        status: Optional[str] = None,
        verified: Optional[bool] = None,
        error: Optional[str] = None,
        strategy: str = "direct",
        task_id: str = "",
    ) -> None:
        """Record an executed action/tool for this conversation with full semantic structure."""
        try:
            from collections import deque
            from .contracts import ExecutionRecord

            store = self._conv_actions.setdefault(conv_id, deque(maxlen=10))

            # Derive domain if not specified
            if not domain or domain == "general":
                t_low = (tool_name or agent_name or "").lower()
                if "media" in t_low:
                    domain = "media"
                elif "system" in t_low or "window" in t_low or "process" in t_low or "volume" in t_low:
                    domain = "system"
                elif "browser" in t_low:
                    domain = "browser"
                elif "doc" in t_low:
                    domain = "document"

            if status is None:
                if ok:
                    status = "SUCCESS"
                elif error and "cancel" in str(error).lower():
                    status = "CANCELLED"
                else:
                    status = "FAILED"

            if verified is None:
                verified = (status == "SUCCESS")

            rec = ExecutionRecord(
                timestamp=time.time(),
                conversation_id=conv_id,
                task_id=task_id,
                agent=agent_name,
                strategy=strategy,
                tool_name=tool_name or agent_name,
                domain=domain,
                requested_target=requested_target or (str(message)[:300] if message else None),
                resolved_target=resolved_target,
                parameters=dict(parameters or {}),
                result_summary=result_summary or str(message)[:300],
                status=status,
                verified=bool(verified),
                error=error,
                metadata={"raw_message": str(message)[:300]},
            )
            store.append(rec)
        except Exception as exc:
            logger.debug("[Understand] action memory write failed: %s", exc)

    def _format_last_actions(self, conv_id: str) -> str:
        store = getattr(self, "_conv_actions", {}).get(conv_id)
        if not store:
            return ""
        lines = []
        for i, a in enumerate(reversed(list(store)[-5:]), 1):
            tool = (
                getattr(a, "tool_name", None)
                or getattr(a, "agent", None)
                or (a.get("tool_name") if isinstance(a, dict) else None)
                or (a.get("agent") if isinstance(a, dict) else None)
                or "action"
            )
            status = (
                getattr(a, "status", None)
                or (a.get("status") if isinstance(a, dict) else None)
                or ("SUCCESS" if getattr(a, "verified", False) else "FAILED")
            )
            is_verified = (
                getattr(a, "verified", False)
                if not isinstance(a, dict)
                else a.get("verified", False)
            )
            verified_str = " (verified)" if is_verified else " (unverified)"
            if status != "SUCCESS":
                verified_str = ""

            target = (
                getattr(a, "resolved_target", None)
                or getattr(a, "requested_target", None)
                or (a.get("resolved_target") or a.get("requested_target") if isinstance(a, dict) else None)
            )

            # Format: 1. [media_play] target="Tum Hi Ho" → SUCCESS (verified)
            if target and str(target).strip():
                lines.append(f"{i}. [{tool}] target=\"{target}\" → {status}{verified_str}")
            else:
                summary = str(
                    getattr(a, "result_summary", "")
                    or getattr(a, "message", "")
                    or (a.get("result_summary") if isinstance(a, dict) else "")
                    or ""
                ).strip()
                if summary:
                    lines.append(f"{i}. [{tool}] {summary[:120]} → {status}{verified_str}")
                else:
                    lines.append(f"{i}. [{tool}] → {status}{verified_str}")
        return "\n".join(lines)

    def _last_action(self, conv_id: str) -> Optional[Any]:
        store = getattr(self, "_conv_actions", {}).get(conv_id)
        if store:
            return list(store)[-1]
        return None

    @staticmethod
    def _is_status_artifact(line: str) -> bool:
        """True for OUR OWN plumbing/status line formats (never user content).
        These must not enter LLM context — models echo them into replies.
        Matches only our emitted formats, never natural language."""
        s = line.strip()
        if not s:
            return False
        low = s.lower()
        if "tools dispatched" in low or "calling tools" in low:
            return True
        # Our bracketed markers: "[system: ...]" (colon inside) AND
        # "[media] ..." / "[media]: ..." (bare tag prefix) formats
        if re.match(r"^\[[a-z][a-z0-9_-]*\s*:[^\]]*\]", s, re.IGNORECASE):
            return True
        m_tag = re.match(r"^\[([a-z][a-z0-9_-]{1,15})\]\s*:?\s*", s)
        if m_tag:
            rest = s[m_tag.end():]
            # '[1] something' style numeric citations are NOT artifacts
            if not m_tag.group(1).isdigit():
                return True
        return (
            s.startswith("youtube:")
            or s.startswith("spotify:")
            or s.startswith("Searching ")
            or s.startswith("Matching ")
            or s.startswith("[Snapshot:")
            or s.startswith("[Manifest:")
            or "[▶ Watch" in s
        )

    @classmethod
    def _scrub_status_line(cls, line: str) -> str:
        """Drop a line entirely if it's one of our status artifacts."""
        if cls._is_status_artifact(line):
            return ""
        return line

    async def _handle_fast_chat(self, task_id: str, message: str, conv_id: str, context: dict[str, Any]) -> None:
        """Fast-path direct LLM streaming response."""
        if not self.ai_handler:
            await self._emit_final_response(task_id, "AI Handler unavailable.", conv_id, user_message=message)
            return

        if self.ws_broadcast:
            try:
                from ..ws_protocol import WSMessage, PROTOCOL_VERSION
                self._create_tracked_task(self.ws_broadcast(WSMessage(
                    v=PROTOCOL_VERSION,
                    type="persona_changed",
                    task_id=task_id,
                    payload={"persona": "general", "agent": "general"}
                )))
            except Exception as _persona_err:
                logger.debug("Failed to broadcast general persona: %s", _persona_err)

        sys_prompt = "You are Makima, a smart, precise, and helpful personal AI assistant. Reply conversationally and directly. Use markdown formatting where it aids clarity."
        if self.personality:
            if hasattr(self.personality, "build_system_prompt"):
                sys_prompt = self.personality.build_system_prompt()
            elif hasattr(self.personality, "system_prompt"):
                sys_prompt = self.personality.system_prompt

        # Grounding Makima's real active agents dynamically from orchestrator
        if self.orchestrator and hasattr(self.orchestrator, "agents") and self.orchestrator.agents:
            real_agents = []
            for name, inst in self.orchestrator.agents.items():
                agent_obj = getattr(inst, "agent", inst)
                doc_lines = [line.strip() for line in (agent_obj.__doc__ or "").splitlines() if line.strip()]
                desc = getattr(agent_obj, "DESCRIPTION", None) or (doc_lines[0] if doc_lines else name)
                real_agents.append(f"{name} ({desc})")
            if real_agents:
                sys_prompt += (
                    f"\n\n[YOUR REAL REGISTERED CAPABILITIES & AGENTS]\n"
                    f"Active registered agents: {', '.join(real_agents)}.\n"
                    f"When the user asks what agents or tools you can use or asks about a specific agent, "
                    f"answer accurately from this active list. NEVER hallucinate non-existent agents."
                )

        sys_prompt += (
            "\n\n[STRICT CONVERSATIONAL INSTRUCTIONS]\n"
            "1. NEVER output a recap, table, or list summarizing previous user requests or agent actions unless the user explicitly used the word 'summary', 'recap', or 'history'.\n"
            "2. Always answer ONLY the user's latest message directly, naturally, and concisely.\n"
            "3. Do NOT describe your internal architecture or list past actions taken unless specifically asked.\n"
            "4. Capability questions ('can you skip ads on YouTube?', 'can you control volume/apps/files?'): accurately confirm that YES, your registered agents CAN do these — NEVER output generic refusals like 'I cannot control your PC'. BUT in this direct-conversation mode you have NO tool access yourself: NEVER claim an action was performed, completed, set, saved, or is playing. If the user asks you to actually DO something, reply with ONE short line that you are handing it to the appropriate agent — nothing more."
        )

        if context.get("memory_results"):
            mem_text = "\n".join(f"- {m}" for m in context["memory_results"] if m)
            if mem_text:
                sys_prompt += f"\n\n[RELEVANT RECALLED MEMORY]\n{mem_text}\nUse this relevant user context to accurately answer personal questions."

        user_content: Union[str, list[dict[str, Any]]] = message
        attachment_parts = [
            item.get("content")
            for item in context.get("attachments", [])
            if isinstance(item, dict) and isinstance(item.get("content"), dict)
        ]
        if attachment_parts:
            user_content = [{"type": "text", "text": message}, *attachment_parts]

        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]

        # Inject sanitized recent conversational turns for seamless multi-turn context
        if context.get("history"):
            for turn in context["history"][-self._history_window :]:
                if isinstance(turn, dict) and "role" in turn and "content" in turn:
                    role = turn["role"]
                    c_text = str(turn["content"] or "").strip()
                    # Strip out technical prefix lines, URLs, and telemetry markers
                    cleaned_lines = []
                    for line in c_text.splitlines():
                        if not self._is_status_artifact(line):
                            cleaned_lines.append(line)
                    cleaned_content = "\n".join(cleaned_lines).strip()
                    if cleaned_content:
                        messages.append({"role": role, "content": cleaned_content})

        messages.append({"role": "user", "content": user_content})

        try:
            from ..ws_protocol import build_ai_chunk
            try:
                emitted = False
                chunks: list[str] = []
                buf = ""

                async def _emit_clean(text: str) -> None:
                    nonlocal emitted
                    if not text:
                        return
                    if self.ws_broadcast:
                        await self.ws_broadcast(build_ai_chunk(task_id, text, is_final=False))
                    chunks.append(text)
                    emitted = True

                async for chunk in self.ai_handler.stream_chat(messages=messages, task="general"):
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        await _emit_clean(self._scrub_status_line(line + "\n"))
                if buf:
                    await _emit_clean(self._scrub_status_line(buf))

                if emitted:
                    final_text = "".join(chunks)
                    # Belt-and-braces scrub before persistence
                    scrubber = getattr(self.ai_handler, "_sanitize_response", None)
                    if callable(scrubber):
                        try:
                            res_scrubbed = scrubber(final_text)
                            if isinstance(res_scrubbed, str):
                                final_text = res_scrubbed
                        except Exception:
                            pass
                    if self.memory and hasattr(self.memory, "save_turn") and final_text:
                        mem_coro = self.memory.save_turn(final_text, role="assistant", conversation_id=conv_id)
                        mem_task = asyncio.create_task(mem_coro)
                        self._background_tasks.add(mem_task)
                        mem_task.add_done_callback(self._background_tasks.discard)
                    self.record_turn(message, final_text, context=context)
                    if self.ws_broadcast:
                        await self.ws_broadcast(build_ai_chunk(task_id, "", is_final=True))
                else:
                    # Fallback to non-streaming chat_complete if stream didn't emit
                    logger.debug("Streaming fast_chat produced 0 chunks, falling back to chat_complete")
                    reply = await self.ai_handler.chat_complete(messages=messages)
                    await self._emit_final_response(task_id, reply, conv_id, user_message=message)

            except Exception as stream_err:
                logger.warning("Streaming fast_chat error (%s), falling back to chat_complete", stream_err)
                reply = await self.ai_handler.chat_complete(messages=messages)
                await self._emit_final_response(task_id, reply, conv_id, user_message=message)
                
        except Exception as e:
            logger.error("Direct response failed for task %s: %s", task_id, e, exc_info=True)
            await self._emit_final_response(
                task_id,
                "I couldn't complete that response. Please try again.",
                conv_id,
                user_message=message,
            )

    def record_turn(
        self,
        user_message: str,
        ai_response: str = "",
        context: Optional[dict[str, Any]] = None,
        skip_session: bool = False,
    ) -> None:
        """Record a completed conversation turn into personality, learning, and SQLiteSession."""
        if self.personality and hasattr(self.personality, "process_turn"):
            try:
                self.personality.process_turn(user_message, ai_response, context=context)
            except Exception as e:
                logger.debug("Failed to process turn in personality: %s", e)

        if skip_session:
            return

        # Multi-turn Short-term Persistence via OpenAI Agents SDK SQLiteSession (non-blocking background task)
        ctx = context or {}
        sess_id = (
            ctx.get("user_session_id")
            or ctx.get("conversation_id")
            or ctx.get("session_id")
            or "default_session"
        )
        items_to_add = []
        if user_message:
            items_to_add.append({"role": "user", "content": user_message})
        if ai_response:
            items_to_add.append({"role": "assistant", "content": ai_response})
        if items_to_add:
            async def _async_record_session() -> None:
                sess = None
                loop = asyncio.get_running_loop()
                try:
                    from agents import SQLiteSession
                    db_dir = os.path.expanduser("~/.makima")
                    os.makedirs(db_dir, exist_ok=True)
                    db_path = os.path.join(db_dir, "sessions.db")
                    sess = await loop.run_in_executor(
                        None, lambda: SQLiteSession(session_id=str(sess_id), db_path=db_path)
                    )
                    await sess.add_items(items_to_add)
                except Exception as sess_err:
                    logger.debug("[OrchestrationEngine] SQLiteSession record_turn failed: %s", sess_err)
                finally:
                    if sess is not None:
                        try:
                            await loop.run_in_executor(None, sess.close)
                        except Exception:
                            pass

            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(_async_record_session())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            except RuntimeError:
                pass
