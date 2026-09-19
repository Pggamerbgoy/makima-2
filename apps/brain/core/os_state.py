"""
Makima v8.0 — OSWorldState
Singleton cache of live OS state: process list, open windows, CPU trend, last actions.
TTL-based refresh prevents redundant OS scans on every tool call.
Relocated to apps/brain/core/os_state.py to decouple from legacy agents directory.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Optional

try:
    import psutil
except ImportError:
    psutil = None

try:
    import win32gui
except ImportError:
    win32gui = None

logger = logging.getLogger("makima.os_state")


class OSWorldState:
    """Singleton. Live cache of OS state — TTL-based refresh, thread-safe."""

    _instance: Optional["OSWorldState"] = None

    PROCESS_TTL: float = 30.0   # re-scan processes after 30s
    WINDOW_TTL:  float = 10.0   # re-scan windows after 10s
    MAX_CPU_SAMPLES: int = 300  # 5 min @ 1s = 300 samples
    MAX_ACTIONS: int = 20       # rolling last-actions log

    def __new__(cls) -> "OSWorldState":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._process_cache: list[dict] = []
        self._process_cache_ts: float = 0.0

        self._window_cache: list[str] = []
        self._window_cache_ts: float = 0.0

        self._window_metadata_cache: list[dict[str, Any]] = []
        self._window_metadata_cache_ts: float = 0.0
        self.WINDOW_METADATA_TTL: float = 1.0  # 1.0s TTL cache for rich window scan

        self._cpu_trend: deque[float] = deque(maxlen=self.MAX_CPU_SAMPLES)
        self._last_actions: deque[str] = deque(maxlen=self.MAX_ACTIONS)
        self._window_transitions: deque[tuple[float, str]] = deque(maxlen=120)
        self._last_fg_window: str = ""
        self._last_fg_time: float = time.monotonic()
        self._clipboard_history: deque[dict[str, Any]] = deque(maxlen=5)
        self._lock = asyncio.Lock()

    # ── Process cache ─────────────────────────────────────────────────────────

    async def get_processes(self, force: bool = False) -> list[dict]:
        """Return cached process list; refresh if TTL expired or force=True."""
        now = time.monotonic()
        if (not force
                and (now - self._process_cache_ts) < self.PROCESS_TTL
                and self._process_cache):
            return self._process_cache

        async with self._lock:
            # Double-check inside lock (another coroutine may have refreshed)
            if (not force
                    and (time.monotonic() - self._process_cache_ts) < self.PROCESS_TTL
                    and self._process_cache):
                return self._process_cache

            if not psutil:
                return []

            def _scan() -> list[dict]:
                procs = []
                for p in psutil.process_iter(
                    ['pid', 'name', 'cpu_percent', 'memory_info', 'status']
                ):
                    try:
                        info = p.info
                        procs.append({
                            "pid":    info['pid'],
                            "name":   info['name'] or "",
                            "cpu":    info['cpu_percent'] or 0.0,
                            "mem_mb": round(
                                info['memory_info'].rss / (1024 * 1024), 2
                            ) if info['memory_info'] else 0.0,
                            "status": info['status'],
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
                return procs

            self._process_cache    = await asyncio.to_thread(_scan)
            self._process_cache_ts = time.monotonic()
            return self._process_cache

    def invalidate_processes(self) -> None:
        """Force next get_processes() to re-scan. Call after kill_process."""
        self._process_cache = []
        self._process_cache_ts = 0.0

    def invalidate_process_cache(self) -> None:
        """Alias for invalidate_processes."""
        self.invalidate_processes()

    # ── Window cache ──────────────────────────────────────────────────────────

    async def get_open_windows(self, force: bool = False) -> list[str]:
        """Return cached visible window titles; refresh if TTL expired."""
        now = time.monotonic()
        if (not force
                and (now - self._window_cache_ts) < self.WINDOW_TTL
                and self._window_cache):
            return self._window_cache

        async with self._lock:
            if not win32gui:
                self._window_cache = []
                return []

            def _enum() -> list[str]:
                titles: list[str] = []
                def cb(hwnd: int, _: Any) -> None:
                    if win32gui.IsWindowVisible(hwnd):
                        t = win32gui.GetWindowText(hwnd)
                        if t.strip():
                            titles.append(t)
                win32gui.EnumWindows(cb, None)
                return titles

            self._window_cache    = await asyncio.to_thread(_enum)
            self._window_cache_ts = time.monotonic()
            return self._window_cache

    def get_open_windows_sync(self, force: bool = False) -> list[str]:
        """Return cached visible window titles synchronously; refresh if TTL expired."""
        now = time.monotonic()
        if (not force
                and (now - self._window_cache_ts) < self.WINDOW_TTL
                and self._window_cache):
            return list(self._window_cache)

        if not win32gui:
            self._window_cache = []
            return []

        try:
            titles: list[str] = []
            def cb(hwnd: int, _: Any) -> None:
                if win32gui.IsWindowVisible(hwnd):
                    t = win32gui.GetWindowText(hwnd)
                    if t.strip():
                        titles.append(t)
            win32gui.EnumWindows(cb, None)
            self._window_cache    = titles
            self._window_cache_ts = time.monotonic()
            return list(self._window_cache)
        except Exception:
            return list(self._window_cache)

    def get_window_count(self) -> int:
        """Return count of open visible windows synchronously."""
        return len(self.get_open_windows_sync())

    async def get_open_window_metadata(
        self, force: bool = False, active_only: bool = True
    ) -> list[dict[str, Any]]:
        """Return cached rich window list (HWND, title, pid, process_name); refresh if TTL expired."""
        now = time.monotonic()
        if (
            not force
            and (now - self._window_metadata_cache_ts) < self.WINDOW_METADATA_TTL
            and self._window_metadata_cache
        ):
            return [dict(w) for w in self._window_metadata_cache]

        async with self._lock:
            if (
                not force
                and (time.monotonic() - self._window_metadata_cache_ts) < self.WINDOW_METADATA_TTL
                and self._window_metadata_cache
            ):
                return [dict(w) for w in self._window_metadata_cache]

            if not win32gui:
                self._window_metadata_cache = []
                return []

            def _scan() -> list[dict[str, Any]]:
                try:
                    import win32con
                    import win32process
                except ImportError:
                    win32con = win32process = None

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
                    logger.debug("[os_state] EnumWindows scan error: %s", e)
                return results

            self._window_metadata_cache = await asyncio.to_thread(_scan)
            self._window_metadata_cache_ts = time.monotonic()
            return [dict(w) for w in self._window_metadata_cache]

    def invalidate_windows(self) -> None:
        """Force next get_open_windows() to re-scan. Call after manage_window or launch_app."""
        self._window_cache = []
        self._window_cache_ts = 0.0
        self._window_metadata_cache = []
        self._window_metadata_cache_ts = 0.0

    def invalidate_window_cache(self) -> None:
        """Alias for invalidate_windows."""
        self.invalidate_windows()

    # ── CPU trend ─────────────────────────────────────────────────────────────

    def record_cpu_sample(self, pct: float) -> None:
        self._cpu_trend.append(pct)

    def cpu_avg(self) -> Optional[float]:
        if not self._cpu_trend:
            return None
        return round(sum(self._cpu_trend) / len(self._cpu_trend), 1)

    # ── Action log ────────────────────────────────────────────────────────────

    def record_action(self, action: str) -> None:
        self._last_actions.append(f"{time.strftime('%H:%M:%S')} {action}")

    # ── Context summary ───────────────────────────────────────────────────────

    async def get_context_summary(self) -> dict[str, Any]:
        """Lightweight OS snapshot for injecting into agent system prompt."""
        procs   = await self.get_processes()
        windows = await self.get_open_windows()

        top_mem = sorted(procs, key=lambda p: p['mem_mb'], reverse=True)[:5]
        top_cpu = sorted(procs, key=lambda p: p['cpu'],    reverse=True)[:5]

        return {
            "process_count": len(procs),
            "top_memory":    [f"{p['name']} ({p['mem_mb']}MB)" for p in top_mem],
            "top_cpu":       [f"{p['name']} ({p['cpu']}%)"    for p in top_cpu],
            "open_windows":  windows[:10],
            "foreground_window": self.get_foreground_window(),
            "battery": self.get_battery_status(),
            "cpu_avg_5min":  self.cpu_avg(),
            "recent_actions": list(self._last_actions)[-5:],
        }

    def get_foreground_window(self) -> str:
        """Return the title of the current foreground window on Windows."""
        if not win32gui:
            return ""
        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd and win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                now = time.monotonic()
                if title and title != self._last_fg_window:
                    self._last_fg_window = title
                    self._last_fg_time = now
                    self._window_transitions.append((now, title))
                return title
        except Exception:
            pass
        return ""

    def get_battery_status(self) -> dict[str, Any]:
        """Query laptop battery status safely using psutil."""
        if not psutil or not hasattr(psutil, "sensors_battery"):
            return {"has_battery": False, "percent": 100.0, "power_plugged": True, "secsleft": None}
        try:
            bat = psutil.sensors_battery()
            if bat is None:
                return {"has_battery": False, "percent": 100.0, "power_plugged": True, "secsleft": None}
            secs = bat.secsleft if getattr(bat, "secsleft", None) != getattr(psutil, "POWER_TIME_UNLIMITED", -1) else None
            return {
                "has_battery": True,
                "percent": round(float(bat.percent), 1),
                "power_plugged": bool(bat.power_plugged),
                "secsleft": secs,
            }
        except Exception as e:
            logger.debug("[os_state] Battery sensor probe error: %s", e)
            return {"has_battery": False, "percent": 100.0, "power_plugged": True, "secsleft": None}

    def get_window_transitions(self, window_s: float = 60.0) -> list[tuple[float, str]]:
        """Return foreground window transition events within window_s seconds."""
        now = time.monotonic()
        return [(t, win) for t, win in self._window_transitions if now - t <= window_s]

    async def get_window_process_map(self) -> dict[str, str]:
        """Return a mapping of visible window title -> process executable name."""
        if not win32gui or not psutil:
            return {}

        def _map() -> dict[str, str]:
            mapping: dict[str, str] = {}
            try:
                import win32process
                def cb(hwnd: int, _: Any) -> None:
                    if win32gui.IsWindowVisible(hwnd):
                        t = win32gui.GetWindowText(hwnd).strip()
                        if t:
                            try:
                                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                                p = psutil.Process(pid)
                                mapping[t] = p.name()
                            except Exception:
                                pass
                win32gui.EnumWindows(cb, None)
            except Exception:
                pass
            return mapping

        return await asyncio.to_thread(_map)

    async def get_active_audio_owner(self) -> Optional[str]:
        """Probe live Windows Core Audio sessions to find the process currently playing audio."""
        def _audio_probe() -> Optional[str]:
            try:
                from pycaw.pycaw import AudioUtilities
                sessions = AudioUtilities.GetAllSessions()
                for session in sessions:
                    if session.State == 1:  # AudioSessionStateActive
                        proc = session.Process
                        if proc:
                            return proc.name()
            except Exception:
                pass

            if psutil:
                for proc in psutil.process_iter(['name']):
                    try:
                        name = (proc.info['name'] or '').lower()
                        if any(m in name for m in ('spotify', 'brave', 'chrome', 'msedge', 'vlc', 'foobar2000')):
                            return proc.info['name']
                    except Exception:
                        pass
            return None

        return await asyncio.to_thread(_audio_probe)

    # ── Master OS Audio Volume ────────────────────────────────────────────────

    async def get_master_volume(self) -> float | None:
        """Read the Windows master volume as a 0-100 percentage, or None."""
        import sys
        if sys.platform != "win32":
            return None
        try:
            from ctypes import POINTER, cast
            import comtypes
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            return round(volume.GetMasterVolumeLevelScalar() * 100.0)
        except Exception:
            return None

    async def set_master_volume(self, pct: float) -> float | None:
        """Set the Windows master volume to `pct` (0-100). Returns actual value read back (0-100)."""
        import sys
        if sys.platform != "win32":
            return None
        pct = max(0.0, min(100.0, float(pct)))
        try:
            from ctypes import POINTER, cast
            import comtypes
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            volume.SetMasterVolumeLevelScalar(pct / 100.0, None)
            return round(volume.GetMasterVolumeLevelScalar() * 100.0)
        except Exception:
            pass

        try:
            import ctypes
            VK_VOLUME_DOWN = 0xAE
            VK_VOLUME_UP = 0xAF
            steps = max(1, int(pct / 2))

            def _apply():
                for _ in range(50):
                    ctypes.windll.user32.keybd_event(VK_VOLUME_DOWN, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(VK_VOLUME_DOWN, 0, 2, 0)
                    time.sleep(0.005)
                for _ in range(steps):
                    ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 2, 0)
                    time.sleep(0.005)

            await asyncio.to_thread(_apply)
            return pct
        except Exception:
            return None

    # ── Clipboard history & proactive state ───────────────────────────────────

    def record_clipboard(self, text: str, source: str = "user") -> None:
        """Record non-empty copied text into rolling history buffer."""
        if not text or not isinstance(text, str):
            return
        clean = text.strip()
        if not clean or clean == "[Clipboard is empty]" or clean.startswith("Failed to") or clean.startswith("Error"):
            return
        if self._clipboard_history and self._clipboard_history[-1].get("text") == clean:
            return
        self._clipboard_history.append({
            "text": clean,
            "timestamp": time.time(),
            "source": source,
            "length": len(clean),
        })

    def get_latest_clipboard_history(self) -> Optional[dict[str, Any]]:
        """Return the most recent non-empty clipboard record from rolling history."""
        return dict(self._clipboard_history[-1]) if self._clipboard_history else None

    def get_clipboard_text(self, fallback_history: bool = True) -> str:
        """
        Safely retrieve current Windows system clipboard text.
        If current clipboard is empty and fallback_history is True,
        returns the most recent non-empty text from rolling history.
        """
        current_text = ""
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    current_text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or ""
                elif win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                    raw = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                    if isinstance(raw, bytes):
                        current_text = raw.decode("utf-8", errors="replace")
                    else:
                        current_text = str(raw or "")
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass

        if not current_text:
            try:
                import pyperclip
                current_text = pyperclip.paste() or ""
            except Exception:
                pass

        if current_text and current_text.strip():
            self.record_clipboard(current_text, source="system")
            return current_text

        if fallback_history and self._clipboard_history:
            return self._clipboard_history[-1]["text"]

        return "[Clipboard is empty]"


def get_os_state() -> OSWorldState:
    """Module-level accessor — always returns the singleton."""
    return OSWorldState()


async def get_master_volume() -> float | None:
    return await get_os_state().get_master_volume()


async def set_master_volume(pct: float) -> float | None:
    return await get_os_state().set_master_volume(pct)
