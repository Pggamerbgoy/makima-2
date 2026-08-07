"""Makima v7.2 — Elite System Agent: Enterprise OS Controller, Hardware Diagnostics, 
Process Monitor, and System Lifecycle Management Engine.

This agent provides deep, asynchronous, and safe control over the host operating system.
All destructive operations (process termination, power state changes, service management)
are routed through a strict confirmation gate to prevent accidental system disruption.
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import socket
import subprocess
from typing import Any, Callable, Awaitable, Optional

# ==============================================================================
# ZERO-CRASH RESILIENCE IMPORTS
# ==============================================================================
try:
    import psutil
except ImportError:
    psutil = None
    logging.getLogger("makima.agents.system").warning("psutil not found. Hardware/Process tools disabled.")

try:
    import win32gui
    import win32con
    import win32process
except ImportError:
    win32gui = win32con = win32process = None
    logging.getLogger("makima.agents.system").warning("pywin32 not found. Advanced window management disabled.")

try:
    import pyautogui
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None
    logging.getLogger("makima.agents.system").warning("pyautogui not found. Native mouse control disabled.")

from .base_agent import BaseAgent

logger = logging.getLogger("makima.agents.system")


def _process_running(probe: str) -> bool:
    """True if any running process name matches probe (case-insensitive, .exe tolerant)."""
    if not psutil:
        return False
    probe = probe.lower().replace(".exe", "")
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name in (probe, probe + ".exe") or probe in name:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


async def launch_app_verified(app_path: str, arguments: list[str] | None = None) -> str:
    """Launch an application asynchronously with pre-launch name verification and
    post-launch process verification. Single source of truth: the SystemAgent tool
    map, the global ToolRegistry, and workflow_builder all route through this, so
    every caller gets the same verified behavior."""
    import os
    import shutil
    import sys

    raw_app = (app_path or "").strip()
    app_clean = os.path.basename(raw_app).replace(".exe", "").strip().lower()

    # Common application name resolution
    app_alias_map = {
        "brave": "brave",
        "chrome": "chrome",
        "msedge": "msedge",
        "edge": "msedge",
        "microsoftedge": "msedge",
        "notepad": "notepad",
        "calc": "calc",
        "calculator": "calc",
        "cmd": "cmd",
        "terminal": "wt",
        "explorer": "explorer",
    }
    target_cmd = app_alias_map.get(app_clean, raw_app)

    # Pre-launch verification: refuse unknown apps that don't resolve to anything real
    if app_clean not in app_alias_map:
        is_abs_existing = os.path.isabs(raw_app) and os.path.exists(raw_app)
        if not is_abs_existing and not shutil.which(app_clean):
            known = ", ".join(sorted(app_alias_map))
            return (f"App '{app_path}' not recognized — refusing to launch blindly. "
                    f"Known apps: {known}. Or pass a full path to an existing executable "
                    f"(e.g. C:\\Program Files\\App\\app.exe).")

    args = [target_cmd] + (arguments or [])

    # If opening Chrome, Brave, or Edge, ensure remote debugging port is present for CDP
    if app_clean in ("chrome", "brave", "edge", "msedge"):
        if not any("--remote-debugging-port" in a for a in args):
            args.append("--remote-debugging-port=9222")

    # Windows shell execution fallback (start "" app_name)
    if sys.platform == "win32":
        try:
            full_cmd = subprocess.list2cmdline(args)
            # FIX: Use async subprocess instead of blocking os.system
            await asyncio.create_subprocess_shell(
                f'start "" {full_cmd}',
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info(f"[system] Launched application via Windows shell: {full_cmd}")
            # Post-launch verification: poll for a matching process
            probe = os.path.basename(target_cmd).replace(".exe", "").lower()
            for _ in range(10):
                await asyncio.sleep(0.5)
                if _process_running(probe):
                    return f"Launched {app_clean.capitalize()} successfully (verified running)."
            return (f"Launch command sent for '{app_clean}', but no matching process "
                    f"appeared within 5s. The app name may be wrong or it failed to start.")
        except Exception as e_start:
            logger.warning(f"[system] Shell start failed for {target_cmd}: {e_start}")

    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return f"Launched {app_clean.capitalize()} successfully (PID: {process.pid})."
    except Exception:
        try:
            cmd_str = subprocess.list2cmdline(args)
            process = await asyncio.create_subprocess_shell(
                cmd_str, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return f"Launched {app_clean.capitalize()} via shell successfully."
        except Exception as e:
            return f"Failed to launch {app_clean}: {str(e)}"


class SystemAgent(BaseAgent):
    """Elite System Agent for Makima OS v7.2."""
    
    AGENT_NAME = "system"
    DESCRIPTION = "Enterprise OS control, hardware diagnostics, process monitoring, and lifecycle management."
    CAPABILITIES = ["process_management", "hardware_monitor", "desktop_cleanup", "file_operations", "system_diagnostics", "window_management", "service_management", "power_management"]
    AGENT_TOOLS = ["get_system_stats", "get_process_list", "kill_process", "set_process_priority", "launch_app", "manage_window", "system_power", "network_diagnostics", "manage_service", "get_network_adapters", "organize_desktop", "move_file", "copy_file", "rename_file", "write_file", "read_file", "mouse_click", "mouse_draw"]
    TAGS = ["system", "os", "local", "files", "desktop", "process", "hardware", "mouse", "drawing"]

    _CRITICAL_PROCESSES = {
        "lsass", "winlogon", "wininit", "csrss", "smss", "services", "svchost",
        "explorer", "dwm", "system", "registry", "session manager", "winmgmt",
        "spoolsv", "audiodg", "fontdrvhost", "taskhostw", "sihost"
    }
    _JUNK_PROCESSES = {"onedrive", "widgets", "cortana", "widgetservice"}

    
    SYSTEM_PROMPT = """You are Makima's Elite System Agent. You are an enterprise-grade OS controller, 
