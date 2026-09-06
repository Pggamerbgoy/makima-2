"""
Makima OS — Standalone Browser Capability Tools
Location: apps/brain/tools/browser_tools.py

Consolidates all 27 browser capability tools from Makima's tool registry and BrowserAgent:
1.  browser_navigate
2.  browser_click
3.  browser_fill
4.  browser_get_text
5.  browser_distill_dom
6.  browser_screenshot
7.  browser_run_js
8.  browser_press_key
9.  browser_select_option
10. browser_hover
11. browser_scroll
12. browser_wait_for
13. browser_go_back
14. browser_go_forward
15. browser_reload
16. browser_extract_links
17. browser_get_attribute
18. browser_check_exists
19. browser_upload_file
20. browser_list_tabs
21. browser_close_tab
22. browser_switch_tab
23. browser_evaluate_xpath
24. browser_network_intercept
25. browser_set_range
26. browser_parallel_scrape
27. browser_parallel_search
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

logger = logging.getLogger("makima.tools.browser")

# ---------------------------------------------------------------------------
# Global Controller Hook & Singleton Lifecycle
# ---------------------------------------------------------------------------
_custom_browser_controller: Optional[Any] = None


def set_browser_controller(controller: Optional[Any]) -> None:
    """Explicitly inject a BrowserController instance (useful for testing & mocking)."""
    global _custom_browser_controller
    _custom_browser_controller = controller


def get_browser_controller() -> Optional[Any]:
    """Retrieve the currently injected or active BrowserController if available."""
    global _custom_browser_controller
    if _custom_browser_controller is not None:
        return _custom_browser_controller
    try:
        from ..agents.browser_agent import BrowserAgent
        return BrowserAgent._shared_bc
    except Exception:
        return None


async def get_or_create_browser_controller(config: Optional[dict] = None) -> Any:
    """
    Get or create the shared BrowserController singleton with thread-safe lock.
    """
    global _custom_browser_controller
    if _custom_browser_controller is not None:
        return _custom_browser_controller

    from ..agents.browser_agent import BrowserAgent
    async with BrowserAgent._bc_lock:
        if BrowserAgent._shared_bc is None:
            from ..browser_controller import BrowserController
            BrowserAgent._shared_bc = BrowserController(
                config=config or {},
                ai_handler=None,
                ws_broadcast=None,
            )
            await BrowserAgent._shared_bc.start()
            BrowserAgent._bc_refcount = 1
            logger.info("browser_tools: initialized shared BrowserController singleton")
        return BrowserAgent._shared_bc


async def _dispatch_bc(method_name: str, **kwargs: Any) -> Any:
    """Helper to dispatch tool calls to the active BrowserController."""
    try:
        bc = await get_or_create_browser_controller()
        if bc is None:
            return f"[Error] BrowserController is not available."
        method = getattr(bc, method_name, None)
        if not callable(method):
            return f"[Error] BrowserController has no method '{method_name}'"
        result = await method(**kwargs)
        return result if result is not None else ""
    except Exception as e:
        logger.error("browser_tools dispatch('%s') failed: %s", method_name, e)
        return f"[Tool error: {method_name} — {e}]"


# ---------------------------------------------------------------------------
# Individual 27 Browser Capability Tool Functions
# ---------------------------------------------------------------------------

async def browser_navigate(url: str, expected_domain: str = "", tab: str = "default", **kwargs: Any) -> str:
    """Navigate to a URL in the shared browser session."""
    res = await _dispatch_bc("navigate", url=url, expected_domain=expected_domain, tab=tab, **kwargs)
    return str(res)


async def browser_click(selector: str = "", text: str = "", tab: str = "default", **kwargs: Any) -> str:
    """Click an element by CSS selector or visible text."""
    res = await _dispatch_bc("click", selector=selector, text=text, tab=tab, **kwargs)
    return str(res)


async def browser_fill(selector: str, text: str = "", value: str = "", tab: str = "default", **kwargs: Any) -> str:
    """Fill an input field by CSS selector."""
    res = await _dispatch_bc("fill", selector=selector, text=text, value=value, tab=tab, **kwargs)
    return str(res)


async def browser_get_text(selector: Optional[str] = None, tab: str = "default", **kwargs: Any) -> str:
    """Get text content of an element or the whole page."""
    res = await _dispatch_bc("get_text", selector=selector, tab=tab, **kwargs)
    return str(res)


async def browser_distill_dom(selector: Optional[str] = None, tab: str = "default", max_chars: int = 4000, **kwargs: Any) -> str:
    """Return a compact, LLM-readable DOM summary of the current page."""
    res = await _dispatch_bc("distill_dom", selector=selector, tab=tab, max_chars=max_chars, **kwargs)
    return str(res)


async def browser_screenshot(tab: str = "default", **kwargs: Any) -> Optional[str]:
    """Capture a base64 screenshot of the current page."""
    return await _dispatch_bc("get_screenshot_b64", tab=tab, **kwargs)


async def browser_run_js(script: str, tab: str = "default", **kwargs: Any) -> str:
    """Execute a JavaScript snippet in the page context and return the result."""
    res = await _dispatch_bc("run_js", script=script, tab=tab, **kwargs)
    return str(res)


async def browser_press_key(key: str, selector: str = "", tab: str = "default", **kwargs: Any) -> str:
    """Press a keyboard key, optionally targeting an element."""
    res = await _dispatch_bc("press_key", key=key, selector=selector, tab=tab, **kwargs)
    return str(res)


async def browser_select_option(selector: str, value: str = "", label: str = "", tab: str = "default", **kwargs: Any) -> str:
    """Select a dropdown option by value or label."""
    res = await _dispatch_bc("select_option", selector=selector, value=value, label=label, tab=tab, **kwargs)
    return str(res)


async def browser_hover(selector: str, tab: str = "default", **kwargs: Any) -> str:
    """Hover over an element by CSS selector."""
    res = await _dispatch_bc("hover", selector=selector, tab=tab, **kwargs)
    return str(res)


async def browser_scroll(direction: str = "down", amount: int = 800, selector: str = "", tab: str = "default", **kwargs: Any) -> str:
    """Scroll the page or element up/down by a pixel amount."""
    res = await _dispatch_bc("scroll", direction=direction, amount=amount, selector=selector, tab=tab, **kwargs)
    return str(res)


async def browser_wait_for(selector: str = "", state: str = "visible", tab: str = "default", timeout_ms: int = 10000, **kwargs: Any) -> str:
    """Wait for a CSS selector to reach a given state (visible, hidden, attached, detached)."""
    res = await _dispatch_bc("wait_for", selector=selector, state=state, tab=tab, timeout_ms=timeout_ms, **kwargs)
    return str(res)


async def browser_go_back(tab: str = "default", **kwargs: Any) -> str:
    """Navigate back in the browser history."""
    res = await _dispatch_bc("go_back", tab=tab, **kwargs)
    return str(res)


async def browser_go_forward(tab: str = "default", **kwargs: Any) -> str:
    """Navigate forward in the browser history."""
    res = await _dispatch_bc("go_forward", tab=tab, **kwargs)
    return str(res)


async def browser_reload(tab: str = "default", **kwargs: Any) -> str:
    """Reload the current page."""
    res = await _dispatch_bc("reload_page", tab=tab, **kwargs)
    return str(res)


async def browser_extract_links(tab: str = "default", limit: int = 50, **kwargs: Any) -> str:
    """Extract all hyperlinks from the current page."""
    res = await _dispatch_bc("extract_links", tab=tab, limit=limit, **kwargs)
    return str(res)


async def browser_get_attribute(selector: str, attribute: str, tab: str = "default", **kwargs: Any) -> str:
    """Get an HTML attribute value from an element."""
    res = await _dispatch_bc("get_element_attribute", selector=selector, attribute=attribute, tab=tab, **kwargs)
    return str(res)


async def browser_check_exists(selector: str, tab: str = "default", **kwargs: Any) -> str:
    """Check whether an element matching a CSS selector exists on the page."""
    res = await _dispatch_bc("check_element_exists", selector=selector, tab=tab, **kwargs)
    return str(res)


async def browser_upload_file(selector: str, file_path: str, tab: str = "default", **kwargs: Any) -> str:
    """Upload a file via a file input element."""
    res = await _dispatch_bc("upload_file", selector=selector, file_path=file_path, tab=tab, **kwargs)
    return str(res)


async def browser_list_tabs(**kwargs: Any) -> str:
    """List all open browser tabs by name."""
    res = await _dispatch_bc("list_tabs", **kwargs)
    return str(res)


async def browser_close_tab(tab: str, **kwargs: Any) -> str:
    """Close a named browser tab."""
    res = await _dispatch_bc("close_tab", tab=tab, **kwargs)
    return str(res)


async def browser_switch_tab(tab: str = "default", **kwargs: Any) -> str:
    """Switch the active browser tab."""
    res = await _dispatch_bc("switch_tab", tab=tab, **kwargs)
    return str(res)


async def browser_evaluate_xpath(xpath: str, tab: str = "default", **kwargs: Any) -> str:
    """Evaluate an XPath expression and return matched element text."""
    res = await _dispatch_bc("evaluate_xpath", xpath=xpath, tab=tab, **kwargs)
    return str(res)


async def browser_network_intercept(url_pattern: str = "", action: str = "block", tab: str = "default", **kwargs: Any) -> str:
    """Block or allow network requests matching a URL pattern."""
    res = await _dispatch_bc("network_intercept", url_pattern=url_pattern, action=action, tab=tab, **kwargs)
    return str(res)


async def browser_set_range(selector: str, value: float, tab: str = "default", **kwargs: Any) -> str:
    """Set a range/slider input to a numeric value."""
    res = await _dispatch_bc("set_range_value", selector=selector, value=value, tab=tab, **kwargs)
    return str(res)


async def browser_parallel_scrape(
    urls: Union[List[str], str],
    max_concurrency: int = 4,
    timeout_ms: int = 15000,
    **kwargs: Any,
) -> str:
    """Scrapes multiple URLs concurrently using isolated browser tabs via asyncio.gather."""
    res = await _dispatch_bc("parallel_scrape", urls=urls, max_concurrency=max_concurrency, timeout_ms=timeout_ms, **kwargs)
    return str(res)


async def browser_parallel_search(
    query: str,
    max_results: int = 3,
    tab: str = "default",
    **kwargs: Any,
) -> str:
    """Executes a web search and fans out to scrape the top organic result pages concurrently in parallel."""
    res = await _dispatch_bc("parallel_search", query=query, max_results=max_results, tab=tab, **kwargs)
    return str(res)


# ---------------------------------------------------------------------------
# Declarative Definitions and Registry Mapping
# ---------------------------------------------------------------------------

BROWSER_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL in the shared browser session. Use expected_domain to validate destination.",
        "func": browser_navigate,
        "method": "navigate",
        "schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to"},
                "expected_domain": {"type": "string", "description": "Optional expected domain", "default": ""},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["url"],
        },
        "hints": ["browser", "media", "research", "code"],
        "task_tags": ["browser", "web", "navigate"],
        "priority": 2,
    },
    {
        "name": "browser_click",
        "description": "Click an element by CSS selector or visible text.",
        "func": browser_click,
        "method": "click",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to click", "default": ""},
                "text": {"type": "string", "description": "Visible button or link text", "default": ""},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser", "media"],
        "task_tags": ["browser", "web", "click"],
        "priority": 2,
    },
    {
        "name": "browser_fill",
        "description": "Fill an input field by CSS selector.",
        "func": browser_fill,
        "method": "fill",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of input"},
                "text": {"type": "string", "description": "Text to fill", "default": ""},
                "value": {"type": "string", "description": "Value to fill (alias for text)", "default": ""},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["selector"],
        },
        "hints": ["browser", "media"],
        "task_tags": ["browser", "web", "fill"],
        "priority": 2,
    },
    {
        "name": "browser_get_text",
        "description": "Get text content of an element or the whole page.",
        "func": browser_get_text,
        "method": "get_text",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Optional CSS selector", "default": None},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser", "media"],
        "task_tags": ["browser", "web", "text"],
        "priority": 2,
    },
    {
        "name": "browser_distill_dom",
        "description": "Return a compact, LLM-readable DOM summary of the current page.",
        "func": browser_distill_dom,
        "method": "distill_dom",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Optional CSS selector scoping the distillation", "default": None},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
                "max_chars": {"type": "integer", "description": "Maximum characters to return", "default": 4000},
            },
            "required": [],
        },
        "hints": ["browser", "research"],
        "task_tags": ["browser", "web", "dom"],
        "priority": 2,
    },
    {
        "name": "browser_screenshot",
        "description": "Capture a base64 screenshot of the current page.",
        "func": browser_screenshot,
        "method": "get_screenshot_b64",
        "schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "screenshot"],
        "priority": 2,
    },
    {
        "name": "browser_run_js",
        "description": "Execute a JavaScript snippet in the page context and return the result.",
        "func": browser_run_js,
        "method": "run_js",
        "schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "JavaScript code to execute"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["script"],
        },
        "hints": ["browser", "media"],
        "task_tags": ["browser", "web", "js"],
        "priority": 2,
    },
    {
        "name": "browser_press_key",
        "description": "Press a keyboard key, optionally targeting an element.",
        "func": browser_press_key,
        "method": "press_key",
        "schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key identifier (e.g. 'Enter', 'Tab', 'Escape')"},
                "selector": {"type": "string", "description": "Target CSS selector", "default": ""},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["key"],
        },
        "hints": ["browser", "media"],
        "task_tags": ["browser", "web", "keyboard"],
        "priority": 2,
    },
    {
        "name": "browser_select_option",
        "description": "Select a dropdown option by value or label.",
        "func": browser_select_option,
        "method": "select_option",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Select element CSS selector"},
                "value": {"type": "string", "description": "Option value to select", "default": ""},
                "label": {"type": "string", "description": "Option visible label to select", "default": ""},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["selector"],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "select"],
        "priority": 2,
    },
    {
        "name": "browser_hover",
        "description": "Hover over an element by CSS selector.",
        "func": browser_hover,
        "method": "hover",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Target CSS selector"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["selector"],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "hover"],
        "priority": 2,
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page or element up/down by a pixel amount.",
        "func": browser_scroll,
        "method": "scroll",
        "schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction", "default": "down"},
                "amount": {"type": "integer", "description": "Pixels to scroll", "default": 800},
                "selector": {"type": "string", "description": "Optional container selector", "default": ""},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "scroll"],
        "priority": 2,
    },
    {
        "name": "browser_wait_for",
        "description": "Wait for a CSS selector to reach a given state (visible, hidden, attached, detached).",
        "func": browser_wait_for,
        "method": "wait_for",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to wait for", "default": ""},
                "state": {"type": "string", "enum": ["visible", "hidden", "attached", "detached"], "description": "Target state", "default": "visible"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
                "timeout_ms": {"type": "integer", "description": "Timeout in milliseconds", "default": 10000},
            },
            "required": [],
        },
        "hints": ["browser", "media"],
        "task_tags": ["browser", "web", "wait"],
        "priority": 2,
    },
    {
        "name": "browser_go_back",
        "description": "Navigate back in the browser history.",
        "func": browser_go_back,
        "method": "go_back",
        "schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "navigation"],
        "priority": 2,
    },
    {
        "name": "browser_go_forward",
        "description": "Navigate forward in the browser history.",
        "func": browser_go_forward,
        "method": "go_forward",
        "schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "navigation"],
        "priority": 2,
    },
    {
        "name": "browser_reload",
        "description": "Reload the current page.",
        "func": browser_reload,
        "method": "reload_page",
        "schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "reload"],
        "priority": 2,
    },
    {
        "name": "browser_extract_links",
        "description": "Extract all hyperlinks from the current page.",
        "func": browser_extract_links,
        "method": "extract_links",
        "schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
                "limit": {"type": "integer", "description": "Max links to extract", "default": 50},
            },
            "required": [],
        },
        "hints": ["browser", "research"],
        "task_tags": ["browser", "web", "links"],
        "priority": 2,
    },
    {
        "name": "browser_get_attribute",
        "description": "Get an HTML attribute value from an element.",
        "func": browser_get_attribute,
        "method": "get_element_attribute",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Element CSS selector"},
                "attribute": {"type": "string", "description": "Attribute name (e.g. href, src, value)"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["selector", "attribute"],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "attribute"],
        "priority": 2,
    },
    {
        "name": "browser_check_exists",
        "description": "Check whether an element matching a CSS selector exists on the page.",
        "func": browser_check_exists,
        "method": "check_element_exists",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to test"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["selector"],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "exists"],
        "priority": 2,
    },
    {
        "name": "browser_upload_file",
        "description": "Upload a file via a file input element.",
        "func": browser_upload_file,
        "method": "upload_file",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "File input CSS selector"},
                "file_path": {"type": "string", "description": "Local absolute path of file to upload"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["selector", "file_path"],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "upload"],
        "priority": 2,
    },
    {
        "name": "browser_list_tabs",
        "description": "List all open browser tabs by name.",
        "func": browser_list_tabs,
        "method": "list_tabs",
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "tabs"],
        "priority": 2,
    },
    {
        "name": "browser_close_tab",
        "description": "Close a named browser tab.",
        "func": browser_close_tab,
        "method": "close_tab",
        "schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "description": "Tab name to close"},
            },
            "required": ["tab"],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "tabs"],
        "priority": 2,
    },
    {
        "name": "browser_switch_tab",
        "description": "Switch the active browser tab.",
        "func": browser_switch_tab,
        "method": "switch_tab",
        "schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "description": "Tab name to activate", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "tabs"],
        "priority": 2,
    },
    {
        "name": "browser_evaluate_xpath",
        "description": "Evaluate an XPath expression and return matched element text.",
        "func": browser_evaluate_xpath,
        "method": "evaluate_xpath",
        "schema": {
            "type": "object",
            "properties": {
                "xpath": {"type": "string", "description": "XPath expression"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["xpath"],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "xpath"],
        "priority": 2,
    },
    {
        "name": "browser_network_intercept",
        "description": "Block or allow network requests matching a URL pattern.",
        "func": browser_network_intercept,
        "method": "network_intercept",
        "schema": {
            "type": "object",
            "properties": {
                "url_pattern": {"type": "string", "description": "URL wildcard/regex pattern", "default": ""},
                "action": {"type": "string", "enum": ["block", "allow"], "description": "Action", "default": "block"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": [],
        },
        "hints": ["browser"],
        "task_tags": ["browser", "web", "network"],
        "priority": 2,
    },
    {
        "name": "browser_set_range",
        "description": "Set a range/slider input to a numeric value.",
        "func": browser_set_range,
        "method": "set_range_value",
        "schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Slider element CSS selector"},
                "value": {"type": "number", "description": "Target numeric value"},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["selector", "value"],
        },
        "hints": ["browser", "media"],
        "task_tags": ["browser", "web", "input"],
        "priority": 2,
    },
    {
        "name": "browser_parallel_scrape",
        "description": "Scrapes multiple URLs concurrently using isolated browser tabs via asyncio.gather.",
        "func": browser_parallel_scrape,
        "method": "parallel_scrape",
        "schema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of URLs or comma-separated string to scrape concurrently",
                },
                "max_concurrency": {"type": "integer", "description": "Concurrency cap (max 4)", "default": 4},
                "timeout_ms": {"type": "integer", "description": "Per-page timeout in ms", "default": 15000},
            },
            "required": ["urls"],
        },
        "hints": ["browser", "research"],
        "task_tags": ["browser", "web", "scrape", "parallel"],
        "priority": 2,
    },
    {
        "name": "browser_parallel_search",
        "description": "Executes a web search and immediately fans out to scrape the top organic result pages concurrently in parallel.",
        "func": browser_parallel_search,
        "method": "parallel_search",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "max_results": {"type": "integer", "description": "Number of organic search results to scrape", "default": 3},
                "tab": {"type": "string", "description": "Tab name", "default": "default"},
            },
            "required": ["query"],
        },
        "hints": ["browser", "research"],
        "task_tags": ["browser", "web", "search", "parallel"],
        "priority": 2,
    },
]


def register_browser_tools(registry: Any) -> None:
    """
    Registers all 27 browser capability tools into Makima's ToolRegistry.
    Compatible with registry.register_tool(), registry.register(), registry.add_tool(), or dict-like registries.
    """
    if registry is None:
        logger.warning("register_browser_tools: registry is None, skipping")
        return

    registered_count = 0
    for tool_def in BROWSER_TOOL_DEFINITIONS:
        name = tool_def["name"]
        func = tool_def["func"]
        description = tool_def["description"]
        schema = tool_def["schema"]
        category = "browser"
        hints = tool_def.get("hints", ["browser"])
        task_tags = tool_def.get("task_tags", ["browser", "web"])
        priority = tool_def.get("priority", 2)

        try:
            if hasattr(registry, "register_tool"):
                registry.register_tool(
                    name=name,
                    description=description,
                    func=func,
                    schema=schema,
                    category=category,
                    agent_hints=hints,
                    task_tags=task_tags,
                    priority=priority,
                    parallel_safe=False,
                    timeout_s=45.0,
                )
            elif hasattr(registry, "register"):
                registry.register(
                    name=name,
                    func=func,
                    description=description,
                    schema=schema,
                    category=category,
                    agent_hints=hints,
                    task_tags=task_tags,
                    priority=priority,
                )
            elif hasattr(registry, "add_tool"):
                registry.add_tool(
                    name=name,
                    func=func,
                    description=description,
                    schema=schema,
                    category=category,
                )
            else:
                registry[name] = func
            registered_count += 1
        except Exception as e:
            logger.warning("Failed to register browser tool '%s': %s", name, e)

    logger.info("Successfully registered %d browser capability tools into ToolRegistry.", registered_count)
