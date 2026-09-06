"""
Makima OS v9.0 — Canonical World State Service
Facade over domain state providers (OS, Windows, Processes, Filesystem, Audio) with explicit invalidation.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("makima.world_state")


class WorldStateService:
    """
    Unified facade for querying external world state.
    Encapsulates domain providers with explicit event-driven invalidation hooks.
    All consumers query WorldStateService rather than directly invoking OS providers.
    """

    _instance: Optional["WorldStateService"] = None
    _init_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "WorldStateService":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_ts: dict[str, float] = {}
        self.DEFAULT_TTL: float = 1.0  # 1 second TTL

    async def get_snapshot(self, domain: str = "os", force: bool = False) -> dict[str, Any]:
        """Get an immutable snapshot of domain state."""
        now = time.monotonic()
        if not force and domain in self._cache:
            if (now - self._cache_ts.get(domain, 0.0)) < self.DEFAULT_TTL:
                return dict(self._cache[domain])

        snapshot: dict[str, Any] = {}
        if domain in ("os", "system"):
            snapshot = await self._probe_os()
        elif domain in ("process", "processes"):
            snapshot = await self._probe_processes()
        elif domain in ("window", "windows"):
            snapshot = await self._probe_windows()
        elif domain in ("filesystem", "fs"):
            snapshot = await self._probe_filesystem()
        elif domain in ("audio", "media"):
            snapshot = await self._probe_audio()
        else:
            snapshot = {"domain": domain, "status": "unknown"}

        self._cache[domain] = snapshot
        self._cache_ts[domain] = now
        return dict(snapshot)

    def get_foreground_window(self) -> str:
        """Query active foreground window title via OS domain provider."""
        try:
            from ..agents.os_state import get_os_state
            return get_os_state().get_foreground_window()
        except Exception as e:
            logger.warning("[world_state] get_foreground_window failed: %s", e)
            return ""

    async def get_active_audio_owner(self) -> Optional[str]:
        """Query application currently producing audio."""
        try:
            import inspect
            from ..agents.os_state import get_os_state
            res = get_os_state().get_active_audio_owner()
            if inspect.isawaitable(res):
                return await res
            return res
        except Exception as e:
            logger.warning("[world_state] get_active_audio_owner failed: %s", e)
            return None

    def cpu_avg(self) -> Optional[float]:
        """Query CPU usage 5-minute rolling average."""
        try:
            from ..agents.os_state import get_os_state
            return get_os_state().cpu_avg()
        except Exception as e:
            logger.warning("[world_state] cpu_avg failed: %s", e)
            return None

    async def get_processes(self) -> list[dict[str, Any]]:
        """Query active OS processes."""
        try:
            import inspect
            from ..agents.os_state import get_os_state
            res = get_os_state().get_processes()
            if inspect.isawaitable(res):
                return await res
            return res
        except Exception as e:
            logger.warning("[world_state] get_processes failed: %s", e)
            return []

    async def get_open_windows(self) -> list[dict[str, Any]]:
        """Query open visible windows."""
        try:
            import inspect
            from ..agents.os_state import get_os_state
            res = get_os_state().get_open_windows()
            if inspect.isawaitable(res):
                return await res
            return res
        except Exception as e:
            logger.warning("[world_state] get_open_windows failed: %s", e)
            return []

    def get_open_windows_sync(self, force: bool = False) -> list[str]:
        """Query open visible windows synchronously via OS provider or cached snapshot."""
        now = time.monotonic()
        if not force and "window" in self._cache:
            if (now - self._cache_ts.get("window", 0.0)) < self.DEFAULT_TTL:
                cached = self._cache["window"]
                if "open_windows" in cached and isinstance(cached["open_windows"], list):
                    return list(cached["open_windows"])

        try:
            from ..agents.os_state import get_os_state
            os_state = get_os_state()
            wins = []
            if hasattr(os_state, "get_open_windows_sync"):
                wins = list(os_state.get_open_windows_sync(force=force))
            elif getattr(os_state, "_window_cache", None):
                wins = list(os_state._window_cache)
            self._cache["window"] = {"open_windows": wins, "window_count": len(wins)}
            self._cache_ts["window"] = now
            return wins
        except Exception as e:
            logger.warning("[world_state] get_open_windows_sync failed: %s", e)
        return []

    def get_window_count(self) -> int:
        """Query count of open visible windows synchronously."""
        windows = self.get_open_windows_sync()
        return len(windows)

    def get_battery_status(self) -> dict[str, Any]:
        """Query laptop battery status via OS state provider."""
        try:
            from ..agents.os_state import get_os_state
            return get_os_state().get_battery_status()
        except Exception as e:
            logger.debug("[world_state] get_battery_status failed: %s", e)
            return {"has_battery": False, "percent": 100.0, "power_plugged": True}

    def get_mental_state(self) -> dict[str, Any]:
        """Query user mental and cognitive load state."""
        try:
            from ..mental_state import get_mental_state_detector
            return get_mental_state_detector().detect_state().to_dict()
        except Exception as e:
            logger.debug("[world_state] get_mental_state failed: %s", e)
            return {"state": "normal", "confidence": 0.5, "rationale": "Default baseline"}

    def invalidate(self, domain: str) -> None:
        """Explicitly invalidate cached state for a domain after action execution."""
        self._cache.pop(domain, None)
        self._cache_ts.pop(domain, None)

        # Trigger underlying provider invalidations
        try:
            from ..agents.os_state import get_os_state
            os_state = get_os_state()
            if domain in ("process", "processes", "os"):
                if hasattr(os_state, "invalidate_processes"):
                    os_state.invalidate_processes()
                elif hasattr(os_state, "invalidate_process_cache"):
                    os_state.invalidate_process_cache()
            if domain in ("window", "windows", "os"):
                if hasattr(os_state, "invalidate_windows"):
                    os_state.invalidate_windows()
                elif hasattr(os_state, "invalidate_window_cache"):
                    os_state.invalidate_window_cache()
        except Exception as inv_err:
            logger.debug("[world_state] Provider invalidation error: %s", inv_err)

        logger.debug("[world_state] Invalidated domain cache: %s", domain)

    def invalidate_processes(self) -> None:
        self.invalidate("process")

    def invalidate_process_cache(self) -> None:
        self.invalidate("process")

    def invalidate_windows(self) -> None:
        self.invalidate("window")

    def invalidate_window_cache(self) -> None:
        self.invalidate("window")

    # ─────────────────────────────────────────────────────────────────────────
    # Private Domain Probes
    # ─────────────────────────────────────────────────────────────────────────
    async def _probe_os(self) -> dict[str, Any]:
        try:
            from ..agents.os_state import get_os_state
            os_state = get_os_state()
            open_wins = await os_state.get_open_windows() if hasattr(os_state, "get_open_windows") else getattr(os_state, "_window_cache", [])
            return {
                "platform": "win32",
                "foreground_window": os_state.get_foreground_window(),
                "open_window_count": len(open_wins or []),
                "cpu_avg_5m": os_state.cpu_avg(),
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"platform": "win32", "error": str(e), "timestamp": time.time()}

    async def _probe_processes(self) -> dict[str, Any]:
        try:
            from ..agents.os_state import get_os_state
            os_state = get_os_state()
            procs = await os_state.get_processes()
            return {
                "process_count": len(procs),
                "top_processes": procs[:10] if procs else [],
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"process_count": 0, "error": str(e), "timestamp": time.time()}

    async def _probe_windows(self) -> dict[str, Any]:
        try:
            from ..agents.os_state import get_os_state
            os_state = get_os_state()
            windows = await os_state.get_open_windows()
            return {
                "window_count": len(windows),
                "open_windows": windows[:15] if windows else [],
                "foreground_window": os_state.get_foreground_window(),
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"window_count": 0, "error": str(e), "timestamp": time.time()}

    async def _probe_audio(self) -> dict[str, Any]:
        try:
            from ..agents.os_state import get_os_state
            owner = await get_os_state().get_active_audio_owner()
            return {
                "active_audio_owner": owner,
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"active_audio_owner": None, "error": str(e), "timestamp": time.time()}

    async def _probe_filesystem(self) -> dict[str, Any]:
        cwd = os.getcwd()
        return {
            "current_working_directory": cwd,
            "exists": os.path.exists(cwd),
            "timestamp": time.time(),
        }


def get_world_state() -> WorldStateService:
    return WorldStateService()
