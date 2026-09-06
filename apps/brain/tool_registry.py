"""
Makima v8.0 — Production Tool Registry

Improvements:
  - ToolMeta: category, agent_hints, task_tags, priority, is_destructive
  - Per-agent filtered manifest — LLM only sees tools relevant to current agent
  - SOTA Dynamic ACI Distillation (SWE-agent & Gorilla): query-based tool pruning
  - Universal Parameter Normalization & Aliasing
  - Manifest cache — rebuilt only when a new tool is registered
  - Call telemetry — per-tool call_count, failure_count, avg_latency_ms
  - Parallel tool call dispatch — run multiple tools concurrently
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional, Sequence

from .tools.types import Tool, ToolCapability, ToolContext, ToolDefinition, ToolPolicy, ToolResult, GroundedSemanticSpec

logger = logging.getLogger("makima.tool_registry")


def is_tool_enabled_for_query(
    tool_name: str,
    query: str,
    category: str = "general",
    task_tags: Optional[list[str]] = None,
) -> bool:
    """
    Dynamic category & intent-based tool enabling predicate (OpenAI Agents SDK pattern).
    
    Rules:
      - Media tools: is_enabled only when query contains 'music/gaana/volume/song/play/spotify/youtube'
      - Browser tools: is_enabled only when query contains 'browser/open/search/website/url'
      - System tools: is_enabled when query contains 'open/kholo/launch/app/window/screenshot'
      - Code tools: is_enabled when query contains 'code/python/script/bug/fix'
      - MCP tools: filtered by domain (media MCP requires media keywords, etc.)
    """
    if not query:
        return True

    q_lower = query.lower()
    t_name = tool_name.lower().replace("call_", "").strip()
    tags = [t.lower() for t in (task_tags or [])]
    cat = (category or "general").lower()

    # If the user explicitly mentions the tool name, always enable it
    if t_name in q_lower or t_name.replace("_", " ") in q_lower:
        return True

    # 1. System / Window / Filesystem Tools Filter
    is_system_tool = (
        cat in ("system", "window", "filesystem")
        or t_name.startswith("system_")
        or any(tag in ("system", "window", "file", "process", "power") for tag in tags)
    )
    if is_system_tool:
        system_keywords = (
            "open", "launch", "kholo", "chalao", "start", "app", "window", "close",
            "band", "kill", "process", "screenshot", "screen", "clipboard", "clean",
            "temp", "power", "restart", "shutdown", "sleep", "specs", "ram", "cpu",
            "file", "folder", "copy", "move", "directory", "disk", "stats", "hardware",
            "system", "memory", "computer", "pc", "laptop", "specifications", "status",
            "performance", "battery", "usage", "load", "task", "monitor"
        )
        return any(kw in q_lower for kw in system_keywords)

    # 2. Media Tools Filter (music / gaana / volume)
    is_media_tool = (
        cat == "media"
        or t_name.startswith("media_")
        or t_name in ("set_volume", "get_volume", "mute", "unmute")
        or (cat not in ("system", "window", "filesystem", "code", "devops", "browser")
            and any(tag in ("media", "music", "spotify", "youtube", "audio") for tag in tags))
    )
    if is_media_tool:
        media_keywords = (
            "music", "gaana", "gaane", "gana", "volume", "song", "songs", "play", "pause", "resume",
            "track", "spotify", "youtube", "audio", "sound", "seek", "mute", "unmute",
            "bja", "bjao", "bajao", "sunao", "sunwao", "chalao", "awaz", "awaaz", "next", "previous",
            "playlist", "listen", "video", "yt", "singer", "artist"
        )
        return any(kw in q_lower for kw in media_keywords)

    # 3. Browser Tools Filter (browser / open / search)
    is_browser_tool = (
        cat == "browser"
        or t_name.startswith("browser_")
        or any(tag in ("browser", "web", "scrape", "search") for tag in tags)
    )
    if is_browser_tool:
        browser_keywords = (
            "browser", "open", "search", "url", "website", "site", "web", "scrape",
            "click", "google", "navigate", "chrome", "firefox", "edge", "brave", "kholo",
            "dhoondo", "visit", "browse", "page", "youtube", "spotify", "music", "song",
            "songs", "gaana", "gaane", "gana", "play", "bja", "bjao", "bajao", "sunao",
            "video", "audio", "listen", "chalao"
        )
        return any(kw in q_lower for kw in browser_keywords)

    # 4. Code / DevOps Tools Filter
    is_code_tool = (
        cat in ("code", "devops")
        or any(tag in ("code", "dev", "git", "bash", "terminal") for tag in tags)
    )
    if is_code_tool:
        code_keywords = (
            "code", "python", "script", "file", "write", "bug", "refactor",
            "fix", "test", "git", "bash", "run", "terminal", "docker", "likho"
        )
        return any(kw in q_lower for kw in code_keywords)

    # 5. MCP Tools Filter
    if cat == "mcp" or t_name.startswith("mcp_"):
        # If it's a media-related MCP tool, apply media keywords
        if any(tag in ("media", "music", "audio") for tag in tags) or "media" in t_name or "spotify" in t_name or "youtube" in t_name:
            media_keywords = ("music", "gaana", "gana", "volume", "song", "play", "spotify", "youtube", "sound", "audio")
            return any(kw in q_lower for kw in media_keywords)
        # If it's a browser-related MCP tool, apply browser keywords
        if any(tag in ("browser", "web") for tag in tags) or "browser" in t_name or "puppeteer" in t_name:
            browser_keywords = ("browser", "open", "search", "url", "website", "site", "web")
            return any(kw in q_lower for kw in browser_keywords)
        # Otherwise enable if any tag or name token appears in query
        name_tokens = t_name.replace("mcp_", "").split("_")
        return any(tok in q_lower for tok in name_tokens if len(tok) > 3)

    # General tools: enabled if tool name tokens match query
    return any(tok in q_lower for tok in t_name.split("_") if len(tok) > 2)


_BUILTIN_TOOL_CAPABILITIES: dict[str, Any] = {
    "launch_app": ToolCapability(
        domain="system", operation="open", target_type="app",
        state_transition=("closed", "running"), polarity="affirmative"
    ),
    "kill_process": ToolCapability(
        domain="system", operation="kill", target_type="process",
        state_transition=("running", "terminated"), polarity="negative"
    ),
    "manage_window": {
        "close": ToolCapability(domain="window", operation="close", target_type="window", state_transition=("open", "closed"), polarity="negative"),
        "minimize": ToolCapability(domain="window", operation="minimize", target_type="window", state_transition=("visible", "iconic"), polarity="negative"),
        "maximize": ToolCapability(domain="window", operation="maximize", target_type="window", state_transition=("normal", "maximized"), polarity="affirmative"),
        "restore": ToolCapability(domain="window", operation="restore", target_type="window", state_transition=("minimized", "normal"), polarity="affirmative"),
        "focus": ToolCapability(domain="window", operation="focus", target_type="window", state_transition=("background", "foreground"), polarity="affirmative"),
    },
    "system_power": {
        "shutdown": ToolCapability(domain="system", operation="shutdown", target_type="system", state_transition=("running", "off"), polarity="negative"),
        "restart": ToolCapability(domain="system", operation="restart", target_type="system", state_transition=("running", "running"), polarity="negative"),
        "sleep": ToolCapability(domain="system", operation="sleep", target_type="system", state_transition=("running", "sleep"), polarity="negative"),
        "hibernate": ToolCapability(domain="system", operation="hibernate", target_type="system", state_transition=("running", "sleep"), polarity="negative"),
    },
    "manage_service": {
        "start": ToolCapability(domain="system", operation="start", target_type="service", state_transition=("stopped", "running"), polarity="affirmative"),
        "stop": ToolCapability(domain="system", operation="stop", target_type="service", state_transition=("running", "stopped"), polarity="negative"),
        "restart": ToolCapability(domain="system", operation="restart", target_type="service", state_transition=("running", "running"), polarity="affirmative"),
        "status": ToolCapability(domain="system", operation="status", target_type="service", state_transition=("any", "any"), polarity="neutral"),
    },
    "move_file": ToolCapability(
        domain="filesystem", operation="move", target_type="file",
        state_transition=("src_exists", "dst_exists"), polarity="affirmative"
    ),
    "copy_file": ToolCapability(
        domain="filesystem", operation="copy", target_type="file",
        state_transition=("src_exists", "both_exist"), polarity="affirmative"
    ),
    "write_file": ToolCapability(
        domain="filesystem", operation="write", target_type="file",
        state_transition=("unwritten", "written"), polarity="affirmative"
    ),
    "read_file": ToolCapability(
        domain="filesystem", operation="read", target_type="file",
        state_transition=("any", "any"), polarity="neutral"
    ),
    "set_volume": ToolCapability(
        domain="system", operation="set_volume", target_type="volume",
        state_transition=("any", "adjusted"), polarity="neutral"
    ),
    "snap_window": ToolCapability(
        domain="window", operation="snap", target_type="window",
        state_transition=("any", "snapped"), polarity="affirmative"
    ),
    "get_clipboard": ToolCapability(
        domain="system", operation="read", target_type="clipboard",
        state_transition=("any", "any"), polarity="neutral"
    ),
    "set_clipboard": ToolCapability(
        domain="system", operation="write", target_type="clipboard",
        state_transition=("any", "updated"), polarity="affirmative"
    ),
    "take_screenshot": ToolCapability(
        domain="system", operation="capture", target_type="screen",
        state_transition=("any", "captured"), polarity="neutral"
    ),
    "show_notification": ToolCapability(
        domain="system", operation="notify", target_type="notification",
        state_transition=("any", "displayed"), polarity="neutral"
    ),
    "clean_temp_files": ToolCapability(
        domain="filesystem", operation="clean", target_type="temp_files",
        state_transition=("junk", "cleaned"), polarity="negative"
    ),
    "browser_navigate": ToolCapability(
        domain="browser", operation="navigate", target_type="url",
        state_transition=("idle", "loaded"), polarity="affirmative"
    ),
    "browser_run_js": {
        "play": ToolCapability(domain="media", operation="play", target_type="media_player", state_transition=("paused", "playing"), polarity="affirmative"),
        "pause": ToolCapability(domain="media", operation="pause", target_type="media_player", state_transition=("playing", "paused"), polarity="negative"),
        "stop": ToolCapability(domain="media", operation="stop", target_type="media_player", state_transition=("playing", "stopped"), polarity="negative"),
        "resume": ToolCapability(domain="media", operation="resume", target_type="media_player", state_transition=("paused", "playing"), polarity="affirmative"),
        "default": ToolCapability(domain="browser", operation="execute", target_type="dom", state_transition=("any", "any"), polarity="neutral"),
    },
}

# SAGE-Agent (ACL Findings 2026): Parameter-level critical slots mapping
_BUILTIN_CRITICAL_PARAMETERS: dict[str, tuple[str, ...]] = {
    "create_excel": ("sheets_data", "columns", "rows"),
    "create_spreadsheet": ("sheets_data", "columns", "rows"),
    "create_word": ("content_blocks", "sections"),
    "create_document": ("content_blocks", "sections"),
    "create_ppt": ("slides_data",),
    "create_presentation": ("slides_data",),
    "create_pdf": ("md_filepath", "markdown_content", "html_content"),
    "write_file": ("path", "content"),
    "delete_file": ("path",),
    "move_file": ("src", "dst"),
    "copy_file": ("src", "dst"),
    "kill_process": ("pid", "process_name"),
    "send_whatsapp_message": ("recipient", "message"),
    "send_telegram_message": ("chat_id", "message"),
    "send_discord_message": ("channel_id", "message"),
    "send_email": ("to", "body"),
    "play_media": ("query",),
    "set_volume": ("level",),
    "launch_app": ("app_name",),
}


@dataclass
class ToolMeta:
    """Rich metadata for a registered tool."""
    name: str
    description: str
    func: Callable[..., Coroutine[Any, Any, Any]]
    schema: dict

    # Routing metadata — used by get_manifest_for_agent() and task routing
    category: str = "general"            # e.g. "browser", "system", "media", "file", "search"
    agent_hints: list[str] = field(default_factory=list)  # agents that typically use this tool
    task_tags: list[str] = field(default_factory=list)    # e.g. ["web", "search", "scrape"]
    priority: int = 5                    # 1 (highest) – 10 (lowest), used for manifest ordering
    is_destructive: bool = False         # True → orchestrator may prompt confirmation
    is_deterministic: bool = False       # True → single deterministic action bypasses synthesis hop
    parallel_safe: bool = True          # False for stateful tools (browser, shell)
    timeout_s: float = 30.0             # Per-tool configurable timeout
    max_retries: int = 0
    retry_backoff_s: float = 1.0
    retryable_keywords: tuple[str, ...] = field(default_factory=tuple)
    permissions: dict[str, str] = field(default_factory=dict)
    default_permission: str = "allow"
    capabilities: dict[str, ToolCapability] | ToolCapability | None = None
    critical_parameters: tuple[str, ...] = field(default_factory=tuple)  # SAGE-Agent: critical parameter slots
    parameter_domains: dict[str, Any] = field(default_factory=dict)      # SAGE-Agent: domain bounds
    enabled: bool = True

    # Runtime telemetry (mutated in-place, never serialised)
    call_count: int = field(default=0, compare=False, repr=False)
    failure_count: int = field(default=0, compare=False, repr=False)
    total_latency_ms: float = field(default=0.0, compare=False, repr=False)
    _cached_tool: Optional[Any] = field(default=None, compare=False, repr=False)

    def get_capability(self, kwargs: Optional[dict[str, Any]] = None) -> Optional[ToolCapability]:
        """Dynamically resolve parameter-aware capability for this tool."""
        if not self.capabilities:
            return None
        if isinstance(self.capabilities, ToolCapability):
            return self.capabilities
        if isinstance(self.capabilities, dict) and kwargs:
            op_key = kwargs.get("action") or kwargs.get("operation") or kwargs.get("command") or kwargs.get("mode")
            if op_key and str(op_key).lower() in self.capabilities:
                return self.capabilities[str(op_key).lower()]
            if "default" in self.capabilities:
                return self.capabilities["default"]
        return next(iter(self.capabilities.values())) if isinstance(self.capabilities, dict) and self.capabilities else None

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.call_count if self.call_count else 0.0

    def to_openai_function(self) -> dict:
        """Render as an OpenAI-compatible function spec."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }


