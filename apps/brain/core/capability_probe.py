"""
Makima OS -- CapabilityProbe
Location: apps/brain/core/capability_probe.py

Runtime environment awareness for Makima.

Tools should NOT hardcode execution strategies. Instead, they query CapabilityProbe
which detects what is actually available on this machine and recommends the best approach.

Architecture:
  - Probes run lazily on first query, then cached with a TTL
  - Each probe returns a CapabilityResult describing availability + preferred method
  - Centralized get_capability() function is the only public API tools need

Usage:
    from .core.capability_probe import get_capability

    wa = get_capability("whatsapp")
    if wa.method == "uri_scheme":
        os.startfile("whatsapp://send?phone=...")
    elif wa.method == "web_cdp":
        ...
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("makima.capability_probe")

_PROBE_TTL_S = 600.0  # 10 minutes


@dataclass
class CapabilityResult:
    """Result of a single capability probe."""
    name: str
    available: bool
    method: str
    meta: dict = field(default_factory=dict)
    probed_at: float = field(default_factory=time.monotonic)
    note: str = ""

    def is_fresh(self, ttl: float = _PROBE_TTL_S) -> bool:
        return (time.monotonic() - self.probed_at) < ttl

    def __bool__(self) -> bool:
        return self.available


def _probe_whatsapp() -> CapabilityResult:
    """Detect best WhatsApp strategy: uri_scheme > web_cdp > web_managed > none."""
    if sys.platform == "win32":
        try:
            import winreg
            for hive, path in [
                (winreg.HKEY_CURRENT_USER, r"Software\Classes\whatsapp"),
                (winreg.HKEY_CLASSES_ROOT, r"whatsapp"),
            ]:
                try:
                    with winreg.OpenKey(hive, path) as key:
                        winreg.QueryValueEx(key, "URL Protocol")
                        logger.info("[CapabilityProbe] whatsapp: uri_scheme (WhatsApp Desktop installed)")
                        return CapabilityResult(
                            name="whatsapp", available=True, method="uri_scheme",
                            meta={"registry_key": path},
                            note="WhatsApp Desktop installed -- using native URI scheme (no browser needed)",
                        )
                except (FileNotFoundError, OSError):
                    continue
        except ImportError:
            pass

    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", 9222)) == 0:
                logger.info("[CapabilityProbe] whatsapp: web_cdp (Chrome on :9222)")
                return CapabilityResult(
                    name="whatsapp", available=True, method="web_cdp",
                    meta={"cdp_port": 9222},
                note="Chrome with --remote-debugging-port=9222 running -- attaching to existing session",
            )
    except Exception as chrome_err:
        logger.debug("[capability_probe] Chrome attach probe failed: %s", chrome_err)

    try:
        import playwright  # noqa: F401
        return CapabilityResult(
            name="whatsapp", available=True, method="web_managed",
            note="WhatsApp Desktop not found. Will open browser window -- scan QR code to log in.",
        )
    except ImportError:
        pass

    return CapabilityResult(
        name="whatsapp", available=False, method="none",
        note="WhatsApp Desktop not installed and no browser automation available.",
    )


def _probe_chrome() -> CapabilityResult:
    """Detect Chrome/Edge/Brave installation and CDP availability."""
    exe_candidates: list[tuple[str, str]] = []
    if sys.platform == "win32":
        ev = os.path.expandvars
        for name, path in [
            ("chrome", ev(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe")),
            ("chrome", ev(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe")),
            ("chrome", ev(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
            ("edge",   ev(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe")),
            ("edge",   ev(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe")),
            ("brave",  ev(r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe")),
            ("brave",  ev(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe")),
        ]:
            if os.path.isfile(path):
                exe_candidates.append((name, path))
    elif sys.platform == "darwin":
        for name, path in [
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("edge",   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("brave",  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
        ]:
            if os.path.isfile(path):
                exe_candidates.append((name, path))
    else:
        for name, cmd in [("chrome", "google-chrome"), ("chrome", "chromium-browser")]:
            found = shutil.which(cmd)
            if found:
                exe_candidates.append((name, found))

    import socket
    cdp_alive = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                cdp_alive = s.connect_ex(("127.0.0.1", 9222)) == 0
    except Exception as cdp_err:
        logger.debug("[capability_probe] CDP socket probe failed: %s", cdp_err)

    if exe_candidates:
        best_name, best_path = exe_candidates[0]
        return CapabilityResult(
            name="chrome", available=True,
            method="cdp_attached" if cdp_alive else "launch",
            meta={"exe_path": best_path, "browser": best_name, "cdp_alive": cdp_alive},
            note=f"{best_name} at {best_path}" + (" (CDP on :9222)" if cdp_alive else ""),
        )
    return CapabilityResult(name="chrome", available=False, method="none",
                            note="No Chrome/Edge/Brave found.")


def _probe_telegram() -> CapabilityResult:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("MAKIMA_TELEGRAM_TOKEN") or "").strip()
    if token:
        return CapabilityResult(name="telegram", available=True, method="bot_api",
                                meta={"token_prefix": token[:8] + "..."},
                                note="Telegram Bot API token configured")
    return CapabilityResult(name="telegram", available=False, method="none",
                            note="No TELEGRAM_BOT_TOKEN. Add via Settings -> Connectors -> Telegram.")


def _probe_discord() -> CapabilityResult:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if token:
        return CapabilityResult(name="discord", available=True, method="bot_api",
                                meta={"token_set": True}, note="Discord bot token configured")
    return CapabilityResult(name="discord", available=False, method="none",
                            note="No DISCORD_BOT_TOKEN. Add via Settings -> Connectors.")


def _probe_github() -> CapabilityResult:
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        return CapabilityResult(name="github", available=True, method="rest_api",
                                meta={"token_set": True}, note="GitHub token configured")
    return CapabilityResult(name="github", available=False, method="none",
                            note="No GITHUB_TOKEN. Add via Settings -> Connectors.")


def _probe_playwright() -> CapabilityResult:
    try:
        import playwright  # noqa: F401
        return CapabilityResult(name="playwright", available=True, method="installed",
                                note="Playwright available for browser automation")
    except ImportError:
        return CapabilityResult(name="playwright", available=False, method="none",
                                note="Playwright not installed. Run: pip install playwright && playwright install")


def _probe_spotify() -> CapabilityResult:
    """Detect Spotify Desktop or Web Player capability."""
    if sys.platform == "win32":
        for p in [
            os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe"),
        ]:
            if os.path.isfile(p):
                return CapabilityResult(
                    name="spotify", available=True, method="desktop_app",
                    meta={"exe_path": p}, note="Spotify Desktop app installed",
                )
    chrome_res = _probe_chrome()
    if chrome_res.available:
        return CapabilityResult(
            name="spotify", available=True, method="web_player",
            note="Spotify Web Player supported via browser",
        )
    return CapabilityResult(
        name="spotify", available=False, method="none",
        note="Neither Spotify Desktop nor supported browser available.",
    )


def _probe_email() -> CapabilityResult:
    """Detect Gmail / SMTP email capability."""
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "").strip()
    if pw:
        return CapabilityResult(
            name="email", available=True, method="smtp_api",
            note="Gmail / SMTP password configured",
        )
    return CapabilityResult(
        name="email", available=False, method="none",
        note="No GMAIL_APP_PASSWORD. Add via Settings -> Connectors -> Gmail.",
    )


_PROBE_REGISTRY: dict[str, Any] = {
    "whatsapp":   _probe_whatsapp,
    "chrome":     _probe_chrome,
    "telegram":   _probe_telegram,
    "discord":    _probe_discord,
    "github":     _probe_github,
    "playwright": _probe_playwright,
    "spotify":    _probe_spotify,
    "email":      _probe_email,
}


class CapabilityProbe:
    """Centralized runtime capability detector with TTL-cached results."""

    _instance: CapabilityProbe | None = None

    def __init__(self) -> None:
        self._cache: dict[str, CapabilityResult] = {}

    @classmethod
    def instance(cls) -> CapabilityProbe:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, capability: str, force_refresh: bool = False) -> CapabilityResult:
        cached = self._cache.get(capability)
        if cached and cached.is_fresh() and not force_refresh:
            return cached
        probe_fn = _PROBE_REGISTRY.get(capability)
        if not probe_fn:
            return CapabilityResult(name=capability, available=False, method="none",
                                    note=f"Unknown capability '{capability}' -- no probe registered.")
        try:
            result = probe_fn()
        except Exception as exc:
            logger.warning("[CapabilityProbe] Probe for '%s' raised: %s", capability, exc)
            result = CapabilityResult(name=capability, available=False, method="none",
                                      note=f"Probe error: {exc}")
        self._cache[capability] = result
        return result

    def probe_all(self, force_refresh: bool = False) -> dict[str, CapabilityResult]:
        return {name: self.get(name, force_refresh=force_refresh) for name in _PROBE_REGISTRY}

    def summary(self) -> list[str]:
        lines = []
        for name, result in self.probe_all().items():
            status = "OK" if result.available else "NO"
            lines.append(f"[{status}] {name:12s} [{result.method}] -- {result.note}")
        return lines

    def snapshot_dict(self) -> dict[str, dict]:
        return {
            name: {"available": r.available, "method": r.method, "note": r.note, "meta": r.meta}
            for name, r in self.probe_all().items()
        }

    def invalidate(self, capability: str | None = None) -> None:
        if capability:
            self._cache.pop(capability, None)
        else:
            self._cache.clear()


def get_capability(name: str, force_refresh: bool = False) -> CapabilityResult:
    """
    Primary API for tools to query system capabilities.

    Example:
        from .core.capability_probe import get_capability
        wa = get_capability("whatsapp")
        if wa.method == "uri_scheme":
            # Use WhatsApp Desktop URI -- no browser needed
        elif wa.method == "web_cdp":
            # Attach to user's Chrome
        else:
            # Fall back to managed Playwright Chromium
    """
    return CapabilityProbe.instance().get(name, force_refresh=force_refresh)


def run_startup_probe() -> dict[str, CapabilityResult]:
    """Run all probes at startup and log results. Called from main.py lifespan."""
    probe = CapabilityProbe.instance()
    results = probe.probe_all(force_refresh=True)
    logger.info("[CapabilityProbe] System capability scan:")
    for line in probe.summary():
        logger.info("  %s", line)
    return results
