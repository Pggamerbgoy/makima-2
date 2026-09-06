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
import re
import difflib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Optional

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

# ── Declarative Agent Tool Decorator ──────────────────────────────────────────
def agent_tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    category: str = "general",
    is_destructive: bool = False,
    is_deterministic: bool = False,
    parallel_safe: bool = True,
    risk_level: str = "LOW",
    schema: Optional[dict[str, Any]] = None,
):
    """Declarative decorator for agent tool functions with automated schema derivation."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._is_agent_tool = True
        func._tool_name = name or func.__name__
        func._tool_description = description or (func.__doc__ or "").strip()
        func._tool_category = category
        func._tool_is_destructive = is_destructive
        func._tool_is_deterministic = is_deterministic
        func._tool_parallel_safe = parallel_safe
        func._tool_risk_level = risk_level
        func._tool_schema = schema
        return func
    return decorator


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

# ---------------------------------------------------------------------------
# Shared prompt discipline block — appended to every agent's SYSTEM_PROMPT.
# Born from real failures: agents claiming success on unverified writes,
# hallucinating playback state, echoing internal plumbing strings to users.
# ---------------------------------------------------------------------------
TOOL_DISCIPLINE_BLOCK = """

STRUCTURED REASONING & TOOL AUDIT (MANDATORY BEFORE EVERY ACTION):
1. PRE-ACTION THINKING: Before calling ANY tool or taking any action, perform concise step-by-step reasoning in a <thinking>...</thinking> block:
   - Identify the user's primary goal.
   - Tool Audit: Review your available tools and choose the exact tool and parameters suited for the task.
   - Multi-Path Strategy: If the primary tool fails or encounters an auth/permission wall, identify the immediate alternative fallback path.
   - Desktop Agency: Never surrender or refuse upfront with generic AI excuses.
2. NO RAW JSON ENVELOPES: Never output raw JSON objects such as `{"tool": ...}` or `{"reply": ...}` to the user. Always reply in natural, clean prose with markdown.

TOOL DISCIPLINE & RESULT HONESTY (NON-NEGOTIABLE):
1. NEVER claim an action succeeded unless a tool result in THIS conversation confirms it (file written, message delivered, process killed, song playing). No tool result = no success claim.
2. If a tool errors, times out, or returns empty — say so plainly and offer the next step. Never paper over failures with optimism.
3. Report the ACTUAL absolute paths / IDs / values you used. For file saves, always state the full resolved path.
4. NEVER fabricate file contents, search results, metrics, statuses, or metadata (movie names, actor names, versions) you are not certain about.
5. INTERNAL PLUMBING STRINGS such as "[system: calling tools: ...]", "[media]: ...", "calling tools:", agent-activity markers are FORBIDDEN in replies. They belong to the activity feed only.

RESPONSE FORMATTING DISCIPLINE (CHATGPT & GEMINI SOTA STANDARD FOR ALL REPLIES):
1. STRUCTURAL HIERARCHY:
   - Use clean `## Level 2 Headers` for logical sectioning in deep, analytical, technical, or multi-step responses. Avoid raw `# Title` at the start of standard chat responses.
2. BOLD LEAD-IN BULLET POINTS:
   - When listing items, explaining features, or breaking down diagnostics, ALWAYS lead with a bold keyword followed by concise, high-signal explanation:
     - **Component / Metric**: 1–2 focused sentences explaining the result, status, or mechanism.
3. VISUALLY BREATHABLE PARAGRAPHS:
   - Keep prose paragraphs compact (2–3 sentences max) with clean blank lines between them. Never generate monolithic walls of text.
4. CODE & TECHNICAL BLOCKS:
   - All code, scripts, commands, and configs MUST use fenced code blocks with explicit language tags (` ```python `, ` ```bash `, ` ```json `, ` ```yaml `).
5. TABLES FOR COMPARISONS:
   - Use clean Markdown tables whenever comparing 2+ options, specifications, or benchmarks.
6. CALLOUTS & TAKEAWAYS:
   - Use blockquotes for key insights or warnings (`> 💡 **Key Takeaway**: ...`, `> ⚠️ **Caution**: ...`).
7. CASUAL & GREETING REPLIES:
   - Keep casual chat and greetings natural, warm, and concise (1–2 prose sentences) without unnecessary markdown, bullet lists, or headers.

