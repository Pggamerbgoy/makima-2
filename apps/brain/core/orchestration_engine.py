"""
Makima OS — Orchestration Engine
Location: apps/brain/core/orchestration_engine.py

Clean async message dispatcher:
1. WebSocket message receive
2. Context inject (clipboard, active window)
3. make_unified_agent() call
4. Runner.run_streamed() response streaming
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

# Multi-user & multi-device isolated request context.
# default=None: a shared mutable dict would leak across concurrent requests.
client_request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "client_request_context", default=None
)

try:
    from ..ws_protocol import (
        build_ai_chunk,
        build_canvas_item,
        build_toast_notification,
    )
except (ImportError, ValueError):
    from apps.brain.ws_protocol import (
        build_ai_chunk,
        build_canvas_item,
        build_toast_notification,
    )

try:
    from agents import (
        AgentUpdatedStreamEvent,
        InputGuardrailTripwireTriggered,
        OutputGuardrailTripwireTriggered,
        RawResponsesStreamEvent,
        RunItemStreamEvent,
    )
except ImportError:
    class InputGuardrailTripwireTriggered(Exception):  # type: ignore[no-redef]
        pass

    class OutputGuardrailTripwireTriggered(Exception):  # type: ignore[no-redef]
        pass

    AgentUpdatedStreamEvent = Any  # type: ignore[assignment,misc]
    RawResponsesStreamEvent = Any  # type: ignore[assignment,misc]
    RunItemStreamEvent = Any  # type: ignore[assignment,misc]

logger = logging.getLogger("orchestration_engine")


class OrchestrationEngine:
    """
    Primary entry point for user messages in Makima OS.
    Dispatches directly to unified agent via OpenAI Agents SDK Runner.
    """

    def __init__(
        self,
        ai_handler: Any = None,
        agent_orchestrator: Any = None,
        eternal_memory: Any = None,
        ws_broadcast: Callable | None = None,
        personality: Any = None,
        task_manager: Any = None,
        tool_registry: Any = None,
        config: dict[str, Any] | None = None,
        preference_engine: Any | None = None,
        settings_store: Any | None = None,
        **kwargs: Any,
    ):
        self.config = config or {}
        self._max_turns = int(self.config.get("agent", {}).get("max_turns") or 25)
        self.ai_handler = ai_handler
        self.orchestrator = agent_orchestrator
        self.memory = eternal_memory or kwargs.get("memory")
        self.ws_broadcast = ws_broadcast
        self.personality = personality
        self.task_manager = task_manager
        self.tool_registry = tool_registry or kwargs.get("tool_registry")
        self.durable_task_engine = kwargs.get("durable_task_engine") or getattr(agent_orchestrator, "durable_task_engine", None)
        self.preference_engine = preference_engine or kwargs.get("preference_engine")
        self.skill_library = kwargs.get("skill_library")
        self.settings_store = settings_store or kwargs.get("settings_store")

        self._persona_prompt_map: dict[str, str] = {
            "general": "",
            "professional": "Adopt a professional, polished tone. Prefer precise language, structured answers, and formal salutations.",
            "friendly": "Adopt a warm, friendly, conversational tone. Be approachable, use casual language, and add light humor where appropriate.",
            "concise": "Adopt an ultra-concise style. Answer with the minimum words needed. No filler, no preamble — just the answer.",
            "mentor": "Adopt a patient mentor tone. Explain concepts step-by-step, ask guiding questions when the user seems confused, and celebrate progress.",
        }
        if self.preference_engine is None and self.memory is not None:
            try:
                from .preference_engine import PreferenceEngine
                self.preference_engine = PreferenceEngine(
                    eternal_memory=self.memory,
                    tool_registry=self.tool_registry,
                )
            except Exception as pref_err:
                logger.debug("PreferenceEngine init failed: %s", pref_err)

        self._active_tasks: set[str] = set()
        self._cancelled_tasks: set[str] = set()
        self._task_handles: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._cached_unified_agents: dict[str, Any] = {}
        self._sdk_run_config: Any | None = None
        self._active_conversation_id: str = "default_session"

        logger.info("OrchestrationEngine active — max_turns=%d", self._max_turns)

    def _get_user_setting(self, key: str, default: Any = None) -> Any:
        """Read a user setting from UserSettingsStore, with safe fallback."""
        store = getattr(self, "settings_store", None)
        if store is None or not hasattr(store, "get_settings"):
            return default
        try:
            return store.get_settings().get(key, default)
        except Exception:
            return default

    def get_status(self) -> dict[str, Any]:
        """Snapshot for GET /status — real task/agent state, not a hardcoded fleet."""
        tool_names: list[str] = []
        reg = getattr(self, "tool_registry", None) or getattr(self.orchestrator, "tool_registry", None)
        if reg is not None:
            tools = getattr(reg, "tools", None)
            if isinstance(tools, dict):
                tool_names = sorted(str(k) for k in tools.keys())
            elif hasattr(reg, "list_tools"):
                try:
                    listed = reg.list_tools()
                    tool_names = sorted(str(getattr(t, "name", t)) for t in listed)
                except Exception:
                    tool_names = []
        cached_agents = sorted(self._cached_unified_agents.keys())
        return {
            "type": "orchestration_engine",
            "active_tasks": len(self._active_tasks),
            "cancelled_tasks": len(self._cancelled_tasks),
            "tracked_handles": len(self._task_handles),
            "cached_unified_agents": cached_agents,
            "tool_count": len(tool_names),
            "tools_sample": tool_names[:40],
            "conversation_id": self._active_conversation_id,
        }

    def _get_or_create_unified_agent(self, task: str = "fast_chat") -> Any:
        """Lazily initialize and cache unified Makima agents per task."""
        if task not in self._cached_unified_agents:
            from .sdk_bridge import make_unified_agent
            reg = getattr(self, "tool_registry", None) or getattr(self.orchestrator, "tool_registry", None)
            self._cached_unified_agents[task] = make_unified_agent(
                ai_handler=self.ai_handler,
                tool_registry=reg,
                ws_broadcast=self.ws_broadcast,
                orchestrator=self.orchestrator,
                task=task,
                config=self.config,
            )
        return self._cached_unified_agents[task]

    def _get_sdk_run_config(self) -> Any:
        if self._sdk_run_config is None:
            from .sdk_bridge import get_default_run_config
            self._sdk_run_config = get_default_run_config(ai_handler=self.ai_handler)
        return self._sdk_run_config

    def _create_tracked_task(self, coro: Any) -> asyncio.Task:
        async def _safe_coro() -> None:
            try:
                await coro
            except Exception as e:
                logger.debug("Background task exception: %s", e)

        task = asyncio.create_task(_safe_coro())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def cancel_task(self, task_id: str) -> bool:
        self._cancelled_tasks.add(task_id)
        if getattr(self, "task_manager", None):
            try:
                await self.task_manager.cancel_task(task_id)
            except Exception as cancel_err:
                logger.debug("task_manager.cancel_task failed for %s: %s", task_id, cancel_err)
        handle = self._task_handles.get(task_id)
        if handle and handle is not asyncio.current_task() and not handle.done():
            handle.cancel()
        if self.ws_broadcast:
            try:
                await self.ws_broadcast(build_ai_chunk(task_id, "[Task cancelled by user]", is_final=True))
            except Exception as chunk_err:
                logger.debug("Cancel notice broadcast failed for %s: %s", task_id, chunk_err)
        logger.info("Task %s cancelled", task_id)
        return True

    def is_task_cancelled(self, task_id: str) -> bool:
        if task_id in self._cancelled_tasks:
            return True
        if getattr(self, "task_manager", None):
            task = getattr(self.task_manager, "_tasks", {}).get(task_id)
            if task and getattr(task, "state", None) == "CANCELLED":
                return True
        return False

    async def run_autonomous_goal(
        self, task_id: str, goal: str, conversation_id: str = "default_session", context: dict[str, Any] | None = None
    ) -> str:
        ctx = dict(context or {}, is_autonomous_goal=True, task_id=task_id, conversation_id=conversation_id)

        async def _run() -> None:
            try:
                if self.ws_broadcast:
                    await self.ws_broadcast(build_toast_notification(task_id, f"Autonomous Goal: {goal[:60]}...", level="info"))
                await self.handle_message(task_id, goal, context=ctx)
            except Exception as exc:
                logger.error("Autonomous goal %s failed: %s", task_id, exc)
                if self.ws_broadcast:
                    await self.ws_broadcast(build_ai_chunk(task_id, f"Autonomous goal issue: {exc}", is_final=True))

        self._create_tracked_task(_run())
        return task_id

    async def handle_text_command(
        self,
        text: str,
        session_id: str = "default_session",
        task_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Compatibility bridge for macro execution and external command callers."""
        tid = task_id or f"task_{int(time.time() * 1000)}"
        ctx = dict(kwargs.get("context") or {}, conversation_id=session_id)
        await self.handle_message(task_id=tid, message=text, context=ctx)

    async def close(self) -> None:
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        self._background_tasks.clear()

    def _decompose_goal_milestones(self, raw_message: str, is_autonomous: bool = False) -> list[dict[str, Any]]:
        """Decompose multi-step instructions into an ordered checklist of plan milestones."""
        clean = (raw_message or "").strip()
        if not clean:
            return []

        # Split pattern for multi-step goals
        # English: then, and then, after that, followed by, first/second
        # Hinglish: fir, phir, uske baad, pehle, aur fir
        # Numbered: 1. 2. or newlines with steps
        split_pattern = r"(?:\s*(?:,\s*)?(?:\band\s+then\b|\bthen\b|\bafter\s+that\b|,\s*and\b|,\s*aur\b|\bfir\b|\bphir\b|\buske\s+baad\b|\baur\s+fir\b|\bpehle\b)\s*|\s*;\s*|\n+\s*(?:\d+[\.\)]|-)\s*)"
        parts = [p.strip() for p in re.split(split_pattern, clean, flags=re.IGNORECASE) if p and len(p.strip()) > 3]

        if len(parts) >= 2 or (is_autonomous and len(parts) >= 1):
            milestones = []
            for idx, part in enumerate(parts[:5], 1):
                title = part.capitalize()
                if len(title) > 60:
                    title = title[:57] + "..."
                milestones.append({
                    "id": idx,
                    "title": title,
                    "status": "pending",
                })
            return milestones
        return []

    async def handle_message(self, task_id: str, message: str, context: dict[str, Any] | None = None) -> None:
        """
        1. WebSocket message receive karna
        2. Context inject karna (clipboard, active window)
        3. make_unified_agent() call karna
        4. Runner.run_streamed() se response stream karna
        """
        context = dict(context or {})
        raw_message = message or ""
        clean_msg = raw_message.strip()
        if not clean_msg:
            return

        conv_id = context.get("conversation_id") or self._active_conversation_id or "default_session"
        self._active_conversation_id = conv_id
        context["conversation_id"] = conv_id
        context["raw_user_message"] = raw_message

        # In-flight dedup: ignore re-delivery of a task that is already running
        # (double-click / WS retry). Entry is discarded in finally, so real retries work.
        if task_id in self._active_tasks:
            logger.warning("[OrchestrationEngine] Duplicate turn ignored for task %s (already in-flight).", task_id)
            return
        self._active_tasks.add(task_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            self._task_handles[task_id] = current_task

        ctx_token = client_request_context.set({
            "provider": context.get("provider"),
            "model": context.get("model"),
            "api_key": context.get("api_key"),
            "base_url": context.get("base_url"),
            "conversation_id": conv_id,
            "task_id": task_id,
        })

        execution_failed = False
        t_start = time.monotonic()

        try:
            from agents import Runner

            from .sdk_bridge import (
                MakimaRunHooks,
                get_sdk_session,
                is_dangerous_command,
            )

            # 1. Hard OS Safety Check
            if is_dangerous_command(raw_message):
                logger.warning("[OrchestrationEngine] Blocked dangerous command: %s", raw_message)
                execution_failed = True
                if self.ws_broadcast:
                    await self.ws_broadcast(build_toast_notification(task_id, "Yeh command safe nahi hai — block kar diya.", level="warning"))
                    await self.ws_broadcast(build_ai_chunk(task_id, "Yeh command safe nahi hai — block kar diya.", is_final=True))
                return

            # 2. Context Injection (Active Window / Clipboard / AIHandler)
            session = get_sdk_session(conv_id or context.get("user_session_id") or "makima_main")
            context.update({
                "task_id": task_id,
                "conversation_id": conv_id,
                "ws_broadcast": self.ws_broadcast,
                "raw_message": raw_message,
                "message": raw_message,
                "ai_handler": self.ai_handler,
            })

            # Privacy mode: skip EternalMemory save_turn for both user and assistant turns
            privacy_mode = bool(self._get_user_setting("privacy_mode", False))

            # Record user turn in episodic memory & trigger entity sync
            if self.memory and hasattr(self.memory, "save_turn") and not privacy_mode:
                try:
                    await self.memory.save_turn(raw_message, role="user", conversation_id=conv_id)
                except Exception as mem_err:
                    logger.debug("save_turn user error: %s", mem_err)

            # O2: independent context fetches run concurrently (wall-clock = max, not sum).
            async def _fetch_fg_win() -> str:
                preset = context.get("foreground_window")
                if preset:
                    return str(preset)
                try:
                    from .os_state import get_os_state
                    return get_os_state().get_foreground_window() or ""
                except Exception as _exc:
                    logger.debug("suppressed: %s", _exc)
                    return ""

            async def _fetch_clip() -> str:
                preset = context.get("clipboard")
                if preset and preset != "[Clipboard is empty]":
                    return str(preset)
                try:
                    from .os_state import get_os_state
                    return get_os_state().get_clipboard_text(fallback_history=True) or ""
                except Exception as _exc:
                    logger.debug("suppressed: %s", _exc)
                    return ""

            async def _fetch_rules() -> list[str]:
                if self.memory and hasattr(self.memory, "search_rules"):
                    try:
                        return await self.memory.search_rules(raw_message, top_k=3) or []
                    except Exception as rule_err:
                        logger.debug("search_rules injection error: %s", rule_err)
                return []

            async def _fetch_prefs() -> list[str]:
                pref_eng = getattr(self, "preference_engine", None)
                if pref_eng and hasattr(pref_eng, "get_relevant_preferences"):
                    try:
                        return await pref_eng.get_relevant_preferences(raw_message) or []
                    except Exception as pref_err:
                        logger.debug("[OrchestrationEngine] Preference injection error: %s", pref_err)
                return []

            fg_win, clip_text, matched_rules, pref_hints = await asyncio.gather(
                _fetch_fg_win(), _fetch_clip(), _fetch_rules(), _fetch_prefs()
            )

            env_blocks = []
            if fg_win:
                env_blocks.append(f"[Active Window: {fg_win}]")
            if clip_text and clip_text != "[Clipboard is empty]":
                env_blocks.append(f"[Active Clipboard / Source Data:\n{clip_text}\n]")
            if matched_rules:
                rule_lines = "\n".join(f"• {r.strip()}" for r in matched_rules if r and r.strip())
                if rule_lines:
                    env_blocks.append(f"[Active User Preferences / Behavioral Rules:\n{rule_lines}\n]")
            if pref_hints:
                env_blocks.extend(pref_hints)
                context["preference_hints"] = pref_hints

            # Inject CapabilityProbe snapshot — LLM knows what strategies are actually available
            try:
                from .capability_probe import CapabilityProbe
                probe = CapabilityProbe.instance()
                snapshot = probe.probe_all()
                available = [
                    f"  • {name}: {r.method} — {r.note}"
                    for name, r in snapshot.items() if r.available
                ]
                unavailable = [
                    f"  • {name}: ❌ {r.note}"
                    for name, r in snapshot.items() if not r.available
                ]
                cap_lines = []
                if available:
                    cap_lines.append("Available:")
                    cap_lines.extend(available)
                if unavailable:
                    cap_lines.append("Not configured:")
                    cap_lines.extend(unavailable)
                if cap_lines:
                    env_blocks.append(
                        "[System Capability Snapshot — use these strategies for your goal:\n"
                        + "\n".join(cap_lines)
                        + "\n]"
                    )
            except Exception as cap_err:
                logger.debug("[OrchestrationEngine] CapabilityProbe injection error: %s", cap_err)

            agent_input = f"{chr(10).join(env_blocks)}\n\nUser Instruction: {raw_message}" if env_blocks else raw_message

            # ── General settings injection: system_prompt + persona ──
            user_system_prompt = str(self._get_user_setting("system_prompt", "") or "").strip()
            persona_key = str(self._get_user_setting("persona", "general") or "general").strip().lower()
            persona_instruction = self._persona_prompt_map.get(persona_key, "")
            settings_blocks: list[str] = []
            if user_system_prompt:
                settings_blocks.append(
                    "[User-Defined System Prompt — always follow these instructions]\n" + user_system_prompt
                )
            if persona_instruction:
                settings_blocks.append(
                    "[Persona Directive — adopt this tone]\n" + persona_instruction
                )
            if settings_blocks:
                agent_input = (
                    chr(10).join(settings_blocks)
                    + "\n\n"
                    + agent_input
                )

            # Token-bomb guard: clipboard/paste can balloon the prompt; cap it.
            if len(agent_input) > 30000:
                agent_input = agent_input[:30000] + "\n[Input truncated: exceeded 30000 chars]"

            # 3. Call make_unified_agent()
            has_image = bool(context.get("image") or context.get("images") or context.get("screenshot") or "data:image/" in raw_message)
            if not has_image and re.search(r"\b(screen\s*(?:dekho|pe|par|shot)|what'?s on my screen|dekho meri screen|look at my screen)\b", raw_message, re.IGNORECASE):
                try:
                    from ..tools.system_tools import take_screenshot

                    shot_res = await take_screenshot(target="fullscreen")
                    if isinstance(shot_res, dict) and shot_res.get("status") == "success":
                        shot_path = shot_res.get("path") or shot_res.get("file_path")
                        if shot_path:
                            context["screenshot"] = shot_path
                            has_image = True
                except Exception as shot_err:
                    logger.debug("[OrchestrationEngine] Screenshot capture failed: %s", shot_err)

            agent = self._get_or_create_unified_agent(task="vision" if has_image else "fast_chat")

            # Decompose multi-step goal milestones if applicable
            is_autonomous = bool(context.get("is_autonomous_goal"))
            milestones = self._decompose_goal_milestones(raw_message, is_autonomous=is_autonomous)
            if milestones and self.ws_broadcast:
                try:
                    from ..ws_protocol import build_plan_milestones
                    await self.ws_broadcast(build_plan_milestones(
                        task_id=task_id,
                        title=f"Execution Plan ({len(milestones)} steps)",
                        steps=milestones,
                    ))
                except Exception as ms_err:
                    logger.debug("[OrchestrationEngine] Milestone broadcast error: %s", ms_err)

            hooks = MakimaRunHooks(
                ws_broadcast=self.ws_broadcast,
                task_id=task_id,
                plan_milestones=milestones,
                tool_registry=self.tool_registry,
            )

            # 4. Stream response via Runner.run_streamed()
            try:
                run_stream = Runner.run_streamed(
                    agent, agent_input, session=session, context=context, max_turns=self._max_turns, hooks=hooks, run_config=self._get_sdk_run_config()
                )
                streamed_chunks: list[str] = []
                async for stream_ev in run_stream.stream_events():
                    # Typed SDK stream events (AgentUpdated / RunItem / RawResponses)
                    # rather than duck-typed OpenAI wire `data.type` matching.
                    if isinstance(stream_ev, RawResponsesStreamEvent):
                        d = stream_ev.data
                        ev_type = getattr(d, "type", "")
                        if ev_type == "response.output_text.delta":
                            tok = getattr(d, "delta", "")
                            if tok and self.ws_broadcast:
                                streamed_chunks.append(tok)
                                await self.ws_broadcast(build_ai_chunk(task_id, tok, is_final=False))
                        elif ev_type == "response.output_text.done" and streamed_chunks and not streamed_chunks[-1].endswith("\n"):
                            streamed_chunks.append("\n\n")
                    elif isinstance(stream_ev, AgentUpdatedStreamEvent):
                        new_agent = getattr(stream_ev, "new_agent", None)
                        if new_agent is not None:
                            logger.debug("[OrchestrationEngine] Stream agent updated: %s", getattr(new_agent, "name", new_agent))
                    elif isinstance(stream_ev, RunItemStreamEvent):
                        # Tool lifecycle already surfaced via MakimaRunHooks; log only.
                        if getattr(stream_ev, "name", "") == "tool_called":
                            item = getattr(stream_ev, "item", None)
                            tool_name = getattr(getattr(item, "raw_item", None), "name", None) or getattr(item, "name", None)
                            if tool_name:
                                logger.debug("[OrchestrationEngine] Stream tool called: %s", tool_name)

                final_out = str(run_stream.final_output or "".join(streamed_chunks)).strip()
                clean_text = re.sub(r"<(?:thinking|think|thought|reasoning)>.*?</(?:thinking|think|thought|reasoning)>", "", final_out, flags=re.DOTALL).strip() if final_out else ""

                if self.ws_broadcast:
                    if not streamed_chunks:
                        await self.ws_broadcast(build_ai_chunk(task_id, clean_text or "Done.", is_final=False))
                    await self.ws_broadcast(build_ai_chunk(task_id, "", is_final=True, media=context.get("media"), sources=context.get("sources")))
                    canvas_msg = self._maybe_build_canvas_item(task_id, clean_text)
                    if canvas_msg:
                        await self.ws_broadcast(canvas_msg)

                if clean_text:
                    if self.memory and hasattr(self.memory, "save_turn") and not privacy_mode:
                        try:
                            await self.memory.save_turn(clean_text, role="assistant", conversation_id=conv_id)
                        except Exception as mem_err:
                            logger.debug("save_turn error: %s", mem_err)
                    if not privacy_mode:
                        self.record_turn(raw_message, clean_text, context=context)
            finally:
                if hasattr(session, "close"):
                    try:
                        session.close()
                    except Exception as _exc:
                        logger.debug("suppressed: %s", _exc)

        except InputGuardrailTripwireTriggered:
            execution_failed = True
            refusal = (
                "I refuse to execute this instruction. "
                "Prompt injection and unsafe override directives are blocked by system safety guardrails."
            )
            logger.warning(
                "[OrchestrationEngine] Guardrail tripwire triggered for task %s — prompt injection blocked.",
                task_id,
            )
            if self.ws_broadcast:
                try:
                    await self.ws_broadcast(build_toast_notification(task_id, "⛔ Prompt injection blocked by safety guardrail.", level="error"))
                    await self.ws_broadcast(build_ai_chunk(task_id, refusal, is_final=True))
                except Exception as _exc:
                    logger.debug("suppressed: %s", _exc)
        except OutputGuardrailTripwireTriggered:
            execution_failed = True
            refusal = "I withheld that reply because it looked like it would expose a credential or private key. Ask again without secrets."
            logger.warning(
                "[OrchestrationEngine] Output guardrail tripwire triggered for task %s — secret leak blocked.",
                task_id,
            )
            if self.ws_broadcast:
                try:
                    await self.ws_broadcast(build_toast_notification(task_id, "⛔ Output blocked: potential secret/credential leak.", level="error"))
                    await self.ws_broadcast(build_ai_chunk(task_id, refusal, is_final=True))
                except Exception as _exc:
                    logger.debug("suppressed: %s", _exc)
        except asyncio.CancelledError:
            logger.info("[OrchestrationEngine] Task %s cancelled", task_id)
            if self.ws_broadcast:
                try:
                    await self.ws_broadcast(build_ai_chunk(task_id, "[Task cancelled by user]", is_final=True))
                except Exception as _exc:
                    logger.debug("suppressed: %s", _exc)
        except Exception as e:
            execution_failed = True
            err_msg = str(e) or type(e).__name__
            logger.exception("OrchestrationEngine error processing task %s: %s", task_id, err_msg)
            if self.ws_broadcast:
                try:
                    await self.ws_broadcast(build_toast_notification(task_id, f"⚠️ Error: {err_msg[:120]}", level="error"))
                    await self.ws_broadcast(build_ai_chunk(task_id, f"I couldn't complete that request due to an error: {err_msg}. Please check logs or try again.", is_final=True))
                except Exception as _exc:
                    logger.debug("suppressed: %s", _exc)
        finally:
            # NOTE: no task_manager.fail/complete here — OE never creates TaskManager
            # entries, so those calls were no-op warnings every turn. DurableTaskEngine
            # syncs its own checkpoints with TaskManager directly.
            self._active_tasks.discard(task_id)
            self._cancelled_tasks.discard(task_id)
            self._task_handles.pop(task_id, None)
            try:
                logger.debug(
                    "[OrchestrationEngine] Turn %s finished in %.1fs (failed=%s).",
                    task_id, time.monotonic() - t_start, execution_failed,
                )
            except Exception as _exc:
                logger.debug("suppressed: %s", _exc)
            try:
                client_request_context.reset(ctx_token)
            except Exception as _exc:
                logger.debug("suppressed: %s", _exc)

    def _maybe_build_canvas_item(self, task_id: str, text: str):
        """Emit a canvas_item when the final answer contains a substantial code block."""
        if not text:
            return None
        blocks = re.findall(r"```([\w.+#-]*)\n(.*?)```", text, flags=re.DOTALL)
        if not blocks:
            return None
        lang, content = max(blocks, key=lambda pair: len(pair[1]))
        content = content.strip("\n")
        if len(content) < 40:
            return None
        return build_canvas_item(
            task_id,
            title=f"{(lang or 'text')} artifact",
            language=lang or "text",
            content=content,
        )

    def record_turn(self, user_message: str, ai_response: str = "", context: dict[str, Any] | None = None) -> None:
        """Forward turn to personality engine if available."""
        if self.personality and hasattr(self.personality, "process_turn"):
            try:
                self.personality.process_turn(user_message, ai_response, context=context)
            except Exception as _exc:
                logger.debug("suppressed: %s", _exc)
