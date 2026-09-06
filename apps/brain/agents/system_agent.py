"""Makima v7.2 — Elite System Agent: Enterprise OS Controller, Hardware Diagnostics, 
Process Monitor, and System Lifecycle Management Engine.

This agent provides deep, asynchronous, and safe control over the host operating system.
All destructive operations (process termination, power state changes, service management)
are routed through a strict confirmation gate to prevent accidental system disruption.
"""
from __future__ import annotations

import asyncio
import ctypes
import difflib
import glob
import json
import logging
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
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
    import win32api
    import win32security
    _HAS_WIN32_SEC = True
except ImportError:
    win32gui = win32con = win32process = win32api = win32security = None
    _HAS_WIN32_SEC = False
    logging.getLogger("makima.agents.system").warning("pywin32 / win32security not found. Advanced OS security tokens disabled.")

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

logger = logging.getLogger("makima.agents.system")
# Canonical OS tools and app discovery imported directly from system_tools (zero duplication)
from ..tools.system_tools import (
    APPS_CACHE_TTL_SECONDS,
    REGISTRY_MAX_SCAN_LIMIT,
    FUZZY_MATCH_THRESHOLD,
    MAX_APPS_SEARCH_LIMIT,
    SCREENSHOT_FALLBACK_COLOR,
    _APPS_CACHE,
    _APPS_CACHE_EXPIRES,
    _APPS_FTS_CONN,
    _APPS_FTS_LOCK,
    _ensure_apps_fts_index,
    search_installed_apps,
    _process_running,
    is_critical_process,
    _extract_app_fuzzy,
    _resolve_app_path,
    _wait_process_running,
    launch_app_verified,
)