class ToolRegistry:
    """
    Central registry — single source of truth for every callable tool.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolMeta] = {}
        self._manifest_cache: dict[str, list[dict]] = {}
        self._execution_runtime: Any = None
        self._unsafe_locks: dict[str, asyncio.Lock] = {}

    def set_execution_runtime(self, runtime: Any) -> None:
        """Set the canonical ExecutionRuntime reference for tool dispatch."""
        self._execution_runtime = runtime

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_tool(
        self,
        name: str,
        description: str,
        func: Callable[..., Coroutine[Any, Any, Any]],
        schema: dict,
        *,
        category: str = "general",
        agent_hints: Optional[list[str]] = None,
        task_tags: Optional[list[str]] = None,
        priority: int = 5,
        is_destructive: bool = False,
        is_deterministic: bool = False,
        parallel_safe: bool = True,
        timeout_s: float = 30.0,
        max_retries: int = 0,
        retry_backoff_s: float = 1.0,
        retryable_keywords: Optional[tuple[str, ...] | list[str]] = None,
        permissions: Optional[dict[str, str]] = None,
        default_permission: str = "allow",
        capabilities: Optional[dict[str, ToolCapability] | ToolCapability] = None,
        critical_parameters: Optional[tuple[str, ...] | list[str]] = None,
        parameter_domains: Optional[dict[str, Any]] = None,
    ) -> None:
        """Register an async Python function as a callable tool."""
        # Ensure description is not generic if docstring is available on func
        if not description or description.lower().startswith(("agent-local tool:", "auto-discovered tool")):
            actual_f = func.func if isinstance(func, functools.partial) else func
            doc = inspect.getdoc(actual_f) or getattr(actual_f, "__doc__", None)
            if doc and isinstance(doc, str) and doc.strip():
                description = doc.strip().split("\n\n")[0].replace("\n", " ").strip()

        resolved_caps = capabilities or _BUILTIN_TOOL_CAPABILITIES.get(name)
        resolved_crit = tuple(critical_parameters) if critical_parameters is not None else _BUILTIN_CRITICAL_PARAMETERS.get(name, ())
        self._tools[name] = ToolMeta(
            name=name,
            description=description,
            func=func,
            schema=schema,
            category=category,
            agent_hints=agent_hints or [],
            task_tags=task_tags or [],
            priority=priority,
            is_destructive=is_destructive,
            is_deterministic=is_deterministic,
            parallel_safe=parallel_safe,
            timeout_s=timeout_s,
            max_retries=max_retries,
            retry_backoff_s=retry_backoff_s,
            retryable_keywords=tuple(retryable_keywords or ()),
            permissions=dict(permissions or {}),
            default_permission=default_permission,
            capabilities=resolved_caps,
            critical_parameters=resolved_crit,
            parameter_domains=dict(parameter_domains or {}),
        )
        self._manifest_cache.clear()
        logger.debug("Registered tool: %s [cat=%s]", name, category)

    def register_dynamic_skill(self, skill: Any) -> None:
        """Register a synthesized Skill instance as an executable tool."""
        name = getattr(skill, "name", str(skill))
        desc = getattr(skill, "description", f"Dynamic skill: {name}")
        schema = getattr(skill, "parameters_schema", {})
        if not isinstance(schema, dict) or "type" not in schema:
            schema = {
                "type": "object",
                "properties": schema if isinstance(schema, dict) else {},
            }

        async def _dynamic_skill_wrapper(**kwargs: Any) -> Any:
            if hasattr(skill, "execute"):
                return await skill.execute(kwargs)
            return f"Skill '{name}' executed with {kwargs}"

        self.register_tool(
            name=name,
            description=desc,
            func=_dynamic_skill_wrapper,
            schema=schema,
            category="dynamic_skill",
            task_tags=["skill", "dynamic", "voyager"],
        )

    def unregister_tool(self, name: str) -> bool:
        """Unregister a tool dynamically and invalidate manifest cache."""
        if name in self._tools:
            del self._tools[name]
            self._manifest_cache.clear()
            logger.debug("Unregistered tool: %s", name)
            return True
        return False

    def set_tool_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a tool dynamically without removing its metadata."""
        if name in self._tools:
            self._tools[name].enabled = enabled
            self._manifest_cache.clear()
            logger.debug("Set tool %s enabled=%s", name, enabled)
            return True
        return False

    def is_tool_enabled(self, name: str) -> bool:
        """Check if a registered tool is enabled."""
        meta = self._tools.get(name)
        return meta.enabled if meta else False

    def is_deterministic(self, name: str) -> bool:
        """Check if a tool is marked as a pure deterministic action."""
        meta = self._tools.get(name)
        if meta:
            return getattr(meta, "is_deterministic", False)
        return False

    def __contains__(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_capability(self, name: str, kwargs: Optional[dict[str, Any]] = None) -> Optional[ToolCapability]:
        """Resolve capability descriptor for a tool and its execution arguments."""
        meta = self._tools.get(name)
        if not meta:
            cap = _BUILTIN_TOOL_CAPABILITIES.get(name)
            if isinstance(cap, ToolCapability):
                return cap
            elif isinstance(cap, dict) and kwargs:
                op_key = kwargs.get("action") or kwargs.get("operation") or kwargs.get("command") or kwargs.get("mode")
                if op_key and str(op_key).lower() in cap:
                    return cap[str(op_key).lower()]
                if "default" in cap:
                    return cap["default"]
            return None
        return meta.get_capability(kwargs)

    def register(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Flexible registration supporting:
        1. Tool object instance: register(tool)
        2. Positional args: register(name, func, description, schema=None, category=None)
        3. Keyword args: register(name=..., func=..., description=..., schema=..., category=...)
        Also handles parameter aliases (coroutine/handler -> func, parameters -> schema).
        """
        if args and isinstance(args[0], Tool):
            tool = args[0]
            self.register_tool(
                name=tool.name,
                description=tool.description,
                func=tool.handler,
                schema=tool.schema,
                category=tool.category,
                agent_hints=tool.agent_hints,
                task_tags=tool.task_tags,
                priority=tool.priority,
                is_destructive=tool.is_destructive,
                timeout_s=tool.policy.timeout_s,
                max_retries=tool.policy.max_retries,
                retry_backoff_s=tool.policy.retry_backoff_s,
                retryable_keywords=tool.policy.retryable_keywords,
                permissions=tool.policy.permissions,
                default_permission=tool.policy.default_permission,
            )
            return

        name = kwargs.get("name")
        func = kwargs.get("func") or kwargs.get("coroutine") or kwargs.get("handler")
        description = kwargs.get("description", "")
        schema = kwargs.get("schema") or kwargs.get("parameters")
        category = kwargs.get("category", "general")

        # Positional arguments: (name, func, description, schema, category)
        if len(args) >= 1 and name is None:
            name = str(args[0])
        if len(args) >= 2 and func is None:
            func = args[1]
        if len(args) >= 3 and not description:
            description = str(args[2])
        if len(args) >= 4 and schema is None:
            schema = args[3]
        if len(args) >= 5 and category == "general":
            category = str(args[4])

        if not name or func is None:
            raise ValueError(
                f"Tool registration requires at least a name and callable func. Got args={args}, kwargs={kwargs}"
            )

        if schema is None:
            schema = {"type": "object", "properties": {}}

        reg_kwargs = {
            "name": name,
            "description": description,
            "func": func,
            "schema": schema,
            "category": category,
        }
        for k in (
            "agent_hints",
            "task_tags",
            "priority",
            "is_destructive",
            "parallel_safe",
            "timeout_s",
            "max_retries",
            "retry_backoff_s",
            "retryable_keywords",
            "permissions",
            "default_permission",
            "capabilities",
        ):
            if k in kwargs:
                reg_kwargs[k] = kwargs[k]

        self.register_tool(**reg_kwargs)

    def add_tool(self, *args: Any, **kwargs: Any) -> None:
        """Alias for register()."""
        self.register(*args, **kwargs)

    def register_tool_obj(self, tool: Tool) -> None:
        """Alias for register(tool) for object-oriented tool registration."""
        self.register(tool)

    def register_tool_group(self, tools: list[dict]) -> None:
        """Batch registration."""
        for t in tools:
            self.register_tool(**t)

    def register_wasm_skill(self, name: str, description: str, skill_teacher: Any) -> None:
        """Register a WASM skill as a tool."""
        async def _wasm_wrapper(input_data: str) -> str:
            return skill_teacher.execute_skill(name, input_data)

        self.register_tool(
            name=name,
            description=description,
            func=_wasm_wrapper,
            schema={
                "type": "object",
                "properties": {"input_data": {"type": "string"}},
                "required": ["input_data"],
            },
            category="skill",
        )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_tool(self, name: str) -> Optional[ToolMeta]:
        """Retrieve tool metadata by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolMeta]:
        return list(self._tools.values())

    def get_all_tools(self) -> dict[str, ToolMeta]:
        """Return a mapping of all registered tool names to their metadata."""
        return dict(self._tools)

    def get_all_tool_names(self) -> list[str]:
        """Return a list of all registered tool names."""
        return list(self._tools.keys())

    def get_tools_for_agent(self, agent_name: str) -> list[ToolMeta]:
        norm_name = agent_name.replace("_agent", "")
        return [
            t for t in self._tools.values()
            if not t.agent_hints or agent_name in t.agent_hints or norm_name in t.agent_hints
        ]

    async def dispatch_llm_tool_call(self, name: str, params: dict, context: dict) -> str:
        return await self.execute_tool(name, params, context)

    def tools_by_category(self, category: str) -> list[str]:
        return [n for n, t in self._tools.items() if t.category == category]

    def tools_by_tag(self, tag: str) -> list[str]:
        return [n for n, t in self._tools.items() if tag in t.task_tags]

    # ------------------------------------------------------------------
    # Manifest Generation & SOTA ACI Distillation
    # ------------------------------------------------------------------

    def get_manifest(self) -> list[dict]:
        """Full OpenAI-compatible tools manifest (all tools, sorted by priority)."""
        return self._build_manifest("")

    def get_manifest_for_agent(self, agent_name: str) -> list[dict]:
        """Agent-filtered manifest."""
        return self._build_manifest(agent_name)

    def get_tools_dynamic(self, query: str, max_tools: int = 25) -> list[ToolMeta]:
        """
        Dynamically filters registered tools for a given user query using category
        intent rules (OpenAI Agents SDK pattern):
          - Media tools: is_enabled when query contains 'music/gaana/volume/song/play'
          - Browser tools: is_enabled when query contains 'browser/open/search/website/url'
          - System tools: is_enabled when query contains 'open/app/window/screenshot'
          - Code tools: is_enabled when query contains 'code/python/script'
          - If query mentions tool name directly: always enabled
        """
        if not query:
            return []

        enabled_tools: list[ToolMeta] = []
        for t in self._tools.values():
            if not t.enabled:
                continue
            if is_tool_enabled_for_query(t.name, query, category=t.category, task_tags=t.task_tags):
                enabled_tools.append(t)

        # Sort by priority (lower number = higher priority)
        enabled_tools.sort(key=lambda t: t.priority)
        return enabled_tools[:max_tools]

    @classmethod
    def coerce_value(cls, val: Any, expected_type: str) -> Any:
        """Coerce raw LLM string values to schema-defined primitive types."""
        if val is None:
            return None
        if expected_type in ("integer", "int"):
            if isinstance(val, bool):
                return int(val)
            if isinstance(val, (int, float)):
                return int(val)
            if isinstance(val, str):
                v_str = val.strip()
                try:
                    return int(float(v_str))
                except (ValueError, TypeError):
                    return val
        elif expected_type in ("number", "float"):
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    return float(val.strip())
                except (ValueError, TypeError):
                    return val
        elif expected_type in ("boolean", "bool"):
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                v_lower = val.strip().lower()
                if v_lower in ("true", "1", "yes", "y", "on"):
                    return True
                if v_lower in ("false", "0", "no", "n", "off"):
                    return False
            elif isinstance(val, (int, float)):
                return bool(val)
        elif expected_type in ("string", "str"):
            if not isinstance(val, (dict, list, str)):
                return str(val)
        return val

    @classmethod
    def normalize_params(
        cls,
        tool_name: str,
        raw_params: dict[str, Any],
        schema: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Universal Parameter Aliasing, Normalization & Schema-Driven Type Coercion:
        1. Standardizes common parameter name aliases across diverse LLM outputs.
        2. Coerces primitive types (int, float, bool) according to the tool's JSON schema properties.
        """
        if not isinstance(raw_params, dict):
            return {}

        normalized = dict(raw_params)
        
        # Path aliases
        for path_alias in ("file_path", "filepath", "path", "filename", "file_name", "target_file"):
            if path_alias in normalized and "path" not in normalized and "file_path" not in normalized:
                normalized["file_path"] = normalized[path_alias]
                normalized["path"] = normalized[path_alias]

        # Content aliases
        for text_alias in ("text", "content", "data", "body", "code"):
            if text_alias in normalized and "content" not in normalized and "text" not in normalized:
                normalized["content"] = normalized[text_alias]
                normalized["text"] = normalized[text_alias]

        # URL aliases
        for url_alias in ("target_url", "link", "href", "web_url", "site"):
            if url_alias in normalized and "url" not in normalized:
                normalized["url"] = normalized[url_alias]

        # Selector aliases
        for sel_alias in ("css_selector", "xpath_selector", "target", "element"):
            if sel_alias in normalized and "selector" not in normalized:
                normalized["selector"] = normalized[sel_alias]

        # Query aliases
        for q_alias in ("search_query", "q", "query_str", "term"):
            if q_alias in normalized and "query" not in normalized:
                normalized["query"] = normalized[q_alias]

        # set_volume: remap non-numeric `level` direction strings to `delta`
        # so _tool_set_volume's delta-based path handles them correctly.
        # The LLM often sends level="increase"/"decrease" for relative requests
        # like "thodi sound badhao" — these are not integer levels and must be
        # translated before type-validation rejects them. We map to delta directly
        # because _tool_set_volume already handles delta= perfectly (line 1751).
        if tool_name in ("set_volume", "adjust_volume", "volume"):
            _INCREASE_WORDS = frozenset(
                ("increase", "raise", "up", "loud", "louder", "badhao", "badha",
                 "higher", "more", "tez", "zyada", "increment", "increment_small",
                 "increment_medium", "increment_large")
            )
            _DECREASE_WORDS = frozenset(
                ("decrease", "lower", "down", "dheere", "kam", "quiet", "quieter",
                 "softer", "less", "slower", "reduce", "decrement", "decrement_small",
                 "decrement_medium", "decrement_large")
            )
            raw_level = normalized.get("level")
            if isinstance(raw_level, str) and not raw_level.strip().lstrip("+-").isdigit():
                direction = raw_level.strip().lower()
                if direction in _INCREASE_WORDS:
                    normalized.setdefault("delta", 10)  # default +10 if no delta already set
                    del normalized["level"]
                elif direction in _DECREASE_WORDS:
                    normalized.setdefault("delta", -10)  # default -10 if no delta already set
                    del normalized["level"]
                else:
                    # Unknown non-numeric string in level — remove it so the tool
                    # falls back to its own default-delta logic (no level, no delta → +10).
                    del normalized["level"]

            # Also coerce delta if given as a numeric string (e.g. delta="10")
            raw_delta = normalized.get("delta")
            if isinstance(raw_delta, str):
                try:
                    normalized["delta"] = int(float(raw_delta.strip()))
                except (ValueError, TypeError):
                    del normalized["delta"]

        # Schema-driven primitive type coercion
        if schema and isinstance(schema, dict):
            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                for param_key, param_val in list(normalized.items()):
                    if param_key in properties and isinstance(properties[param_key], dict):
                        expected_type = properties[param_key].get("type")
                        if expected_type and isinstance(expected_type, str):
                            normalized[param_key] = cls.coerce_value(param_val, expected_type)

        return normalized

    def _build_manifest(self, agent_name: str) -> list[dict]:
        if agent_name in self._manifest_cache:
            return self._manifest_cache[agent_name]

        tools_sorted = sorted(self._tools.values(), key=lambda x: x.priority)
        active_tools = [t for t in tools_sorted if getattr(t, "enabled", True)]

        if not agent_name:
            manifest = [t.to_openai_function() for t in active_tools]
        else:
            norm_name = agent_name.replace("_agent", "")
            manifest = [
                t.to_openai_function()
                for t in active_tools
                if not t.agent_hints or agent_name in t.agent_hints or norm_name in t.agent_hints
            ]

        self._manifest_cache[agent_name] = manifest
        return manifest

    def get_telemetry(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "category": t.category,
                "call_count": t.call_count,
                "failure_count": t.failure_count,
                "success_rate": round(
                    (t.call_count - t.failure_count) / t.call_count * 100, 1
                ) if t.call_count else 100.0,
                "avg_latency_ms": round(t.avg_latency_ms, 1),
            }
            for t in self._tools.values()
        ]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def execute_tool(self, name: str, params: dict, context: Any = None) -> str:
        if isinstance(context, ToolContext):
            tool_context = context
        elif isinstance(context, dict):
            tool_context = ToolContext(
                consumer=str(context.get("consumer") or "direct_llm"),
                task_id=str(context.get("task_id") or ""),
                metadata=context,
            )
        else:
            tool_context = ToolContext(consumer="direct_llm")
        return await self.call_tool(
            name,
            consumer=tool_context.consumer,
            context=tool_context,
            **params,
        )

    async def call_tool(
        self,
        name: str,
        *,
        consumer: str = "unknown",
        context: Optional[ToolContext] = None,
        **kwargs: Any,
    ) -> str:
        if name not in self._tools:
            raise KeyError(f"Tool not found: {name}")

        meta = self._tools[name]
        if not getattr(meta, "enabled", True):
            return f"Error executing tool {name}: Tool is currently disabled."

        tool_context = context or ToolContext(consumer=consumer)

        lock = None
        if not getattr(meta, "parallel_safe", True):
            lock = self._unsafe_locks.setdefault(name, asyncio.Lock())
            lock_timeout = max(15.0, float(meta.timeout_s) + 5.0)
            try:
                await asyncio.wait_for(lock.acquire(), timeout=lock_timeout)
            except asyncio.TimeoutError:
                logger.error("Tool %s lock acquisition timed out after %.1fs (resource busy/deadlock)", name, lock_timeout)
                return f"Error executing tool {name}: Tool lock acquisition timed out after {lock_timeout:.1f}s (resource busy or deadlock)."

        try:
            if self._execution_runtime:
                res = await self._execution_runtime.execute_tool(name, params=kwargs, context=tool_context)
                return str(res if res is not None else "")
            else:
                # Direct fallback invocation with single normalization
                normalized = self.normalize_params(name, kwargs, schema=meta.schema)
                sig = inspect.signature(meta.func)
                if "context" in sig.parameters:
                    normalized["context"] = tool_context
                t0 = time.monotonic()
                try:
                    if inspect.iscoroutinefunction(meta.func):
                        raw = await asyncio.wait_for(meta.func(**normalized), timeout=meta.timeout_s)
                    else:
                        r = meta.func(**normalized)
                        raw = await asyncio.wait_for(r, timeout=meta.timeout_s) if inspect.isawaitable(r) else r
                    meta.call_count += 1
                    meta.total_latency_ms += (time.monotonic() - t0) * 1000
                    if isinstance(raw, (dict, list)):
                        return json.dumps(raw, default=str)
                    return str(raw if raw is not None else "")
                except Exception as exc:
                    meta.call_count += 1
                    meta.failure_count += 1
                    meta.total_latency_ms += (time.monotonic() - t0) * 1000
                    logger.error("Tool %s failed: %s", name, exc)
                    return f"Error executing tool {name}: {exc}"
        finally:
            if lock and lock.locked():
                lock.release()

    async def call_tools_parallel(
        self,
        calls: list[dict[str, Any]],
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        async def _run_one(call: dict) -> dict:
            name = call.get("name", "")
            params = call.get("params", {})
            call_id = call.get("call_id", name)
            t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(self.call_tool(name, **params), timeout=timeout)
                return {"call_id": call_id, "name": name, "result": result,
                        "success": True, "latency_ms": round((time.monotonic() - t0) * 1000, 1)}
            except asyncio.TimeoutError:
                return {"call_id": call_id, "name": name,
                        "result": f"[Tool timeout after {timeout}s]",
                        "success": False, "latency_ms": timeout * 1000}
            except Exception as e:
                return {"call_id": call_id, "name": name, "result": f"[Tool error: {e}]",
                        "success": False, "latency_ms": round((time.monotonic() - t0) * 1000, 1)}

        return list(await asyncio.gather(*[_run_one(c) for c in calls]))

    def to_sdk_function_tool(
        self,
        name: str,
        execution_runtime: Optional[Any] = None,
        agent: Optional[Any] = None,
    ) -> Any:
        """Convert a registered tool into an OpenAI Agents SDK FunctionTool."""
        from .core.sdk_bridge import to_sdk_function_tool
        return to_sdk_function_tool(
            tool_name=name,
            registry=self,
            execution_runtime=execution_runtime or self._execution_runtime,
            agent=agent,
        )

    def to_sdk_tools(
        self,
        names: Sequence[str],
        execution_runtime: Optional[Any] = None,
        agent: Optional[Any] = None,
    ) -> list[Any]:
        """Convert a list of registered tool names into OpenAI Agents SDK FunctionTool instances."""
        from .core.sdk_bridge import to_sdk_tools
        return to_sdk_tools(
            tool_names=names,
            registry=self,
            execution_runtime=execution_runtime or self._execution_runtime,
            agent=agent,
        )
