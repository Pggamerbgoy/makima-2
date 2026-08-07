"""
Makima v7.1 — Window Manager

Win32 + psutil integration.
Capabilities: Launch apps, focus windows, close apps, window snapping (Left/Right/Max).
Destructive ops (taskkill / close without save) require confirmation gate.
Used primarily by SystemAgent via ToolRegistry.
"""
from __future__ import annotations
import asyncio
import logging
import subprocess
import os
import shutil
import shlex

logger = logging.getLogger("makima.window_manager")

class WindowManager:
    def __init__(self, ws_broadcast=None):
        self.ws_broadcast = ws_broadcast
        try:
            import win32gui
            import win32process
            import win32con
            import psutil
            self._available = True
        except ImportError:
            self._available = False
            logger.warning("Windows APIs not available. WindowManager stubbed.")

    def _resolve_app_path(self, app_path: str) -> str | None:
        """Resolve short app names (e.g. 'brave', 'chrome', 'notepad') to absolute paths on Windows."""
        if not app_path:
            return None

        # 1. Direct path check (absolute path or relative in cwd)
        if os.path.exists(app_path):
            return os.path.abspath(app_path)

        candidates = [app_path]
        if not app_path.lower().endswith((".exe", ".bat", ".cmd")):
            candidates.append(app_path + ".exe")

        # 2. Check PATH via shutil.which
        for cand in candidates:
            which_path = shutil.which(cand)
            if which_path and os.path.exists(which_path):
                return which_path

        # 3. Check Windows Registry App Paths
        try:
            import winreg
            for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for cand in candidates:
                    try:
                        with winreg.OpenKey(
                            root_key,
                            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{cand}",
                            0,
                            winreg.KEY_READ,
                        ) as key:
                            val, _ = winreg.QueryValueEx(key, "")
                            if val and os.path.exists(val):
                                return val
                    except OSError:
                        continue
        except ImportError:
            pass

        # 4. Check known browser and common application install locations
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))

        known_locations: dict[str, list[str]] = {
            "brave": [
                os.path.join(program_files, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                os.path.join(program_files_x86, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                os.path.join(local_app_data, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            ],
            "chrome": [
                os.path.join(program_files, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(program_files_x86, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(local_app_data, r"Google\Chrome\Application\chrome.exe"),
            ],
            "edge": [
                os.path.join(program_files, r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(program_files_x86, r"Microsoft\Edge\Application\msedge.exe"),
            ],
            "msedge": [
                os.path.join(program_files, r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(program_files_x86, r"Microsoft\Edge\Application\msedge.exe"),
            ],
            "firefox": [
                os.path.join(program_files, r"Mozilla Firefox\firefox.exe"),
                os.path.join(program_files_x86, r"Mozilla Firefox\firefox.exe"),
            ],
            "calc": [
                r"C:\Windows\System32\calc.exe",
            ],
            "calculator": [
                r"C:\Windows\System32\calc.exe",
            ],
            "notepad": [
                r"C:\Windows\System32\notepad.exe",
                r"C:\Windows\notepad.exe",
            ],
            "explorer": [
                r"C:\Windows\explorer.exe",
            ],
            "spotify": [
                os.path.join(os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming")), r"Spotify\Spotify.exe"),
            ],
            "code": [
                os.path.join(local_app_data, r"Programs\Microsoft VS Code\Code.exe"),
                os.path.join(program_files, r"Microsoft VS Code\Code.exe"),
            ],
            "cursor": [
                os.path.join(local_app_data, r"Programs\cursor\Cursor.exe"),
            ],
        }

        key_name = app_path.lower()
        if key_name.endswith(".exe"):
            key_name = key_name[:-4]

        if key_name in known_locations:
            for cand_path in known_locations[key_name]:
                if os.path.exists(cand_path):
                    return cand_path

        return None

    async def launch_app(self, app_path: str, args: str = "") -> str:
        """Launch an executable or application by short name or absolute path."""
        resolved = self._resolve_app_path(app_path)
        if resolved:
            try:
                cmd = [resolved]
                if args:
                    cmd.extend(shlex.split(args, posix=False))
                subprocess.Popen(cmd)
                return f"Successfully launched {os.path.basename(resolved)}"
            except Exception as e:
                return f"Failed to launch app: {e}"

        # Fallback to os.startfile on Windows for registered protocols/aliases
        try:
            if hasattr(os, "startfile"):
                os.startfile(app_path)
                return f"Successfully launched {app_path}"
        except Exception:
            pass

        return f"Error: Cannot find {app_path}"

    async def focus_window(self, partial_title: str) -> str:
        """Find and focus a window by partial title match."""
        if not self._available:
            return "Error: Window APIs not available."
            
        import win32gui
        import win32com.client
        
        hwnd = self._find_window(partial_title)
        if not hwnd:
            return f"Could not find window matching '{partial_title}'"
            
        try:
            import win32con
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            try:
                win32gui.BringWindowToTop(hwnd)
                # Trick to force foreground on Windows
                shell = win32com.client.Dispatch("WScript.Shell")
                shell.SendKeys('%')
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                # Even if SetForegroundWindow raises Windows Foreground Lock exception, BringWindowToTop/ShowWindow succeeded
                pass
            return f"Focused window matching '{partial_title}'"
        except Exception as e:
            return f"Failed to focus window: {e}"

    async def snap_window(self, partial_title: str, position: str) -> str:
        """Snap a window (left, right, max, min)."""
        if not self._available:
            return "Error: Window APIs not available."
            
        import win32gui
        import win32con
        
        hwnd = self._find_window(partial_title)
        if not hwnd:
            return f"Could not find window matching '{partial_title}'"
            
        try:
            if position.lower() == "max":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            elif position.lower() == "min":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            else:
                return "Only max and min snapping currently supported in this version."
            return f"Window snapped to {position}"
        except Exception as e:
            return f"Failed to snap window: {e}"

    async def close_app(self, task_id: str, app_name: str, force: bool = False) -> str:
        """Close an app. If force=True, requires confirmation."""
        if force:
            if self.ws_broadcast:
                from . import ws_protocol
                await self.ws_broadcast(ws_protocol.build_action_confirm(
                    task_id=task_id,
                    action="force_close_app",
                    description=f"Force kill {app_name}? Unsaved work will be lost.",
                    risk_level="high"
                ))
            return "Confirmation required to force close."
            
        # Normal close
        if not self._available:
            return "Error: Window APIs not available."
            
        import psutil
        count = 0
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and app_name.lower() in proc.info['name'].lower():
                try:
                    proc.terminate()
                    count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
        return f"Sent close signal to {count} process(es) matching '{app_name}'."

    def _find_window(self, partial_title: str) -> int:
        import win32gui
        found_hwnd = 0
        
        def callback(hwnd, ctx):
            nonlocal found_hwnd
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if partial_title.lower() in title.lower():
                    found_hwnd = hwnd
            return True
                    
        try:
            win32gui.EnumWindows(callback, 0)
        except Exception:
            pass
        return found_hwnd
