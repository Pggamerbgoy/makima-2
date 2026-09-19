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
        import os, base64, time as _time, asyncio as _asyncio
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
            import openai
        except ImportError:
            return "[generate_image] openai package not installed. Run: pip install openai"

        # Clamp n to valid range (1-4 for DALL-E-3 actually requires n=1)
        n_images = max(1, min(int(n or 1), 1))
        # Validate size
        valid_sizes = {"256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"}
        img_size = size if size in valid_sizes else "1024x1024"

        def _call_openai() -> str:
            import openai as _openai  # noqa: F811
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
                except Exception:
                    pass

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
            except Exception:
                pass

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
        from ..tools.mcp_adapter import AsyncMcpMultiplexer, AsyncMcpHttpClient, McpToolAdapter
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
        url = entry.get("url")
        env = entry.get("env") or None
        headers = entry.get("headers") or None

        if not command and not url:
            logger.warning("MCP server '%s' has no valid command or url — skipping", name)
            return
        try:
            if url:
                client = AsyncMcpHttpClient(base_url=url, headers=headers)
            else:
                client = AsyncMcpMultiplexer(command=command, env=env)
            await client.start()
            adapter = McpToolAdapter(client=client, prefix=f"mcp_{name}")
            registered = await adapter.discover_and_register(tool_registry)
            # Hold strong references so GC doesn't kill the subprocess or client
            tool_registry._mcp_multiplexers.append(client)
            logger.info(
                "MCP server '%s' online — registered %d tools: %s",
                name, len(registered), registered,
            )
        except Exception as mcp_err:
            logger.error("Failed to start MCP server '%s': %s", name, mcp_err)

    import asyncio as _asyncio
    await _asyncio.gather(*[_start_one(entry) for entry in mcp_config], return_exceptions=True)
    logger.info("MCP tool registration complete: %d server(s) processed", len(mcp_config))