"""
Makima OS — Model Context Protocol (MCP) Server (Anthropic Specification)

Allows external IDEs and agents (Claude Desktop, Cursor, VS Code, Windsurf, etc.)
to connect to Makima over standard JSON-RPC 2.0 stdio and execute Makima's 140+ native OS tools.

Configuration for Claude Desktop (claude_desktop_config.json):
{
  "mcpServers": {
    "makima": {
      "command": "python",
      "args": ["-m", "apps.brain.mcp_server"]
    }
  }
}
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import sys
from typing import Any, Optional

# CRITICAL MCP RULE: Never write log output to stdout.
# stdout is strictly reserved for JSON-RPC 2.0 message frames.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [makima.mcp_server] %(message)s",
)
logger = logging.getLogger("makima.mcp_server")


class MakimaMcpServer:
    """
    Standard MCP stdio server providing external tools access to Makima's ToolRegistry.
    """

    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME = "makima-os"
    SERVER_VERSION = "1.0.0"

    def __init__(self, tool_registry: Optional[Any] = None) -> None:
        self.registry = tool_registry
        self._running: bool = False

    async def initialize_tools(self) -> None:
        """Ensure core tools are registered if registry was not pre-populated."""
        if self.registry is None:
            from apps.brain.tool_registry import ToolRegistry
            from apps.brain.core.tool_loader import register_core_tools

            self.registry = ToolRegistry()
            register_core_tools(self.registry)
            logger.info("MakimaMcpServer: Initialized %d core tools.", len(self.registry.get_all_tool_names()))

    async def handle_request(self, request: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Process a single JSON-RPC 2.0 request and return the response."""
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {}) or {}

        # 1. initialize
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {
                            "listChanged": False,
                        },
                    },
                    "serverInfo": {
                        "name": self.SERVER_NAME,
                        "version": self.SERVER_VERSION,
                    },
                },
            }

        # 2. notifications/initialized or notifications/cancelled
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None

        # 3. ping
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        # 4. tools/list
        if method == "tools/list":
            tools_list = []
            if self.registry:
                for tool_meta in self.registry.get_all():
                    if not getattr(tool_meta, "enabled", True):
                        continue
                    schema = tool_meta.schema or {"type": "object", "properties": {}}
                    tools_list.append({
                        "name": tool_meta.name,
                        "description": tool_meta.description or f"Makima OS tool: {tool_meta.name}",
                        "inputSchema": schema,
                    })
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tools_list},
            }

        # 5. tools/call
        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}

            if not self.registry or not self.registry.has_tool(tool_name):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: Tool '{tool_name}' not found."}],
                        "isError": True,
                    },
                }

            tool_meta = self.registry.get_tool(tool_name)
            try:
                from apps.brain.core.sdk_bridge import adapt_tool_call_args

                call_args = adapt_tool_call_args(tool_meta.func, arguments)
                timeout = getattr(tool_meta, "timeout_s", 60.0) or 60.0

                res = tool_meta.func(**call_args)
                if inspect.isawaitable(res):
                    res = await asyncio.wait_for(res, timeout=timeout)

                output_str = str(res) if not isinstance(res, (dict, list)) else json.dumps(res, ensure_ascii=False, indent=2)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": output_str}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                logger.error("Tool execution failed for '%s': %s", tool_name, exc, exc_info=True)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Execution error in {tool_name}: {exc}"}],
                        "isError": True,
                    },
                }

        # Unknown method
        if req_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }
        return None

    async def run_stdio(self) -> None:
        """Main stdio loop reading JSON-RPC requests line-by-line."""
        await self.initialize_tools()
        logger.info("Makima MCP Server listening on stdio...")
        self._running = True

        while self._running:
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                clean_line = line.strip()
                if not clean_line:
                    continue

                try:
                    req_data = json.loads(clean_line)
                except json.JSONDecodeError:
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error: Invalid JSON"},
                    }
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()
                    continue

                response = await self.handle_request(req_data)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except Exception as e:
                logger.error("Error in MCP stdio loop: %s", e)
                break


async def main() -> None:
    parser = argparse.ArgumentParser(description="Makima OS MCP Stdio Server")
    parser.add_argument("--list-tools", action="store_true", help="Print all available tool names and exit")
    args = parser.parse_args()

    server = MakimaMcpServer()
    await server.initialize_tools()

    if args.list_tools:
        names = server.registry.get_all_tool_names() if server.registry else []
        print(f"Total tools available: {len(names)}", file=sys.stderr)
        for n in sorted(names):
            print(f"  - {n}", file=sys.stderr)
        return

    await server.run_stdio()


if __name__ == "__main__":
    asyncio.run(main())
