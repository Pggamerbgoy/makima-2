"""
Makima v8.1 — Model Context Protocol (MCP) Tool Adapter & Async Multiplexer (Anthropic Specification)

Allows Makima to connect to standard MCP servers (local stdio subprocesses or remote transports),
multiplex concurrent JSON-RPC 2.0 requests/responses asynchronously over stdin/stdout,
discover available tools and resources via standard JSON-RPC methods, and register them directly
into Makima's ToolRegistry.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from .types import Tool, ToolDefinition, ToolPolicy

logger = logging.getLogger("makima.tools.mcp")


class AsyncMcpMultiplexer:
    """
    High-performance, non-blocking MCP JSON-RPC 2.0 client.
    Enables full bi-directional multiplexing over stdio without serializing locks
    during response waiting.
    """

    def __init__(self, command: list[str], env: Optional[dict[str, str]] = None) -> None:
        self.command = command
        self.env = env
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_counter: int = 0
        self._pending_futures: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._write_lock = asyncio.Lock()
        # Bug 7 fix: track initialization state so restart triggers re-handshake
        self._initialized: bool = False

    @property
    def _request_id(self) -> int:
        return self._request_counter

    @_request_id.setter
    def _request_id(self, val: int) -> None:
        self._request_counter = val

    @property
    def _pending_requests(self) -> dict[int, asyncio.Future[dict[str, Any]]]:
        return self._pending_futures

    async def start(self) -> None:
        """Spawn subprocess and launch continuous background stdout reader loop."""
        if self.process and self.process.returncode is None:
            return
        logger.info("Starting Async MCP multiplexer stdio server: %s", " ".join(self.command))
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        # Bug 7 fix: clear initialized flag whenever the process is (re)started
        self._initialized = False
        self._reader_task = asyncio.create_task(self._stdout_reader_loop())
        logger.info("Async MCP multiplexer online: pid=%s", self.process.pid)

    async def _stdout_reader_loop(self) -> None:
        """Continuously read JSON-RPC responses and resolve corresponding caller futures."""
        try:
            while self.process and self.process.stdout and self.process.returncode is None:
                line = await self.process.stdout.readline()
                if not line:
                    break  # Stream closed
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue
                try:
                    payload = json.loads(line_str)
                    req_id = payload.get("id")
                    if req_id is not None and req_id in self._pending_futures:
                        future = self._pending_futures.pop(req_id)
                        if not future.done():
                            future.set_result(payload)
                except json.JSONDecodeError:
                    logger.warning("MCP invalid JSON-RPC line received: %s", line_str[:100])
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("MCP reader loop exception: %s", e)
        finally:
            # Drain any remaining pending futures with failure
            for fid, fut in list(self._pending_futures.items()):
                if not fut.done():
                    fut.set_exception(RuntimeError("MCP server process closed connection"))
            self._pending_futures.clear()

    async def _reader_loop(self) -> None:
        """Backward compatibility alias for _stdout_reader_loop."""
        await self._stdout_reader_loop()

    async def stop(self) -> None:
        """Cancel reader loop, close stdin, terminate process gracefully."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self.process:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                    await self.process.stdin.wait_closed()
                except Exception:
                    pass
            if self.process.returncode is None:
                try:
                    self.process.terminate()
                    await asyncio.wait_for(self.process.wait(), timeout=3.0)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
            self.process = None

    async def call_method(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        timeout_s: float = 30.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Dispatches a concurrent JSON-RPC method without blocking sibling requests.
        Holds write lock only during stdin write + drain.
        """
        timeout = kwargs.get("timeout", timeout_s)

        if not self.process or self.process.returncode is not None:
            await self.start()

        # Bug 7 fix: re-send initialize handshake if the process was restarted.
        # MCP protocol requires initialize as the very first call on every new connection.
        if not self._initialized and method != "initialize":
            try:
                await self._send_raw_method("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "MakimaBrain", "version": "8.1"},
                }, timeout=timeout)
                self._initialized = True
                logger.info("MCP re-initialize handshake completed after process restart")
            except Exception as init_err:
                logger.warning("MCP re-initialize failed: %s", init_err)

        if method == "initialize":
            self._initialized = True

        return await self._send_raw_method(method, params, timeout=timeout)

    async def _send_raw_method(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Low-level JSON-RPC dispatch. Does not perform re-initialization checks."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        async with self._write_lock:
            self._request_counter += 1
            req_id = self._request_counter
            self._pending_futures[req_id] = future

            packet = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            raw_bytes = (json.dumps(packet) + "\n").encode("utf-8")

            if not self.process or not self.process.stdin:
                self._pending_futures.pop(req_id, None)
                raise RuntimeError("MCP process stdin unavailable")

            self.process.stdin.write(raw_bytes)
            await self.process.stdin.drain()

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            if "error" in response:
                err = response["error"]
                code = err.get("code") if isinstance(err, dict) else "unknown"
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                raise RuntimeError(f"MCP RPC Error ({code}): {msg}")
            return response.get("result", {})
        except asyncio.TimeoutError:
            self._pending_futures.pop(req_id, None)
            raise TimeoutError(f"MCP method '{method}' (id={req_id}) timed out after {timeout}s")



# Backward compatibility alias
McpStdioClient = AsyncMcpMultiplexer


class McpToolAdapter:
    """Discovers tools from an MCP client and registers them into Makima's ToolRegistry."""

    def __init__(self, client: AsyncMcpMultiplexer | McpStdioClient, prefix: str = "mcp") -> None:
        self.client = client
        self.prefix = prefix

    async def discover_and_register(self, registry: Any) -> list[str]:
        """Query MCP server via tools/list and register all definitions in ToolRegistry."""
        try:
            # Initialize handshake
            await self.client.call_method("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "MakimaBrain", "version": "8.1"},
            })

            result = await self.client.call_method("tools/list", {})
            tools_data = result.get("tools", [])
            registered_names: list[str] = []

            for t_data in tools_data:
                raw_name = t_data.get("name", "")
                if not raw_name:
                    continue
                tool_name = f"{self.prefix}_{raw_name}" if self.prefix else raw_name
                desc = t_data.get("description", f"MCP Tool: {raw_name}")
                schema = t_data.get("inputSchema", {"type": "object", "properties": {}})

                # Bug 6 fix: capture `client` by value in the closure, NOT `self`.
                # If the McpToolAdapter is GC'd after discover_and_register() returns,
                # capturing `self` would cause AttributeError at tool call time.
                _client = self.client

                async def handler(_mcp_raw_name=raw_name, _client=_client, **kwargs) -> Any:
                    call_res = await _client.call_method("tools/call", {
                        "name": _mcp_raw_name,
                        "arguments": kwargs,
                    })
                    content = call_res.get("content", [])
                    if isinstance(content, list):
                        text_blocks = [c.get("text", "") for c in content if c.get("type") == "text"]
                        return "\n".join(text_blocks) if text_blocks else str(call_res)
                    return str(call_res)

                definition = ToolDefinition(
                    name=tool_name,
                    description=desc,
                    parameters=schema,
                )
                policy = ToolPolicy(timeout_s=60.0, max_retries=1)

                tool_obj = Tool(
                    definition=definition,
                    handler=handler,
                    policy=policy,
                )
                if hasattr(registry, "register"):
                    registry.register(tool_obj)
                elif hasattr(registry, "register_tool_obj"):
                    registry.register_tool_obj(tool_obj)
                else:
                    registry.register_tool(
                        name=tool_obj.name,
                        description=tool_obj.description,
                        func=tool_obj.handler,
                        schema=tool_obj.schema,
                        category=tool_obj.category,
                        agent_hints=tool_obj.agent_hints,
                        task_tags=tool_obj.task_tags,
                        priority=tool_obj.priority,
                        is_destructive=tool_obj.is_destructive,
                        timeout_s=tool_obj.policy.timeout_s,
                        max_retries=tool_obj.policy.max_retries,
                        retry_backoff_s=tool_obj.policy.retry_backoff_s,
                        retryable_keywords=tool_obj.policy.retryable_keywords,
                        permissions=tool_obj.policy.permissions,
                        default_permission=tool_obj.policy.default_permission,
                    )
                registered_names.append(tool_name)
                logger.info("Registered MCP tool: %s", tool_name)

            return registered_names
        except Exception as e:
            logger.error("Failed to discover MCP tools: %s", e)
            return []


__all__ = [
    "AsyncMcpMultiplexer",
    "McpStdioClient",
    "McpToolAdapter",
]
