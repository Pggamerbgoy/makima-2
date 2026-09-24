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

    # ── generate_image ───────────────────────────────────────────────────────
    async def _generate_image(
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        **kwargs: Any,
    ) -> str:
        """
        Generate an image from a text prompt using OpenAI gpt-image-1.
        Saves image to ~/.makima/images/ and returns path + base64 data URL.
        """
        import asyncio as _asyncio
        import base64
        import os
        import time as _time
        clean_prompt = (prompt or "").strip()
        if not clean_prompt:
            return "[generate_image] No prompt provided."

        handler_obj = services.get("ai_handler") if hasattr(services, "get") else None
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENAI_KEY")
            or getattr(handler_obj, "_openai_api_key", None)
            or getattr(handler_obj, "api_key", None)
        )

        if not api_key:
            return (
                "[generate_image] No OpenAI API key found. "
                "Set OPENAI_API_KEY environment variable to enable image generation."
            )

        try:
            import importlib.util

            if importlib.util.find_spec("openai") is None:
                return "[generate_image] openai package not installed. Run: pip install openai"
        except Exception:
            return "[generate_image] openai package not installed. Run: pip install openai"

        # Clamp n to valid range (1-4 for DALL-E-3 actually requires n=1)
        n_images = max(1, min(int(n or 1), 1))
        # Validate size
        valid_sizes = {"256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"}
        img_size = size if size in valid_sizes else "1024x1024"

        def _call_openai() -> str:
            import openai as _openai
            client = _openai.OpenAI(api_key=api_key)
            try:
                resp = client.images.generate(
                    model="gpt-image-1",
                    prompt=clean_prompt,
                    size=img_size,
                    n=n_images,
                )
            except _openai.BadRequestError as e:
                return f"[generate_image] Content policy rejection: {e}"
            except _openai.AuthenticationError:
                return "[generate_image] Invalid OpenAI API key."
            except _openai.RateLimitError:
                return "[generate_image] OpenAI rate limit exceeded. Try again later."
            except Exception as e:
                return f"[generate_image] OpenAI API error: {e}"
            finally:
                try:
                    client.close()
                except Exception as close_err:
                    logger.debug("OpenAI image client close failed: %s", close_err)

            # Save and return
            img_dir = os.path.expanduser("~/.makima/images")
            os.makedirs(img_dir, exist_ok=True)
            timestamp = _time.strftime("%Y%m%d_%H%M%S")
            saved_paths = []
            b64_images = []

            for idx, img_data in enumerate(resp.data):
                suffix = f"_{idx}" if len(resp.data) > 1 else ""
                fname = f"gen_{timestamp}{suffix}.png"
                fpath = os.path.join(img_dir, fname)

                # gpt-image-1 returns b64_json; dall-e-3 may return url
                if hasattr(img_data, "b64_json") and img_data.b64_json:
                    raw = base64.b64decode(img_data.b64_json)
                    with open(fpath, "wb") as f:
                        f.write(raw)
                    b64 = img_data.b64_json
                elif hasattr(img_data, "url") and img_data.url:
                    import urllib.request
                    urllib.request.urlretrieve(img_data.url, fpath)
                    with open(fpath, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                else:
                    continue
                saved_paths.append(fpath)
                b64_images.append(b64)

            if not saved_paths:
                return "[generate_image] No images returned by API."

            result_parts = [f"Generated {len(saved_paths)} image(s) for: '{clean_prompt[:80]}'"]
            for i, (path, b64) in enumerate(zip(saved_paths, b64_images)):
                result_parts.append(f"Image {i+1}: {path}")
                result_parts.append(f"data:image/png;base64,{b64[:200]}...  [{len(b64)} chars total]")
                result_parts.append(f"FULL_BASE64:{b64}")
            return "\n".join(result_parts)

        return await _asyncio.to_thread(_call_openai)

    _safe("generate_image", lambda: {
        "name": "generate_image",
        "description": "Generate an image from a text prompt using OpenAI gpt-image-1. Returns saved path and base64 PNG.",
        "func": _generate_image,
        "schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed text description of the image to generate."},
                "size": {
                    "type": "string",
                    "enum": ["256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"],
                    "description": "Image resolution (default '1024x1024').",
                },
                "quality": {
                    "type": "string",
                    "enum": ["standard", "hd"],
                    "description": "Image quality (default 'standard'). 'hd' costs more tokens.",
                },
            },
            "required": ["prompt"],
        },
        "category": "creative",
        "agent_hints": ["creative", "research", "commander", "general"],
        "task_tags": ["image", "generate", "creative", "art", "dalle", "vision"],
        "priority": 1,
    })

    # ── search_installed_apps (OS-wide application discovery) ────────────────
    try:
        from ..tools.system_tools import search_installed_apps
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

    # ── describe_my_capabilities (Self-Discovery Tool) ───────────────────────
    def _describe_capabilities(
        category: Any = "",
        detailed: Any = False,
        lang: Any = "hinglish",
        **kwargs: Any,
    ) -> str:
        """Describe Makima's real live capabilities and available tools from ToolRegistry."""
        cat_str = str(category).strip() if category is not None and not isinstance(category, (dict, list)) else ""
        cat = cat_str if cat_str and cat_str.lower() not in ("", "all", "summary", "none") else None
        is_detailed = bool(detailed) and str(detailed).lower().strip() not in ("false", "0", "no", "off")
        lang_str = str(lang or "hinglish").strip().lower()
        return tool_registry.get_manifest_summary(detailed=is_detailed, lang=lang_str, category=cat)

    _safe("describe_my_capabilities", lambda: {
        "name": "describe_my_capabilities",
        "description": "Describe Makima's real, live workstation capabilities, registered tools, and what she can do on the desktop. Call this whenever the user asks 'tum kya kya kar sakti ho', 'what can you do', 'apni powers batao', 'capabilities summary', or asks what tools exist in a specific domain (e.g. browser, media, system, documents, memory).",
        "func": _describe_capabilities,
        "schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional domain filter (e.g. 'system', 'browser', 'media', 'filesystem', 'document', 'memory', 'calendar', 'finance', 'data', 'devops', 'security', 'notification', 'web', 'creative'). Leave empty for full overview.",
                },
                "detailed": {
                    "type": "boolean",
                    "description": "If true, lists individual tool names and their descriptions under each domain.",
                    "default": False,
                },
                "lang": {
                    "type": "string",
                    "enum": ["hinglish", "en"],
                    "description": "Language for the capability description: 'hinglish' (default) or 'en' (English).",
                    "default": "hinglish",
                },
            },
            "required": [],
        },
        "category": "system",
        "agent_hints": ["system", "commander", "general", "fast_chat"],
        "task_tags": ["system", "capabilities", "self_discovery", "manifest", "help"],
        "priority": 1,
    })

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
        ("memory", "..tools.memory_tools", "register_memory_tools"),
        ("whatsapp", "..tools.whatsapp_tools", "register_whatsapp_tools"),
        ("telegram", "..tools.telegram_tools", "register_telegram_tools"),
        ("reminder", "..tools.reminder_tools", "register_reminder_tools"),
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

            call_kwargs = {}
            try:
                sig = _inspect.signature(_fn)
                if "services" in sig.parameters:
                    call_kwargs["services"] = services
            except Exception as sig_err:
                logger.debug("Signature introspection failed for %s: %s", _fn_name, sig_err)

            if _inspect.iscoroutinefunction(_fn):
                import asyncio as _asyncio
                try:
                    loop = _asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    _t = loop.create_task(_fn(tool_registry, **call_kwargs))
                    _loader_tasks.add(_t)
                    _t.add_done_callback(_loader_tasks.discard)
                else:
                    _asyncio.get_event_loop().run_until_complete(_fn(tool_registry, **call_kwargs))
            else:
                _fn(tool_registry, **call_kwargs)
            logger.info("Registered %s tools from %s", _domain, _fn_name)
        except Exception as _e:
            logger.warning("Failed to register %s tools: %s", _domain, _e)


