"""
Makima v8.0 — Production Tool Registry

Improvements over v7.1:
  - ToolMeta: category, agent_hints, task_tags, priority, is_destructive
  - Per-agent filtered manifest — LLM only sees tools relevant to current agent
    (60-tool full manifest confused LLM and wasted tokens)
  - Manifest cache — rebuilt only when a new tool is registered
  - Call telemetry — per-tool call_count, failure_count, avg_latency_ms
  - Parallel tool call dispatch — run multiple tools concurrently
  - register_tool_group() — batch registration for cleaner main.py
  - Tool search by tag/category for dynamic routing
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("makima.tool_registry")


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

    # Runtime telemetry (mutated in-place, never serialised)
    call_count: int = field(default=0, compare=False, repr=False)
    failure_count: int = field(default=0, compare=False, repr=False)
    total_latency_ms: float = field(default=0.0, compare=False, repr=False)

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

    Thread-safety: all public mutating methods are synchronous and safe to
    call from a single async event loop; the registry itself is not designed
    for concurrent modification from multiple threads.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolMeta] = {}
        # Manifest cache keyed by agent_name ("" = full manifest).
        # Invalidated on every register_tool / register_tool_group call.
        self._manifest_cache: dict[str, list[dict]] = {}

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
    ) -> None:
        """Register an async Python function as a callable tool."""
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
        )
        self._manifest_cache.clear()          # Invalidate cache
        logger.debug("Registered tool: %s [cat=%s]", name, category)

    def register_tool_group(self, tools: list[dict]) -> None:
        """
        Batch registration. Each dict must have the same keys as register_tool().
        Example:
            registry.register_tool_group([
                dict(name="browser_navigate", description="...", func=nav, schema={...},
                     category="browser", agent_hints=["browser", "research"]),
                ...
            ])
        """
        for t in tools:
            self.register_tool(**t)

    def register_wasm_skill(self, name: str, description: str, skill_teacher: Any) -> None:
        """Register a WASM skill as a tool (single-string input)."""
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
        return name in self._tools

    def get_tool(self, name: str) -> Optional[ToolMeta]:
        return self._tools.get(name)

    def tools_by_category(self, category: str) -> list[str]:
        return [n for n, t in self._tools.items() if t.category == category]

    def tools_by_tag(self, tag: str) -> list[str]:
        return [n for n, t in self._tools.items() if tag in t.task_tags]

    # ------------------------------------------------------------------
    # Manifest generation
    # ------------------------------------------------------------------

    def get_manifest(self) -> list[dict]:
        """Full OpenAI-compatible tools manifest (all tools, sorted by priority)."""
        return self._build_manifest("")

    def get_manifest_for_agent(self, agent_name: str) -> list[dict]:
        """
        Agent-filtered manifest. Returns tools where agent_hints is empty
        (globally available) OR contains agent_name. Also includes tools with
        no agent_hints (they're shared by design).

        Benefits: smaller manifest → fewer wasted tokens, less LLM confusion,
        faster response times. A research_agent never needs browser_set_range;
        a media_agent never needs shell_exec.
        """
        return self._build_manifest(agent_name)

    def get_manifest_by_task(self, task_tag: str) -> list[dict]:
        """Return tools tagged for a specific task type (e.g. 'web', 'code', 'media')."""
        key = f"task:{task_tag}"
        if key in self._manifest_cache:
            return self._manifest_cache[key]
        filtered = [
            t.to_openai_function()
            for t in sorted(self._tools.values(), key=lambda x: x.priority)
            if task_tag in t.task_tags or not t.task_tags
        ]
        self._manifest_cache[key] = filtered
        return filtered

    def _build_manifest(self, agent_name: str) -> list[dict]:
        if agent_name in self._manifest_cache:
            return self._manifest_cache[agent_name]

        tools_sorted = sorted(self._tools.values(), key=lambda x: x.priority)

        if not agent_name:
            # Full manifest
            manifest = [t.to_openai_function() for t in tools_sorted]
        else:
            manifest = [
                t.to_openai_function()
                for t in tools_sorted
                if not t.agent_hints or agent_name in t.agent_hints
            ]

        self._manifest_cache[agent_name] = manifest
        return manifest

    def get_telemetry(self) -> list[dict]:
        """Runtime stats for all tools — useful for health dashboard."""
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

    async def call_tool(self, name: str, **kwargs: Any) -> str:
        """
        Dispatch a single tool call.  Records telemetry (latency + failures).
        Raises KeyError if tool not found.
        """
        if name not in self._tools:
            raise KeyError(f"Tool not found: {name}")

        meta = self._tools[name]
        t0 = time.monotonic()
        meta.call_count += 1

        try:
            result = await meta.func(**kwargs)
            latency = (time.monotonic() - t0) * 1000
            meta.total_latency_ms += latency
            return str(result)
        except RuntimeError as e:
            meta.failure_count += 1
            if "CAPTCHA detected" in str(e):
                raise
            logger.error("Tool %s failed: %s", name, e)
            return f"Error executing tool {name}: {e}"
        except Exception as e:
            meta.failure_count += 1
            logger.error("Tool %s failed: %s", name, e)
            return f"Error executing tool {name}: {e}"
        finally:
            pass  # latency already recorded above on success path

    async def call_tools_parallel(
        self,
        calls: list[dict[str, Any]],
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        """
        Execute multiple tool calls concurrently.

        Args:
            calls: list of dicts, each with keys: "name", "params" (dict), "call_id" (optional)
            timeout: per-call timeout in seconds

        Returns:
            list of dicts: {"call_id", "name", "result", "success", "latency_ms"}
        """
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
