"""
Makima v7.2 — Elite Tools Registry
Dynamically registers all elite and personal assistant tools with zero-crash resilience.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger("makima.tools")


class EliteToolRegistry:
    """
    Central registry for all Makima OS tools. 
    Ensures non-blocking async execution and graceful degradation if specific tool modules fail to load.
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._register_all()

    def _register_all(self) -> None:
        """Registers all core elite and personal assistant tool categories."""
        
        # 1-3: Core Elite Tools
        self._safe_register("data tools", ".data_tools", "register_data_tools")
        self._safe_register("security tools", ".security_tools", "register_security_tools")
        self._safe_register("devops tools", ".devops_tools", "register_devops_tools")

        # 4-6: New Personal Assistant Tools
        self._safe_register("finance tools", ".finance_tools", "register_finance_tools")
        self._safe_register("calendar tools", ".calendar_tools", "register_calendar_tools")
        self._safe_register("notification tools", ".notification_tools", "register_notification_tools")

    def _safe_register(self, category: str, module_name: str, func_name: str) -> None:
        """
        Safely imports a tool module and executes its registration function.
        Catches and logs any import or execution errors to maintain zero-crash resilience.
        """
        try:
            module = importlib.import_module(module_name, package=__name__)
            register_func: Callable[[EliteToolRegistry], None] = getattr(module, func_name)
            register_func(self)
            logger.debug("Successfully registered %s.", category)
        except Exception as e:
            logger.warning("Failed to register %s: %s", category, e)

    def register(self, name: str, func: Callable[..., Any], description: str = "") -> None:
        """
        Registers a single tool function into the registry.
        
        Args:
            name: Unique identifier for the tool.
            func: The callable (sync or async) that executes the tool's logic.
            description: A brief description of the tool's capabilities for the LLM.
        """
        self._tools[name] = {"func": func, "desc": description}

    async def call_tool(self, name: str, **kwargs: Any) -> Any:
        """
        Asynchronously executes a registered tool.
        Automatically handles both async and synchronous tool functions without blocking the event loop.
        
        Args:
            name: The unique identifier of the tool to call.
            **kwargs: Arguments to pass to the tool function.
            
        Returns:
            The result of the tool execution.
        """
        if name not in self._tools:
            raise RuntimeError(f"Tool '{name}' not found in registry.")

        func = self._tools[name]["func"]

        try:
            if inspect.iscoroutinefunction(func):
                return await func(**kwargs)
            else:
                # Offload synchronous tools to a thread pool to prevent blocking the async event loop
                return await asyncio.to_thread(func, **kwargs)
        except Exception as e:
            logger.error("Error executing tool '%s': %s", name, e, exc_info=True)
            raise

    def get_tool_list(self) -> list[dict[str, str]]:
        """
        Retrieves a formatted list of all registered tools and their descriptions.
        Useful for injecting tool schemas into LLM system prompts.
        """
        return [{"name": k, "description": v["desc"]} for k, v in self._tools.items()]

    def has_tool(self, name: str) -> bool:
        """Checks if a specific tool is currently registered and available."""
        return name in self._tools
