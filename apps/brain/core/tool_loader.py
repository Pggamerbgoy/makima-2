"""
Makima OS — Declarative Tool Loader
Location: apps/brain/core/tool_loader.py

Registers SHARED tools whose handlers live in standalone modules.
Agents that own an implementation register their own tools via
BaseAgent._register_local_tools or _TOOL_MAP — this loader is only for
cross-agent, stateless handlers (web_search, fetch_url) and browser tool
SCHEMAS (implementation delegated to BrowserAgent._shared_bc singleton).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_loader_tasks: set[Any] = set()


def register_core_tools(tool_registry: Any, services: Any = None) -> None:
    """
    Register shared system tools + browser tool schemas into tool_registry.
    """
    if not tool_registry:
        logger.warning("register_core_tools: tool_registry is None — skipping")
        return

    registered = 0

    def _safe(name: str, loader: Any) -> None:
        nonlocal registered
        try:
            tool_registry.register_tool(**loader())
            registered += 1
            logger.info("Registered core tool: %s", name)
        except Exception as e:
            logger.warning("Failed to register core tool '%s': %s", name, e)

    # ── web_search ───────────────────────────────────────────────────────────
    try:
        from ..web_search_tool import web_search
        _safe("web_search", lambda: {
            "name": "web_search",
            "description": "Search the web and return a formatted list of results for synthesis.",
            "func": web_search,
            "schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "max_results": {"type": "integer", "description": "Max results (1-10)", "default": 5},
                },
                "required": ["query"],
            },
            "category": "web",
            "agent_hints": ["research", "code", "messaging", "security", "data_analyst", "browser", "media"],
            "task_tags": ["web", "search"],
            "priority": 1,
        })
    except ImportError as e:
        logger.warning("Cannot import web_search_tool: %s", e)

    # ── fetch_url ────────────────────────────────────────────────────────────
    try:
        from ..web_search_tool import fetch_url
        _safe("fetch_url", lambda: {
            "name": "fetch_url",
            "description": "Fetch raw URL content (JSON/XML/text) and return it as a string.",
            "func": fetch_url,
            "schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 20},
                },
                "required": ["url"],
            },
            "category": "web",
            "agent_hints": ["research", "code", "browser", "security", "data_analyst"],
            "task_tags": ["web", "fetch"],
            "priority": 1,
        })
    except ImportError as e:
        logger.warning("Cannot import fetch_url from web_search_tool: %s", e)

    # ── search_installed_apps (OS-wide application discovery) ────────────────
    try:
        from ..agents.system_agent import search_installed_apps
        _safe("search_installed_apps", lambda: {
            "name": "search_installed_apps",
            "description": "Search, list, or discover what applications or software are installed on the computer (e.g. 'konse apps hain', 'check if blender is installed', 'list installed apps'). Use ONLY for discovery/listing. NEVER use to open or launch an app (use launch_app instead).",
            "func": search_installed_apps,
            "schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional keyword or app name to search in the installed apps list (e.g. 'browser', 'media', 'code')"},
                    "limit": {"type": "integer", "description": "Maximum number of search results to return (default 15)"},
                },
                "required": [],
            },
            "category": "system",
            "agent_hints": ["system", "commander", "automation", "general"],
            "task_tags": ["system", "os", "apps", "software", "search", "list", "discover"],
            "priority": 1,
        })
    except ImportError as e:
        logger.warning("Cannot import search_installed_apps: %s", e)

    # ── browser tool schemas (27 tools) ──────────────────────────────────────
    try:
        from ..tools.browser_tools import register_browser_tools
        register_browser_tools(tool_registry)
        logger.info("Declarative core tools registration: browser tools registered")
    except Exception as e:
        logger.warning("Failed to register browser tools from browser_tools module: %s", e)

    # ── P1 Fix: Register domain-specific tool modules
    _domain_tool_loaders = [
        ("calendar", "..tools.calendar_tools", "register_calendar_tools"),
        ("media", "..tools.media_tools", "register_media_tools"),
        ("finance", "..tools.finance_tools", "register_finance_tools"),
        ("notification", "..tools.notification_tools", "register_notification_tools"),
        ("data", "..tools.data_tools", "register_data_tools"),
        ("devops", "..tools.devops_tools", "register_devops_tools"),
        ("security", "..tools.security_tools", "register_security_tools"),
        ("system", "..tools.system_tools", "register_system_tools"),
        ("document", "..tools.document_tools", "register_document_tools"),
    ]
    for _domain, _module_path, _fn_name in _domain_tool_loaders:
        try:
            import importlib as _il
            import inspect as _inspect
            _mod = _il.import_module(_module_path, package=__package__)
            _fn = getattr(_mod, _fn_name, None)
            if _fn is None:
                logger.warning("Tool module '%s' missing function '%s' — skipping", _module_path, _fn_name)
                continue
            if _inspect.iscoroutinefunction(_fn):
                import asyncio as _asyncio
                try:
                    loop = _asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    _t = loop.create_task(_fn(tool_registry))
                    _loader_tasks.add(_t)
                    _t.add_done_callback(_loader_tasks.discard)
                else:
                    _asyncio.get_event_loop().run_until_complete(_fn(tool_registry))
            else:
                _fn(tool_registry)
            logger.info("Registered %s tools from %s", _domain, _fn_name)
        except Exception as _e:
            logger.warning("Failed to register %s tools: %s", _domain, _e)


async def register_mcp_tools(tool_registry: Any, mcp_config: list) -> None:
    """
    Bug 1 fix: Wire MCP servers into the ToolRegistry at startup.

    For each enabled entry in the mcp_servers config list, spawns an
    AsyncMcpMultiplexer subprocess, performs the MCP initialize handshake,
    discovers available tools via tools/list, and registers them into
    tool_registry so the SemanticPlanner's 'direct' strategy can use them.

    Servers are started concurrently; per-server errors are logged and skipped
    without blocking the remaining servers.
    """
    if not mcp_config or not tool_registry:
        return

    try:
        from ..tools.mcp_adapter import AsyncMcpMultiplexer, McpToolAdapter
    except ImportError as e:
        logger.warning("MCP adapter unavailable — skipping MCP tool registration: %s", e)
        return

    # Store live multiplexers on the registry so they're not GC'd
    if not hasattr(tool_registry, "_mcp_multiplexers"):
        tool_registry._mcp_multiplexers = []

    async def _start_one(entry: dict) -> None:
        if not entry.get("enabled", True):
            return
        name = entry.get("name", "mcp")
        command = entry.get("command")
        env = entry.get("env") or None
        if not command or not isinstance(command, list):
            logger.warning("MCP server '%s' has no valid command — skipping", name)
            return
        try:
            mux = AsyncMcpMultiplexer(command=command, env=env)
            await mux.start()
            adapter = McpToolAdapter(client=mux, prefix=f"mcp_{name}")
            registered = await adapter.discover_and_register(tool_registry)
            # Hold strong references so GC doesn't kill the subprocess or handlers
            tool_registry._mcp_multiplexers.append(mux)
            logger.info(
                "MCP server '%s' online — registered %d tools: %s",
                name, len(registered), registered,
            )
        except Exception as mcp_err:
            logger.error("Failed to start MCP server '%s': %s", name, mcp_err)

    import asyncio as _asyncio
    await _asyncio.gather(*[_start_one(entry) for entry in mcp_config], return_exceptions=True)
    logger.info("MCP tool registration complete: %d server(s) processed", len(mcp_config))