hardware diagnostics expert, process monitor, and system lifecycle management engine.

PROCESS SAFETY RULES (follow before every kill/launch decision):
- NEVER kill critical system processes (lsass, winlogon, wininit, csrss, smss, services,
  svchost, explorer, dwm, system, audiodg). The kill_process tool refuses them — do not
  try to work around the refusal.
- Before acting, call get_process_list() and reason about each candidate: high CPU/RAM +
  non-system name = wasteful/bloat (junk); system-owned name = dangerous (critical);
  otherwise normal. Explain the reasoning briefly in your reply.
- Prefer killing only what the user explicitly asked for or what clearly matches a filter.
- launch_app refuses unknown app names — if refused, suggest the closest known app
  or ask the user for a full path instead of guessing.

You have access to the following advanced tools (native function calls):
- get_system_stats(): Retrieve comprehensive CPU, RAM, Disk, and Network statistics.
- get_process_list(filter_name: str, top_n: int): List running processes, optionally filtered by name.
- kill_process(process_name: str, pid: int, force: bool): Terminate a process by name (e.g. 'msedge', 'chrome', 'notepad') or PID.
- set_process_priority(pid: int, priority: str): Set process priority (idle, below_normal, normal, above_normal, high, realtime).
- launch_app(app_path: str, arguments: list[str]): Launch an application with optional arguments.
- manage_window(action: str, title: str): Manage windows (focus, minimize, maximize, restore, close).
- system_power(task_id: str, action: str): Control system power (sleep, hibernate, restart, shutdown). (HIGHLY DESTRUCTIVE)
- network_diagnostics(target: str, action: str): Run network diagnostics (ping, tracert, dns).
- manage_service(task_id: str, service_name: str, action: str): Start, stop, or restart a system service. (DESTRUCTIVE)
- organize_desktop(): Automatically organize loose files on Desktop into categorized subfolders (Documents, Images, Videos, Audio, Code, Archives, Shortcuts).
- move_file(source_path: str, target_folder_or_path: str): Move a file or folder from source to target folder/path (supports Desktop, Downloads, Documents, etc.).
- copy_file(source_path: str, target_folder_or_path: str): Copy a file or folder to a destination path.
- rename_file(file_path: str, new_name: str): Rename a file or folder in place.
- get_network_adapters(): List all network interfaces and their IP configurations.
- write_file(path: str, content: str): Write content to a file on disk.
- read_file(path: str): Read the contents of a file from disk.
- mouse_click(x: int, y: int, button: str, double: bool): Native mouse click at desktop coordinates (x, y).
- mouse_draw(shape: str, start_x: int, start_y: int, size: int, points: list): Draw shapes ('circle', 'square', 'star', 'heart', 'triangle') or custom mouse paths in Paint/Photoshop/Canvas.

CRITICAL INSTRUCTIONS (ReAct loop):
- When the user's request needs an action, CALL the appropriate tool via function calling.
  Never describe what you would do — actually call it.
- You will receive the tool's result after each call. Use it to decide the next step:
  think, act, observe, repeat — until the task is complete.
- If a tool fails or returns an error, try a different approach (corrected parameters or a
  different tool) before giving up. Never invent results — only report what tools actually returned.
