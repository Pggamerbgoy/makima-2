"""
Makima OS v9.0 — World State Service (slim facade).

Keeps ONLY what os_state cannot do alone:
  1. TTL-cached multi-domain snapshots (get_snapshot + probes) — used by
     ExecutionRuntime pre/post-state verification.
  2. Domain fan-out invalidation (invalidate) — used by ExecutionRuntime.
  3. Mental-state bridge (get_mental_state) — used by ProactiveOrchestrator.

Everything else (foreground window, CPU, processes, windows, battery,
clipboard, audio) lives in os_state — call get_os_state() directly.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, ClassVar, Self, cast

from .os_state import get_os_state

logger = logging.getLogger("makima.world_state")


class WorldStateService:
    """
    Slim facade: snapshots + invalidation + mental-state bridge.
    For live OS values (windows, CPU, battery, clipboard), use os_state directly.
    """

    _instance: ClassVar[WorldStateService | None] = None
    _init_lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls) -> Self:
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cast(Self, cls._instance)

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
        if (
            not force
            and domain in self._cache
            and (now - self._cache_ts.get(domain, 0.0)) < self.DEFAULT_TTL
        ):
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

    # ─────────────────────────────────────────────────────────────────────────
    # Private Domain Probes
    # ─────────────────────────────────────────────────────────────────────────
    async def _probe_os(self) -> dict[str, Any]:
        try:
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
