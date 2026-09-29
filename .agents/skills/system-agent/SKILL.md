---
name: system-agent
description: OS management, app launching, window focus/snapping/control, clipboard, screenshots, desktop notifications, temp cleanup, desktop file organization, process list/kill, service management, and power actions for Makima's System Agent.
---

# System Agent Skill Guide

## Overview
The **System Agent** (`apps/brain/agents/system_agent.py`) is Makima's OS management, process orchestration, window control, filesystem, and hardware diagnostics engine. It provides robust error handling, Win32 window integration, atomic pre-mutation snapshot rollbacks, and fast non-blocking execution.

## Triggering Rules
Make sure to trigger this skill unconditionally whenever the user requests:
- **Application Lifecycle**: Launching (`launch_app`), discovering (`search_installed_apps`), or terminating (`kill_process`).
- **Window Management & Snapping**: Focus/Switch (`focus`), minimize/hide (`minimize`), maximize (`maximize`), restore (`restore`), close (`close`) via `manage_window`, or split-screen/tile via `snap_window`.
- **System Master Volume**: Setting volume percentage (`set_volume`) or muting.
- **Clipboard Management**: Reading (`get_clipboard`) or copying text (`set_clipboard`).
- **Screenshots & Vision**: Desktop or active window capture (`take_screenshot`).
- **Desktop Notifications**: Native Windows 10/11 toast alerts (`show_notification`).
- **Storage & Temp Cleanup**: Scanning and deleting temp junk files (`clean_temp_files`).
- **Filesystem & Desktop Operations**: Tidying loose files (`organize_desktop`), moving (`move_file`), copying (`copy_file`), renaming (`rename_file`), reading (`read_file`), writing (`write_file`).
- **Hardware & Network Diagnostics**: CPU/RAM/Battery statistics (`get_system_stats`), network ping/traceroute/DNS (`network_diagnostics`), NIC interfaces (`get_network_adapters`).
- **Power & Services**: Sleep, restart, shutdown (`system_power`), Windows service control (`manage_service`).

## Core Execution Protocols & Rules

1. **Direct Tool Calling over Simulation**:
   - Always invoke the appropriate tool immediately via function calling. Never output simulated actions without calling tools.

2. **Window Control & Snapping Discipline**:
   - For focus requests ("saamne lao", "switch to", "bring to front"), call `manage_window(action="focus", title="<name>")`.
   - For split-screen / tiling ("left me lagao", "right side karo", "center me lao"), call `snap_window(position="left"|"right"|"center"|..., title="<name>")`.
   - For hiding requests ("saamne se hatao", "minimize", "hide", "chupao"), call non-destructive `manage_window(action="minimize", title="<name>")`.
   - For active window actions, use `title="active"`.

3. **Desktop & Temp Storage Cleanup Protocol**:
   - `organize_desktop(confirmed=False)` and `clean_temp_files(confirmed=False)` generate dry-run previews.
   - Once user confirms ("confirm", "proceed", "ha kar do"), execute with `confirmed=True`.

4. **Safety & Invariants**:
   - Never terminate critical OS processes (`lsass`, `winlogon`, `wininit`, `csrss`, `smss`, `services`, `svchost`, `explorer`, `dwm`, `audiodg`).
   - File mutations automatically take pre-mutation shadow snapshots (`~/.makima/snapshots`) for transaction rollback.

5. **Conversational Hinglish/English Output**:
   - Reply in crisp, natural conversational style matching user language without raw JSON dumps.

## Available Tool Reference

| Tool | Parameters | Description |
|---|---|---|
| `launch_app` | `app_path: str, arguments: list[str]` | Launch installed apps, shortcuts, and UWP execution aliases. |
| `manage_window` | `action: str, title: str` | Control windows (`focus`, `minimize`, `maximize`, `restore`, `close`). Use `title='active'` for foreground. |
| `snap_window` | `position: str, title: str` | Snap/tile window (`left`, `right`, `top`, `bottom`, `top_left`, `top_right`, `bottom_left`, `bottom_right`, `center`). |
| `set_volume` | `level: int` | Set master system volume (0-100%). |
| `get_clipboard` | *(none)* | Read text currently on the Windows system clipboard. |
| `set_clipboard` | `text: str` | Copy text content to the Windows system clipboard. |
| `take_screenshot` | `target: str, title: str` | Capture fullscreen or active window screenshot to disk. |
| `show_notification` | `title: str, message: str, app_id: str` | Send native Windows desktop toast notification. |
| `clean_temp_files` | `confirmed: bool` | Scan and clean temporary junk files with dry-run preview. |
| `organize_desktop` | `target_folder: str, confirmed: bool` | Organize loose files into categories with dry-run safety. |
| `search_installed_apps` | `query: str, limit: int` | Discover installed software and shortcuts across OS. |
| `get_system_stats` | *(none)* | Retrieve CPU, RAM, Disk, and Battery metrics. |
| `get_process_list` | `filter_name: str, top_n: int` | List top running processes with resource usage. |
| `kill_process` | `process_name: str, pid: int, force: bool` | Terminate application process safely. |
| `set_process_priority` | `pid: int, priority: str` | Change process scheduling priority. |
| `system_power` | `action: str` | System power state (`sleep`, `hibernate`, `restart`, `shutdown`). |
| `network_diagnostics` | `target: str, action: str` | Ping, traceroute, or DNS diagnostics. |
| `get_network_adapters` | *(none)* | Query network adapters and IP configurations. |
| `manage_service` | `service_name: str, action: str` | Start, stop, or restart Windows services. |
| `read_file` | `path: str` | Read text file contents from disk. |
| `write_file` | `path: str, content: str` | Write text file contents to disk. |
| `move_file` | `source_path: str, target_folder_or_path: str` | Move file with pre-mutation snapshot. |
| `copy_file` | `source_path: str, target_folder_or_path: str` | Copy file or directory. |
| `rename_file` | `file_path: str, new_name: str` | Rename file in place with snapshot. |
| `mouse_click` | `x: int, y: int, button: str, double: bool` | Native desktop mouse click. |
| `mouse_draw` | `shape: str, start_x: int, start_y: int, size: int, points: list` | Draw vector shapes or mouse paths. |
