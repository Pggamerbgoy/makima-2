"""
Makima v7.2 — Elite Base Agent
Upgrades: Async tool locking, Jaccard semantic attention, OpenTelemetry hooks, 
advanced context compression, and zero-crash resilience.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Optional
from collections import Counter
import math

logger = logging.getLogger("makima.agents.base")

# Graceful telemetry imports
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    _HAS_OTEL = True
    _tracer = trace.get_tracer("makima.agents")
except ImportError:
    _HAS_OTEL = False
    _tracer = None

_SCREEN_TRIGGERS = frozenset({
    "screen", "look at", "on my screen", "window", "read this",
    "what am i", "desktop", "ui", "what's open", "kya open hai",
    "dikhao", "dekho", "visible", "showing", "current app",
})
_CLIPBOARD_TRIGGERS = frozenset({
    "clipboard", "paste", "copied", "copy", "this text", "jo copy kiya", "pasted",
})
_DOCUMENT_TRIGGERS = frozenset({
    "document", "file", "pdf", "doc", "spreadsheet", "yeh file", "is file",
})

# ── Skill loader ──────────────────────────────────────────────────────────────
# Each agent has a SKILL.md at .agents/skills/<agent-name>/SKILL.md.
# This is loaded ONCE at agent init and injected into every LLM call so the
# agent actually follows its own skill rules instead of ignoring them.
# Zero-crash: if the file is missing or unreadable, silently returns "".
_SKILL_ROOT = Path(__file__).resolve().parents[3] / ".agents" / "skills"

def _load_agent_skill(agent_name: str) -> str:
    """Load SKILL.md for the given agent name. Returns "" on any failure."""
    path = _SKILL_ROOT / f"{agent_name}-agent" / "SKILL.md"
    if not path.exists():
        # Also try without -agent suffix (e.g. codebase-analysis)
        path = _SKILL_ROOT / agent_name / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
        # Strip YAML front-matter (--- ... ---) so it doesn't confuse the LLM
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3:].strip()
        return text
    except Exception:
        return ""

class BaseAgent(ABC):
    AGENT_NAME: str = "base"
    DESCRIPTION: str = "Base agent"
    SYSTEM_PROMPT: str = "You are a helpful AI assistant."

    # Elite: Self-declared capabilities for the capability registry
    CAPABILITIES: list[str] = []   # e.g., ["web_search", "code_generation"]
    AGENT_TOOLS: list[str] = []    # e.g., ["web_search", "shell", "file_read"]
    TAGS: list[str] = []           # e.g., ["research", "web", "analysis"]

    _pending_confirmations: ClassVar[dict[str, tuple[asyncio.Event, str, dict[str, bool]]]] = {}
    
    MAX_SCREEN_CHARS: int = 4000
    MAX_CLIPBOARD_CHARS: int = 2000
    MAX_DOCUMENT_CHARS: int = 6000
    MAX_MEMORY_RESULTS: int = 5
    
    _SCREEN_AGENTS: frozenset[str] = frozenset({"code", "automation", "system", "browser", "data_analyst", "security", "devops"})

    def __init__(
        self,
        ai_handler: Any,
        memory: Any = None,
        tool_registry: Any = None,
        ws_broadcast: Optional[Callable[..., Awaitable[Any]]] = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        coordination: Any = None,
    ) -> None:
        self.ai_handler = ai_handler
        self.memory = memory
        self.tool_registry = tool_registry
        self.ws_broadcast = ws_broadcast
        self.orchestrator = orchestrator
        self.guardrails = guardrails
        self.coordination = coordination  # Elite coordination hub

        self._partial_result: str = ""
        self._tokens_used: int = 0
        self._tool_calls_made: int = 0
        self._cancelled: bool = False
        self._exec_start: float = 0.0
        self._is_interactive: bool = False

        # Elite: Async locks per tool to prevent race conditions in parallel subtasks
        self._tool_locks: dict[str, asyncio.Lock] = {}
        self._tool_locks_lock = asyncio.Lock()

        # Learning system — injected by main.py after coordinator is initialised.
        # None-safe everywhere: all learning calls are guarded by `if _lc`.
        self._learning_coordinator: Any = None   # LearningCoordinator instance
        self._learning_engine: Any = None        # LearningEngine instance (for style prefs)

        # Load SKILL.md from .agents/skills/<AGENT_NAME>-agent/SKILL.md and
        # inject into every LLM call via _build_messages. This was the root
        # cause of "skills not being used" — the files existed but nothing
        # ever read them. Now loaded once at init, zero-cost per call.
        _raw_skill = _load_agent_skill(self.AGENT_NAME)
        if _raw_skill:
            self._skill_content = f"\n\n---\n## Agent Skill Guide\n{_raw_skill}\n---"
            logger.debug("[%s] Skill loaded (%d chars)", self.AGENT_NAME, len(_raw_skill))
        else:
            self._skill_content = ""

    @abstractmethod
    async def execute(self, task_id: str, message: str, context: dict[str, Any],
                      entities: dict[str, Any]) -> str:
        pass

    def set_execution_mode(self, is_interactive: bool) -> None:
        self._is_interactive = is_interactive

    def _reset_state(self) -> None:
        self._partial_result = ""
        self._tokens_used = 0
        self._tool_calls_made = 0
        self._cancelled = False
        self._exec_start = time.monotonic()

    def get_partial_result(self) -> str:
        return self._partial_result

    def get_execution_stats(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self._exec_start if self._exec_start else 0.0
        return {
            "agent": self.AGENT_NAME,
            "elapsed_seconds": round(elapsed, 2),
            "tokens_used": self._tokens_used,
            "tool_calls": self._tool_calls_made,
            "was_cancelled": self._cancelled,
        }

    async def cancel(self) -> None:
        self._cancelled = True
        logger.info("[%s] Execution cancelled", self.AGENT_NAME)

    async def _execute_react_loop(
        self,
        task_id: str,
        message: str,
        context: dict[str, Any],
        task: str = "general",
        extra_system: str = "",
        max_iterations: int = 3,
    ) -> str:
        """Deprecated alias — delegates to the production ReAct loop (_execute_with_tools)."""
        logger.warning("[%s] _execute_react_loop is deprecated, use _execute_with_tools instead", self.AGENT_NAME)
        return await self._execute_with_tools(
            task_id,
            message,
            context,
            task=task,
            extra_system=extra_system,
            max_turns=max_iterations,
            stream_progress=False,
        )

    @staticmethod
    def _tool_failed(result: Any) -> bool:
        if result is None:
            return True
        if isinstance(result, dict):
            if result.get("error") or result.get("status") in ("error", "failed", "failure"):
                return True
            if result.get("success") is False:
                return True
        s = str(result).lower()
        if s.startswith("[tool error") or s.startswith("error:") or s.startswith("[error]") or s.startswith("exception:"):
            return True
        return any(x in s for x in [
            "[tool error", "error executing tool", "vision click failed",
            "captcha detected", "[cancelled]", "[timeout]", "[nav_failed]"
        ])

    async def _get_tool_lock(self, tool_name: str) -> asyncio.Lock:
        async with self._tool_locks_lock:
            if tool_name not in self._tool_locks:
                self._tool_locks[tool_name] = asyncio.Lock()
            return self._tool_locks[tool_name]

    async def _llm_call_raw(self, messages: list[dict], task: str = "general", **kwargs) -> Any:
        if self._cancelled:
            from ..ai_handler import LLMResponse
            return LLMResponse(text=self._partial_result or "[Cancelled]", backend="cancelled", model="none")

        span = _tracer.start_span(f"llm_call_{task}") if (_HAS_OTEL and _tracer) else None
        try:
            response = await self.ai_handler.generate(messages, task=task, **kwargs)
            self._tokens_used += getattr(response, "total_tokens", 0)
            text = getattr(response, "text", str(response))
            self._partial_result = text
            if span and hasattr(span, "set_status"): span.set_status(Status(StatusCode.OK))
            return response
        except Exception as e:
            logger.error("[%s] LLM call failed: %s", self.AGENT_NAME, e)
            if span and hasattr(span, "set_status"): span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
        finally:
            if span and hasattr(span, "end"): span.end()

    async def _llm_call(self, messages: list[dict], task: str = "general", **kwargs) -> str:
        res = await self._llm_call_raw(messages, task=task, **kwargs)
        return getattr(res, "text", str(res))

    async def _llm_parse(self, message: str, schema: dict, context_hint: str = "") -> dict:
        """Universal LLM-based parameter extractor — eliminates hardcoded regex in agents.

        Instead of regex-parsing "baarish ka mausam hai accha gaana bajao" and getting
        garbage, this asks the LLM: "what does the user want?" and gets back clean JSON.

        Args:
            message: Raw user message (full natural language, any language)
            schema: Dict describing what fields to extract, e.g.:
                {
                    "action": "one of: play|pause|next|volume",
                    "query": "search query — infer from mood/context if not explicit",
                    "connector": "spotify or youtube"
                }
            context_hint: Optional extra instruction for the LLM

        Returns:
            Parsed dict with extracted fields. Falls back to {"_raw": message} on failure.
            Never raises — always returns a dict.

        Example:
            params = await self._llm_parse(
                "baarish ka mausam hai accha gaana bajao",
                {"action": "play|pause|next", "query": "song/artist to search", "connector": "spotify|youtube"},
                context_hint="User may describe mood or weather — infer an appropriate music query from context"
            )
            # Returns: {"action": "play", "query": "rainy day songs", "connector": "spotify"}
        """
        schema_lines = "\n".join(f"  {k}: {v}" for k, v in schema.items())
        extra = f"\n\nExtra context: {context_hint}" if context_hint else ""
        prompt = (
            f"Extract structured parameters from this user message.\n"
            f"Message: '{message}'\n"
            f"Extract these fields (use null if not present):\n{schema_lines}{extra}\n\n"
            f"IMPORTANT: If the message describes a mood, situation, or context (e.g. 'rainy weather', "
            f"'gym session', 'sad mood') rather than an explicit query, INFER an appropriate value "
            f"(e.g. a fitting song genre/mood).\n"
            f"Respond ONLY with a JSON object. No explanation."
        )
        try:
            resp = await self._llm_call(
                [{"role": "user", "content": prompt}],
                task="entity_extraction",
                temperature=0.1,
                max_tokens=120,
            )
            parsed = self.ai_handler.try_parse_json(resp)
            if isinstance(parsed, dict) and parsed:
                # Remove null values so callers can use .get() with defaults
                return {k: v for k, v in parsed.items() if v is not None}
        except Exception as e:
            logger.debug("[%s] _llm_parse failed: %s", self.AGENT_NAME, e)
        return {"_raw": message}


    async def _pre_tool_gate(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Hook — override in subclasses to gate tool execution (e.g. destructive-op confirmation).

        Returns (blocked, reason). When blocked=True the tool is NOT executed and the
        reason is fed back into the conversation so the LLM can respond accordingly.
        """
        return False, ""

    async def _use_tool(self, tool_name: str, **params) -> Any:
        if self._cancelled:
            return f"[Cancelled — {tool_name} not executed]"

        self._tool_calls_made += 1
        if self.guardrails is not None:
            try:
                from ..agent_guardrails import GuardrailExceeded
                reason = self.guardrails.check_limits(self._tool_calls_made, self._is_interactive)
                if reason:
                    self._cancelled = True
                    raise GuardrailExceeded(reason)
                # Duplicate-tool-call guard: 3 identical (tool, params) in a
                # row = stuck loop. Recorded per-agent; see AgentGuardrails.
                if hasattr(self.guardrails, "record_tool_call"):
                    self.guardrails.record_tool_call(self.AGENT_NAME, tool_name, params)
                    repeat_reason = self.guardrails.check_repetitive(self.AGENT_NAME)
                    if repeat_reason:
                        self._cancelled = True
                        raise GuardrailExceeded(repeat_reason)
            except ImportError:
                pass

        # Agent-local _TOOL_MAP takes precedence when present — tools defined
        # directly on the agent (e.g. system_agent's _TOOL_MAP) carry richer
        # implementations (alias maps, CDP ports) than any global registration.
        # Tools absent from the local map fall through to the ToolRegistry.
        local_map = getattr(self, "_TOOL_MAP", None)
        if local_map and tool_name in local_map:
            try:
                return await local_map[tool_name](**params)
            except TypeError as te:
                logger.error("[%s] Local tool '%s' parameter mismatch: %s", self.AGENT_NAME, tool_name, te)
                return f"[Tool error: {tool_name} — {te}]"
            except Exception as e:
                logger.error("[%s] Local tool '%s' failed: %s", self.AGENT_NAME, tool_name, e)
                return f"[Tool error: {tool_name} — {e}]"

        if not self.tool_registry:
            raise RuntimeError(f"Tool registry not available, cannot call {tool_name}")

        lock = await self._get_tool_lock(tool_name)
        
        # KAMI-14 FIX: Prevent lock starvation with 10s timeout and guaranteed release
        try:
            await asyncio.wait_for(lock.acquire(), timeout=10.0)
            try:
                res = await self.tool_registry.call_tool(tool_name, **params)
                return res
            except Exception as e:
                logger.error("[%s] Tool '%s' failed: %s", self.AGENT_NAME, tool_name, e)
                return f"[Tool error: {tool_name} — {e}]"
            finally:
                lock.release()
        except asyncio.TimeoutError:
            logger.error("[%s] Tool '%s' locked for too long (deadlock).", self.AGENT_NAME, tool_name)
            return f"[Tool error: {tool_name} — System deadlock detected]"

    async def _broadcast_status(self, task_id: str, status: str) -> None:
        if not self.ws_broadcast: return
        try:
            from ..ws_protocol import build_ai_chunk
            await self.ws_broadcast(build_ai_chunk(task_id, f" [{self.AGENT_NAME}: {status}] "))
        except Exception: pass

    async def _confirm_action(self, task_id: str, action: str, description: str,
                              risk_level: str = "medium", timeout: float = 60.0) -> bool:
        if not self.ws_broadcast: return False
        confirm_id = f"{task_id}_{action}"
        event = asyncio.Event()
        res = {"approved": False}
        BaseAgent._pending_confirmations[confirm_id] = (event, action, res)
        try:
            from ..ws_protocol import build_action_confirm
            await self.ws_broadcast(build_action_confirm(
                task_id=task_id, action=action, description=description, risk_level=risk_level,
            ))
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return res["approved"]
        except asyncio.TimeoutError:
            return False
        finally:
            BaseAgent._pending_confirmations.pop(confirm_id, None)

    @classmethod
    async def resolve_confirmation(cls, confirm_id: str, approved: bool) -> bool:
        entry = cls._pending_confirmations.get(confirm_id)
        if not entry: return False
        event, _, res = entry
        res["approved"] = approved
        event.set()
        return True

    @staticmethod
    def _jaccard_similarity(text1: str, text2: str) -> float:
        """Elite: Zero-dependency semantic similarity for context filtering."""
        set1 = set(text1.lower().split())
        set2 = set(text2.lower().split())
        if not set1 or not set2: return 0.0
        intersection = set1.intersection(set2)
        union = set1.union(set2)
        return len(intersection) / len(union)

    def _apply_attention(self, user_message: str, context: dict[str, Any]) -> dict[str, Any]:
        msg_lower = user_message.lower()
        filtered: dict[str, Any] = {}

        if (self.AGENT_NAME in self._SCREEN_AGENTS or any(t in msg_lower for t in _SCREEN_TRIGGERS)):
            screen = context.get("screen_context", "")
            if screen: filtered["screen_context"] = screen[:self.MAX_SCREEN_CHARS]

        if any(t in msg_lower for t in _CLIPBOARD_TRIGGERS):
            clip = context.get("clipboard", "")
            if clip: filtered["clipboard"] = clip[:self.MAX_CLIPBOARD_CHARS]
        if any(t in msg_lower for t in _DOCUMENT_TRIGGERS):
            doc = context.get("document", "")
            if doc: filtered["document"] = doc[:self.MAX_DOCUMENT_CHARS]

        memory = context.get("memory_results", [])
        if memory:
            scored = [(m, self._jaccard_similarity(user_message, str(m))) for m in memory]
            scored.sort(key=lambda x: x[1], reverse=True)
            msg_words = len(user_message.split())
            max_mem = min(self.MAX_MEMORY_RESULTS, max(2, 2 + msg_words // 5))
            filtered["memory_results"] = [m[0] for m in scored[:max_mem]]

        filtered["history"] = context.get("history", [])
        return filtered

    async def _llm_call(self, messages: list[dict], task: str = "general", **kwargs) -> str:
        res = await self._llm_call_raw(messages, task=task, **kwargs)
        return getattr(res, "text", str(res))

    async def _llm_parse(self, message: str, schema: dict, context_hint: str = "") -> dict:
        """Universal LLM-based parameter extractor — eliminates hardcoded regex in agents.

        Instead of regex-parsing "baarish ka mausam hai accha gaana bajao" and getting
        garbage, this asks the LLM: "what does the user want?" and gets back clean JSON.

        Args:
            message: Raw user message (full natural language, any language)
            schema: Dict describing what fields to extract, e.g.:
                {
                    "action": "one of: play|pause|next|volume",
                    "query": "search query — infer from mood/context if not explicit",
                    "connector": "spotify or youtube"
                }
            context_hint: Optional extra instruction for the LLM

        Returns:
            Parsed dict with extracted fields. Falls back to {"_raw": message} on failure.
            Never raises — always returns a dict.

        Example:
            params = await self._llm_parse(
                "baarish ka mausam hai accha gaana bajao",
                {"action": "play|pause|next", "query": "song/artist to search", "connector": "spotify|youtube"},
                context_hint="User may describe mood or weather — infer an appropriate music query from context"
            )
            # Returns: {"action": "play", "query": "rainy day songs", "connector": "spotify"}
        """
        schema_lines = "\n".join(f"  {k}: {v}" for k, v in schema.items())
        extra = f"\n\nExtra context: {context_hint}" if context_hint else ""
        prompt = (
            f"Extract structured parameters from this user message.\n"
            f"Message: '{message}'\n"
            f"Extract these fields (use null if not present):\n{schema_lines}{extra}\n\n"
            f"IMPORTANT: If the message describes a mood, situation, or context (e.g. 'rainy weather', "
            f"'gym session', 'sad mood') rather than an explicit query, INFER an appropriate value "
            f"(e.g. a fitting song genre/mood).\n"
            f"Respond ONLY with a JSON object. No explanation."
        )
        try:
            resp = await self._llm_call(
                [{"role": "user", "content": prompt}],
                task="entity_extraction",
                temperature=0.1,
                max_tokens=120,
            )
            parsed = self.ai_handler.try_parse_json(resp)
            if isinstance(parsed, dict) and parsed:
                # Remove null values so callers can use .get() with defaults
                return {k: v for k, v in parsed.items() if v is not None}
        except Exception as e:
            logger.debug("[%s] _llm_parse failed: %s", self.AGENT_NAME, e)
        return {"_raw": message}


    async def _pre_tool_gate(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Hook — override in subclasses to gate tool execution (e.g. destructive-op confirmation).

        Returns (blocked, reason). When blocked=True the tool is NOT executed and the
        reason is fed back into the conversation so the LLM can respond accordingly.
        """
        return False, ""

    async def _use_tool(self, tool_name: str, **params) -> Any:
        if self._cancelled:
            return f"[Cancelled — {tool_name} not executed]"

        self._tool_calls_made += 1
        if self.guardrails is not None:
            try:
                from ..agent_guardrails import GuardrailExceeded
                reason = self.guardrails.check_limits(self._tool_calls_made, self._is_interactive)
                if reason:
                    self._cancelled = True
                    raise GuardrailExceeded(reason)
                # Duplicate-tool-call guard: 3 identical (tool, params) in a
                # row = stuck loop. Recorded per-agent; see AgentGuardrails.
                if hasattr(self.guardrails, "record_tool_call"):
                    self.guardrails.record_tool_call(self.AGENT_NAME, tool_name, params)
                    repeat_reason = self.guardrails.check_repetitive(self.AGENT_NAME)
                    if repeat_reason:
                        self._cancelled = True
                        raise GuardrailExceeded(repeat_reason)
            except ImportError:
                pass

        # Agent-local _TOOL_MAP takes precedence when present — tools defined
        # directly on the agent (e.g. system_agent's _TOOL_MAP) carry richer
        # implementations (alias maps, CDP ports) than any global registration.
        # Tools absent from the local map fall through to the ToolRegistry.
        local_map = getattr(self, "_TOOL_MAP", None)
        if local_map and tool_name in local_map:
            try:
                return await local_map[tool_name](**params)
            except TypeError as te:
                logger.error("[%s] Local tool '%s' parameter mismatch: %s", self.AGENT_NAME, tool_name, te)
                return f"[Tool error: {tool_name} — {te}]"
            except Exception as e:
                logger.error("[%s] Local tool '%s' failed: %s", self.AGENT_NAME, tool_name, e)
                return f"[Tool error: {tool_name} — {e}]"

        if not self.tool_registry:
            raise RuntimeError(f"Tool registry not available, cannot call {tool_name}")

        lock = await self._get_tool_lock(tool_name)
        
        # KAMI-14 FIX: Prevent lock starvation with 10s timeout and guaranteed release
        try:
            await asyncio.wait_for(lock.acquire(), timeout=10.0)
            try:
                res = await self.tool_registry.call_tool(tool_name, **params)
                return res
            except Exception as e:
                logger.error("[%s] Tool '%s' failed: %s", self.AGENT_NAME, tool_name, e)
                return f"[Tool error: {tool_name} — {e}]"
            finally:
                lock.release()
        except asyncio.TimeoutError:
            logger.error("[%s] Tool '%s' locked for too long (deadlock).", self.AGENT_NAME, tool_name)
            return f"[Tool error: {tool_name} — System deadlock detected]"

    async def _broadcast_status(self, task_id: str, status: str) -> None:
        if not self.ws_broadcast: return
        try:
            from ..ws_protocol import build_ai_chunk
            await self.ws_broadcast(build_ai_chunk(task_id, f" [{self.AGENT_NAME}: {status}] "))
        except Exception: pass

    async def _confirm_action(self, task_id: str, action: str, description: str,
                              risk_level: str = "medium", timeout: float = 60.0) -> bool:
        if not self.ws_broadcast: return False
        confirm_id = f"{self.AGENT_NAME}_{task_id}_{action}"
        event = asyncio.Event()
        res = {"approved": False}
        BaseAgent._pending_confirmations[confirm_id] = (event, action, res)
        try:
            from ..ws_protocol import build_action_confirm
            await self.ws_broadcast(build_action_confirm(
                task_id=task_id, action=action, description=description, risk_level=risk_level,
            ))
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return res["approved"]
        except asyncio.TimeoutError:
            return False
        finally:
            BaseAgent._pending_confirmations.pop(confirm_id, None)

    @classmethod
    async def resolve_confirmation(cls, confirm_id: str, approved: bool) -> bool:
        entry = cls._pending_confirmations.get(confirm_id)
        if not entry: return False
        event, _, res = entry
        res["approved"] = approved
        event.set()
        return True

    @staticmethod
    def _jaccard_similarity(text1: str, text2: str) -> float:
        """Elite: Zero-dependency semantic similarity for context filtering."""
        set1 = set(text1.lower().split())
        set2 = set(text2.lower().split())
        if not set1 or not set2: return 0.0
        intersection = set1.intersection(set2)
        union = set1.union(set2)
        return len(intersection) / len(union)

    def _apply_attention(self, user_message: str, context: dict[str, Any]) -> dict[str, Any]:
        msg_lower = user_message.lower()
        filtered: dict[str, Any] = {}

        if (self.AGENT_NAME in self._SCREEN_AGENTS or any(t in msg_lower for t in _SCREEN_TRIGGERS)):
            screen = context.get("screen_context", "")
            if screen: filtered["screen_context"] = screen[:self.MAX_SCREEN_CHARS]

        if any(t in msg_lower for t in _CLIPBOARD_TRIGGERS):
            clip = context.get("clipboard", "")
            if clip: filtered["clipboard"] = clip[:self.MAX_CLIPBOARD_CHARS]

        if any(t in msg_lower for t in _DOCUMENT_TRIGGERS):
            doc = context.get("document", "")
            if doc: filtered["document"] = doc[:self.MAX_DOCUMENT_CHARS]

        memory = context.get("memory_results", [])
        if memory:
            scored = [(m, self._jaccard_similarity(user_message, str(m))) for m in memory]
            scored.sort(key=lambda x: x[1], reverse=True)
            msg_words = len(user_message.split())
            max_mem = min(self.MAX_MEMORY_RESULTS, max(2, 2 + msg_words // 5))
            filtered["memory_results"] = [m[0] for m in scored[:max_mem]]

        filtered["history"] = context.get("history", [])
        return filtered

    def _build_messages(self, user_message: str, context: dict, extra_system: str = "",
                         apply_attention: bool = True) -> list[dict]:
        ctx = self._apply_attention(user_message, context) if apply_attention else context
        system = self.SYSTEM_PROMPT
        if self._skill_content:
            system += self._skill_content
        if extra_system:
            system += f"\n\n{extra_system}"

        # ── User Style Preferences (from explicit feedback/regen signals) ───
        try:
            _prefs = context.get("style_prefs") if isinstance(context, dict) else None
            if not _prefs:
                _le_inst = getattr(self, "_learning_engine", None)
                if _le_inst:
                    _prefs = getattr(_le_inst, "cached_style_prefs", None) or getattr(_le_inst, "style_prefs", None)
            if _prefs and isinstance(_prefs, dict):
                pref_lines = "; ".join(f"{k}={v}" for k, v in _prefs.items())
                system += f"\n\n[USER STYLE PREFERENCES - follow these]\n{pref_lines}"
        except Exception:
            pass  # Never crash _build_messages

        # ── Implicit User Persona Summary (music taste, routines, tech prefs) ──
        try:
            _persona_text = ""
            _le_inst = getattr(self, "_learning_engine", None)
            if _le_inst and hasattr(_le_inst, "get_user_persona_summary"):
                # Run synchronously; get_user_persona_summary builds from cached traits
                # If still async, fall back to cached property
                _snapshot = getattr(_le_inst, "cached_user_persona", {}) or {}
                if _snapshot:
                    parts: list[str] = []
                    music = _snapshot.get("music_tastes") or {}
                    routines = _snapshot.get("daily_routines") or {}
                    tech = _snapshot.get("tech_preferences") or {}
                    style_info = _snapshot.get("style_and_language") or {}

                    artists = music.get("favorite_artists", [])
                    genres = music.get("genres", [])
                    moods = music.get("moods", [])
                    work_hours = routines.get("work_hours", "")
                    morning_habits = routines.get("morning_habits", [])
                    evening_habits = routines.get("evening_habits", [])
                    gym_sched = routines.get("gym_schedule", "")
                    preferred_os = tech.get("preferred_os", "")
                    prog_langs = tech.get("programming_languages", [])
                    editor = tech.get("editor", "")
                    comm_style = style_info.get("communication_style", "")
                    resp_len = style_info.get("response_length_preference", "")
                    tone = style_info.get("tone", "")

                    if artists:
                        parts.append(f"User enjoys music by {', '.join(str(a) for a in artists[:5])}.")
                    if genres:
                        parts.append(f"Prefers genres: {', '.join(str(g) for g in genres[:3])}.")
                    if moods:
                        parts.append(f"Often listens to {', '.join(str(m) for m in moods[:3])} moods.")
                    if work_hours:
                        parts.append(f"Works around {work_hours}.")
                    if morning_habits:
                        parts.append(f"Mornings: {', '.join(str(h) for h in morning_habits[:3])}.")
                    if evening_habits:
                        parts.append(f"Evenings: {', '.join(str(h) for h in evening_habits[:2])}.")
                    if gym_sched:
                        parts.append(f"Gym routine: {gym_sched}.")
                    if preferred_os:
                        parts.append(f"Uses {preferred_os}.")
                    if prog_langs:
                        parts.append(f"Codes in {', '.join(str(l) for l in prog_langs[:4])}.")
                    if editor:
                        parts.append(f"Editor: {editor}.")
                    if comm_style:
                        parts.append(f"User communicates {comm_style}-ly.")
                    if resp_len:
                        preference_map = {"short": "concise", "medium": "balanced", "long": "detailed"}
                        parts.append(f"Prefers {preference_map.get(resp_len, resp_len)} responses.")
                    if tone:
                        parts.append(f"Prefers a {tone} conversational tone.")

                    if parts:
                        _persona_text = " USER PROFILE & PERSONA (learned implicitly from chat):\n" + "\n".join(f"  • {p}" for p in parts)
                        logger.debug("[%s] Injected persona (%d traits)", self.AGENT_NAME, len(parts))

            if _persona_text:
                system += _persona_text
        except Exception:
            pass  # Never crash _build_messages — persona is best-effort

        if ctx.get("memory_results"):
            system += "\n\n[RELEVANT MEMORY]"
            for r in ctx["memory_results"]: system += f"\n- {r}"
        if ctx.get("screen_context"): system += f"\n\n[CURRENT SCREEN]\n{ctx['screen_context']}"
        if ctx.get("clipboard"): system += f"\n\n[CLIPBOARD]\n{ctx['clipboard']}"
        if ctx.get("document"): system += f"\n\n[DOCUMENT]\n{ctx['document']}"

        messages = [{"role": "system", "content": system}]
        for turn in ctx.get("history", [])[-6:]:
            if isinstance(turn, dict): messages.append(turn)
        messages.append({"role": "user", "content": user_message})
        return messages

    # ===========================================================================
    # PRODUCTION TOOL EXECUTION ENGINE  (v8.0 additions)
    # ===========================================================================

    async def _execute_with_tools(
        self,
        task_id: str,
        message: str,
        context: dict[str, Any],
        extra_system: str = "",
        max_turns: int = 6,
        task: str = "general",
        stream_progress: bool = True,
    ) -> str:
        """
        Production multi-turn ReAct loop. ALL agents should call this instead of
        writing their own one-shot LLM+tool loops.

        Flow:
          1. Agent-filtered manifest (not the full 60-tool list).
          2. LLM → tool_calls (native) or JSON fallback.
          3. Execute tools in parallel when safe.
          4. Feed results back — repeat until plain text reply or max_turns.
          5. Stream progress to frontend via ws.
        """
        import json

        tools_manifest: list[dict] = []
        if self.tool_registry:
            tools_manifest = self.tool_registry.get_manifest_for_agent(self.AGENT_NAME)

        messages = self._build_messages(message, context, extra_system=extra_system)

        for turn in range(max_turns):
            kwargs: dict[str, Any] = {}
            if tools_manifest:
                kwargs["tools"] = tools_manifest
                kwargs["tool_choice"] = "auto"

            resp = await self._llm_call_raw(messages, task=task, **kwargs)
            text: str = getattr(resp, "text", "") or ""
            raw_tool_calls: list[dict] = getattr(resp, "tool_calls", []) or []

            # PATH A: Native tool_calls
            if raw_tool_calls:
                if stream_progress:
                    names = ", ".join(tc.get("name", "?") for tc in raw_tool_calls)
                    await self._broadcast_status(task_id, f"calling tools: {names}")

                tool_results = await self._dispatch_tool_calls(raw_tool_calls, parallel=True)

                messages.append({
                    "role": "assistant",
                    "content": text or "",
                    "tool_calls": [
                        {"id": tc.get("id", f"call_{i}"), "type": "function",
                         "function": {"name": tc.get("name", ""), "arguments": tc.get("arguments", "{}")}}
                        for i, tc in enumerate(raw_tool_calls)
                    ],
                })
                for tc, tr in zip(raw_tool_calls, tool_results):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", "call_0"),
                        "content": str(tr)[:4000],
                    })

                failures = [(tc.get("name",""), tr) for tc, tr in zip(raw_tool_calls, tool_results) if self._tool_failed(tr)]
                if failures and turn < max_turns - 1:
                    fail_info = "; ".join(f"'{n}': {r[:100]}" for n, r in failures)
                    messages.append({"role": "user", "content":
                        f"[SYSTEM] Tool failures: {fail_info}. Try a different approach or parameters."})
                continue

            # PATH B: JSON fallback
            parsed = self.ai_handler.try_parse_json(text) if text else None
            if isinstance(parsed, dict) and parsed.get("tool"):
                tool_name = parsed["tool"]
                params = parsed.get("params", {})
                reply = parsed.get("reply", "")
                if stream_progress:
                    await self._broadcast_status(task_id, f"calling {tool_name}")
                blocked, reason = await self._pre_tool_gate(tool_name, params)
                if blocked:
                    tool_result = f"[BLOCKED] {reason}"
                else:
                    tool_result = await self._use_tool(tool_name, **params)
                self._partial_result = str(tool_result)
                if self._tool_failed(tool_result) and turn < max_turns - 1:
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content":
                        f"[SYSTEM TOOL ERROR] '{tool_name}' failed: '{tool_result}'. Try differently."})
                    continue
                return f"{tool_result}\n\n{reply}".strip() if reply else str(tool_result)

            # PATH C: Plain text reply — done
            if text:
                self._partial_result = text
                return text
            break

        return self._partial_result or "I could not complete that task."

    async def _dispatch_tool_calls(self, raw_tool_calls: list[dict], parallel: bool = True) -> list[str]:
        """Dispatch multiple tool calls, optionally in parallel."""
        import json as _json

        async def _call_one(tc: dict) -> str:
            name = tc.get("name", "")
            raw_args = tc.get("arguments", "{}")
            try:
                params = _json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except Exception:
                params = {}
            blocked, reason = await self._pre_tool_gate(name, params)
            if blocked:
                return f"[BLOCKED] {reason}"
            return await self._use_tool(name, **params)

        if parallel and len(raw_tool_calls) > 1:
            return list(await asyncio.gather(*[_call_one(tc) for tc in raw_tool_calls]))
        results = []
        for tc in raw_tool_calls:
            results.append(await _call_one(tc))
        return results

    async def _run_parallel_tools(self, tool_calls: list[tuple[str, dict]], timeout: float = 20.0) -> list[tuple[str, str]]:
        """Run (tool_name, params) pairs in parallel with per-call timeout."""
        async def _safe(name: str, params: dict) -> tuple[str, str]:
            try:
                res = await asyncio.wait_for(self._use_tool(name, **params), timeout=timeout)
                return name, str(res)
            except asyncio.TimeoutError:
                return name, f"[Tool timeout: {name}]"
            except Exception as e:
                return name, f"[Tool error: {name} - {e}]"
        return list(await asyncio.gather(*[_safe(n, p) for n, p in tool_calls]))

    async def _stream_progress(self, task_id: str, message: str) -> None:
        """Send real-time progress update to frontend via WebSocket."""
        if not self.ws_broadcast:
            return
        try:
            from ..ws_protocol import build_ai_chunk
            await self.ws_broadcast(build_ai_chunk(task_id, f"\n> {message}\n"))
        except Exception:
            pass