async def register_mcp_tools(tool_registry: Any, mcp_config: list) -> None:
    """
    Wire MCP servers into the ToolRegistry at startup.

    Prefers the OpenAI Agents SDK native client (agents.mcp.MCPServerStdio /
    MCPServerStreamableHttp / MCPServerSse) when available; falls back to the
    custom AsyncMcpMultiplexer adapter. Live server handles are stored on
    tool_registry._mcp_servers for graceful shutdown cleanup.
    """
    if not mcp_config or not tool_registry:
        return

    if not hasattr(tool_registry, "_mcp_servers"):
        tool_registry._mcp_servers = []
    if not hasattr(tool_registry, "_mcp_multiplexers"):
        tool_registry._mcp_multiplexers = []

    native_available = True
    try:
        import importlib.util as _ilu

        if _ilu.find_spec("agents.mcp") is None:
            raise ImportError("agents.mcp not found")
    except ImportError as native_err:
        native_available = False
        logger.warning("agents.mcp unavailable — using custom MCP adapter: %s", native_err)

    async def _register_native(server: Any, name: str) -> list[str]:
        await server.connect()
        try:
            mcp_tools = await server.list_tools()
        except Exception:
            await server.cleanup()
            raise

        from ..tools.types import Tool, ToolDefinition, ToolPolicy

        registered: list[str] = []
        prefix = f"mcp_{name}"
        for t in mcp_tools:
            raw_name = getattr(t, "name", "") or ""
            if not raw_name:
                continue
            tool_name = f"{prefix}_{raw_name}"
            desc = getattr(t, "description", None) or f"MCP Tool: {raw_name}"
            schema = getattr(t, "input_schema", None) or {
                "type": "object",
                "properties": {},
            }
            def _make_handler(srv: Any, rn: str) -> Any:
                async def handler(**kwargs: Any) -> Any:
                    call_res = await srv.call_tool(rn, kwargs)
                    content = getattr(call_res, "content", None) or []
                    texts: list[str] = []
                    for block in content:
                        text = getattr(block, "text", None)
                        if text:
                            texts.append(text)
                    if getattr(call_res, "is_error", False):
                        raise RuntimeError("\n".join(texts) or f"MCP tool '{rn}' failed")
                    return "\n".join(texts) if texts else str(call_res)

                return handler

            tool_obj = Tool(
                definition=ToolDefinition(name=tool_name, description=desc, parameters=schema),
                handler=_make_handler(server, raw_name),
                policy=ToolPolicy(timeout_s=60.0, max_retries=1),
                category="mcp",
            )
            if hasattr(tool_registry, "register"):
                tool_registry.register(tool_obj)
            elif hasattr(tool_registry, "register_tool_obj"):
                tool_registry.register_tool_obj(tool_obj)
            else:
                tool_registry.register_tool(
                    name=tool_obj.name,
                    description=tool_obj.description,
                    func=tool_obj.handler,
                    schema=tool_obj.schema,
                    category=tool_obj.category,
                )
            registered.append(tool_name)

        tool_registry._mcp_servers.append(server)
        return registered

    async def _start_one_native(entry: dict) -> None:
        from typing import cast as _cast

        from agents.mcp import MCPServerSse, MCPServerStdio, MCPServerStreamableHttp
        from agents.mcp.server import (
            MCPServerSseParams,
            MCPServerStdioParams,
            MCPServerStreamableHttpParams,
        )

        name = entry.get("name", "mcp")
        command = entry.get("command")
        url = entry.get("url")
        env = entry.get("env") or None
        headers = entry.get("headers") or None
        transport = (entry.get("transport") or "").lower()

        server: Any
        if command:
            cmd_list = [command] if isinstance(command, str) else list(command)
            if not cmd_list or not all(isinstance(c, str) for c in cmd_list):
                logger.warning("MCP server '%s' has invalid command parts — skipping", name)
                return
            stdio_params: dict[str, Any] = {"command": cmd_list[0], "args": cmd_list[1:]}
            if env:
                import os as _os
                stdio_params["env"] = {
                    **_os.environ,
                    **{str(k): str(v) for k, v in env.items()},
                }
            server = MCPServerStdio(
                params=_cast(MCPServerStdioParams, stdio_params),
                cache_tools_list=True,
                name=name,
            )
        elif url:
            if transport == "sse":
                sse_params: dict[str, Any] = {"url": url}
                if headers:
                    sse_params["headers"] = headers
                server = MCPServerSse(
                    params=_cast(MCPServerSseParams, sse_params),
                    cache_tools_list=True,
                    name=name,
                )
            else:
                http_params: dict[str, Any] = {"url": url}
                if headers:
                    http_params["headers"] = headers
                server = MCPServerStreamableHttp(
                    params=_cast(MCPServerStreamableHttpParams, http_params),
                    cache_tools_list=True,
                    name=name,
                )
        else:
            logger.warning("MCP server '%s' has no valid command or url — skipping", name)
            return

        registered = await _register_native(server, name)
        logger.info(
            "MCP server '%s' online (agents.mcp) — registered %d tools: %s",
            name, len(registered), registered,
        )

    async def _start_one_legacy(entry: dict) -> None:
        try:
            from ..tools.mcp_adapter import (
                AsyncMcpHttpClient,
                AsyncMcpMultiplexer,
                McpToolAdapter,
            )
        except ImportError as e:
            logger.warning("MCP adapter unavailable — skipping MCP tool registration: %s", e)
            return

        name = entry.get("name", "mcp")
        command = entry.get("command")
        url = entry.get("url")
        env = entry.get("env") or None
        headers = entry.get("headers") or None

        if not command and not url:
            logger.warning("MCP server '%s' has no valid command or url — skipping", name)
            return
        try:
            if url:
                client: Any = AsyncMcpHttpClient(base_url=url, headers=headers)
            else:
                if not command:
                    logger.warning("MCP server '%s' has empty command — skipping", name)
                    return
                cmd_list = [command] if isinstance(command, str) else list(command)
                if not all(isinstance(c, str) for c in cmd_list):
                    logger.warning("MCP server '%s' has non-string command parts — skipping", name)
                    return
                client = AsyncMcpMultiplexer(command=cmd_list, env=env)
            await client.start()
            adapter = McpToolAdapter(client=client, prefix=f"mcp_{name}")
            registered = await adapter.discover_and_register(tool_registry)
            tool_registry._mcp_multiplexers.append(client)
            logger.info(
                "MCP server '%s' online (legacy adapter) — registered %d tools: %s",
                name, len(registered), registered,
            )
        except Exception as mcp_err:
            logger.error("Failed to start MCP server '%s': %s", name, mcp_err)

    async def _start_one(entry: dict) -> None:
        if not entry.get("enabled", True):
            return
        if native_available:
            try:
                await _start_one_native(entry)
                return
            except Exception as native_err:
                logger.warning(
                    "Native MCP start failed for '%s' (%s) — falling back to custom adapter",
                    entry.get("name", "mcp"), native_err,
                )
        await _start_one_legacy(entry)

    import asyncio as _asyncio
    results = await _asyncio.gather(
        *[_start_one(entry) for entry in mcp_config], return_exceptions=True
    )
    for res in results:
        if isinstance(res, Exception):
            logger.error("MCP server startup error: %s", res)
    logger.info("MCP tool registration complete: %d server(s) processed", len(mcp_config))