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

import ast
import asyncio
import logging
import operator
import re
from contextvars import ContextVar
from typing import Any, Callable, Optional, Union

# Multi-user & multi-device isolated request context
client_request_context: ContextVar[dict[str, Any]] = ContextVar("client_request_context", default={})

_MATH_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def try_eval_math_expression(text: str) -> Optional[Union[int, float]]:
    """
    Safely evaluate pure mathematical / arithmetic expressions via Python AST.
    Returns numeric result or None. Completely zero-eval, zero-regex table, and injection-safe.
    """
    if not text or not isinstance(text, str):
        return None
    raw = text.strip()
    for prefix in ("calculate", "calc", "solve", "what is", "evaluate", "math:"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):].strip()
    raw = raw.rstrip("?=").strip()
    if not raw or len(raw) > 100:
        return None
    if not any(c.isdigit() for c in raw):
        return None
    if not any(op in raw for op in "+-*/%^"):
        return None
    raw_clean = raw.replace("^", "**")
    if not re.match(r"^[\d\s\.\+\-\*\/\%\(\)]+$", raw_clean):
        return None
    try:
        tree = ast.parse(raw_clean, mode="eval")

        def _eval_node(node: ast.AST) -> Union[int, float]:
            if isinstance(node, ast.Expression):
                return _eval_node(node.body)
            elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            elif isinstance(node, ast.UnaryOp) and type(node.op) in _MATH_OPERATORS:
                return _MATH_OPERATORS[type(node.op)](_eval_node(node.operand))
            elif isinstance(node, ast.BinOp) and type(node.op) in _MATH_OPERATORS:
                left = _eval_node(node.left)
                right = _eval_node(node.right)
                if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 10000):
                    raise OverflowError("Exponent too large")
                if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                    raise ZeroDivisionError("Division by zero")
                return _MATH_OPERATORS[type(node.op)](left, right)
            raise ValueError("Disallowed AST node")

        val = _eval_node(tree)
        if isinstance(val, float) and val.is_integer():
            return int(val)
        return round(val, 6) if isinstance(val, float) else val
    except Exception:
        return None

try:
    from ..ws_protocol import build_ai_chunk, build_toast_notification
except (ImportError, ValueError):
    from apps.brain.ws_protocol import build_ai_chunk, build_toast_notification

try:
    from agents import InputGuardrailTripwireTriggered