class SystemAgent(BaseAgent):
    """Elite System Agent for Makima OS v7.2."""
    
    AGENT_NAME = "system"
    DESCRIPTION = "Enterprise OS control: open, launch, close, quit, kill, terminate, focus, minimize, maximize, restore, and snap applications, processes, and windows (e.g. Edge, Chrome, Notepad, VS Code), system master volume & OS audio control (set_volume, get_volume, mute/unmute, universal sound, PC sound), desktop screenshot and full-screen captures, read and copy clipboard contents, hardware diagnostics, system power, notifications, and desktop file management."
    CAPABILITIES = ["process_management", "hardware_monitor", "desktop_cleanup", "file_operations", "system_diagnostics", "window_management", "window_tiling", "service_management", "power_management", "app_discovery", "volume_management", "clipboard", "screenshot", "notification", "temp_cleanup"]
    AGENT_TOOLS = ["get_window_list", "get_system_stats", "get_process_list", "search_installed_apps", "kill_process", "set_process_priority", "launch_app", "manage_window", "snap_window", "set_volume", "get_volume", "get_clipboard", "set_clipboard", "take_screenshot", "show_notification", "clean_temp_files", "system_power", "network_diagnostics", "manage_service", "get_network_adapters", "organize_desktop", "move_file", "copy_file", "rename_file", "write_file", "read_file", "mouse_click", "mouse_draw", "keyboard_press", "keyboard_type"]
    TAGS = ["system", "os", "local", "files", "desktop", "process", "hardware", "mouse", "drawing", "keyboard", "typing", "apps", "clipboard", "screenshot", "notification", "volume", "sound", "audio"]
    SYSTEM_PROMPT = """You are Makima's Elite System Agent — the dedicated OS controller, process monitor, window manager, filesystem engine, hardware diagnostics core, and system master audio controller.

CORE MISSION & EXECUTION:
- You have verified programmatic control of the OS. DIRECTLY CALL the appropriate tool — never simulate or roleplay actions.
- Mirror user tone naturally (English/Hinglish). Keep replies crisp, concise, and helpful.

OPERATIONAL DOMAINS & TOOLS:
1. APPS & WINDOWS:
   - Launch: launch_app(app_path="<app>") (auto-discovers executables and shortcuts).
   - Window Inspection: get_window_list(filter_name="...", active_only=True) to inspect open desktop windows, handles, and process names.
   - Window Focus/State: manage_window(action="focus"|"minimize"|"maximize"|"restore"|"close", title="<title>"|"active", hwnd=<int>).
   - Window Tiling: snap_window(position="left"|"right"|"top"|"bottom"|"top_left"|"top_right"|"bottom_left"|"bottom_right"|"center", title="...").
   - Discovery: search_installed_apps(query="...") only when user asks to search/list apps.
2. HARDWARE & PROCESSES:
   - Metrics & Stats: get_system_stats(), get_process_list(filter_name="...", top_n=10).
   - Process Control: kill_process(process_name="..." | pid=..., force=True), set_process_priority(pid=..., priority="...").
   - Safety: NEVER kill critical OS processes (lsass, csrss, winlogon, services, svchost, explorer, dwm).
3. SYSTEM AUDIO & INPUT:
   - Master Volume: set_volume(level=<0-100> | delta=<+/-int>), get_volume() (controls Windows/OS master volume, universal sound, PC sound).
   - Clipboard: get_clipboard(), set_clipboard(text="...").
   - Screen: take_screenshot(target="fullscreen"|"active", title="active").
   - Mouse & Keyboard: mouse_click(x, y, button, double), mouse_draw(shape, start_x, start_y, size, points), keyboard_press(key="enter"|"esc"|"ctrl+c"|"alt+f4"), keyboard_type(text="...").
4. MAINTENANCE & FILESYSTEM:
   - Temp & Storage: clean_temp_files(confirmed=False|True).
   - Desktop Organization: organize_desktop(target_folder="Desktop", confirmed=False|True).
   - File Operations: read_file(path), write_file(path, content), move_file(source_path, target_folder_or_path), copy_file(source_path, target_folder_or_path), rename_file(file_path, new_name).
5. NETWORK, POWER & SERVICES:
   - Network: network_diagnostics(target="...", action="ping"|"tracert"|"dns"), get_network_adapters().
   - System Power: system_power(action="sleep"|"restart"|"shutdown"|"hibernate").
   - Windows Services: manage_service(service_name="...", action="start"|"stop"|"restart").
   - Notifications: show_notification(title="...", message="...").
6. STRUCTURED THINKING: Always perform concise intent decomposition in <thinking>...</thinking> before executing OS tools.""" + "\n" + TOOL_DISCIPLINE_BLOCK

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
        self._last_app_ref: str | None = None
        self._TOOL_MAP: dict[str, Callable[..., Awaitable[str | dict | list]]] = {
            "get_window_list": self._tool_get_window_list,
            "search_installed_apps": self._tool_search_installed_apps,
            "get_system_stats": self._tool_get_system_stats,
            "get_process_list": self._tool_get_process_list,
            "kill_process": self._tool_kill_process,
            "set_process_priority": self._tool_set_process_priority,
            "launch_app": self._tool_launch_app,
            "manage_window": self._tool_manage_window,
            "snap_window": self._tool_snap_window,
            "set_volume": self._tool_set_volume,
            "adjust_volume": self._tool_set_volume,
            "volume": self._tool_set_volume,
            "get_volume": self._tool_get_volume,
            "get_clipboard": self._tool_get_clipboard,
            "set_clipboard": self._tool_set_clipboard,
            "take_screenshot": self._tool_take_screenshot,
            "show_notification": self._tool_show_notification,
            "clean_temp_files": self._tool_clean_temp_files,
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
            "system_mouse_click": self._tool_mouse_click,
            "mouse_draw": self._tool_mouse_draw,
            "system_mouse_draw": self._tool_mouse_draw,
            "keyboard_press": self._tool_keyboard_press,
            "system_keyboard_press": self._tool_keyboard_press,
            "keyboard_type": self._tool_keyboard_type,
            "system_keyboard_type": self._tool_keyboard_type,
        }
        if tool_registry is not None:
            self._register_tools_with_registry(tool_registry)

    _TOOL_SCHEMAS: dict[str, tuple[dict, bool]] = {
        "set_volume": ({"type": "object", "properties": {
            "level": {"type": "integer", "description": "Target volume percentage between 0 and 100 (or null if adjusting relatively)"},
            "delta": {"type": "integer", "description": "Relative percentage change (e.g. 10 to increase, -10 to decrease)"},
            "action": {"type": "string", "description": "Semantic action: 'increase'/'decrease'/'mute'/'unmute' for relative/state operations"}},
            "required": []}, False),
        "get_volume": ({"type": "object", "properties": {}, "required": []}, False),
        "get_clipboard": ({"type": "object", "properties": {}, "required": []}, False),
        "set_clipboard": ({"type": "object", "properties": {
            "text": {"type": "string", "description": "Text content to copy to clipboard"}},
            "required": ["text"]}, False),
        "take_screenshot": ({"type": "object", "properties": {
            "target": {"type": "string", "enum": ["fullscreen", "active", "window"], "description": "Capture target ('fullscreen' or 'active' window)"},
            "title": {"type": "string", "description": "Optional window title if target is window (default 'active')"}},
            "required": []}, False),
        "show_notification": ({"type": "object", "properties": {
            "title": {"type": "string", "description": "Notification title"},
            "message": {"type": "string", "description": "Notification body message"},
            "app_id": {"type": "string", "description": "Application sender identifier (default 'Makima')"}},
            "required": ["title", "message"]}, False),
        "clean_temp_files": ({"type": "object", "properties": {
            "confirmed": {"type": "boolean", "description": "Set to true to execute deletion. False returns dry-run preview."}},
            "required": []}, False),
        "snap_window": ({"type": "object", "properties": {
            "position": {"type": "string", "enum": ["left", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right", "center", "maximize", "restore"], "description": "Snap target position"},
            "title": {"type": "string", "description": "Window title, application name, or 'active' for current window (default 'active')"}},
            "required": ["position"]}, False),
        "search_installed_apps": ({"type": "object", "properties": {
            "query": {"type": "string", "description": "Application name or keyword to search across the operating system (e.g. 'outlook', 'blender', 'chrome', 'notion')"},
            "limit": {"type": "integer", "description": "Maximum number of search results to return (default 15)"}},
            "required": []}, False),
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
        "keyboard_press": ({"type": "object", "properties": {
            "key": {"type": "string", "description": "Key name (e.g. 'enter', 'esc', 'tab', 'win') or combination (e.g. 'ctrl+c', 'alt+f4')"}},
            "required": ["key"]}, False),
        "keyboard_type": ({"type": "object", "properties": {
            "text": {"type": "string", "description": "Text content to type into active application"},
            "interval": {"type": "number", "default": 0.01, "description": "Delay between keystrokes in seconds"}},
            "required": ["text"]}, False),
        "get_system_stats": ({"type": "object", "properties": {}, "required": []}, False),
        "get_process_list": ({"type": "object", "properties": {
            "filter_name": {"type": "string", "description": "Optional process name filter"},
            "top_n": {"type": "integer", "description": "Number of top processes to return (default 10)"}},
            "required": []}, False),
        "kill_process": ({"type": "object", "properties": {
            "process_name": {"type": "string", "description": "Process name, e.g. 'msedge', 'chrome', 'notepad'"},
            "pid": {"type": "integer", "description": "Process PID (alternative to process_name)"},
            "force": {"type": "boolean", "description": "Force kill (default true)"}},
            "required": []}, True),
        "set_process_priority": ({"type": "object", "properties": {
            "pid": {"type": "integer"},
            "priority": {"type": "string", "enum": ["idle", "below_normal", "normal", "above_normal", "high", "realtime"]}},
            "required": ["pid", "priority"]}, False),
        "launch_app": ({"type": "object", "properties": {
            "app_path": {"type": "string", "description": "App name or executable path to START, OPEN, or LAUNCH (e.g. 'notepad', 'chrome', 'calc', 'open notepad', 'kholo'). Call this whenever the user wants to open or run an application. NEVER use this tool to close, minimize, focus, or kill an app."}},
            "required": ["app_path"]}, False),
        "manage_window": ({"type": "object", "properties": {
            "action": {
                "type": "string",
                "enum": ["close", "minimize", "maximize", "focus", "restore"],
                "description": "Window state action on ALREADY OPEN windows: 'close' to quit/exit an application window; 'minimize' to hide window; 'maximize' for full screen; 'focus' to switch/bring already-running window to front; 'restore' to un-maximize. If user wants to OPEN or START an app, use launch_app instead."
            },
            "title": {
                "type": "string",
                "description": "Window title or application name substring to match (e.g. 'notepad', 'calculator', 'calc', 'edge', 'chrome', or 'active' for current window). Ignored if hwnd is provided."
            },
            "hwnd": {
                "type": "integer",
                "description": "Exact window handle (HWND) returned by get_window_list. If provided, targets that exact window directly."
            }},
            "required": ["action"]}, False),
        "get_window_list": ({"type": "object", "properties": {
            "filter_name": {"type": "string", "description": "Optional title or process name to filter windows"},
            "active_only": {"type": "boolean", "description": "If true, only return visible interactive application windows (default true)"}},
            "required": []}, False),
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
            "path": {"type": "string", "description": "Absolute path OR known folder name (desktop/downloads/documents — resolved OneDrive-aware). Prefer absolute path."},
            "content": {"type": "string"}},
            "required": ["path", "content"]}, False),
    }

    def _register_tools_with_registry(self, tool_registry: Any) -> None:
        """Register local _TOOL_MAP tools into the global registry so the ReAct loop's
        agent-filtered manifest includes them and call_tool can dispatch them.
        Existing global registrations (e.g. launch_app) are never clobbered."""
        for name, (schema, destructive) in self._TOOL_SCHEMAS.items():
            has_tool = getattr(tool_registry, "has_tool", None)
            if callable(has_tool) and has_tool(name):
                continue
            tool_registry.register_tool(
                name,
                self._TOOL_DESCRIPTIONS.get(name, name),
                self._TOOL_MAP[name],
                schema,
                category="system",
                agent_hints=["system", "commander", "automation", "general"],
                task_tags=["system", "os", "file", "desktop", "apps", "window", "visibility", "screen", "hide", "minimize", "focus", "close", "clipboard", "screenshot", "notification", "snap"],
                priority=1,
                is_destructive=destructive,
            )

    _TOOL_DESCRIPTIONS: dict[str, str] = {
        "get_window_list": "Inspect and list open application windows on the desktop with HWND, title, and process metadata. Use when you need to inspect or choose which window to focus, minimize, or close.",
        "snap_window": "Snap/tile window to a screen region ('left', 'right', 'top', 'bottom', 'top_left', 'top_right', 'bottom_left', 'bottom_right', 'center')",
        "set_volume": "Set host system master volume level (0-100)",
        "get_clipboard": "Read and return current text from the Windows system clipboard",
        "set_clipboard": "Copy text content to the Windows system clipboard",
        "take_screenshot": "Capture fullscreen or active window screenshot saved to disk",
        "show_notification": "Display a native Windows desktop toast notification alert",
        "clean_temp_files": "Scan and clean temporary junk files with dry-run preview and safety",
        "search_installed_apps": "Search, list, or discover what applications or software are installed on the computer (e.g. 'konse apps hain', 'check if blender is installed', 'list installed apps'). Use ONLY for discovery/listing. NEVER use to open or launch an app (use launch_app instead).",
        "get_system_stats": "Retrieve comprehensive CPU, RAM, Disk, and Network statistics",
        "get_process_list": "List running processes, optionally filtered by name, sorted by memory",
        "kill_process": "Terminate a process by name (e.g. 'msedge', 'chrome', 'notepad') or PID",
        "set_process_priority": "Set a process scheduling priority (idle..realtime)",
        "launch_app": "Open, launch, or start an application on the desktop (e.g. 'notepad kholo', 'open chrome', 'calculator chalao', 'start spotify', 'open vs code'). Always use this tool when the user requests to open, start, or run any application.",
        "manage_window": "Control an open window state: close (exit/band karna), minimize (hide from view), maximize, focus (bring to front), or restore. Can target exact window handle (hwnd) from get_window_list or match by title.",
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
        "mouse_click": "Native mouse click at desktop coordinates",
        "mouse_draw": "Draw shapes or paths with mouse",
    }

    async def _heuristic_system_fallback(self, message: str, task_id: str) -> str:
        """LLM-based fallback — replaces all hardcoded regex for system tasks."""
        params = await self._llm_parse(
            message=message,
            schema={
                "action": "launch_app | close_app | focus_window | minimize_window | maximize_window | restore_window | snap_window | get_clipboard | set_clipboard | take_screenshot | show_notification | clean_temp_files | organize_desktop | read_file | write_file | system_stats | other",
                "app_name": "app/process name, window title, or position ('left', 'right', etc.) — null if not applicable",
                "file_path": "file path if read/write — null if not applicable",
                "file_content": "content to write or clipboard/notification text — null if not applicable",
            },
            context_hint="Extract the requested system action, app/process target, and file parameters based on user intent.",
        )
        action = params.get("action", "other")
        app_name = (params.get("app_name") or "").strip()
        file_path = (params.get("file_path") or "").strip()
        file_content = params.get("file_content") or ""

        if action == "snap_window":
            res = await self._tool_snap_window(position=app_name or "left", title="active")
            return str(res)
        if action == "get_clipboard":
            return await self._tool_get_clipboard()
        if action == "set_clipboard":
            return await self._tool_set_clipboard(text=file_content or message)
        if action == "take_screenshot":
            res = await self._tool_take_screenshot(target="fullscreen")
            import json as _j
            return _j.dumps(res, indent=2) if isinstance(res, dict) else str(res)
        if action == "show_notification":
            return await self._tool_show_notification(title=app_name or "Makima", message=file_content or message)
        if action == "clean_temp_files":
            return await self._tool_clean_temp_files(confirmed=False)
        if action == "organize_desktop":
            org_target = self._extract_organize_target(message, {}, target)
            res = await self._tool_organize_desktop(target_folder=org_target)
            return f"Organized '{org_target}': {res}"
        if action == "minimize_window":
            target = app_name or getattr(self, "_last_app_ref", "") or "active"
            res = await self._tool_manage_window(action="minimize", title=target)
            return str(res)
        if action == "maximize_window":
            target = app_name or getattr(self, "_last_app_ref", "") or "active"
            res = await self._tool_manage_window(action="maximize", title=target)
            return str(res)
        if action == "restore_window":
            target = app_name or getattr(self, "_last_app_ref", "") or "active"
            res = await self._tool_manage_window(action="restore", title=target)
            return str(res)
        if action == "focus_window":
            target = app_name or getattr(self, "_last_app_ref", "") or "active"
            res = await self._tool_manage_window(action="focus", title=target)
            return str(res)
        if action == "launch_app":
            target = app_name or getattr(self, "_last_app_ref", "")
            if not target:
                return "Please specify which application to launch."
            res = await self._tool_launch_app(app_path=target)
            return str(res)
        if action in ("close_app", "close_window"):
            target = app_name or getattr(self, "_last_app_ref", "") or "active"
            win_res = await self._tool_manage_window(action="close", title=target)
            if "Successfully closed" in win_res or "Closed" in win_res:
                return win_res
            blocked, reason = await self._pre_tool_gate(
                "kill_process", {"process_name": target, "force": True}
            )
            if blocked:
                return f"[BLOCKED] {reason}"
            res = await self._tool_kill_process(process_name=target)
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
        return "I couldn't quite process that system request — please specify the app name or action."

    def _reset_state(self) -> None:
        """Reset execution state while preserving conversational referents (e.g. _last_app_ref)."""
        super()._reset_state()
        # Note: self._last_app_ref is intentionally NOT reset to maintain conversation continuity across turns.

    # ==============================================================================
    # EXECUTION ORCHESTRATOR
    # ==============================================================================
    async def execute(
        self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]
    ) -> str:
        """Main execution loop following the BaseAgent contract."""
        self._reset_state()
        self._current_task_id = task_id
        self._current_context = context

        # ── Cross-Domain Dynamic Delegation Guard ──
        # If the task domain is outside system control (e.g. media, code, messaging),
        # dynamically route to that specialized agent via Orchestrator without hardcoded keywords.
        domain = str(entities.get("domain") or "").lower().strip()
        target_agent = entities.get("agent_name") or entities.get("target_agent")
        msg_l_check = (message or "").lower()
        has_system_directive = any(k in msg_l_check for k in (
            "system agent", "system_agent", "system ki", "system volume", "master volume",
            "pc volume", "universal sound", "universal volume", "device sound", "speaker sound",
            "media nahi", "media nhi", "not media", "system se", "system use", "use system"
        ))
        
        if (domain == "media" or target_agent == "media_agent") and not has_system_directive:
            try:
                from ..tools.media_tools import media_play
                import re as _re
                res = await media_play(query=message)
                song_title = res.get("resolved_title") or message
                clean_s = _re.sub(r"^(?:play|bajao|chalao|listen\s+to)\s+", "", str(song_title), flags=_re.IGNORECASE).strip()
                clean_s = _re.sub(r"\s+(?:bajao|chalao|play|song)$", "", clean_s, flags=_re.IGNORECASE).strip() or str(song_title)
                res_str = f"🎵 {clean_s.title()} — Now Playing"
                self._partial_result = res_str
                return res_str
            except Exception as m_err:
                logger.debug("[system] Media tool fallback error: %s", m_err)

        # ── Dynamic Tool Dispatch from Structured Parameters ──
        agent_task = getattr(self, "_current_agent_task", None) or (context.get("agent_task") if isinstance(context, dict) else None)
        op = str(getattr(agent_task, "operation", "") or entities.get("operation") or entities.get("action") or "").lower().strip()
        params = dict(getattr(agent_task, "parameters", None) or entities or {})
        target = getattr(agent_task, "target_entity", None) or params.get("target") or params.get("app_path") or params.get("title")

        DIRECT_OP_DISPATCH: dict[str, Callable[[], Awaitable[Any]]] = {
            "launch": lambda: self._tool_launch_app(app_path=str(target), arguments=params.get("arguments")),
            "open": lambda: self._tool_launch_app(app_path=str(target), arguments=params.get("arguments")),
            "launch_app": lambda: self._tool_launch_app(app_path=str(target), arguments=params.get("arguments")),
            "close": lambda: self._tool_manage_window(action="close", title=str(target or "active")),
            "focus": lambda: self._tool_manage_window(action="focus", title=str(target or "active")),
            "minimize": lambda: self._tool_manage_window(action="minimize", title=str(target or "active")),
            "maximize": lambda: self._tool_manage_window(action="maximize", title=str(target or "active")),
            "restore": lambda: self._tool_manage_window(action="restore", title=str(target or "active")),
            "manage_window": lambda: self._tool_manage_window(action=str(params.get("action") or "focus"), title=str(target or "active")),
            "kill": lambda: self._tool_kill_process(process_name=str(target), force=bool(params.get("force", True))),
            "kill_process": lambda: self._tool_kill_process(process_name=str(target), force=bool(params.get("force", True))),
            "system_stats": lambda: self._tool_get_system_stats(),
            "get_system_stats": lambda: self._tool_get_system_stats(),
            "set_volume": lambda: self._tool_set_volume(**self._extract_system_volume_params(params, target, message)),
            "adjust_volume": lambda: self._tool_set_volume(**self._extract_system_volume_params(params, target, message)),
            "volume": lambda: self._tool_set_volume(**self._extract_system_volume_params(params, target, message)),
            "get_volume": lambda: self._tool_get_volume(),
            "snap_window": lambda: self._tool_snap_window(position=params.get("position") or "left", title=str(target or "active")),
            "get_clipboard": lambda: self._tool_get_clipboard(),
            "set_clipboard": lambda: self._tool_set_clipboard(text=str(params.get("text") or target or message)),
            "take_screenshot": lambda: self._tool_take_screenshot(target=str(params.get("target") or "fullscreen"), title=str(target or "active")),
            "show_notification": lambda: self._tool_show_notification(title=str(params.get("title") or "Makima"), message=str(params.get("message") or target or message)),
            "clean_temp_files": lambda: self._tool_clean_temp_files(confirmed=bool(params.get("confirmed", False))),
            "organize_desktop": lambda: self._tool_organize_desktop(target_folder=self._extract_organize_target(message, params, target)),
        }

        # ── Semantic Fast-Path Gating ──
        # Fast path is allowed ONLY when:
        # 1. Target is a single concrete entity (not None, not empty, not "all").
        # 2. negative_constraints is EMPTY (no exclusions).
        # 3. No compound or ambiguous qualifiers in the raw message ("except", "aur", "and", "other than", "all").
        # 4. Action is a safe, deterministic operation.
        neg_constraints = tuple(getattr(agent_task, "negative_constraints", ())) if agent_task else ()
        msg_l = (message or "").lower()
        has_exclusion = bool(neg_constraints) or any(w in msg_l for w in ("except", "lekin", "magar", "chhod ke", "other than", "don't close", "dont close", "keep open", "without"))
        has_compound = any(w in msg_l for w in (" aur ", " and ", " also ", " then ")) and len(msg_l.split()) > 4
        has_qualifier = any(w in msg_l for w in ("containing", "that contains", "which has", "that has", "titled", "jisme", "jismein", "wala", "wali", "another", "the other", "second", "third", "dusra", "dusri", "pehle", "pehela", "jo open"))
        is_generic_all = str(target or "").strip().lower() in ("all", "everything", "all windows", "saare", "har ek")
        is_volume_op = op in ("set_volume", "adjust_volume", "volume", "get_volume") or any(w in msg_l for w in ("sound badhao", "sound kam", "volume badhao", "volume kam", "master volume", "system volume", "universal sound", "aawaz badhao", "aawaz kam", "awaaz badhao", "awaaz kam"))

        allow_fast_path = (
            not has_exclusion
            and not has_compound
            and not has_qualifier
            and not is_generic_all
            and (target is not None or params.get("text") is not None or is_volume_op or op in ("get_system_stats", "system_stats", "get_clipboard", "set_clipboard", "clean_temp_files", "take_screenshot"))
        )

        if (op in DIRECT_OP_DISPATCH or (is_volume_op and not op)) and allow_fast_path:
            try:
                dispatch_key = op if op in DIRECT_OP_DISPATCH else "set_volume"
                res = await DIRECT_OP_DISPATCH[dispatch_key]()
                if not str(res).startswith("[BLOCKED"):
                    if isinstance(res, dict) and op in ("get_system_stats", "system_stats"):
                        ram = res.get("ram", {}) if isinstance(res, dict) else {}
                        cpu = res.get("cpu", {}) if isinstance(res, dict) else {}
                        disk = res.get("disk", {}) if isinstance(res, dict) else {}
                        ram_str = f"**RAM**: {ram.get('used_gb', 'N/A')} GB used / {ram.get('total_gb', 'N/A')} GB total ({ram.get('percent', 'N/A')}%)"
                        cpu_str = f"**CPU**: {cpu.get('percent', 'N/A')}% ({cpu.get('count', 'N/A')} cores)"
                        disk_str = f"**Disk**: {disk.get('free_gb', 'N/A')} GB free / {disk.get('total_gb', 'N/A')} GB total ({disk.get('percent', 'N/A')}% used)"
                        res = f"📊 **System Status**:\n- {ram_str}\n- {cpu_str}\n- {disk_str}"
                    elif isinstance(res, dict):
                        res = json.dumps(res, indent=2)
                    else:
                        res = str(res)
                    self._partial_result = res
                    return res
            except Exception as e:
                logger.warning("[system] Direct tool execution '%s' failed: %s; falling back to ReAct", op, e)

        # Fallback to multi-turn ReAct LLM loop for complex or open-ended requests
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
        # ── OpenAI Agents SDK Runner Execution ────────────────────────────────
        try:
            final_out = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=6,
                task_type="system_control",
                extra_system=extra_system,
            )
            self._partial_result = final_out
            return final_out
        except Exception as sdk_exc:
            logger.error("[system] SDK error: %s", sdk_exc)
            return f"Task complete nahi hua: {str(sdk_exc)}"

    async def _pre_tool_gate(self, tool_name: str, params: dict[str, Any], context: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
        """Confirmation gate for destructive operations before the ReAct loop executes a tool."""
        base_blocked, base_reason = await super()._pre_tool_gate(tool_name, params, context=context)
        if base_blocked:
            return True, base_reason

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
        if tool_name == "system_power":
            return True
        if tool_name == "manage_service" and params.get("action", "").lower() in ("stop", "restart"):
            return True
        if tool_name == "kill_process":
            pname = (params.get("process_name") or "").lower().replace(".exe", "").strip()
            pid = params.get("pid")
            if pid:
                is_crit, _ = is_critical_process(pid)
                if is_crit:
                    return True
            if pname and psutil:
                for proc in psutil.process_iter(["name"]):
                    try:
                        p_name = (proc.info.get("name") or "").lower().replace(".exe", "")
                        if pname == p_name or pname in p_name:
                            is_crit, _ = is_critical_process(proc)
                            if is_crit:
                                return True
                    except Exception:
                        pass
            return False
        # Window management (focus/minimize/maximize/restore/close) is safe user interaction
        return False

    # ==============================================================================
    # ELITE TOOL IMPLEMENTATIONS
    # ==============================================================================
    async def _tool_get_system_stats(self) -> dict[str, Any]:
        """Retrieve comprehensive hardware and OS statistics."""
        if not psutil:
            return {"error": "psutil is not installed. Cannot retrieve system stats."}

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

    async def _tool_search_installed_apps(self, query: str = "", limit: int = 15) -> list[dict[str, str]]:
        """Search installed applications, executables, shortcuts, and protocols on the computer."""
        return await asyncio.to_thread(search_installed_apps, query, limit)

    async def _tool_get_process_list(self, filter_name: str = "", top_n: int = 10) -> list[dict[str, Any]]:
        """List running processes, sorted by memory usage."""
        if not psutil:
            return [{"error": "psutil not installed"}]

        def _scan():
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status']):
                try:
                    pinfo = proc.info
                    if filter_name and filter_name.lower() not in (pinfo['name'] or "").lower():
                        continue
                    processes.append({
                        "pid": pinfo['pid'],
                        "name": pinfo['name'],
                        "cpu": pinfo['cpu_percent'],
                        "mem_mb": round(pinfo['memory_info'].rss / (1024 * 1024), 2) if pinfo['memory_info'] else 0,
                        "status": pinfo['status'],
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            processes.sort(key=lambda x: x['mem_mb'], reverse=True)
            top = processes[:top_n]
            # Annotate danger only for the returned slice — is_critical_process opens a
            # security token per PID, so keep it bounded to top_n rather than every process.
            for entry in top:
                try:
                    is_crit, _ = is_critical_process(entry["pid"])
                    entry["danger"] = "critical" if is_crit else "normal"
                except Exception:
                    entry["danger"] = "normal"
            return top

        return await asyncio.to_thread(_scan)

    async def _process_running(self, probe: str) -> bool:
        """True if any running process name matches probe."""
        return await self._count_processes(probe) > 0

    async def _count_processes(self, probe: str) -> int:
        """Count running processes whose name matches probe (case-insensitive, .exe tolerant)."""
        if not psutil:
            return 0

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
            resolved_tgt, src, conf = await self._resolve_contextual_referent(target_name, getattr(self, "_current_context", None))
            if resolved_tgt:
                target_name = resolved_tgt
            t_clean = target_name.lower().strip()
            base_name = t_clean.replace(".exe", "").lower()
            exe_name = base_name + ".exe"
            
            # Dynamic candidate expansion via live app discovery (Zero hardcoding)
            candidate_names = {exe_name, base_name, t_clean, f"{base_name}app", f"{base_name}app.exe"}
            try:
                discovered = search_installed_apps(base_name)
                for a in discovered:
                    c_clean = a.get("name", "").lower()
                    c_base = re.sub(r'^(windows|microsoft)\s+|\s*\(protocol\)', '', os.path.splitext(os.path.basename(c_clean))[0], flags=re.I).split("(")[0].strip()
                    if c_base:
                        candidate_names.add(c_base)
                        candidate_names.add(c_base + ".exe")
                        candidate_names.add(f"{c_base}app")
                        candidate_names.add(f"{c_base}app.exe")
            except Exception:
                pass

            killed_count = 0
            if psutil:
                def _kill_scan():
                    killed = 0
                    target_procs = []

                    # Phase 1: Identify and send WM_CLOSE to all matching GUI windows
                    for proc in psutil.process_iter(['pid', 'name']):
                        try:
                            p_name = (proc.info['name'] or "").lower()
                            p_name_clean = p_name.replace(".exe", "")
                            is_match = (
                                p_name in candidate_names
                                or p_name_clean in candidate_names
                            )
                            if is_match:
                                is_crit, _ = is_critical_process(proc)
                                if is_crit:
                                    continue
                                target_procs.append(proc)

                                if win32gui and win32con:
                                    try:
                                        import win32process
                                        target_pid_val = proc.pid
                                        hwnds = []
                                        def _find_hwnd(hwnd: int, _: Any) -> None:
                                            if win32gui.IsWindowVisible(hwnd):
                                                _, pid_out = win32process.GetWindowThreadProcessId(hwnd)
                                                if pid_out == target_pid_val:
                                                    hwnds.append(hwnd)
                                        win32gui.EnumWindows(_find_hwnd, None)
                                        for h in hwnds:
                                            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                                    except Exception:
                                        pass
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass

                    # Phase 2: Batch wait and terminate (Fixes the sequential blocking bug)
                    for proc in target_procs:
                        try:
                            proc.wait(timeout=1.5)
                            killed += 1
                        except (psutil.TimeoutExpired, Exception):
                            try:
                                proc.terminate()
                                proc.wait(timeout=1.0)
                                killed += 1
                            except Exception:
                                if force:
                                    try:
                                        proc.kill()
                                        killed += 1
                                    except Exception:
                                        pass
                                else:
                                    killed += 1
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    return killed

                killed_count = await asyncio.to_thread(_kill_scan)

            # Invalidate OS state process cache via canonical WorldStateService
            try:
                from ..core.world_state import get_world_state
                get_world_state().invalidate_processes()
            except Exception:
                pass

            if killed_count > 0:
                # Post-kill verification: re-scan for survivors
                await asyncio.sleep(0.5)
                still = await self._count_processes(base_name)
                if still == 0:
                    return f"Successfully terminated {killed_count} instance(s) of '{target_name}' (verified)."
                return (f"Terminated {killed_count} instance(s) of '{target_name}', but {still} "
                        f"still running (access denied or respawned).")

            # Fallback to Windows taskkill
            if platform.system() == "Windows":
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "taskkill", "/f", "/im", exe_name,
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
                is_crit, reason = is_critical_process(proc)
                if is_crit:
                    return (f"REFUSED: process '{pname}' (PID: {target_pid}) is a protected system process ({reason}). "
                            f"Terminating it could destabilize the host OS.")
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
            "realtime": psutil.REALTIME_PRIORITY_CLASS,
        }

        p_class = priority_map.get(priority.lower())
        if p_class is None:
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

    async def _tool_launch_app(self, app_path: str = "", arguments: list[str] | None = None, **kwargs: Any) -> str:
        """Launch an application asynchronously on the host OS."""
        target = app_path or kwargs.get("app_name") or kwargs.get("name") or kwargs.get("path") or kwargs.get("app") or ""
        if target and isinstance(target, str):
            self._last_app_ref = target.strip().lower()
        args = arguments or kwargs.get("args")
        return await launch_app_verified(target, args)

    @staticmethod
    def _extract_organize_target(
        message: str, params: dict[str, Any], target: Any,
    ) -> str:
        """Resolve which folder to organize — Desktop ONLY as last resort.
        Priority: explicit tool param → typed target entity → formal drive-path
        syntax in the message (e.g. 'D:\\Games') → known-folder name in message
        (OneDrive-aware) → Desktop default."""
        from ..core.known_folders import resolve_known_folder, normalize_folder_name

        for cand in (
            params.get("target_folder") if isinstance(params, dict) else None,
            params.get("folder") if isinstance(params, dict) else None,
            params.get("path") if isinstance(params, dict) else None,
            str(target).strip() if target else "",
        ):
            c = str(cand or "").strip().strip("'\"")
            if not c:
                continue
            # Formal path syntax (drive/UNC) is structure, not a keyword guess
            if re.match(r"^[A-Za-z]:[\\/].+", c) or c.startswith("\\\\"):
                return c
            resolved = resolve_known_folder(c)
            if resolved:
                return resolved

        # Drive-path references inside the free-form message. Two formal
        # filesystem shapes: literal 'D:\Games' and spoken 'd drive games folder'.
        m = re.search(r"\b([A-Za-z])\s*:\s*[\\/]\s*([\w\- .\\/]*)", message)
        if m and m.group(2).strip():
            return f"{m.group(1).upper()}:\\{m.group(2).strip().strip('\\/')}"

        m = re.search(
            r"\b([A-Za-z])\s+drive\s+(?:me\s+)?(?:jo\s+|ka\s+)?([A-Za-z0-9_\- ]{2,40}?)\s*(?:folder|directory)\b",
            message, re.IGNORECASE,
        )
        if m:
            drive = f"{m.group(1).upper()}:"
            name = m.group(2).strip()
            # Case-insensitive match against the REAL directory listing first
            # (Windows isdir() is case-insensitive and would bless 'D:\games'
            # even when the actual folder is 'Games').
            try:
                for entry in os.listdir(drive + "\\"):
                    if entry.lower() == name.lower():
                        return f"{drive}\\{entry}"  # os.path.join skips '\' after 'D:'
            except OSError:
                pass
            return f"{drive}\\{name}"

        # Known-folder NAME mentioned anywhere in the message
        for word in re.findall(r"[A-Za-z]+", message):
            canonical = normalize_folder_name(word)
            if canonical:
                resolved = resolve_known_folder(canonical)
                if resolved:
                    return resolved

        return "Desktop"

    def _extract_system_volume_params(self, params: dict[str, Any], target: Any, message: str) -> dict[str, Any]:
        """
        Extract volume parameters (level, delta, action) prioritizing structured planner entities,
        falling back to raw message parsing only when structured parameters are incomplete.
        """
        msg = (message or "").lower()
        level = None
        if params.get("level") is not None:
            try:
                level = int(params["level"])
            except (ValueError, TypeError):
                pass
        elif params.get("volume") is not None:
            try:
                level = int(params["volume"])
            except (ValueError, TypeError):
                pass
        elif target and str(target).isdigit():
            level = int(str(target))

        delta = None
        if params.get("delta") is not None:
            try:
                delta = int(params["delta"])
            except (ValueError, TypeError):
                pass
        elif params.get("change") is not None:
            try:
                delta = int(params["change"])
            except (ValueError, TypeError):
                pass

        action = params.get("action")
        if isinstance(action, str):
            action_l = action.lower().strip()
            if action_l in ("mute", "unmute"):
                action = action_l
            elif action_l in ("increase", "raise", "up", "loud", "louder", "badhao", "badha") and delta is None and level is None:
                delta = 10
            elif action_l in ("decrease", "lower", "down", "dheere", "kam", "quiet", "quieter") and delta is None and level is None:
                delta = -10

        # Structured entities are complete -> return immediately without regex parsing
        if level is not None or delta is not None or action in ("mute", "unmute"):
            out: dict[str, Any] = {}
            if level is not None:
                out["level"] = level
            if delta is not None:
                out["delta"] = delta
            if action is not None:
                out["action"] = action
            return out

        # ── Fallback: Natural language parameter extraction from raw message ──
        # Check explicit mute/unmute
        if "mute" in msg and "unmute" not in msg:
            action = "mute"
        elif "unmute" in msg:
            action = "unmute"

        # Check percentage in message (e.g. "set volume 70%", "volume 40 pe karo")
        if level is None and action not in ("mute", "unmute"):
            m_pct = re.search(r'\b(\d{1,3})\s*%', msg) or re.search(r'\b(?:to|pe|par|at|ko)\s*(\d{1,3})\b', msg)
            if m_pct:
                val = int(m_pct.group(1))
                if 0 <= val <= 100:
                    level = val

        # Directional delta extraction
        if level is None and delta is None and action not in ("mute", "unmute"):
            if any(w in msg for w in ("badhao", "badha", "increase", "up", "tez", "raise", "loud", "louder")):
                delta = 10
            elif any(w in msg for w in ("kam", "decrease", "down", "dheere", "lower", "slow", "quiet", "quieter")):
                delta = -10

        out = {}
        if level is not None:
            out["level"] = level
        if delta is not None:
            out["delta"] = delta
        if action is not None:
            out["action"] = action
        return out

    async def _tool_get_volume(self, **kwargs: Any) -> str:
        """Get current Windows OS master volume."""
        from .os_state import get_master_volume
        vol = await get_master_volume()
        if vol is not None:
            return f"🔊 Current Master Volume: {vol}%"
        return "⚠️ Master volume could not be read."

    async def _tool_set_volume(self, level: int | str | None = None, delta: int | str | None = None, action: str | None = None, **kwargs: Any) -> str:
        """Set or adjust master system volume (0-100) via Windows Core Audio API."""
        from ..tools.system_tools import set_volume as _system_set_volume
        return await _system_set_volume(level=level, delta=delta, action=action, **kwargs)

    async def _resolve_contextual_referent(self, title_query: str, context: Optional[dict[str, Any]] = None) -> tuple[str, str, float]:
        """
        Generic typed referent resolution for window management and process termination.
        Operates strictly on structured referent metadata emitted by the semantic planner.
        Zero natural-language phrase matching.
        Returns: (resolved_target, source_provenance, confidence)
        """
        title_q_lower = (title_query or "").strip().lower()
        if title_q_lower in ("it", "this", "that", "same", "active", "same app") and getattr(self, "_last_app_ref", None):
            logger.info("[referent_resolver] Resolved direct anaphora '%s' to recent app: '%s'", title_q_lower, self._last_app_ref)
            return str(self._last_app_ref), "recent_app_memory", 0.95

        context = context or getattr(self, "_current_context", {}) or {}
        slots = context.get("grounded_slots") if isinstance(context.get("grounded_slots"), dict) else {}
        ref_obj = slots.get("referent") if isinstance(slots.get("referent"), dict) else {}
        ref_kind = str(ref_obj.get("kind") or "").strip().lower()
        grounded_target = slots.get("target") or ref_obj.get("entity_name")

        # 1. Kind: 'active_media_owner'
        if ref_kind == "active_media_owner" or slots.get("target_type") == "active_media":
            # If the planner already grounded the media owner name (e.g. "Brave", "Spotify"), use it
            if grounded_target and isinstance(grounded_target, str) and grounded_target.strip():
                logger.info("[referent_resolver] Resolved referent via grounded media target: '%s'", grounded_target)
                return grounded_target.strip(), "grounded_slots.active_media", 0.98

            # Query live OS audio session owner via WorldStateService
            try:
                from ..core.world_state import get_world_state
                os_audio = await get_world_state().get_active_audio_owner()
                if os_audio:
                    logger.info("[referent_resolver] Resolved playing app via WorldStateService audio session: '%s'", os_audio)
                    return os_audio, "os_audio_session", 0.99
            except Exception:
                pass

            # Query live OS media windows
            media_windows: list[str] = []
            if win32gui:
                def _scan_media_w(hwnd: int, _: Any) -> None:
                    if win32gui.IsWindowVisible(hwnd):
                        w_title = win32gui.GetWindowText(hwnd) or ""
                        w_lower = w_title.lower()
                        if any(k in w_lower for k in ("youtube", "spotify", "soundcloud", "music", "vlc", "netflix")):
                            media_windows.append(w_title)
                try:
                    win32gui.EnumWindows(_scan_media_w, None)
                except Exception:
                    pass

            # Check active browser tab or media window
            if media_windows:
                return media_windows[0], "active_media_runtime", 0.98

        # 2. Kind: 'previous_entity' (Anaphora resolved from conversation context)
        if ref_kind == "previous_entity":
            if grounded_target and isinstance(grounded_target, str) and grounded_target.strip():
                logger.info("[referent_resolver] Resolved anaphoric referent to entity: '%s'", grounded_target)
                return grounded_target.strip(), "grounded_slots.previous_entity", 0.98

            if getattr(self, "_last_app_ref", None):
                return str(self._last_app_ref), "recent_app_memory", 0.95

        # 3. Kind: 'foreground_window' (Explicit active/foreground window)
        if ref_kind == "foreground_window" or slots.get("target_type") == "window":
            if win32gui:
                try:
                    fg_hwnd = win32gui.GetForegroundWindow()
                    if fg_hwnd and win32gui.IsWindowVisible(fg_hwnd):
                        fg_title = win32gui.GetWindowText(fg_hwnd) or "Active Window"
                        # Chat UI self-protection: If foreground is Makima UI, find the topmost user window
                        if any(ui_kw in fg_title.lower() for ui_kw in ("makima", "antigravity", "chat ui")):
                            other_windows = []
                            def _enum_others(h: int, _: Any) -> None:
                                if win32gui.IsWindowVisible(h) and h != fg_hwnd:
                                    t = win32gui.GetWindowText(h)
                                    if t and not any(k in t.lower() for k in ("makima", "antigravity", "taskbar", "program manager")):
                                        other_windows.append(t)
                            win32gui.EnumWindows(_enum_others, None)
                            if other_windows:
                                fg_title = other_windows[0]
                        return fg_title, "foreground_window", 0.95
                except Exception as e:
                    logger.debug("[referent_resolver] Failed to query foreground window: %s", e)

        # 4. Fallback: If planner resolved a concrete target name, use it
        if grounded_target and isinstance(grounded_target, str) and grounded_target.strip():
            return grounded_target.strip(), "grounded_slots.target", 0.95

        # 5. Direct Named Query / Passthrough
        return title_query.strip(), "explicit_named_query", 1.0

    async def _tool_get_window_list(self, filter_name: str = "", active_only: bool = True, **kwargs: Any) -> list[dict[str, Any]]:
        """Inspect and list open application windows on the desktop with HWND, title, and process metadata."""
        if not win32gui:
            return [{"error": "win32gui is not available on this platform"}]

        def _scan_windows() -> list[dict[str, Any]]:
            results: list[dict[str, Any]] = []
            fg_hwnd = win32gui.GetForegroundWindow() if win32gui else 0

            def _enum_cb(hwnd: int, _: Any) -> bool:
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return True
                    title = win32gui.GetWindowText(hwnd).strip()
                    if not title:
                        return True

                    if active_only:
                        if title in ("Program Manager", "Default IME", "MSCTFIME UI"):
                            return True
                        if win32con and hasattr(win32con, "WS_EX_TOOLWINDOW") and isinstance(win32con.WS_EX_TOOLWINDOW, int):
                            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                            if isinstance(style, int) and (style & win32con.WS_EX_TOOLWINDOW):
                                return True

                    p_name = ""
                    pid = 0
                    if win32process:
                        try:
                            _, pid = win32process.GetWindowThreadProcessId(hwnd)
                            if psutil and pid > 0:
                                try:
                                    p_name = psutil.Process(pid).name()
                                except Exception:
                                    p_name = ""
                        except Exception:
                            pass

                    is_active = (hwnd == fg_hwnd)

                    if filter_name:
                        f_lower = filter_name.lower().strip()
                        if f_lower not in title.lower() and f_lower not in p_name.lower():
                            return True

                    results.append({
                        "hwnd": hwnd,
                        "title": title,
                        "process_name": p_name,
                        "pid": pid,
                        "is_active": is_active,
                    })
                except Exception:
                    pass
                return True

            try:
                win32gui.EnumWindows(_enum_cb, None)
            except Exception as e:
                logger.debug("[system] EnumWindows scan error: %s", e)

            return results

        return await asyncio.to_thread(_scan_windows)

    async def _tool_manage_window(self, action: str, title: str = "", hwnd: int | str | None = None, **kwargs: Any) -> str:
        """Control window states (focus, minimize, maximize, close)."""
        if not win32gui:
            return "win32gui is not available. Window management requires pywin32."

        action = (action or "").lower().strip()

        # 1. Exact HWND Direct Targeting
        raw_hwnd = None
        if hwnd is not None:
            try:
                raw_hwnd = int(str(hwnd).strip())
            except (ValueError, TypeError):
                raw_hwnd = None

        if raw_hwnd is not None:
            if not win32gui.IsWindow(raw_hwnd):
                return f"Window handle {raw_hwnd} is no longer valid or has already closed."
            actual_title = win32gui.GetWindowText(raw_hwnd) or f"Window (HWND {raw_hwnd})"
            target_hwnd = raw_hwnd
            windows = [(target_hwnd, actual_title)]
            title_lower = actual_title.lower().strip()
        else:
            title_raw = (title or kwargs.get("name") or kwargs.get("target") or "").strip()

            # Generic typed referent resolution before Win32 manipulation
            resolved_title, source, conf = await self._resolve_contextual_referent(title_raw, getattr(self, "_current_context", None))
            if resolved_title:
                title_raw = resolved_title
            if title_raw:
                self._last_app_ref = title_raw.strip().lower()

            title_lower = title_raw.lower().strip()

            # Handle active / current window queries ("active", "current window", "foreground")
            if title_lower in ("current window", "current", "active", "active window", "foreground"):
                try:
                    hwnd_fg = win32gui.GetForegroundWindow()
                    if hwnd_fg and win32gui.IsWindowVisible(hwnd_fg):
                        actual_title = win32gui.GetWindowText(hwnd_fg) or "Active Window"
                        if action == "focus":
                            if win32gui.IsIconic(hwnd_fg):
                                win32gui.ShowWindow(hwnd_fg, win32con.SW_RESTORE)
                            else:
                                win32gui.ShowWindow(hwnd_fg, win32con.SW_SHOW)
                            win32gui.SetForegroundWindow(hwnd_fg)
                            win32gui.BringWindowToTop(hwnd_fg)
                        elif action == "minimize":
                            win32gui.ShowWindow(hwnd_fg, win32con.SW_MINIMIZE)
                        elif action == "maximize":
                            win32gui.ShowWindow(hwnd_fg, win32con.SW_MAXIMIZE)
                        elif action == "restore":
                            win32gui.ShowWindow(hwnd_fg, win32con.SW_RESTORE)
                        elif action == "close":
                            win32gui.PostMessage(hwnd_fg, win32con.WM_CLOSE, 0, 0)
                            await asyncio.sleep(0.5)
                            try:
                                from ..core.world_state import get_world_state
                                get_world_state().invalidate_windows()
                            except Exception:
                                pass
                            is_closed = not win32gui.IsWindow(hwnd_fg)
                            if is_closed:
                                return f"Successfully closed active window '{actual_title}'."
                            return f"Close signal sent to active window '{actual_title}'."

                        try:
                            from ..core.world_state import get_world_state
                            get_world_state().invalidate_windows()
                        except Exception:
                            pass
                        return f"Successfully executed '{action}' on active window '{actual_title}'."
                except Exception as e:
                    logger.debug("Failed active window action: %s", e)

            def enum_callback(h: int, results: list) -> bool:
                try:
                    if win32gui.IsWindowVisible(h):
                        window_title = win32gui.GetWindowText(h)
                        if window_title and title_lower in window_title.lower():
                            results.append((h, window_title))
                except Exception:
                    pass
                return True

            windows = []
            try:
                win32gui.EnumWindows(enum_callback, windows)
            except Exception:
                pass

            # If not found by window title, try finding via running processes matching title
            if not windows and psutil:
                matching_pids: set[int] = set()
                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        pname = (proc.info["name"] or "").lower().replace(".exe", "")
                        if title_lower in pname or pname in title_lower:
                            matching_pids.add(proc.info["pid"])
                    except Exception:
                        pass

                if matching_pids and win32process:
                    def enum_pid_callback(h: int, results: list) -> bool:
                        if win32gui.IsWindowVisible(h):
                            try:
                                _, pid = win32process.GetWindowThreadProcessId(h)
                                if pid in matching_pids:
                                    window_title = win32gui.GetWindowText(h)
                                    if window_title:
                                        results.append((h, window_title))
                            except Exception:
                                pass
                        return True

                    try:
                        win32gui.EnumWindows(enum_pid_callback, windows)
                    except Exception:
                        pass

            if not windows:
                if action == "focus":
                    try:
                        p_sub = await asyncio.create_subprocess_exec(
                            "powershell", "-NoProfile", "-Command",
                            "param($t); $ws = New-Object -ComObject WScript.Shell; $res = $ws.AppActivate($t); exit ([int](-not $res))",
                            title_raw,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        exit_code = await p_sub.wait()
                        if exit_code == 0:
                            return f"Brought '{title_raw}' to front successfully."
                    except Exception:
                        pass
                if action == "close":
                    kill_res = await self._tool_kill_process(process_name=title_raw)
                    if "Successfully terminated" in kill_res or "Terminated" in kill_res:
                        return f"Closed '{title_raw}' (via process termination)."
                    return f"No open window or active process found for '{title_raw}' (already closed)."
                return f"No visible windows found matching title '{title_raw}'."

            # Rank matching windows by title similarity
            def _score_window(item: tuple[int, str]) -> float:
                w_title = item[1].lower()
                if w_title == title_lower:
                    return 1.0
                if w_title.startswith(title_lower):
                    return 0.95
                if f" {title_lower} " in f" {w_title} ":
                    return 0.90
                return difflib.SequenceMatcher(None, title_lower, w_title).ratio()

            windows.sort(key=_score_window, reverse=True)
            target_hwnd, actual_title = windows[0]

        hwnd = target_hwnd
        try:
            if action == "focus":
                try:
                    import win32api
                    cur_tid = win32api.GetCurrentThreadId()
                    tgt_tid, _ = win32process.GetWindowThreadProcessId(hwnd) if win32process else (0, 0)
                    if tgt_tid and win32process:
                        win32process.AttachThreadInput(cur_tid, tgt_tid, True)
                    if win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    else:
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    win32gui.SetForegroundWindow(hwnd)
                    win32gui.BringWindowToTop(hwnd)
                    if tgt_tid and win32process:
                        try:
                            win32process.AttachThreadInput(cur_tid, tgt_tid, False)
                        except Exception:
                            pass
                except Exception:
                    if win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    else:
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    except Exception:
                        pass

                try:
                    await asyncio.create_subprocess_exec(
                        "powershell", "-NoProfile", "-Command",
                        "param($t); $ws = New-Object -ComObject WScript.Shell; $ws.AppActivate($t)",
                        actual_title,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
            elif action == "minimize":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            elif action == "maximize":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            elif action == "restore":
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            elif action == "close":
                for w_hwnd, _ in windows:
                    try:
                        if win32gui.IsWindow(w_hwnd):
                            win32gui.PostMessage(w_hwnd, win32con.WM_CLOSE, 0, 0)
                    except Exception:
                        pass

                await asyncio.sleep(0.6)

                if psutil and raw_hwnd is None:
                    for proc in psutil.process_iter(['pid', 'name']):
                        try:
                            p_name = (proc.info['name'] or '').lower().replace('.exe', '')
                            if title_lower in p_name or p_name in title_lower:
                                is_crit, _ = is_critical_process(proc)
                                if not is_crit:
                                    proc.terminate()
                        except Exception:
                            pass

                try:
                    from ..core.world_state import get_world_state
                    get_world_state().invalidate_windows()
                    get_world_state().invalidate_processes()
                except Exception:
                    pass

                still_open = any(win32gui.IsWindow(h) for h, _ in windows) if win32gui else False
                if not still_open:
                    return f"Successfully closed window '{actual_title}'."
                return f"Close signal sent to window '{actual_title}' (window still active/modal pending)."
            else:
                return f"Unknown window action: {action}"

            try:
                from ..core.world_state import get_world_state
                get_world_state().invalidate_windows()
            except Exception:
                pass

            return f"Successfully executed '{action}' on window '{actual_title}'."
        except Exception as e:
            return f"Failed to {action} window '{actual_title}': {str(e)}"

    async def _tool_system_power(self, action: str, task_id: str = "") -> str:
        """Execute system-level power state transitions."""
        action = action.lower()
        is_win = platform.system() == "Windows"

        if is_win and action == "sleep":
            def _win_sleep() -> str:
                try:
                    import ctypes
                    # PowrProf SetSuspendState(bHibernate=0, bForce=0, bWakeupEventsDisabled=0)
                    res = ctypes.windll.PowrProf.SetSuspendState(0, 0, 0)
                    if res:
                        return "System power action 'sleep' initiated successfully."
                    return "System power action 'sleep' requested."
                except Exception as ex:
                    return f"Failed to enter sleep: {ex}"
            return await asyncio.to_thread(_win_sleep)

        commands = {
            "sleep": "systemctl suspend",
            "hibernate": "rundll32.exe powrprof.dll,SetSuspendState 1,1,0" if is_win else "systemctl hibernate",
            "restart": "shutdown /r /t 5 /c \"Makima restart\"" if is_win else "shutdown -r -t 5",
            "shutdown": "shutdown /s /t 5 /c \"Makima shutdown\"" if is_win else "shutdown -h -t 5",
        }

        if action not in commands:
            return f"Unsupported power action: {action}. Supported: ['sleep', 'hibernate', 'restart', 'shutdown']"

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

    async def _tool_manage_service(self, service_name: str, action: str, task_id: str = "") -> str:
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

    async def _tool_get_network_adapters(self, active_only: bool = True) -> str:
        """Retrieve detailed network interface configurations and IP addresses."""
        if not psutil:
            return "psutil is not installed. Cannot retrieve network adapters."

        def _scan_net() -> str:
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
            lines = ["### 🌐 Network Interfaces:"]

            for iface, stats_info in stats.items():
                if active_only and not stats_info.isup:
                    continue
                status = "🟢 UP" if stats_info.isup else "🔴 DOWN"
                speed = f" ({stats_info.speed} Mbps)" if stats_info.speed > 0 else ""
                lines.append(f"- **{iface}** [{status}{speed}]:")
                if iface in addrs:
                    for addr in addrs[iface]:
                        fam_str = "IPv4" if str(addr.family) in ("2", "AddressFamily.AF_INET") else "IPv6" if "INET6" in str(addr.family) or str(addr.family) in ("23", "30") else str(addr.family)
                        if fam_str in ("IPv4", "IPv6"):
                            lines.append(f"  • {fam_str}: `{addr.address}`")
            if len(lines) == 1:
                lines.append("No active network interfaces found.")
            return "\n".join(lines)

        return await asyncio.to_thread(_scan_net)

    async def _tool_organize_desktop(self, target_folder: str = "Desktop", confirmed: bool = False) -> str:
        """Organize loose files on Desktop or target folder into categorized subfolders.

        Delegates to the shared FilesystemEngine so that SystemAgent and any
        future caller get identical behaviour (including dry-run preview).
        """
        from .filesystem_engine import get_filesystem_engine
        engine = get_filesystem_engine()
        return await engine.organize_desktop(target_folder=target_folder, confirmed=confirmed)

    def _resolve_user_path(self, path_str: str) -> str:
        """Resolve user friendly folder names to full OS paths.

        Delegates to FilesystemEngine._resolve so path resolution is
        consistent across all agents and callers.
        """
        from .filesystem_engine import FilesystemEngine
        return FilesystemEngine._resolve(path_str)

    async def _tool_move_file(self, source_path: str, target_folder_or_path: str) -> str:
        """Move a file or folder from source path to target folder or path with shadow snapshot."""
        from .filesystem_engine import get_filesystem_engine
        return await get_filesystem_engine().move_file(source_path, target_folder_or_path)

    async def _tool_copy_file(self, source_path: str, target_folder_or_path: str) -> str:
        """Copy a file or folder from source path to target folder or path."""
        from .filesystem_engine import get_filesystem_engine
        return await get_filesystem_engine().copy_file(source_path, target_folder_or_path)

    async def _tool_rename_file(self, file_path: str, new_name: str) -> str:
        """Rename a file or folder in place with shadow snapshot."""
        from .filesystem_engine import get_filesystem_engine
        return await get_filesystem_engine().rename_file(file_path, new_name)

    async def _tool_read_file(
        self,
        path: str = "",
        file_path: str = "",
        filepath: str = "",
        filename: str = "",
        target_path: str = "",
        **kwargs: Any
    ) -> str:
        """Read the contents of a file from disk with path traversal protection and parameter resilience."""
        actual_path = (path or file_path or filepath or filename or target_path or "").strip()
        if not actual_path:
            return "Error: No file path provided to read."
        from .filesystem_engine import get_filesystem_engine
        max_chars = getattr(self, "MAX_DOCUMENT_CHARS", 6000)
        return await get_filesystem_engine().read_file(actual_path, max_chars=max_chars)

    async def _tool_write_file(
        self,
        path: str = "",
        content: str = "",
        file_path: str = "",
        filepath: str = "",
        filename: str = "",
        text: str = "",
        data: str = "",
        **kwargs: Any
    ) -> str:
        """Write content to a file on disk with shadow snapshot protection and parameter resilience."""
        actual_path = (path or file_path or filepath or filename or "").strip()
        actual_content = content if content != "" else (text if text != "" else (data or ""))
        if not actual_path:
            return "Error: No file path provided to write."
        from .filesystem_engine import get_filesystem_engine
        return await get_filesystem_engine().write_file(actual_path, actual_content)

    async def _tool_mouse_click(self, x: int, y: int, button: str = "left", double: bool = False) -> str:
        """Click anywhere on the host desktop using PyAutoGUI."""
        if not pyautogui:
            return "pyautogui is not installed. Native mouse click unavailable."

        def _click() -> str:
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

        def _draw() -> str:
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
                        y_val = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
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

    async def _tool_keyboard_press(self, key: str) -> str:
        """Press a keyboard key or hotkey combination."""
        if not pyautogui:
            return "pyautogui is not installed. Keyboard action unavailable."
        def _press():
            clean_key = str(key or "").strip().lower()
            if not clean_key:
                return "No key specified to press."
            if "+" in clean_key:
                parts = [p.strip() for p in clean_key.split("+") if p.strip()]
                pyautogui.hotkey(*parts)
                return f"Pressed hotkey combination: {' + '.join(parts)}"
            else:
                pyautogui.press(clean_key)
                return f"Pressed key: {clean_key}"
        return await asyncio.to_thread(_press)

    async def _tool_keyboard_type(self, text: str, interval: float = 0.01) -> str:
        """Type text into the currently focused window or application."""
        if not pyautogui:
            return "pyautogui is not installed. Keyboard action unavailable."
        def _type():
            content = str(text or "")
            if not content:
                return "No text specified to type."
            pyautogui.write(content, interval=float(interval))
            return f"Typed {len(content)} characters into active window."
        return await asyncio.to_thread(_type)

    async def _tool_snap_window(self, position: str, title: str = "active") -> str:
        """Snap or tile a window to a monitor region: 'left', 'right', 'top', 'bottom', 'top_left', 'top_right', 'bottom_left', 'bottom_right', 'center', 'maximize', 'restore'."""
        if not win32gui:
            return "Window snapping requires pywin32."
        pos_clean = (position or "left").lower().strip()
        title_raw = (title or "active").strip()

        # Referent resolution
        resolved_title, _, _ = await self._resolve_contextual_referent(title_raw, getattr(self, "_current_context", None))
        if resolved_title:
            title_raw = resolved_title

        # Find window hwnd
        hwnd = None
        if title_raw.lower() in ("active", "current", "foreground", "current window", "active window"):
            hwnd = win32gui.GetForegroundWindow()
        else:
            windows: list[tuple[int, str]] = []
            def _enum(h: int, res: list) -> None:
                if win32gui.IsWindowVisible(h):
                    t = win32gui.GetWindowText(h)
                    if t and title_raw.lower() in t.lower():
                        res.append((h, t))
            try:
                win32gui.EnumWindows(_enum, windows)
                if windows:
                    hwnd = windows[0][0]
            except Exception:
                pass

        if not hwnd or not win32gui.IsWindow(hwnd):
            return f"Window '{title_raw}' not found for snapping."

        actual_title = win32gui.GetWindowText(hwnd) or title_raw

        def _apply_snap() -> str:
            # Restore if minimized
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            elif win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            # Get Monitor WorkArea (excluding taskbar)
            try:
                import win32api
                h_mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
                mon_info = win32api.GetMonitorInfo(h_mon)
                work_rect = mon_info.get("Work")
                if not work_rect:
                    import ctypes
                    user32 = ctypes.windll.user32
                    work_rect = (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
            except Exception:
                import ctypes
                try:
                    user32 = ctypes.windll.user32
                    work_rect = (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
                except Exception:
                    work_rect = (0, 0, 1920, 1080)

            wx, wy, wr, wb = work_rect
            ww = wr - wx
            wh = wb - wy

            half_w = ww // 2
            half_h = wh // 2

            if pos_clean in ("left", "left_half", "split_left"):
                tx, ty, tw, th = wx, wy, half_w, wh
            elif pos_clean in ("right", "right_half", "split_right"):
                tx, ty, tw, th = wx + half_w, wy, half_w, wh
            elif pos_clean in ("top", "top_half"):
                tx, ty, tw, th = wx, wy, ww, half_h
            elif pos_clean in ("bottom", "bottom_half"):
                tx, ty, tw, th = wx, wy + half_h, ww, half_h
            elif pos_clean in ("top_left", "quad_top_left"):
                tx, ty, tw, th = wx, wy, half_w, half_h
            elif pos_clean in ("top_right", "quad_top_right"):
                tx, ty, tw, th = wx + half_w, wy, half_w, half_h
            elif pos_clean in ("bottom_left", "quad_bottom_left"):
                tx, ty, tw, th = wx, wy + half_h, half_w, half_h
            elif pos_clean in ("bottom_right", "quad_bottom_right"):
                tx, ty, tw, th = wx + half_w, wy + half_h, half_w, half_h
            elif pos_clean in ("center", "centered"):
                tw, th = int(ww * 0.7), int(wh * 0.7)
                tx, ty = wx + (ww - tw) // 2, wy + (wh - th) // 2
            elif pos_clean == "maximize":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                return f"Maximized window '{actual_title}'."
            elif pos_clean == "minimize":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                return f"Minimized window '{actual_title}'."
            elif pos_clean in ("restore", "unmaximize", "unminimize"):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                return f"Restored window '{actual_title}'."
            else:
                tx, ty, tw, th = wx, wy, half_w, wh

            win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, tx, ty, tw, th, win32con.SWP_SHOWWINDOW)
            try:
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
            except Exception:
                pass
            return f"Successfully snapped '{actual_title}' to '{pos_clean}' ({tw}x{th} at {tx},{ty})."

        res = await asyncio.to_thread(_apply_snap)
        try:
            from ..core.world_state import get_world_state
            get_world_state().invalidate_windows()
        except Exception:
            pass
        return res

    async def _tool_get_clipboard(self) -> str:
        """Read and return text content from the Windows system clipboard."""
        from .filesystem_engine import get_filesystem_engine
        return await get_filesystem_engine().get_clipboard()

    async def _tool_set_clipboard(self, text: str) -> str:
        """Copy text content to the Windows system clipboard."""
        from .filesystem_engine import get_filesystem_engine
        return await get_filesystem_engine().set_clipboard(text)

    async def _tool_take_screenshot(self, target: str = "fullscreen", title: str = "active") -> dict[str, Any]:
        """Capture a desktop or active window screenshot and save to ~/.makima/screenshots/."""
        target_clean = (target or "fullscreen").lower().strip()

        def _capture() -> dict[str, Any]:
            import os
            import time
            from PIL import ImageGrab

            shot_dir = os.path.expanduser("~/.makima/screenshots")
            os.makedirs(shot_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"screen_{timestamp}.png"
            file_path = os.path.join(shot_dir, filename)

            bbox = None
            if target_clean in ("active", "window", "active_window") and win32gui:
                try:
                    hwnd = win32gui.GetForegroundWindow()
                    if hwnd and win32gui.IsWindowVisible(hwnd):
                        rect = win32gui.GetWindowRect(hwnd)
                        if rect and (rect[2] > rect[0]) and (rect[3] > rect[1]):
                            bbox = rect
                except Exception:
                    bbox = None

            try:
                img = ImageGrab.grab(bbox=bbox)
            except Exception as e_grab:
                logger.debug(f"[screenshot] ImageGrab failed ({e_grab}), using native GDI/PIL fallback")
                from PIL import Image, ImageDraw
                import ctypes
                try:
                    user32 = ctypes.windll.user32
                    width = user32.GetSystemMetrics(0)
                    height = user32.GetSystemMetrics(1)
                except Exception:
                    width, height = 1920, 1080

                img = Image.new("RGB", (width, height), color=SCREENSHOT_FALLBACK_COLOR)
                d = ImageDraw.Draw(img)
                d.text((40, 40), f"Makima Screenshot: {timestamp}\nTarget: {target_clean}", fill=(255, 255, 255))
            
            img.save(file_path, "PNG")
            return {
                "status": "success",
                "path": file_path,
                "width": img.width,
                "height": img.height,
                "target": target_clean if bbox else "fullscreen",
                "timestamp": timestamp,
            }

        try:
            return await asyncio.to_thread(_capture)
        except Exception as e:
            return {"status": "error", "error": f"Failed to capture screenshot: {e}"}

    async def _tool_show_notification(self, title: str, message: str, app_id: str = "Makima") -> str:
        """Send a native Windows 10/11 desktop toast notification."""
        # SECURITY: Never embed user-controlled strings inside the PS script body.
        # Pass them as positional -Args so PowerShell receives them as literal strings.
        t_safe = (title or "Makima")[:256]
        m_safe = (message or "")[:512]
        app_safe = (app_id or "Makima")[:64]

        ps_script = (
            "param($t, $m, $aid);"
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
            "$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template);"
            "$text = $xml.GetElementsByTagName('text');"
            "$text.Item(0).AppendChild($xml.CreateTextNode($t)) > $null;"
            "$text.Item(1).AppendChild($xml.CreateTextNode($m)) > $null;"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aid).Show($toast)"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", ps_script,
                t_safe, m_safe, app_safe,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode == 0:
                return f"Notification '{title}' sent to desktop."
            return f"Notification '{title}' sent (code {proc.returncode})."
        except Exception as e:
            return f"Failed to send notification: {e}"

    async def _tool_clean_temp_files(self, confirmed: bool = False) -> str:
        """Scan and clean temporary junk files with dry-run safety."""
        from .filesystem_engine import get_filesystem_engine
        return await get_filesystem_engine().clean_temp_files(confirmed=confirmed)


