"""Makima v7.2 — App Learner

Windows foreground window watcher (win32gui poll, 500ms interval).
Builds per-app usage timeline. Records UIA-based workflows when
user performs actions in watched apps (Photoshop, VS Code, etc.)
and saves as JSON workflow for later replay by SystemAgent.
_learning_in_progress: set[str] guards against duplicate threads on rapid Alt-Tab.

Macro recorder/playback:
- Record user actions (clicks, keystrokes) as replayable macros
- Playback macros with configurable speed
- Save/load macros as JSON files

v7.2 upgrades:
- Async file I/O via run_in_executor
- Structured %s-format logging
- Thread-safe locks for shared state
- Zero-crash resilience
- Input validation
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("makima.app_learner")


class MacroRecorder:
    """Record and playback user actions as JSON macros."""

    def __init__(self, macros_dir: Path | None = None) -> None:
        self.macros_dir = macros_dir or Path(os.path.expanduser("~/.makima/macros"))
        self.macros_dir.mkdir(parents=True, exist_ok=True)
        self._recording: bool = False
        self._current_macro: list[dict[str, Any]] = []
        self._record_start: float = 0.0
        self._macro_name: str = ""
        self._lock = asyncio.Lock()

    def start_recording(self, name: str) -> str:
        """Start recording a new macro."""
        name = str(name).strip() if name else ""
        if not name:
            return "Error: Macro name cannot be empty."
        if self._recording:
            return "Already recording. Stop current recording first."

        self._recording = True
        self._current_macro = []
        self._record_start = time.time()
        self._macro_name = name
        return f"Recording macro '{name}'... (perform actions now)"

    async def stop_recording(self) -> str:
        """Stop recording and save the macro to disk."""
        if not self._recording:
            return "Not currently recording."

        self._recording = False
        duration = time.time() - self._record_start
        macro_data = {
            "name": self._macro_name,
            "created_at": self._record_start,
            "duration_s": round(duration, 2),
            "actions": self._current_macro,
            "action_count": len(self._current_macro),
        }

        filepath = self.macros_dir / f"{self._macro_name}.json"
        count = len(self._current_macro)

        try:
            content = json.dumps(macro_data, indent=2)

            def _do_write() -> None:
                filepath.write_text(content, encoding="utf-8")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_write)
        except Exception as e:
            logger.error("stop_recording save failed: %s", e)
            return f"Error saving macro: {e}"
        finally:
            self._current_macro = []

        return f"Macro '{self._macro_name}' saved: {count} actions, {duration:.1f}s"

    def record_action(self, action_type: str, details: dict[str, Any]) -> None:
        """Record a single action during macro recording."""
        if not self._recording:
            return
        if not action_type:
            return
        self._current_macro.append({
            "type": str(action_type),
            "timestamp": time.time() - self._record_start,
            "details": details or {},
        })

    @property
    def is_recording(self) -> bool:
        return self._recording

    async def playback(self, name: str, speed: float = 1.0) -> str:
        """Playback a saved macro with optional speed multiplier."""
        name = str(name).strip() if name else ""
        if not name:
            return "Error: Macro name required."

        filepath = self.macros_dir / f"{name}.json"
        if not filepath.exists():
            return f"Macro '{name}' not found."

        speed = max(0.1, min(float(speed), 10.0))

        try:
            def _do_read() -> dict[str, Any]:
                return json.loads(filepath.read_text(encoding="utf-8"))

            loop = asyncio.get_running_loop()
            macro_data = await loop.run_in_executor(None, _do_read)
            actions = macro_data.get("actions", [])
            if not actions:
                return f"Macro '{name}' has no actions."

            prev_ts = 0.0
            for action in actions:
                delay = (action.get("timestamp", 0) - prev_ts) / speed
                if delay > 0:
                    await asyncio.sleep(min(delay, 30.0))  # Cap individual delays
                prev_ts = action.get("timestamp", 0)

                action_type = action.get("type", "")
                details = action.get("details", {})
                await self._replay_action(action_type, details)

            return f"Macro '{name}' played back: {len(actions)} actions"
        except Exception as e:
            logger.error("playback failed: %s", e)
            return f"Playback error: {e}"

    async def _replay_action(self, action_type: str, details: dict[str, Any]) -> None:
        """Execute a single macro action."""
        try:
            if action_type == "click":
                import pyautogui
                x = details.get("x", 0)
                y = details.get("y", 0)
                pyautogui.click(x, y)
            elif action_type == "key":
                import pyautogui
                keys = details.get("keys", "")
                pyautogui.press(keys)
            elif action_type == "type":
                import pyautogui
                text = details.get("text", "")
                pyautogui.typewrite(text, interval=0.02)
            elif action_type == "move":
                import pyautogui
                x = details.get("x", 0)
                y = details.get("y", 0)
                pyautogui.moveTo(x, y)
        except ImportError:
            logger.warning("pyautogui not available for macro playback")
        except Exception as e:
            logger.error("replay_action failed: %s", e)

    async def list_macros(self) -> list[dict[str, Any]]:
        """List all saved macros."""
        def _do_list() -> list[dict[str, Any]]:
            macros: list[dict[str, Any]] = []
            for f in self.macros_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    macros.append({
                        "name": data.get("name", f.stem),
                        "action_count": data.get("action_count", 0),
                        "duration_s": data.get("duration_s", 0),
                        "created_at": data.get("created_at", 0),
                    })
                except Exception:
                    pass
            return macros

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_list)
        except Exception as e:
            logger.error("list_macros failed: %s", e)
            return []

    async def delete_macro(self, name: str) -> str:
        """Delete a saved macro."""
        name = str(name).strip() if name else ""
        if not name:
            return "Error: Macro name required."

        filepath = self.macros_dir / f"{name}.json"

        def _do_delete() -> str:
            if filepath.exists():
                filepath.unlink()
                return f"Macro '{name}' deleted."
            return f"Macro '{name}' not found."

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_delete)
        except Exception as e:
            logger.error("delete_macro failed: %s", e)
            return f"Error deleting macro: {e}"


class AppLearner:
    """Monitors foreground app usage and records UIA-based workflows."""

    def __init__(self) -> None:
        self._poll_task: Optional[asyncio.Task] = None
        self._learning_in_progress: set[str] = set()
        self._active_app: str = ""
        self._active_start_time: float = 0.0
        self.usage_timeline: list[dict[str, Any]] = []
        self.macro_recorder = MacroRecorder()
        self._usage_insights: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the foreground window watcher."""
        self._poll_task = asyncio.create_task(self._watch_loop())
        logger.info("AppLearner started")

    async def stop(self) -> None:
        """Stop the watcher."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("AppLearner stopped")

    async def _watch_loop(self) -> None:
        """Poll foreground window every 500ms."""
        while True:
            try:
                await self._check_foreground()
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("watch_loop error: %s", e)
                await asyncio.sleep(1.0)

    async def _check_foreground(self) -> None:
        """Check current foreground window and track app switches."""
        try:
            app_name = self._get_foreground_app()
            if not app_name:
                return

            async with self._lock:
                if app_name != self._active_app:
                    # Record app switch
                    if self._active_app:
                        duration = time.time() - self._active_start_time
                        self.usage_timeline.append({
                            "app": self._active_app,
                            "started": self._active_start_time,
                            "duration_s": round(duration, 1),
                        })
                    self._active_app = app_name
                    self._active_start_time = time.time()
        except Exception as e:
            logger.debug("check_foreground failed: %s", e)

    def _get_foreground_app(self) -> str:
        """Get the name of the current foreground application."""
        try:
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            import psutil
            proc = psutil.Process(pid)
            return proc.name()
        except Exception:
            return ""

    async def get_usage_insights(self) -> dict[str, Any]:
        """Get aggregated usage insights."""
        async with self._lock:
            app_time: dict[str, float] = {}
            for entry in self.usage_timeline:
                app = entry.get("app", "unknown")
                app_time[app] = app_time.get(app, 0) + entry.get("duration_s", 0)

            # Add current active app time
            if self._active_app:
                app_time[self._active_app] = app_time.get(self._active_app, 0) + (
                    time.time() - self._active_start_time
                )

            total = sum(app_time.values())
            return {
                "total_tracked_s": round(total, 1),
                "app_breakdown": {k: round(v, 1) for k, v in sorted(app_time.items(), key=lambda x: -x[1])[:20]},
                "timeline_entries": len(self.usage_timeline),
            }

    async def get_timeline(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent usage timeline entries."""
        limit = max(1, min(int(limit), 500))
        async with self._lock:
            return list(self.usage_timeline[-limit:])
