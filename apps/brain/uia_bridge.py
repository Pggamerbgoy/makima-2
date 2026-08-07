"""
Makima v7.1 — UIA Bridge

Wraps pywinauto (backend="uia") for deep UI Automation control beyond
what WindowManager's win32gui/psutil primitives can do: finding controls
by name/control_type/auto_id *inside* a window, reading element info,
setting text, and clicking specific controls (not just whole windows).

Spec (from makima_v7_improved_plan.md, "DESKTOP CONTROL" section):
- pywinauto + uiautomation wrapper
- 3-retry policy per lookup
- 5s timeout per lookup
- Coordinate-click fallback if UIA element resolution or native click fails

Non-negotiable rules:
- Never crash the brain: catch/log errors, degrade gracefully if pywinauto
  or the UIA backend isn't available on this machine.
- Destructive whole-window actions (closing, force-kill) stay in
  WindowManager. This module is find/click/type/read on controls only.
- All pywinauto/COM calls are blocking — every one of them runs inside
  run_in_executor() with an explicit timeout, per the brain's PyO3/gRPC
  concurrency-safety convention.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("makima.uia_bridge")


@dataclass
class ElementInfo:
    """Lightweight, JSON-serializable description of a UIA element."""

    name: str
    control_type: str
    auto_id: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    is_enabled: bool = True
    is_visible: bool = True

    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return ((left + right) // 2, (top + bottom) // 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "control_type": self.control_type,
            "auto_id": self.auto_id,
            "rect": self.rect,
            "enabled": self.is_enabled,
            "visible": self.is_visible,
        }


class UIABridge:
    """
    Deep UI Automation control: find/click/type inside a specific window,
    beyond whole-window operations already handled by WindowManager.
    """

    DEFAULT_TIMEOUT_S = 5.0
    MAX_RETRIES = 3
    RETRY_BACKOFF_S = 0.5

    def __init__(self, ws_broadcast=None):
        self.ws_broadcast = ws_broadcast
        self._available = False
        self._backend_error = ""

        try:
            import pywinauto  # noqa: F401
            from pywinauto import Desktop  # noqa: F401

            self._available = True
        except ImportError as e:
            self._backend_error = str(e)
            logger.warning(
                "pywinauto not available — UIABridge stubbed "
                "(deep UI control disabled, WindowManager still works). %s",
                e,
            )

    def is_available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Find
    # ------------------------------------------------------------------

    async def find_element(
        self,
        window_title: str,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        auto_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> Optional[ElementInfo]:
        """
        Find a control inside a window by best-effort matching on
        name / control_type / auto_id. Retries up to MAX_RETRIES times
        with backoff — UIA trees can be transiently unstable while an
        app is still rendering or mid-animation.
        """
        if not self._available:
            return None

        loop = asyncio.get_event_loop()

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                element = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        self._find_element_sync,
                        window_title,
                        name,
                        control_type,
                        auto_id,
                    ),
                    timeout=timeout,
                )
                if element:
                    return element
            except asyncio.TimeoutError:
                logger.debug(
                    "UIA find_element timed out (attempt %d/%d) window=%r name=%r",
                    attempt,
                    self.MAX_RETRIES,
                    window_title,
                    name,
                )
            except Exception as e:
                logger.debug(
                    "UIA find_element error (attempt %d/%d): %s",
                    attempt,
                    self.MAX_RETRIES,
                    e,
                )

            if attempt < self.MAX_RETRIES:
                await asyncio.sleep(self.RETRY_BACKOFF_S * attempt)

        return None

    def _resolve(self, desktop, window_title: str, name, control_type, auto_id):
        """Shared window+control resolution. Must run in executor thread."""
        win = desktop.window(title_re=f".*{window_title}.*")
        if not win.exists(timeout=1):
            return None, None

        kwargs: dict[str, Any] = {}
        if name:
            kwargs["title_re"] = f".*{name}.*"
        if control_type:
            kwargs["control_type"] = control_type
        if auto_id:
            kwargs["auto_id"] = auto_id

        ctrl = win.child_window(**kwargs) if kwargs else win
        if not ctrl.exists(timeout=1):
            return win, None
        return win, ctrl

    def _find_element_sync(
        self,
        window_title: str,
        name: Optional[str],
        control_type: Optional[str],
        auto_id: Optional[str],
    ) -> Optional[ElementInfo]:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        try:
            _win, ctrl = self._resolve(desktop, window_title, name, control_type, auto_id)
            if ctrl is None:
                return None

            rect = ctrl.rectangle()
            return ElementInfo(
                name=ctrl.window_text() or (name or ""),
                control_type=control_type or ctrl.element_info.control_type,
                auto_id=auto_id or "",
                rect=(rect.left, rect.top, rect.right, rect.bottom),
                is_enabled=ctrl.is_enabled(),
                is_visible=ctrl.is_visible(),
            )
        except Exception as e:
            logger.debug("UIA child_window resolution failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Click
    # ------------------------------------------------------------------

    async def click_element(
        self,
        window_title: str,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        auto_id: Optional[str] = None,
        fallback_coordinates: Optional[tuple[int, int]] = None,
    ) -> str:
        """
        Click a control inside a window. Tries a native UIA click first;
        falls back to a raw coordinate click at the element's center
        (or an explicitly supplied fallback coordinate) if UIA resolution
        or the native click fails for any reason.
        """
        element = await self.find_element(window_title, name, control_type, auto_id)
        label = name or control_type or auto_id or "element"

        if element:
            clicked = await self._click_via_uia(window_title, name, control_type, auto_id)
            if clicked:
                return f"Clicked '{label}' in '{window_title}' via UIA."

            cx, cy = element.center()
            if self._click_via_coordinates(cx, cy):
                return (
                    f"Clicked '{label}' in '{window_title}' "
                    f"via coordinate fallback ({cx}, {cy})."
                )
            return f"Found '{label}' but both UIA click and coordinate fallback failed."

        if fallback_coordinates:
            cx, cy = fallback_coordinates
            if self._click_via_coordinates(cx, cy):
                return f"'{label}' not found via UIA — clicked raw coordinates ({cx}, {cy})."

        return f"Could not find or click '{label}' in '{window_title}'."

    async def _click_via_uia(
        self,
        window_title: str,
        name: Optional[str],
        control_type: Optional[str],
        auto_id: Optional[str],
    ) -> bool:
        if not self._available:
            return False
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._click_via_uia_sync,
                    window_title,
                    name,
                    control_type,
                    auto_id,
                ),
                timeout=self.DEFAULT_TIMEOUT_S,
            )
        except Exception as e:
            logger.debug("UIA click failed: %s", e)
            return False

    def _click_via_uia_sync(
        self,
        window_title: str,
        name: Optional[str],
        control_type: Optional[str],
        auto_id: Optional[str],
    ) -> bool:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        try:
            _win, ctrl = self._resolve(desktop, window_title, name, control_type, auto_id)
            if ctrl is None:
                return False
            ctrl.click_input()
            return True
        except Exception as e:
            logger.debug("UIA click_input failed: %s", e)
            return False

    def _click_via_coordinates(self, x: int, y: int) -> bool:
        """Raw mouse click fallback via win32api — no UIA dependency needed."""
        try:
            import win32api
            import win32con

            win32api.SetCursorPos((x, y))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            return True
        except Exception as e:
            logger.debug("Coordinate click fallback failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Type / set text
    # ------------------------------------------------------------------

    async def set_text(
        self,
        window_title: str,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        auto_id: Optional[str] = None,
        text: str = "",
    ) -> str:
        """Set text into an editable control (textbox, combobox edit, etc.)."""
        label = name or control_type or auto_id or "element"

        if not self._available:
            return "Error: UIA backend not available on this machine."

        loop = asyncio.get_event_loop()
        try:
            ok = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._set_text_sync,
                    window_title,
                    name,
                    control_type,
                    auto_id,
                    text,
                ),
                timeout=self.DEFAULT_TIMEOUT_S,
            )
        except Exception as e:
            logger.debug("UIA set_text failed: %s", e)
            ok = False

        if ok:
            return f"Set text in '{label}'."
        return f"Failed to set text in '{label}'."

    def _set_text_sync(
        self,
        window_title: str,
        name: Optional[str],
        control_type: Optional[str],
        auto_id: Optional[str],
        text: str,
    ) -> bool:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        try:
            _win, ctrl = self._resolve(desktop, window_title, name, control_type, auto_id)
            if ctrl is None:
                return False

            try:
                ctrl.set_text(text)
            except Exception:
                # Not all controls support set_text() — fall back to keystrokes.
                ctrl.set_focus()
                ctrl.type_keys(text, with_spaces=True)
            return True
        except Exception as e:
            logger.debug("UIA set_text/type_keys failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Read tree (for LLM context — "what's in this window?")
    # ------------------------------------------------------------------

    async def get_element_tree(
        self, window_title: str, max_depth: int = 3
    ) -> list[dict[str, Any]]:
        """
        Return a shallow, JSON-serializable tree of a window's UIA controls,
        for feeding into an LLM as context. Depth-capped to avoid flooding
        the token budget on complex apps.
        """
        if not self._available:
            return []

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._get_element_tree_sync, window_title, max_depth
                ),
                timeout=self.DEFAULT_TIMEOUT_S,
            )
        except Exception as e:
            logger.debug("UIA get_element_tree failed: %s", e)
            return []

    def _get_element_tree_sync(
        self, window_title: str, max_depth: int
    ) -> list[dict[str, Any]]:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        win = desktop.window(title_re=f".*{window_title}.*")
        if not win.exists(timeout=1):
            return []

        out: list[dict[str, Any]] = []

        def _walk(ctrl, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                rect = ctrl.rectangle()
                out.append(
                    {
                        "name": ctrl.window_text(),
                        "control_type": ctrl.element_info.control_type,
                        "depth": depth,
                        "rect": (rect.left, rect.top, rect.right, rect.bottom),
                    }
                )
            except Exception:
                pass

            try:
                for child in ctrl.children():
                    _walk(child, depth + 1)
            except Exception:
                pass

        _walk(win, 0)
        return out