- Once the task is done, reply to the user in plain conversational text (Hinglish/English mixed
  is fine). Do NOT wrap your final reply in JSON or code fences."""

    def __init__(
        self,
        ai_handler: Any = None,
        memory: Any = None,
        tool_registry: Any = None,
        ws_broadcast: Optional[Callable[..., Awaitable[Any]]] = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        **kwargs: Any
    ) -> None:
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, **kwargs)
        self._current_task_id: str | None = None
        self._TOOL_MAP: dict[str, Callable[..., Awaitable[str | dict | list]]] = {
            "get_system_stats": self._tool_get_system_stats,
            "get_process_list": self._tool_get_process_list,
            "kill_process": self._tool_kill_process,
            "set_process_priority": self._tool_set_process_priority,
            "launch_app": self._tool_launch_app,
            "manage_window": self._tool_manage_window,
            "system_power": self._tool_system_power,
            "network_diagnostics": self._tool_network_diagnostics,
            "manage_service": self._tool_manage_service,
            "get_network_adapters": self._tool_get_network_adapters,
            "organize_desktop": self._tool_organize_desktop,
            "move_file": self._tool_move_file,
            "copy_file": self._tool_copy_file,
            "rename_file": self._tool_rename_file,
            "read_file": self._tool_read_file,
            "write_file": self._tool_write_file,
            "mouse_click": self._tool_mouse_click,
            "mouse_draw": self._tool_mouse_draw,
        }
        if tool_registry is not None:
            self._register_tools_with_registry(tool_registry)

    _TOOL_SCHEMAS: dict[str, tuple[dict, bool]] = {
        "mouse_click": ({"type": "object", "properties": {
            "x": {"type": "integer", "description": "X desktop coordinate"},
            "y": {"type": "integer", "description": "Y desktop coordinate"},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            "double": {"type": "boolean", "default": False}},
            "required": ["x", "y"]}, False),
        "mouse_draw": ({"type": "object", "properties": {
            "shape": {"type": "string", "description": "Shape: circle, square, star, heart, triangle, or custom"},
            "start_x": {"type": "integer", "default": 500},
            "start_y": {"type": "integer", "default": 500},
            "size": {"type": "integer", "default": 150},
            "points": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}, "description": "Custom path points [[x1,y1], [x2,y2], ...]"}},
            "required": []}, False),
        "get_system_stats": ({"type": "object", "properties": {}, "required": []}, False),
        "get_process_list": ({"type": "object", "properties": {
            "filter_name": {"type": "string", "description": "Optional process name filter"},
            "top_n": {"type": "integer", "description": "Number of top processes to return (default 10)"}},
            "required": []}, False),
        "kill_process": ({"type": "object", "properties": {
            "process_name": {"type": "string", "description": "Process name, e.g. 'chrome', 'notepad', 'msedge'"},
            "pid": {"type": "integer", "description": "Process PID (alternative to process_name)"},
            "force": {"type": "boolean", "description": "Force kill (default true)"}},
            "required": []}, True),
        "set_process_priority": ({"type": "object", "properties": {
            "pid": {"type": "integer"},
            "priority": {"type": "string", "enum": ["idle", "below_normal", "normal", "above_normal", "high", "realtime"]}},
            "required": ["pid", "priority"]}, False),
        "launch_app": ({"type": "object", "properties": {
            "app_path": {"type": "string", "description": "App name or path, e.g. 'chrome', 'notepad'"},
            "arguments": {"type": "array", "items": {"type": "string"}, "description": "Optional CLI arguments"}},
            "required": ["app_path"]}, False),
        "manage_window": ({"type": "object", "properties": {
            "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "restore", "close"]},
            "title": {"type": "string", "description": "Window title substring to match"}},
            "required": ["action", "title"]}, False),
        "system_power": ({"type": "object", "properties": {
            "action": {"type": "string", "enum": ["sleep", "hibernate", "restart", "shutdown"]}},
            "required": ["action"]}, True),
        "network_diagnostics": ({"type": "object", "properties": {
            "target": {"type": "string", "description": "Hostname or IP to diagnose"},
            "action": {"type": "string", "enum": ["ping", "tracert", "dns"], "description": "Diagnostic action (default ping)"}},
            "required": ["target"]}, False),
        "manage_service": ({"type": "object", "properties": {
            "service_name": {"type": "string"},
            "action": {"type": "string", "enum": ["start", "stop", "restart"]}},
            "required": ["service_name", "action"]}, True),
        "get_network_adapters": ({"type": "object", "properties": {}, "required": []}, False),
        "organize_desktop": ({"type": "object", "properties": {
            "target_folder": {"type": "string", "description": "Folder to organize (default Desktop)"}},
            "required": []}, False),
        "move_file": ({"type": "object", "properties": {
            "source_path": {"type": "string"},
            "target_folder_or_path": {"type": "string"}},
            "required": ["source_path", "target_folder_or_path"]}, False),
        "copy_file": ({"type": "object", "properties": {
            "source_path": {"type": "string"},
            "target_folder_or_path": {"type": "string"}},
            "required": ["source_path", "target_folder_or_path"]}, False),
        "rename_file": ({"type": "object", "properties": {
            "file_path": {"type": "string"},
            "new_name": {"type": "string"}},
            "required": ["file_path", "new_name"]}, False),
        "read_file": ({"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]}, False),
        "write_file": ({"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}},
            "required": ["path", "content"]}, False),
    }

    def _register_tools_with_registry(self, tool_registry: Any) -> None:
        """Register local _TOOL_MAP tools into the global registry so the ReAct loop's
        agent-filtered manifest includes them and call_tool can dispatch them.
        Existing global registrations (e.g. launch_app) are never clobbered."""
        for name, (schema, destructive) in self._TOOL_SCHEMAS.items():
            if tool_registry.has_tool(name):
                continue
            tool_registry.register_tool(
                name,
                self._TOOL_DESCRIPTIONS.get(name, name),
                self._TOOL_MAP[name],
                schema,
                category="system",
                agent_hints=["system"],
                task_tags=["system", "os", "file", "desktop"],
                priority=1,
                is_destructive=destructive,
            )

    _TOOL_DESCRIPTIONS: dict[str, str] = {
        "get_system_stats": "Retrieve comprehensive CPU, RAM, Disk, and Network statistics",
        "get_process_list": "List running processes, optionally filtered by name, sorted by memory",
        "kill_process": "Terminate a process by name (e.g. 'msedge', 'chrome', 'notepad') or PID",
        "set_process_priority": "Set a process scheduling priority (idle..realtime)",
        "launch_app": "Launch an application with optional arguments",
        "manage_window": "Control a window: focus, minimize, maximize, restore, or close",
        "system_power": "Control system power: sleep, hibernate, restart, or shutdown (HIGHLY DESTRUCTIVE)",
        "network_diagnostics": "Run network diagnostics: ping, tracert, or DNS lookup",
        "manage_service": "Start, stop, or restart a system service (DESTRUCTIVE)",
        "get_network_adapters": "List all network interfaces and their IP configurations",
        "organize_desktop": "Organize loose files on the Desktop into categorized subfolders",
        "move_file": "Move a file or folder from source to target folder/path",
        "copy_file": "Copy a file or folder to a destination path",
        "rename_file": "Rename a file or folder in place",
        "read_file": "Read the contents of a file from disk",
        "write_file": "Write content to a file on disk",
    }

    async def _heuristic_system_fallback(self, message: str, task_id: str) -> str:
        """LLM-based fallback — replaces all hardcoded regex for system tasks."""
        params = await self._llm_parse(
            message=message,
            schema={
                "action": "launch_app | close_app | organize_desktop | read_file | write_file | system_stats | other",
                "app_name": "app/process name to launch or close — null if not applicable",
                "file_path": "file path if read/write — null if not applicable",
                "file_content": "content to write — null if not applicable",
            },
            context_hint=(
                "Hinglish: kholo/chalao=launch_app, band karo=close_app. "
                "Organize/clean desktop=organize_desktop. cpu/ram/disk=system_stats. "
                "Extract EXACT app name as user said it."
            ),
        )
        action = params.get("action", "other")
        app_name = (params.get("app_name") or "").strip()
        file_path = (params.get("file_path") or "").strip()
        file_content = params.get("file_content") or ""

        if action == "organize_desktop":
            res = await self._tool_organize_desktop()
            return f"Organized Desktop: {res}"
        if action == "launch_app" and app_name:
            res = await self._tool_launch_app(app_path=app_name)
            return str(res)
        if action == "close_app" and app_name:
            res = await self._tool_kill_process(process_name=app_name)
            return str(res)
        if action == "read_file" and file_path:
            res = await self._use_tool("read_file", path=file_path)
            return f"Contents of {file_path}:\n{res}"
        if action == "write_file" and file_path:
            res = await self._use_tool("write_file", path=file_path, content=file_content)
            return f"Done. Written to {file_path}."
        if action == "system_stats":
            res = await self._tool_get_system_stats()
            import json as _j
            return f"System Stats:\n{_j.dumps(res, indent=2) if isinstance(res, dict) else res}"
        return "I couldn\'t quite process that system request — please specify the app name or action."

    # ==============================================================================
    # EXECUTION ORCHESTRATOR
    # ==============================================================================
    async def execute(self, task_id: str, message: str, context: dict[str, Any],
                      entities: dict[str, Any]) -> str:
        """Main execution loop following the BaseAgent contract.

        Runs the production ReAct loop (_execute_with_tools): the LLM decides
        which tool to call (native tool_calls), results are fed back for
        self-correction, and it loops until a plain-text final answer. The
        router's deterministic fast-path hint is preserved via extra_system.
        _heuristic_system_fallback remains only as a last-resort safety net
        when the loop produces no usable output.
        """
        self._reset_state()
        self._current_task_id = task_id

        # The router's deterministic fast-path (command_router.py) already
        # figures out coarse intent from keywords BEFORE this agent ever
        # sees the message — e.g. "...organize...desktop..." gets tagged
        # entities={"action": "file_management"} with 0.99 confidence. That
        # signal was previously computed and then silently dropped: this
        # execute() never looked at `entities` at all, so the agent's own
        # tool-selection LLM call had to re-guess the right tool purely
        # from freeform phrasing, with no benefit from the router already
        # having figured it out. This was the concrete mechanism behind
        # "organize my desktop" sometimes not calling organize_desktop() —
        # a correctly-routed request could still be silently misread by
        # the agent's own tool-picking step, since the router's confident
        # extraction of *what kind* of request it is never reached the
        # prompt that actually decides which tool to call.
        extra_system = ""
        if entities.get("action") == "file_management":
            extra_system = (
                "ROUTER HINT: this message was classified as a file/desktop "
                "management request. If the user is asking to organize, "
                "clean, sort, or arrange files on their Desktop, use the "
                "organize_desktop tool — don't answer conversationally "
                "instead of calling it."
            )

        # Inject real-time OS context for the LLM
        sys_context = {
            "os": platform.system(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "hostname": socket.gethostname(),
            "python_version": platform.python_version()
        }
        extra_system = f"Current Host Context: {json.dumps(sys_context)}\n" + extra_system

        result = await self._execute_with_tools(
            task_id,
            message,
            context,
            task="system_control",
            extra_system=extra_system,
            max_turns=3,
        )
        if not result or result == "I could not complete that task.":
            logger.info("[system] ReAct loop produced no usable output — falling back to heuristic.")
            return await self._heuristic_system_fallback(message, task_id)
        return result

    async def _pre_tool_gate(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Confirmation gate for destructive operations before the ReAct loop executes a tool."""
        if await self._check_destructive(tool_name, params):
            risk = "critical" if tool_name == "system_power" else "high"
            desc = f"Execute {tool_name} with params: {json.dumps(params)}"
            confirmed = await self._confirm_action(
                self._current_task_id or "task", "destructive", desc, risk_level=risk
            )
            if not confirmed:
                return True, f"{tool_name} was not approved by the user."
        return False, ""

    # ==============================================================================
    # DESTRUCTIVE OPERATION GATE
    # ==============================================================================
    async def _check_destructive(self, tool_name: str, params: dict[str, Any]) -> bool:
        """Determines if a tool invocation requires user confirmation."""
        if tool_name in ("kill_process", "system_power", "manage_service"):
            return True
        if tool_name == "manage_window" and params.get("action", "").lower() == "close":
            return True
        return False

    # ==============================================================================
    # ELITE TOOL IMPLEMENTATIONS
    # ==============================================================================
    async def _tool_get_system_stats(self) -> dict[str, Any]:
        """Retrieve comprehensive hardware and OS statistics."""
        if not psutil:
            return {"error": "psutil is not installed. Cannot retrieve system stats."}

        # psutil.cpu_percent(interval=0.1) blocks + disk/process iteration is
        # IO-heavy — run in a worker thread so the event loop (UI overlay,
        # media playback) never stutters. (asyncio.to_thread takes a sync fn.)
        def _stats():
            stats = {
                "cpu": {
                    "usage_percent": psutil.cpu_percent(interval=0.1),
                    "cores_logical": psutil.cpu_count(logical=True),
                    "cores_physical": psutil.cpu_count(logical=False),
                    "freq": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None
                },
                "memory": psutil.virtual_memory()._asdict(),
                "swap": psutil.swap_memory()._asdict(),
                "disks": [],
                "network_io": {}
            }

            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    stats["disks"].append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "usage": usage._asdict()
                    })
                except PermissionError:
                    continue

            net_io = psutil.net_io_counters()
            if net_io:
                stats["network_io"] = net_io._asdict()

            return stats

        return await asyncio.to_thread(_stats)

    async def _tool_get_process_list(self, filter_name: str = "", top_n: int = 10) -> list[dict[str, Any]]:
        """List running processes, sorted by memory usage."""
        if not psutil:
            return [{"error": "psutil not installed"}]

        # Full-process psutil.process_iter() is a slow OS-enumeration call that
        # would block the event loop — run it in a worker thread.
        def _scan():
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status']):
                try:
                    pinfo = proc.info
                    if filter_name and filter_name.lower() not in pinfo['name'].lower():
                        continue
                    processes.append({
                        "pid": pinfo['pid'],
                        "name": pinfo['name'],
                        "cpu": pinfo['cpu_percent'],
                        "mem_mb": round(pinfo['memory_info'].rss / (1024 * 1024), 2) if pinfo['memory_info'] else 0,
                        "status": pinfo['status'],
                        "danger": "critical" if (pinfo['name'] or "").lower().replace(".exe", "").strip() in self._CRITICAL_PROCESSES
                                  else ("junk" if (pinfo['name'] or "").lower().replace(".exe", "").strip() in self._JUNK_PROCESSES
                                        else "normal")
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            processes.sort(key=lambda x: x['mem_mb'], reverse=True)
            return processes[:top_n]

        return await asyncio.to_thread(_scan)

    async def _process_running(self, probe: str) -> bool:
        """True if any running process name matches probe."""
        return await self._count_processes(probe) > 0

    async def _count_processes(self, probe: str) -> int:
        """Count running processes whose name matches probe (case-insensitive, .exe tolerant)."""
        if not psutil:
            return 0

        # Full OS enumeration blocks — run in a worker thread.
        def _count():
            probe_l = probe.lower().replace(".exe", "")
            count = 0
            for proc in psutil.process_iter(["name"]):
                try:
                    name = (proc.info["name"] or "").lower()
                    if name in (probe_l, probe_l + ".exe") or probe_l in name:
                        count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return count

        return await asyncio.to_thread(_count)

    async def _tool_kill_process(
        self,
        task_id: str = "",
        pid: int | str | None = None,
        process_name: str | None = None,
        name: str | None = None,
        force: bool = True,
    ) -> str:
        """Terminate a process by PID or process name (e.g. 'msedge', 'chrome', 'notepad')."""
        target_name = process_name or name or (str(pid) if pid is not None and not str(pid).isdigit() else None)
        target_pid = int(pid) if pid is not None and str(pid).isdigit() else None

        if target_name:
            t_clean = target_name.lower().strip()
            if t_clean.replace(".exe", "").strip() in self._CRITICAL_PROCESSES:
                return (f"REFUSED: '{target_name}' is a critical system process. "
                        f"Terminating it could destabilize or crash the system.")
            # Common process name alias map
            alias_map = {
                "brave": "brave.exe",
                "brave browser": "brave.exe",
                "chrome": "chrome.exe",
                "chrome browser": "chrome.exe",
                "edge": "msedge.exe",
                "msedge": "msedge.exe",
                "edge browser": "msedge.exe",
                "microsoft edge": "msedge.exe",
                "notepad": "notepad.exe",
                "calculator": "calc.exe",
                "calc": "calc.exe",
                "vscode": "code.exe",
                "vs code": "code.exe",
            }
            exe_name = alias_map.get(t_clean) or (t_clean.replace(".exe", "") + ".exe")
            base_name = exe_name.replace(".exe", "").lower()
            
            killed_count = 0
            if psutil:
                # psutil.process_iter() full enumeration blocks — run in a thread.
                def _kill_scan():
                    killed = 0
                    for proc in psutil.process_iter(['pid', 'name']):
                        try:
                            p_name = (proc.info['name'] or "").lower()
                            if p_name in (exe_name.lower(), base_name, t_clean) or base_name in p_name:
                                if force:
                                    proc.kill()
                                else:
                                    proc.terminate()
                                killed += 1
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    return killed

                killed_count = await asyncio.to_thread(_kill_scan)

            if killed_count > 0:
                # Post-kill verification: re-scan for survivors
                await asyncio.sleep(1.0)
                still = await self._count_processes(base_name)
                if still == 0:
                    return f"Successfully terminated {killed_count} instance(s) of '{target_name}' (verified)."
                return (f"Terminated {killed_count} instance(s) of '{target_name}', but {still} "
                        f"still running (access denied or respawned).")

            # Fallback to Windows taskkill
            if platform.system() == "Windows":
                try:
                    proc = await asyncio.create_subprocess_shell(
                        f"taskkill /f /im {exe_name}",
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    stdout, stderr = await proc.communicate()
                    if proc.returncode == 0:
                        await asyncio.sleep(1.0)
                        still = await self._count_processes(base_name)
                        if still == 0:
                            return f"Successfully terminated '{exe_name}' via taskkill (verified)."
                        return f"taskkill reported success, but {still} instance(s) of '{exe_name}' still running."
                except Exception:
                    pass
            return f"No running processes found matching '{target_name}'."

        if target_pid is not None and psutil:
            try:
                proc = psutil.Process(target_pid)
                pname = proc.name()
                if (pname or "").lower().replace(".exe", "").strip() in self._CRITICAL_PROCESSES:
                    return (f"REFUSED: process '{pname}' (PID: {target_pid}) is a critical system process. "
                            f"Terminating it could destabilize or crash the system.")
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                return f"Successfully terminated process {pname} (PID: {target_pid})."
            except psutil.NoSuchProcess:
                return f"Process with PID {target_pid} not found."
            except Exception as e:
                return f"Failed to kill process: {str(e)}"

        return "Please specify a valid PID or process_name (e.g. 'msedge', 'chrome')."

    async def _tool_set_process_priority(self, pid: int, priority: str) -> str:
        """Adjust the scheduling priority of a running process."""
        if not psutil:
            return "psutil not installed."
            
        priority_map = {
            "idle": psutil.IDLE_PRIORITY_CLASS,
            "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
            "normal": psutil.NORMAL_PRIORITY_CLASS,
            "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
            "high": psutil.HIGH_PRIORITY_CLASS,
            "realtime": psutil.REALTIME_PRIORITY_CLASS
        }
        
        p_class = priority_map.get(priority.lower())
        if not p_class:
            return f"Invalid priority: {priority}. Valid: {list(priority_map.keys())}"
            
        try:
            proc = psutil.Process(pid)
            proc.nice(p_class)
            return f"Successfully set priority of PID {pid} ({proc.name()}) to {priority}."
        except psutil.NoSuchProcess:
            return f"Process {pid} not found."
        except psutil.AccessDenied:
            return f"Access denied to change priority for PID {pid}. Run as admin."
        except Exception as e:
            return f"Failed to set priority: {str(e)}"

    async def _tool_launch_app(self, app_path: str, arguments: list[str] | None = None) -> str:
        """Launch an application asynchronously on the host OS."""
        return await launch_app_verified(app_path, arguments)

    async def _tool_manage_window(self, action: str, title: str) -> str:
        """Control window states (focus, minimize, maximize, close)."""
        if not win32gui:
            return "win32gui is not available. Window management requires pywin32."
            
        def enum_callback(hwnd: int, results: list) -> None:
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                if title.lower() in window_title.lower():
                    results.append((hwnd, window_title))

        windows: list[tuple[int, str]] = []
        win32gui.EnumWindows(enum_callback, windows)
        
        if not windows:
            return f"No visible windows found matching title '{title}'."
            
        hwnd, actual_title = windows[0]
        action = action.lower()
        
        try:
            if action == "focus":
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
            elif action == "minimize":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            elif action == "maximize":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            elif action == "restore":
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            elif action == "close":
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            else:
                return f"Unknown window action: {action}"
                
            return f"Successfully executed '{action}' on window '{actual_title}'."
        except Exception as e:
            return f"Failed to {action} window '{actual_title}': {str(e)}"

    async def _tool_system_power(self, task_id: str, action: str) -> str:
        """Execute system-level power state transitions."""
        action = action.lower()
        is_win = platform.system() == "Windows"
        
        commands = {
            "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0" if is_win else "systemctl suspend",
            "hibernate": "rundll32.exe powrprof.dll,SetSuspendState 1,1,0" if is_win else "systemctl hibernate",
            "restart": "shutdown /r /t 5 /c \"Makima restart\"" if is_win else "shutdown -r -t 5",
            "shutdown": "shutdown /s /t 5 /c \"Makima shutdown\"" if is_win else "shutdown -h -t 5"
        }
        
        if action not in commands:
            return f"Unsupported power action: {action}. Supported: {list(commands.keys())}"
            
        try:
            process = await asyncio.create_subprocess_shell(
                commands[action], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                return f"System power action '{action}' initiated successfully."
            else:
                return f"Power action failed: {stderr.decode().strip()}"
        except Exception as e:
            return f"Failed to execute power action: {str(e)}"

    async def _tool_network_diagnostics(self, target: str, action: str = "ping") -> str:
        """Run network diagnostic commands."""
        action = action.lower()
        is_win = platform.system() == "Windows"
        
        if action == "ping":
            cmd = ["ping", "-n", "4", target] if is_win else ["ping", "-c", "4", target]
        elif action in ("tracert", "traceroute"):
            cmd = ["tracert", target] if is_win else ["traceroute", target]
        elif action == "dns":
            try:
                ip = socket.gethostbyname(target)
                return f"DNS resolution for {target}: {ip}"
            except socket.gaierror as e:
                return f"DNS resolution failed for {target}: {str(e)}"
        else:
            return f"Unsupported network action: {action}"
            
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
            output = stdout.decode('utf-8', errors='ignore') or stderr.decode('utf-8', errors='ignore')
            if len(output) > 1500:
                output = output[:1500] + "\n... [Output Truncated]"
            return output.strip()
        except asyncio.TimeoutError:
            return f"Network diagnostics timed out for {target}."
        except Exception as e:
            return f"Network diagnostics failed: {str(e)}"

    async def _tool_manage_service(self, task_id: str, service_name: str, action: str) -> str:
        """Start, stop, or restart background OS services."""
        action = action.lower()
        if action not in ("start", "stop", "restart"):
            return f"Unsupported service action: {action}. Use start, stop, or restart."
            
        is_win = platform.system() == "Windows"
        
        if action == "restart":
            cmd_stop = ["net", "stop", service_name] if is_win else ["systemctl", "stop", service_name]
            cmd_start = ["net", "start", service_name] if is_win else ["systemctl", "start", service_name]
            try:
                await asyncio.create_subprocess_exec(*cmd_stop, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                process = await asyncio.create_subprocess_exec(*cmd_start, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = await process.communicate()
                return f"Service {service_name} restarted. Output: {stdout.decode().strip()}"
            except Exception as e:
                return f"Failed to restart service: {str(e)}"
        else:
            cmd = ["net", action, service_name] if is_win else ["systemctl", action, service_name]
            try:
                process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = await process.communicate()
                output = stdout.decode().strip() or stderr.decode().strip()
                return f"Service {service_name} {action} executed. Output: {output}"
            except Exception as e:
                return f"Failed to {action} service: {str(e)}"

    async def _tool_get_network_adapters(self) -> list[dict[str, Any]]:
        """Retrieve detailed network interface configurations."""
        if not psutil:
            return [{"error": "psutil not installed"}]

        def _scan_net():
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
            result = []

            for iface, stats_info in stats.items():
                info = {
                    "interface": iface,
                    "isup": stats_info.isup,
                    "duplex": stats_info.duplex,
                    "speed_mbps": stats_info.speed,
                    "mtu": stats_info.mtu,
                    "addresses": []
                }
                if iface in addrs:
                    for addr in addrs[iface]:
                        info["addresses"].append({
                            "family": str(addr.family),
                            "address": addr.address,
                            "netmask": addr.netmask,
                            "broadcast": addr.broadcast
                        })
                result.append(info)

            return result

        return await asyncio.to_thread(_scan_net)

    async def _tool_organize_desktop(self, target_folder: str = "Desktop") -> str:
        """Organize loose files on Desktop into categorized subfolders."""
        import os
        import time
        import shutil
        
        user_home = os.path.expanduser("~")
        possible_paths = [
            os.path.join(user_home, "Desktop"),
            os.path.join(user_home, "OneDrive", "Desktop"),
            os.path.join(user_home, "OneDrive - Personal", "Desktop"),
        ]
        desktop_path = next((p for p in possible_paths if os.path.exists(p)), None)
        if not desktop_path:
            return "Desktop directory not found on your system."

        categories = {
            "Documents": [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx", ".csv", ".epub", ".odt"],
            "Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico"],
            "Videos": [".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv"],
            "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"],
            "Archives": [".zip", ".rar", ".7z", ".tar", ".gz"],
            "Executables": [".exe", ".msi", ".bat", ".cmd"],
            "Code": [".py", ".js", ".ts", ".html", ".css", ".json", ".cpp", ".c", ".rs", ".java"],
            "Shortcuts": [".lnk", ".url"]
        }
        category_dirs = set(categories.keys()).union({"Others"})

        moved_count = 0
        summary = []

        for filename in os.listdir(desktop_path):
            file_path = os.path.join(desktop_path, filename)
            
            # Skip categorization subfolders and hidden system files
            if filename in category_dirs or filename.startswith(".") or filename.startswith("~$"):
                continue
            if os.path.isdir(file_path):
                continue

            ext = os.path.splitext(filename)[1].lower()
            matched_category = None
            for category, ext_list in categories.items():
                if ext in ext_list:
                    matched_category = category
                    break

            if not matched_category:
                matched_category = "Others"

            target_dir = os.path.join(desktop_path, matched_category)
            os.makedirs(target_dir, exist_ok=True)
            dest_path = os.path.join(target_dir, filename)
            
            if os.path.exists(dest_path):
                base, extension = os.path.splitext(filename)
                dest_path = os.path.join(target_dir, f"{base}_{int(time.time())}{extension}")
            
            try:
                shutil.move(file_path, dest_path)
                moved_count += 1
                summary.append(f"• {filename} -> {matched_category}/")
            except Exception as e:
                logger.error(f"[system] Failed to move {filename}: {e}")

        # Flush Windows Desktop Shell visual cache to auto-align icons to grid instantly
        if platform.system() == "Windows":
            try:
                import ctypes
                ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)
            except Exception as e:
                logger.debug(f"[system] Visual desktop refresh failed: {e}")

        if moved_count == 0:
            return "Desktop is already clean and organized! No loose files found."

        return (
            f"Successfully organized {moved_count} desktop files into categorized folders:\n"
            + "\n".join(summary[:15])
            + (f"\n... and {len(summary) - 15} more files." if len(summary) > 15 else "")
        )

    def _resolve_user_path(self, path_str: str) -> str:
        """Resolve user friendly folder names (desktop, downloads, documents, pictures) to full OS paths."""
        import os
        user_home = os.path.expanduser("~")
        p_clean = path_str.strip().strip("'\"").lower()
        
        shortcuts = {
            "desktop": os.path.join(user_home, "Desktop"),
            "downloads": os.path.join(user_home, "Downloads"),
            "documents": os.path.join(user_home, "Documents"),
            "pictures": os.path.join(user_home, "Pictures"),
            "videos": os.path.join(user_home, "Videos"),
            "music": os.path.join(user_home, "Music")
        }
        if p_clean in shortcuts and os.path.exists(shortcuts[p_clean]):
            return shortcuts[p_clean]
        
        # Check if relative to home
        full_p = os.path.expanduser(path_str.strip().strip("'\""))
        if not os.path.isabs(full_p):
            full_p = os.path.join(user_home, full_p)
        return full_p

    async def _tool_move_file(self, source_path: str, target_folder_or_path: str) -> str:
        """Move a file or folder from source path to target folder or path."""
        import os
        import shutil
        src = self._resolve_user_path(source_path)
        dest = self._resolve_user_path(target_folder_or_path)

        if not os.path.exists(src):
            return f"Source file or folder not found: {source_path}"

        if os.path.isdir(dest):
            dest_file = os.path.join(dest, os.path.basename(src))
        else:
            dest_file = dest
            os.makedirs(os.path.dirname(os.path.abspath(dest_file)), exist_ok=True)

        try:
            shutil.move(src, dest_file)
            return f"Successfully moved '{os.path.basename(src)}' to '{dest}'."
        except Exception as e:
            return f"Failed to move '{source_path}': {e}"

    async def _tool_copy_file(self, source_path: str, target_folder_or_path: str) -> str:
        """Copy a file or folder from source path to target folder or path."""
        import os
        import shutil
        src = self._resolve_user_path(source_path)
        dest = self._resolve_user_path(target_folder_or_path)

        if not os.path.exists(src):
            return f"Source file or folder not found: {source_path}"

        try:
            if os.path.isdir(src):
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                if os.path.isdir(dest):
                    dest = os.path.join(dest, os.path.basename(src))
                shutil.copy2(src, dest)
            return f"Successfully copied '{os.path.basename(src)}' to '{dest}'."
        except Exception as e:
            return f"Failed to copy '{source_path}': {e}"

    async def _tool_rename_file(self, file_path: str, new_name: str) -> str:
        """Rename a file or folder in place."""
        import os
        src = self._resolve_user_path(file_path)
        if not os.path.exists(src):
            return f"File or folder not found: {file_path}"

        parent = os.path.dirname(src)
        dest = os.path.join(parent, new_name)
        try:
            os.rename(src, dest)
            return f"Successfully renamed '{os.path.basename(src)}' to '{new_name}'."
        except Exception as e:
            return f"Failed to rename '{file_path}': {e}"

    async def _tool_read_file(self, path: str) -> str:
        """Read the contents of a file from disk with path traversal protection."""
        import os
        src = self._resolve_user_path(path)
        if not os.path.exists(src):
            return f"File not found: {path}"
        if os.path.isdir(src):
            return f"Path is a directory, not a file: {path}"
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(self.MAX_DOCUMENT_CHARS if hasattr(self, 'MAX_DOCUMENT_CHARS') else 6000)
                if len(content) >= 6000:
                    content = content[:6000] + "\n... [TRUNCATED]"
                return content
        except PermissionError as e:
            return f"Permission denied reading '{path}': {e}"
        except Exception as e:
            return f"Failed to read '{path}': {e}"

    async def _tool_write_file(self, path: str, content: str) -> str:
        """Write content to a file on disk with path traversal protection."""
        import os
        src = self._resolve_user_path(path)
        parent = os.path.dirname(src)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        try:
            with open(src, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} characters to '{src}'."
        except PermissionError as e:
            return f"Permission denied writing to '{path}': {e}"
        except Exception as e:
            return f"Failed to write to '{path}': {e}"

    async def _tool_mouse_click(self, x: int, y: int, button: str = "left", double: bool = False) -> str:
        """Click anywhere on the host desktop using PyAutoGUI."""
        if not pyautogui:
            return "pyautogui is not installed. Native mouse click unavailable."

        def _click():
            clicks = 2 if double else 1
            pyautogui.click(x=x, y=y, clicks=clicks, button=button.lower())
            return f"Clicked {button} button at ({x}, {y}) [clicks={clicks}]."

        return await asyncio.to_thread(_click)

    async def _tool_mouse_draw(
        self,
        shape: str = "circle",
        start_x: int = 500,
        start_y: int = 500,
        size: int = 150,
        points: list[list[int]] | None = None,
        duration: float = 2.0,
    ) -> str:
        """Draw shapes or freehand mouse paths on the desktop (Paint, Photoshop, Canvas, etc.)."""
        if not pyautogui:
            return "pyautogui is not installed. Native mouse drawing unavailable."

        def _draw():
            import math
            shape_clean = (shape or "circle").lower().strip()
            pyautogui.moveTo(start_x, start_y)
            pyautogui.mouseDown()

            try:
                if points and isinstance(points, list):
                    for pt in points:
                        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            pyautogui.dragTo(pt[0], pt[1], duration=0.05, button="left")
                    return f"Drew custom path with {len(points)} points starting at ({start_x}, {start_y})."

                if shape_clean == "square":
                    pyautogui.drag(size, 0, duration=0.3, button="left")
                    pyautogui.drag(0, size, duration=0.3, button="left")
                    pyautogui.drag(-size, 0, duration=0.3, button="left")
                    pyautogui.drag(0, -size, duration=0.3, button="left")
                elif shape_clean in ("circle", "ellipse"):
                    steps = 36
                    for i in range(steps + 1):
                        angle = (2 * math.pi * i) / steps
                        px = int(start_x + (size / 2) * math.cos(angle))
                        py = int(start_y + (size / 2) * math.sin(angle))
                        if i > 0:
                            pyautogui.dragTo(px, py, duration=0.03, button="left")
                        else:
                            pyautogui.moveTo(px, py)
                elif shape_clean in ("star", "5star"):
                    outer = size / 2
                    inner = size / 4
                    for i in range(11):
                        angle = (math.pi * i) / 5 - (math.pi / 2)
                        r = outer if i % 2 == 0 else inner
                        px = int(start_x + r * math.cos(angle))
                        py = int(start_y + r * math.sin(angle))
                        if i > 0:
                            pyautogui.dragTo(px, py, duration=0.08, button="left")
                        else:
                            pyautogui.moveTo(px, py)
                elif shape_clean in ("heart", "dil"):
                    steps = 40
                    for i in range(steps + 1):
                        t = (2 * math.pi * i) / steps
                        x_val = 16 * (math.sin(t) ** 3)
                        y_val = -(13 * math.cos(t) - 5 * math.cos(2*t) - 2 * math.cos(3*t) - math.cos(4*t))
                        px = int(start_x + (x_val * size / 32))
                        py = int(start_y + (y_val * size / 32))
                        if i > 0:
                            pyautogui.dragTo(px, py, duration=0.03, button="left")
                        else:
                            pyautogui.moveTo(px, py)
                elif shape_clean == "triangle":
                    pyautogui.drag(size // 2, size, duration=0.3, button="left")
                    pyautogui.drag(-size, 0, duration=0.3, button="left")
                    pyautogui.drag(size // 2, -size, duration=0.3, button="left")
                else:
                    pyautogui.drag(size, size, duration=0.5, button="left")

                return f"Successfully drew '{shape_clean}' at ({start_x}, {start_y}) with size={size}."
            finally:
                pyautogui.mouseUp()

        return await asyncio.to_thread(_draw)