LANGUAGE & TONE:
Mirror the user's language naturally (Hinglish/Hindi/English mix as they use it). Warm, witty, helpful; 1-2 emojis max; concise. Example vibe: "Ho gaya! ✅ File desktop pe save kar di — <full resolved absolute path>".
"""

class BaseAgent(ABC):
    AGENT_NAME: str = "base"
    DESCRIPTION: str = "Base agent"
    SYSTEM_PROMPT: str = "You are a helpful AI assistant."

    # Elite: Self-declared capabilities for the capability registry
    CAPABILITIES: list[str] = []   # e.g., ["web_search", "code_generation"]
    AGENT_TOOLS: list[str] = []    # e.g., ["web_search", "shell", "file_read"]
    TAGS: list[str] = []           # e.g., ["research", "web", "analysis"]
    DOMAIN_LANE: Optional[str] = None      # e.g., "media", "browser", "system", "cognitive", "communication"
    FALLBACK_AGENT: Optional[str] = None   # e.g., "research_agent" / "code_agent" fallback on circuit trip

    _pending_confirmations: ClassVar[dict[str, tuple[asyncio.Event, str, dict[str, bool]]]] = {}
    
    MAX_SCREEN_CHARS: int = 4000
    MAX_CLIPBOARD_CHARS: int = 2000
    MAX_DOCUMENT_CHARS: int = 6000
    MAX_MEMORY_RESULTS: int = 5
    
    _SCREEN_AGENTS: frozenset[str] = frozenset({"code", "automation", "system", "browser", "data_analyst", "security", "devops"})
    _DETERMINISTIC_ACTION_TOOLS: frozenset[str] = frozenset({
        "manage_window", "launch_app", "system_power", "set_process_priority",
        "kill_process", "organize_desktop", "move_file", "copy_file", "rename_file",
        "mouse_click", "mouse_draw", "play_media", "set_volume", "pause_media", "resume_media",
        "set_clipboard", "take_screenshot", "show_notification", "clean_temp_files", "snap_window"
    })
    _INFORMATIONAL_TOOLS: frozenset[str] = frozenset({
        "write_file", "read_file", "get_system_stats", "get_process_list",
        "get_window_list", "get_audio_state",
        "search_installed_apps", "network_diagnostics", "get_network_adapters",
        "web_search", "fetch_url", "memory_search", "graph_query"
    })

    def __init__(
        self,
        ai_handler: Any,
        memory: Any = None,
        tool_registry: Any = None,
        ws_broadcast: Optional[Callable[..., Awaitable[Any]]] = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        coordination: Any = None,
        learning_coordinator: Any = None,
        learning_engine: Any = None,
        **kwargs: Any,
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
        self._current_context: Optional[dict[str, Any]] = None

        # Elite: Async locks per tool to prevent race conditions in parallel subtasks
        self._tool_locks: dict[str, asyncio.Lock] = {}
        self._tool_locks_lock = asyncio.Lock()

        # Learning system — injected at init or by kernel
        self._learning_coordinator: Any = learning_coordinator
        # TODO: ReflexionEngine will handle this
        self._learning_engine: Any = None

        # Load SKILL.md from .agents/skills/<AGENT_NAME>-agent/SKILL.md and
        # inject into every LLM call via _build_messages.
        _raw_skill = _load_agent_skill(self.AGENT_NAME)
        if _raw_skill:
            self._skill_content = f"\n\n---\n## Agent Skill Guide\n{_raw_skill}\n---"
            logger.debug("[%s] Skill loaded (%d chars)", self.AGENT_NAME, len(_raw_skill))
        else:
            self._skill_content = ""

        # Auto-discover declarative @agent_tool methods
        self._auto_register_agent_tools()

    def _auto_register_agent_tools(self) -> None:
        """Discover methods decorated with @agent_tool and register with tool_registry if present."""
        for attr_name in dir(self):
            try:
                method = getattr(self, attr_name)
                if callable(method) and getattr(method, "_is_agent_tool", False):
                    t_name = getattr(method, "_tool_name", attr_name)
                    t_desc = getattr(method, "_tool_description", f"Agent tool {t_name}")
                    t_cat = getattr(method, "_tool_category", "general")
                    t_dest = getattr(method, "_tool_is_destructive", False)
                    t_det = getattr(method, "_tool_is_deterministic", False)
                    t_safe = getattr(method, "_tool_parallel_safe", True)
                    t_schema = getattr(method, "_tool_schema", None) or {"type": "object", "properties": {}}

                    if self.tool_registry and hasattr(self.tool_registry, "register_tool"):
                        has_tool = getattr(self.tool_registry, "has_tool", None)
                        if not (callable(has_tool) and has_tool(t_name)):
                            self.tool_registry.register_tool(
                                name=t_name,
                                description=t_desc,
                                func=method,
                                schema=t_schema,
                                category=t_cat,
                                agent_hints=[self.AGENT_NAME],
                                is_destructive=t_dest,
                                is_deterministic=t_det,
                                parallel_safe=t_safe,
                            )
            except Exception as _e:
                logger.debug("[%s] Tool discovery error for %s: %s", self.AGENT_NAME, attr_name, _e)

    # ── Lifecycle Hooks ────────────────────────────────────────────────────────
    async def on_task_start(self, task_id: str, message: str, context: dict[str, Any]) -> None:
        """Hook called immediately when task begins execution."""
        pass

    async def on_tool_execute(self, tool_name: str, params: dict[str, Any]) -> None:
        """Hook called immediately before physical tool invocation."""
        pass

    async def on_tool_result(self, tool_name: str, result: Any, is_error: bool) -> None:
        """Hook called immediately after tool completes."""
        pass

    async def on_task_complete(self, task_id: str, result: str, stats: dict[str, Any]) -> None:
        """Hook called when task completes."""
        pass

    @abstractmethod
    async def execute(self, task_id: str, message: str, context: dict[str, Any],
                      entities: dict[str, Any]) -> str:
        pass

    def get_or_create_sdk_agent(
        self,
        task_type: str = "fast_chat",
        extra_system: str = "",
        input_guardrails: Optional[list[Any]] = None,
        output_guardrails: Optional[list[Any]] = None,
    ) -> Any:
        """
        Return a cached or newly constructed OpenAI Agents SDK Agent instance.
        Caches the compiled Agent on self._cached_sdk_agent to eliminate per-turn tool/schema compilation overhead.
        """
        if getattr(self, "_cached_sdk_agent", None) is not None and not extra_system:
            return self._cached_sdk_agent

        from agents import Agent
        from ..core.sdk_bridge import (
            MakimaModel,
            to_sdk_tools,
            dangerous_command_guard,
            fabrication_guard,
        )

        sdk_tools = to_sdk_tools(
            self.AGENT_TOOLS,
            registry=self.tool_registry,
            agent=self,
        )
        model_bridge = MakimaModel(
            ai_handler=self.ai_handler,
            task=task_type,
            agent_name=self.AGENT_NAME,
        )
        instructions = self.SYSTEM_PROMPT
        if self._skill_content:
            instructions += self._skill_content
        if extra_system:
            instructions += f"\n\n{extra_system}"

        # Default guardrails based on agent domain
        in_guards = input_guardrails if input_guardrails is not None else [dangerous_command_guard]
        out_guards = output_guardrails if output_guardrails is not None else [fabrication_guard]

        agent_name_clean = getattr(self, "AGENT_NAME", "Specialist").title().replace("_", "")
        if not agent_name_clean.endswith("Agent"):
            agent_name_clean += "Agent"

        agent_inst = Agent(
            name=agent_name_clean,
            instructions=instructions,
            tools=sdk_tools,
            model=model_bridge,
            input_guardrails=in_guards,
            output_guardrails=out_guards,
        )

        if not extra_system:
            self._cached_sdk_agent = agent_inst
        return agent_inst

    async def run_sdk_execution(
        self,
        task_id: str,
        message: str,
        context: Optional[dict[str, Any]] = None,
        *,
        max_turns: int = 8,
        task_type: str = "fast_chat",
        extra_system: str = "",
        input_guardrails: Optional[list[Any]] = None,
        output_guardrails: Optional[list[Any]] = None,
    ) -> str:
        """
        Execute an agent task using OpenAI Agents SDK with high-performance optimizations:
        - Cached compiled SDK Agent instance.
        - ToolExecutionConfig for concurrent function tool dispatch.
        - ToolOutputTrimmer to compact earlier tool outputs and minimize prompt bloat.
        - Real-time token streaming to WebSocket if broadcast is available.
        - SQLiteSession lifecycle cleanup (session.close() guaranteed in finally).
        """
        self._reset_state()
        ctx = context or {}
        self._current_context = ctx
        self._current_task_id = task_id

        try:
            await self.on_task_start(task_id, message, ctx)
        except Exception:
            pass

        from agents import Runner, InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered
        from ..core.sdk_bridge import get_sdk_session, get_default_run_config

        sdk_agent = self.get_or_create_sdk_agent(
            task_type=task_type,
            extra_system=extra_system,
            input_guardrails=input_guardrails,
            output_guardrails=output_guardrails,
        )

        session = get_sdk_session(task_id)
        run_cfg = get_default_run_config(max_concurrency=4, timeout=45.0)

        accumulated_tokens: list[str] = []
        try:
            run_stream = Runner.run_streamed(
                sdk_agent,
                message,
                context=ctx,
                session=session,
                max_turns=max_turns,
                run_config=run_cfg,
            )

            async for ev in run_stream.stream_events():
                if hasattr(ev, "data"):
                    d = ev.data
                    d_type = getattr(d, "type", "")
                    if d_type == "response.output_text.delta":
                        delta_text = getattr(d, "delta", "")
                        if delta_text:
                            accumulated_tokens.append(delta_text)
                            if self.ws_broadcast:
                                try:
                                    from ..ws_protocol import build_ai_chunk
                                    await self.ws_broadcast(build_ai_chunk(task_id, delta_text, is_final=False))
                                except Exception:
                                    pass
                    elif d_type == "response.output_text.done":
                        if accumulated_tokens and not accumulated_tokens[-1].endswith("\n"):
                            accumulated_tokens.append("\n\n")

            final_out = "".join(accumulated_tokens).strip()
            if not final_out:
                final_out = str(getattr(run_stream, "final_output", "") or "Operation completed.")

            self._partial_result = final_out
            try:
                await self.on_task_complete(task_id, final_out, self.get_execution_stats())
            except Exception:
                pass
            return final_out

        except InputGuardrailTripwireTriggered as in_guard:
            logger.warning("[%s] Input guardrail tripped: %s", self.AGENT_NAME, in_guard)
            blocked_msg = "Yeh action security policy ki wajah se block kar di gayi hai."
            self._partial_result = blocked_msg
            try:
                await self.on_task_complete(task_id, blocked_msg, self.get_execution_stats())
            except Exception:
                pass
            return blocked_msg
        except OutputGuardrailTripwireTriggered as out_guard:
            logger.warning("[%s] Output guardrail tripped: %s", self.AGENT_NAME, out_guard)
            safe_msg = "Action execute nahi ho saki ya tools ne confirm nahi kiya."
            self._partial_result = safe_msg
            try:
                await self.on_task_complete(task_id, safe_msg, self.get_execution_stats())
            except Exception:
                pass
            return safe_msg
        finally:
            try:
                if hasattr(session, "close"):
                    session.close()
            except Exception:
                pass

    def set_execution_mode(self, is_interactive: bool) -> None:
        self._is_interactive = is_interactive

    def set_execution_task(self, task: Any) -> None:
        """Accept either a canonical AgentTask contract or a legacy string task_id."""
        if hasattr(task, "task_id"):
            self._current_agent_task = task
            self._current_task_id = getattr(task, "task_id", "task_default")
        elif isinstance(task, str):
            self._current_task_id = task

    @property
    def current_agent_task(self) -> Optional[Any]:
        """Access the canonical typed AgentTask for the active execution."""
        return getattr(self, "_current_agent_task", None)

    def get_execution_task(self) -> Optional[str]:
        """Get the current execution task_id string."""
        return getattr(self, "_current_task_id", None)

    def _reset_state(self) -> None:
        self._partial_result = ""
        self._tokens_used = 0
        self._tool_calls_made = 0
        self._cancelled = False
        self._current_agent_task = None
        self._exec_start = time.monotonic()
        self._current_context = None
        self._current_agent_task = None


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
        if (
            s.startswith("[tool error")
            or s.startswith("error:")
            or s.startswith("[error]")
            or s.startswith("exception:")
            or s.startswith("[failed]")
            or s.startswith("[blocked")
            or s.startswith("[verification failed")
            or s.startswith("[physical verification failed")
        ):
            return True
        return any(x in s for x in [
            "[tool error", "[failed]", "[blocked", "error executing tool", "vision click failed",
            "captcha detected", "[cancelled]", "[timeout]", "[nav_failed]",
            "physical verification failed", "verification failed"
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

        if "agent_name" not in kwargs and getattr(self, "AGENT_NAME", None):
            kwargs["agent_name"] = self.AGENT_NAME

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
                require_json=True,
                temperature=0.1,
                max_tokens=250,
            )
            parsed = self.ai_handler.try_parse_json(resp)
            if isinstance(parsed, dict) and parsed:
                # Remove null values so callers can use .get() with defaults
                return {k: v for k, v in parsed.items() if v is not None}
        except Exception as e:
            logger.debug("[%s] _llm_parse failed: %s", self.AGENT_NAME, e)
        return {"_raw": message}


    # [FALLBACK & GUARDRAILS ONLY — Used during legacy tool invocation and guardrails check]
    async def _pre_tool_gate(self, tool_name: str, params: dict[str, Any], context: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
        """Hook & Generic Semantic Compatibility Gate (Fallback & Security).

        Evaluates proposed tool execution against:
        1. Subclass security gates (e.g. destructive-op confirmation in SystemAgent).
        2. Generic Semantic Compatibility (grounded polarity, desired state, negative constraints).

        Returns (blocked, reason). When blocked=True the tool is NOT executed and the
        reason is fed back into the conversation so the LLM can respond or replan accordingly.
        """
        # Step 1: Semantic compatibility verification
        context = context or getattr(self, "_current_context", {}) or {}
        agent_task = context.get("agent_task") or getattr(self, "_current_agent_task", None)

        if context.get("grounded_slots") or agent_task:
            slots = dict(context.get("grounded_slots") or {})
            if agent_task and hasattr(agent_task, "parameters") and agent_task.parameters:
                slots.update(agent_task.parameters)

            if isinstance(slots, dict) or agent_task:
                cap = None
                if self.tool_registry and hasattr(self.tool_registry, "get_capability"):
                    cap = self.tool_registry.get_capability(tool_name, params)

                polarity = slots.get("polarity", "ALLOW")
                target = str(slots.get("target") or (getattr(agent_task, "target_entity", "") if agent_task else "") or "").strip().lower()
                desired_state = str(slots.get("desired_state") or "").strip().lower()
                constraints = list(slots.get("constraints", []) or [])
                if agent_task and hasattr(agent_task, "negative_constraints") and agent_task.negative_constraints:
                    for nc in agent_task.negative_constraints:
                        if nc not in constraints:
                            constraints.append(nc)
                param_str = str(params).lower()

                def _matches_entity(entity: str, text: str) -> bool:
                    if not entity or not text:
                        return False
                    entity_l = entity.lower().strip()
                    text_l = text.lower().strip()
                    if entity_l in text_l or text_l in entity_l:
                        return True

                    # Generalized binary / app name normalization (strips .exe, punctuation, spaces)
                    entity_clean = re.sub(r'[\s._\-]+', '', entity_l.replace('.exe', ''))
                    text_clean = re.sub(r'[\s._\-]+', '', text_l.replace('.exe', ''))
                    if entity_clean and text_clean and (entity_clean in text_clean or text_clean in entity_clean):
                        return True

                    # Word-level fuzzy similarity for typos (e.g. "roblx" <-> "roblox")
                    tokens = [t for t in re.split(r'[^a-z0-9]+', text_l) if len(t) >= 3]
                    for tok in tokens:
                        if len(tok) >= 3 and len(entity_l) >= 3:
                            ratio = difflib.SequenceMatcher(None, entity_l, tok).ratio()
                            if ratio >= 0.78:
                                return True
                    return False


                # Rule 0: Specific Negative Constraints (e.g. "not_vscode", "not VSCode", "not youtube", "avoid Chrome")
                for c in constraints:
                    if isinstance(c, str):
                        c_clean = c.lower().strip()
                        excluded_entity = ""
                        if c_clean.startswith("not_"):
                            excluded_entity = c_clean[4:].strip()
                        elif c_clean.startswith("not "):
                            excluded_entity = c_clean[4:].strip()
                        elif c_clean.startswith("avoid "):
                            excluded_entity = c_clean[6:].strip()
                        elif c_clean.startswith("keep ") and " running" in c_clean:
                            excluded_entity = c_clean[5:].replace(" running", "").strip()

                        if excluded_entity and (_matches_entity(excluded_entity, param_str) or _matches_entity(excluded_entity, tool_name)):
                            return True, f"[BLOCKED by Semantic Compatibility Gate]: Execution matches negative constraint '{c}' (target: '{excluded_entity}')."

                # Rule 1: Negative Constraint / Deny Polarity
                if str(polarity).upper() in ("DENY", "NEGATIVE"):
                    is_affirmative = (getattr(cap, "polarity", "") == "affirmative") if cap else bool(target and (_matches_entity(target, param_str) or _matches_entity(target, tool_name)))
                    if is_affirmative:
                        if not target or _matches_entity(target, param_str) or _matches_entity(target, tool_name) or (cap and (_matches_entity(target, getattr(cap, "domain", "")) or _matches_entity(target, getattr(cap, "target_type", "")))):
                            op_name = getattr(cap, "operation", "execute") if cap else "execute"
                            tgt_name = target or (getattr(cap, "domain", "") if cap else "resource")
                            return True, f"[BLOCKED by Semantic Compatibility Gate]: User explicitly requested NOT to {op_name} '{tgt_name}'."

                # Rule 2: Desired State Inversion Protection
                if desired_state and cap:
                    trans = getattr(cap, "state_transition", ("", ""))
                    to_state = str(trans[1] if len(trans) > 1 else "").strip().lower()
                    if desired_state in ("not_playing", "stopped", "paused", "closed", "iconic", "sleep", "off", "terminated"):
                        if to_state in ("playing", "running", "open", "maximized", "foreground") or getattr(cap, "polarity", "") == "affirmative":
                            return True, f"[BLOCKED by Semantic Compatibility Gate]: Proposed tool '{tool_name}' transitions state to '{to_state or 'active'}', which contradicts desired state '{desired_state}'."
                    elif desired_state in ("playing", "running", "open", "maximized", "foreground"):
                        if to_state in ("stopped", "paused", "closed", "off", "terminated") or getattr(cap, "polarity", "") == "negative":
                            return True, f"[BLOCKED by Semantic Compatibility Gate]: Proposed tool '{tool_name}' transitions state to '{to_state or 'inactive'}', which contradicts desired state '{desired_state}'."

        # Step 2: Deterministic Precondition Check
        try:
            from ..core.invariant_verifier import InvariantVerifier
            pre_ok, pre_reason = await InvariantVerifier.verify_precondition(tool_name, params)
            if not pre_ok:
                return True, f"[BLOCKED by Precondition Gate]: {pre_reason}"
        except Exception:
            pass

        return False, ""

    def _is_deterministic_action(self, tool_name: str) -> bool:
        """
        Dynamically determine if a tool call is a deterministic action 
        via ToolRegistry metadata, CapabilityMesh, agent method reflection, or fallback set.
        """
        if not tool_name or tool_name in self._INFORMATIONAL_TOOLS:
            return False
        clean_name = tool_name.replace("call_", "").strip()

        # 1. Query dynamic ToolRegistry metadata
        if self.tool_registry:
            tools_map = getattr(self.tool_registry, "_tools", {})
            meta = tools_map.get(clean_name) or tools_map.get(tool_name)
            if meta and getattr(meta, "is_deterministic", False) is True:
                return True
            if hasattr(self.tool_registry, "get_capability"):
                cap = self.tool_registry.get_capability(clean_name) or self.tool_registry.get_capability(tool_name)
                if cap and getattr(cap, "is_deterministic", False) is True:
                    return True
            if hasattr(self.tool_registry, "is_deterministic"):
                if (self.tool_registry.is_deterministic(clean_name) is True) or (self.tool_registry.is_deterministic(tool_name) is True):
                    return True

        # 2. Query agent method reflection / function attribute
        method = getattr(self, f"_tool_{clean_name}", None) or getattr(self, f"_tool_{tool_name}", None)
        if method and (
            getattr(method, "is_deterministic", False) is True
            or getattr(method, "_deterministic", False) is True
            or getattr(method, "_tool_is_deterministic", False) is True
        ):
            return True

        # 3. Check registered agent method metadata
        if hasattr(self, "_TOOL_MAP"):
            handler = self._TOOL_MAP.get(clean_name) or self._TOOL_MAP.get(tool_name)
            if handler and getattr(handler, "_tool_is_deterministic", False) is True:
                return True

        # 4. Fallback check for backward compatibility
        return clean_name in self._DETERMINISTIC_ACTION_TOOLS or tool_name in self._DETERMINISTIC_ACTION_TOOLS

    async def _use_tool(self, tool_name: str, context: Optional[dict[str, Any]] = None, **params) -> Any:
        if self._cancelled:
            return f"[Cancelled — {tool_name} not executed]"

        # Acquire tool mutex lock to prevent race conditions during concurrent execution
        tool_lock = await self._get_tool_lock(tool_name)
        async with tool_lock:
            # Central Semantic Compatibility Gate & Pre-Tool Hook
            eff_context = context if context is not None else getattr(self, "_current_context", None)
            blocked, reason = await self._pre_tool_gate(tool_name, params, context=eff_context)
            if blocked:
                logger.warning("[%s] Tool '%s' BLOCKED by Semantic Compatibility Gate: %s", self.AGENT_NAME, tool_name, reason)
                return reason if reason.startswith("[BLOCKED") else f"[BLOCKED] {reason}"

            self._tool_calls_made += 1
            if self.guardrails is not None:
                try:
                    from ..core.contracts import GuardrailExceeded
                    reason = self.guardrails.check_limits(self._tool_calls_made, self._is_interactive)
                    if reason:
                        self._cancelled = True
                        raise GuardrailExceeded(reason)
                    if hasattr(self.guardrails, "record_tool_call"):
                        self.guardrails.record_tool_call(self.AGENT_NAME, tool_name, params)
                        repeat_reason = self.guardrails.check_repetitive(self.AGENT_NAME)
                        if repeat_reason:
                            self._cancelled = True
                            raise GuardrailExceeded(repeat_reason)
                except ImportError:
                    pass

            # Lifecycle hook: on_tool_execute
            try:
                await self.on_tool_execute(tool_name, params)
            except Exception:
                pass

            # Instantiate unified transactional ToolContext execution envelope
            try:
                import uuid
                from ..tools.types import ToolContext
                slots_data = eff_context.get("grounded_slots") if isinstance(eff_context, dict) else {}
                cap_data = self.tool_registry.get_capability(tool_name, params) if (self.tool_registry and hasattr(self.tool_registry, "get_capability")) else None
                self._last_execution_context = ToolContext(
                    consumer=self.AGENT_NAME,
                    task_id=getattr(self, "_current_task_id", "") or "task_default",
                    execution_id=f"exec_{uuid.uuid4().hex[:10]}",
                    grounded_spec=slots_data if isinstance(slots_data, dict) else None,
                    capability=cap_data,
                )
            except Exception:
                self._last_execution_context = None

            # Canonical v9 ExecutionRuntime Delegation
            from ..core.contracts import Action
            from ..core.execution_runtime import ExecutionRuntime

            runtime = getattr(self, "_execution_runtime", None)
            if not runtime:
                orch = getattr(self, "orchestrator", None)
                if orch and hasattr(orch, "execution_runtime") and orch.execution_runtime:
                    runtime = orch.execution_runtime
                    self._execution_runtime = runtime
                else:
                    lc = getattr(orch, "learning_coordinator", None) if orch else None
                    saga_rec = (
                        getattr(orch, "saga_recovery", None)
                        or getattr(orch, "recovery_manager", None)
                        if orch else None
                    )
                    gr = getattr(orch, "guardrails", None) if orch else None
                    runtime = ExecutionRuntime(
                        tool_registry=self.tool_registry,
                        learning_coordinator=lc,
                        saga_recovery=saga_rec,
                        kernel=orch,
                        guardrails=gr,
                    )
                    self._execution_runtime = runtime

            t_task_id = getattr(self, "_current_task_id", "") or "task_default"
            t0_exec = time.perf_counter()

            if self.ws_broadcast:
                try:
                    from ..ws_protocol import build_tool_call_started
                    await self.ws_broadcast(build_tool_call_started(t_task_id, tool_name, params, agent=self.AGENT_NAME))
                except Exception:
                    pass

            action = Action(
                action_id=f"act_{tool_name}_{time.time_ns()}",
                task_id=t_task_id,
                capability_name=tool_name,
                parameters=params,
            )

            exec_res = await runtime.execute_action(
                action=action,
                context=self._last_execution_context,
                local_tool_map=getattr(self, "_TOOL_MAP", None),
            )

            is_err = not exec_res.is_success
            if is_err:
                rec_report = exec_res.metadata.get("recovery_report")
                if rec_report:
                    res_str = f"[Failed] {exec_res.error} | Recovery: {rec_report}"
                else:
                    res_str = f"[Failed] {exec_res.error}"
            elif exec_res.metadata.get("recovery_strategy"):
                rec_strat = exec_res.metadata.get("recovery_strategy")
                logger.info("[%s] Tool '%s' successfully recovered via strategy '%s'", self.AGENT_NAME, tool_name, rec_strat)
                if isinstance(exec_res.tool_output, (dict, list)):
                    res_str = json.dumps(exec_res.tool_output, ensure_ascii=False)
                else:
                    res_str = str(exec_res.tool_output or "")
            elif isinstance(exec_res.tool_output, (dict, list)):
                res_str = json.dumps(exec_res.tool_output, ensure_ascii=False)
            else:
                res_str = str(exec_res.tool_output or "")
            duration_ms = (time.perf_counter() - t0_exec) * 1000.0

            if self.ws_broadcast:
                try:
                    from ..ws_protocol import build_tool_call_finished
                    await self.ws_broadcast(
                        build_tool_call_finished(
                            t_task_id,
                            tool_name,
                            result=res_str,
                            duration_ms=duration_ms,
                            is_success=not is_err,
                            agent=self.AGENT_NAME,
                        )
                    )
                except Exception:
                    pass

            # Lifecycle hook: on_tool_result
            try:
                await self.on_tool_result(tool_name, res_str, is_err)
            except Exception:
                pass

            return res_str

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
        # Register exact alias keys for lookup
        BaseAgent._pending_confirmations[task_id] = (event, action, res)
        BaseAgent._pending_confirmations[f"{self.AGENT_NAME}_{task_id}_{action}"] = (event, action, res)
        try:
            from ..ws_protocol import build_action_confirm
            await self.ws_broadcast(build_action_confirm(
                task_id=task_id, action=action, description=description, risk_level=risk_level,
            ))
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return res["approved"]
        except asyncio.TimeoutError:
            logger.warning("[%s] Confirmation timed out for %s", self.AGENT_NAME, confirm_id)
            return False
        finally:
            BaseAgent._pending_confirmations.pop(confirm_id, None)
            BaseAgent._pending_confirmations.pop(task_id, None)
            BaseAgent._pending_confirmations.pop(f"{self.AGENT_NAME}_{task_id}_{action}", None)

    @classmethod
    async def resolve_confirmation(cls, confirm_id: str, approved: bool) -> bool:
        entry = cls._pending_confirmations.get(confirm_id)
        if not entry:
            # Suffix or exact prefix match to avoid loose substring collisions
            for k, val in list(cls._pending_confirmations.items()):
                if k == confirm_id or k.endswith(f"_{confirm_id}") or confirm_id.endswith(f"_{k}"):
                    entry = val
                    break
        if not entry:
            logger.warning("[BaseAgent] No pending confirmation found for: %s. Active pending keys: %s", confirm_id, list(cls._pending_confirmations.keys()))
            return False
        event, _, res = entry
        res["approved"] = approved
        event.set()
        return True

    REQUIRED_CONTEXT: tuple[str, ...] = ()

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
        """Apply structured attention and memory relevance scoring."""
        msg_lower = user_message.lower()
        filtered: dict[str, Any] = {}

        if self.AGENT_NAME in self._SCREEN_AGENTS or any(t in msg_lower for t in _SCREEN_TRIGGERS) or context.get("force_screen"):
            screen = context.get("screen_context", "")
            if screen: filtered["screen_context"] = str(screen)[:self.MAX_SCREEN_CHARS]

        if any(t in msg_lower for t in _CLIPBOARD_TRIGGERS) or context.get("force_clipboard"):
            clip = context.get("clipboard", "")
            if clip: filtered["clipboard"] = str(clip)[:self.MAX_CLIPBOARD_CHARS]

        if any(t in msg_lower for t in _DOCUMENT_TRIGGERS) or context.get("force_document"):
            doc = context.get("document", "")
            if doc: filtered["document"] = str(doc)[:self.MAX_DOCUMENT_CHARS]

        # Jaccard relevance scoring for memory results
        memory = context.get("memory_results", [])
        if memory and isinstance(memory, list):
            scored = [(m, self._jaccard_similarity(user_message, str(m))) for m in memory]
            scored.sort(key=lambda x: x[1], reverse=True)
            msg_words = len(user_message.split())
            max_mem = min(self.MAX_MEMORY_RESULTS, max(2, 2 + msg_words // 5))
            filtered["memory_results"] = [m[0] for m in scored[:max_mem]]

        filtered["history"] = context.get("history", [])
        return filtered


    def _build_messages(self, user_message: str, context: dict, extra_system: str = "",
                         apply_attention: bool = True) -> list[dict]:
        if isinstance(context, dict):
            self._current_context = context
        ctx = self._apply_attention(user_message, context) if apply_attention else context
        system = self.SYSTEM_PROMPT
        if self._skill_content:
            system += self._skill_content
        if extra_system:
            system += f"\n\n{extra_system}"

        # Canonical v9 ContextBuilder delegation
        from ..core.context_builder import ContextBuilder
        builder = getattr(self, "_context_builder", None) or ContextBuilder()
        eff_context = dict(context or {})
        if isinstance(ctx, dict):
            eff_context.update(ctx)
        # TODO: ReflexionEngine will handle this

        req_tiers = getattr(self, "REQUIRED_CONTEXT", None)
        return builder.build_messages(
            user_message=user_message,
            agent_name=self.AGENT_NAME,
            system_persona=system,
            context=eff_context,
            extra_system=extra_system,
            required_context_tiers=list(req_tiers) if req_tiers else None,
        )

    # ===========================================================================
    @staticmethod
    def _format_tool_result_for_context(tool_result: Any, max_chars: int = 24000) -> str:
        """Format tool result for LLM context with structure-preserving truncation."""
        tr_str = str(tool_result or "")
        if len(tr_str) <= max_chars:
            return tr_str
        truncated = tr_str[:max_chars]
        last_nl = truncated.rfind("\n")
        if last_nl > max_chars * 0.7:
            truncated = truncated[:last_nl]
        if truncated.count("```") % 2 != 0:
            truncated += "\n```"
        return f"{truncated}\n[...truncated {len(tr_str) - len(truncated)} chars for context efficiency]"