except ImportError:
    class InputGuardrailTripwireTriggered(Exception):  # type: ignore[no-redef]
        pass

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
        ws_broadcast: Optional[Callable] = None,
        personality: Any = None,
        task_manager: Any = None,
        tool_registry: Any = None,
        config: Optional[dict[str, Any]] = None,
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

        self._active_tasks: set[str] = set()
        self._cancelled_tasks: set[str] = set()
        self._task_handles: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._cached_unified_agents: dict[str, Any] = {}
        self._sdk_run_config: Optional[Any] = None
        self._active_conversation_id: str = "default_session"

        logger.info("OrchestrationEngine active — max_turns=%d", self._max_turns)

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
            self._sdk_run_config = get_default_run_config()
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
            except Exception:
                pass
        handle = self._task_handles.get(task_id)
        if handle and handle is not asyncio.current_task() and not handle.done():
            handle.cancel()
        if self.ws_broadcast:
            try:
                await self.ws_broadcast(build_ai_chunk(task_id, "[Task cancelled by user]", is_final=True))
            except Exception:
                pass
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
        self, task_id: str, goal: str, conversation_id: str = "default_session", context: Optional[dict[str, Any]] = None
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

    async def close(self) -> None:
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        self._background_tasks.clear()

    async def handle_message(self, task_id: str, message: str, context: Optional[dict[str, Any]] = None) -> None:
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
        failure_reason = ""

        try:
            from .sdk_bridge import is_dangerous_command, get_sdk_session, MakimaRunHooks
            from agents import Runner

            # 1. Hard OS Safety Check
            if is_dangerous_command(raw_message):
                logger.warning("[OrchestrationEngine] Blocked dangerous command: %s", raw_message)
                execution_failed = True
                failure_reason = "Blocked dangerous command"
                if self.ws_broadcast:
                    await self.ws_broadcast(build_toast_notification(task_id, "Yeh command safe nahi hai — block kar diya.", level="warning"))
                    await self.ws_broadcast(build_ai_chunk(task_id, "Yeh command safe nahi hai — block kar diya.", is_final=True))
                return

            # Fast deterministic math bypass (Zero cloud cost, <0.1ms latency)
            math_val = try_eval_math_expression(clean_msg)
            if math_val is not None:
                answer = f"{clean_msg.rstrip('?=').strip()} = {math_val}"
                if self.ws_broadcast:
                    await self.ws_broadcast(build_ai_chunk(task_id, answer, is_final=True))
                if self.memory and hasattr(self.memory, "save_turn"):
                    try:
                        await self.memory.save_turn(clean_msg, role="user", conversation_id=conv_id)
                        await self.memory.save_turn(answer, role="assistant", conversation_id=conv_id)
                    except Exception:
                        pass
                self.record_turn(clean_msg, answer, context=context)
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

            # Record user turn in episodic memory & trigger entity sync
            if self.memory and hasattr(self.memory, "save_turn"):
                try:
                    await self.memory.save_turn(raw_message, role="user", conversation_id=conv_id)
                except Exception as mem_err:
                    logger.debug("save_turn user error: %s", mem_err)

            env_blocks = []
            fg_win = context.get("foreground_window")
            if not fg_win:
                try:
                    from .world_state import WorldStateService
                    fg_win = WorldStateService().get_foreground_window()
                except Exception:
                    pass
            if fg_win:
                env_blocks.append(f"[Active Window: {fg_win}]")

            clip_text = context.get("clipboard")
            if not clip_text or clip_text == "[Clipboard is empty]":
                try:
                    from .os_state import get_os_state
                    clip_text = get_os_state().get_clipboard_text(fallback_history=True)
                except Exception:
                    pass
            if clip_text and clip_text != "[Clipboard is empty]":
                env_blocks.append(f"[Active Clipboard / Source Data:\n{clip_text}\n]")

            # Inject top matching learned rules / user preferences
            if self.memory and hasattr(self.memory, "search_rules"):
                try:
                    matched_rules = await self.memory.search_rules(raw_message, top_k=3)
                    if matched_rules:
                        rule_lines = "\n".join(f"• {r.strip()}" for r in matched_rules if r and r.strip())
                        if rule_lines:
                            env_blocks.append(f"[Active User Preferences / Behavioral Rules:\n{rule_lines}\n]")
                except Exception as rule_err:
                    logger.debug("search_rules injection error: %s", rule_err)

            agent_input = f"{chr(10).join(env_blocks)}\n\nUser Instruction: {raw_message}" if env_blocks else raw_message

            # 3. Call make_unified_agent()
            has_image = bool(context.get("image") or context.get("images") or context.get("screenshot") or "data:image/" in raw_message)
            agent = self._get_or_create_unified_agent(task="vision" if has_image else "fast_chat")
            hooks = MakimaRunHooks(ws_broadcast=self.ws_broadcast, task_id=task_id)

            # 4. Stream response via Runner.run_streamed()
            try:
                run_stream = Runner.run_streamed(
                    agent, agent_input, session=session, context=context, max_turns=self._max_turns, hooks=hooks, run_config=self._get_sdk_run_config()
                )
                streamed_chunks: list[str] = []
                async for stream_ev in run_stream.stream_events():
                    d = getattr(stream_ev, "data", None)
                    if d:
                        ev_type = getattr(d, "type", "")
                        if ev_type == "response.output_text.delta":
                            tok = getattr(d, "delta", "")
                            if tok and self.ws_broadcast:
                                streamed_chunks.append(tok)
                                await self.ws_broadcast(build_ai_chunk(task_id, tok, is_final=False))
                        elif ev_type == "response.output_text.done" and streamed_chunks and not streamed_chunks[-1].endswith("\n"):
                            streamed_chunks.append("\n\n")

                final_out = str(run_stream.final_output or "".join(streamed_chunks)).strip()
                clean_text = re.sub(r"<(?:thinking|think|thought|reasoning)>.*?</(?:thinking|think|thought|reasoning)>", "", final_out, flags=re.DOTALL).strip() if final_out else ""

                if self.ws_broadcast:
                    if not streamed_chunks:
                        await self.ws_broadcast(build_ai_chunk(task_id, clean_text or "Done.", is_final=False))
                    await self.ws_broadcast(build_ai_chunk(task_id, "", is_final=True, media=context.get("media"), sources=context.get("sources")))

                if clean_text:
                    if self.memory and hasattr(self.memory, "save_turn"):
                        try:
                            await self.memory.save_turn(clean_text, role="assistant", conversation_id=conv_id)
                        except Exception as mem_err:
                            logger.debug("save_turn error: %s", mem_err)
                    self.record_turn(raw_message, clean_text, context=context)
            finally:
                if hasattr(session, "close"):
                    try:
                        session.close()
                    except Exception:
                        pass

        except InputGuardrailTripwireTriggered:
            execution_failed = True
            failure_reason = "Prompt injection and unsafe override directives are blocked by system safety guardrails."
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
                except Exception:
                    pass
        except asyncio.CancelledError:
            logger.info("[OrchestrationEngine] Task %s cancelled", task_id)
            if self.ws_broadcast:
                try:
                    await self.ws_broadcast(build_ai_chunk(task_id, "[Task cancelled by user]", is_final=True))
                except Exception:
                    pass
        except Exception as e:
            execution_failed = True
            err_msg = str(e) or type(e).__name__
            failure_reason = err_msg
            logger.error("OrchestrationEngine error processing task %s: %s", task_id, err_msg, exc_info=True)
            if self.ws_broadcast:
                try:
                    await self.ws_broadcast(build_toast_notification(task_id, f"⚠️ Error: {err_msg[:120]}", level="error"))
                    await self.ws_broadcast(build_ai_chunk(task_id, f"I couldn't complete that request due to an error: {err_msg}. Please check logs or try again.", is_final=True))
                except Exception:
                    pass
        finally:
            if self.task_manager and not self.is_task_cancelled(task_id):
                try:
                    if execution_failed:
                        await self.task_manager.fail_task(task_id, error_message=failure_reason)
                    else:
                        await self.task_manager.complete_task(task_id)
                except Exception:
                    pass
            self._active_tasks.discard(task_id)
            self._cancelled_tasks.discard(task_id)
            self._task_handles.pop(task_id, None)
            try:
                client_request_context.reset(ctx_token)
            except Exception:
                pass

    def record_turn(self, user_message: str, ai_response: str = "", context: Optional[dict[str, Any]] = None) -> None:
        """Forward turn to personality engine if available."""
        if self.personality and hasattr(self.personality, "process_turn"):
            try:
                self.personality.process_turn(user_message, ai_response, context=context)
            except Exception:
                pass
