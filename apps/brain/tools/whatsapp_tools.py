"""
Makima OS -- WhatsApp Tools (v2 - CapabilityProbe-aware)
Location: apps/brain/tools/whatsapp_tools.py

Strategy auto-detection via CapabilityProbe:
  1. uri_scheme  -- WhatsApp Desktop installed → whatsapp://send URI (best, no browser)
  2. web_cdp     -- User's Chrome on :9222 with WhatsApp Web open → reuse session
  3. web_managed -- Playwright Chromium → QR scan required (fallback only)
  4. none        -- Surface a clear user-facing error with instructions

Makima will automatically choose the right approach for the current machine
without any manual configuration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
from typing import Any, Optional

logger = logging.getLogger("makima.tools.whatsapp")

_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


# ---------------------------------------------------------------------------
# Phone number normalization
# ---------------------------------------------------------------------------

def _normalize_phone(value: str) -> Optional[str]:
    """Return digits-only phone number (no +) if valid, else None."""
    if not value or not isinstance(value, str):
        return None
    stripped = value.strip().replace(" ", "").replace("-", "")
    if _PHONE_RE.match(stripped):
        return stripped.lstrip("+")
    return None


_active_adapter: Optional[WhatsAppAdapter] = None

def set_whatsapp_adapter(adapter: Optional[WhatsAppAdapter]) -> None:
    global _active_adapter
    _active_adapter = adapter

def get_whatsapp_adapter() -> Optional[WhatsAppAdapter]:
    return _active_adapter


class WhatsAppAdapter:
    """Backward-compatible adapter interface for WhatsApp tools."""
    _COMPOSE_SELECTOR = "footer div[contenteditable='true'][role='textbox']"

    def __init__(self, browser_controller: Any = None) -> None:
        self.browser_controller = browser_controller

    @staticmethod
    def normalize_phone(value: str) -> Optional[str]:
        return _normalize_phone(value)

    async def search_contacts(self, query: str) -> list[dict[str, Any]]:
        phone = _normalize_phone(query)
        if phone:
            return [{"name": f"+{phone}", "id": phone, "platform": "whatsapp"}]
        bc = self.browser_controller
        if not bc:
            return []
        qr = await bc.check_element_exists(_QR_CANVAS_SELECTOR, tab=_WA_TAB)
        if "1 match" in str(qr) or "visible" in str(qr):
            return []
        res = await bc.run_js(
            f"Array.from(document.querySelectorAll(\"{_CHAT_LIST_ITEM_TITLE}\"))"
            ".slice(0, 8).map(e => e.getAttribute('title')).filter(Boolean)",
            tab=_WA_TAB,
        )
        if isinstance(res, list):
            return [{"name": n, "id": n, "platform": "whatsapp"} for n in res]
        return []

    async def send_message(self, recipient: str, message: str, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            return {
                "status": "needs_confirmation",
                "requires_confirmation": True,
                "message": (
                    f"Confirmation Required: Send this WhatsApp message?\n"
                    f"  To: {recipient}\n"
                    f"  Message: \"{message}\"\n\n"
                    f"Re-call whatsapp_send_message with confirmed=True to send."
                ),
                "recipient": recipient,
                "text": message,
            }
        bc = self.browser_controller
        if bc:
            phone = _normalize_phone(recipient) or recipient
            url = f"{_WA_BASE_URL}/send?phone={phone}&text={urllib.parse.quote(message)}"
            await bc.navigate(url, tab=_WA_TAB)
            await bc.fill_input(_COMPOSE_SELECTOR, message, tab=_WA_TAB)
            await bc.press_key("Enter", selector=self._COMPOSE_SELECTOR, tab=_WA_TAB)
            return {
                "status": "success",
                "message": f"Message sent via WhatsApp to {recipient}",
                "recipient": recipient,
            }
        return {"status": "error", "message": "No browser controller available"}

    async def read_messages(self, recipient: str, limit: int = 10) -> dict[str, Any]:
        bc = self.browser_controller
        if bc:
            msgs = await bc.run_js("...", tab=_WA_TAB)
            return {
                "status": "success",
                "count": len(msgs) if isinstance(msgs, list) else 0,
                "messages": msgs if isinstance(msgs, list) else [],
                "recipient": recipient,
            }
        return {"status": "error", "message": "No browser controller available"}


# ---------------------------------------------------------------------------
# Strategy: URI Scheme (WhatsApp Desktop)
# ---------------------------------------------------------------------------

async def _send_via_uri_scheme(phone: str, message: str) -> str:
    """
    Open WhatsApp Desktop directly using the whatsapp:// URI scheme.
    This is the preferred method when WhatsApp Desktop is installed --
    no browser, no QR code, uses the user's existing logged-in session.
    """
    encoded_message = urllib.parse.quote(message)
    uri = f"whatsapp://send?phone={phone}&text={encoded_message}"

    try:
        if sys.platform == "win32":
            os.startfile(uri)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", uri], check=True)
        else:
            subprocess.run(["xdg-open", uri], check=True)

        # Give the app a moment to open
        await asyncio.sleep(1.5)
        logger.info("[whatsapp] Message composed in WhatsApp Desktop for +%s", phone)
        return (
            f"WhatsApp Desktop opened with message for +{phone}. "
            f"The compose window should now be open -- press Send in the app to deliver."
        )
    except Exception as exc:
        logger.error("[whatsapp] URI scheme failed: %s", exc)
        return f"WhatsApp Desktop could not be opened: {exc}"


# ---------------------------------------------------------------------------
# Strategy: Web (CDP or Managed Playwright)
# ---------------------------------------------------------------------------

_WA_BASE_URL = "https://web.whatsapp.com"
_WA_TAB = "whatsapp"
_COMPOSE_SELECTOR = "footer div[contenteditable='true'][role='textbox']"
_SEARCH_SELECTOR = "div[contenteditable='true'][data-tab='3']"
_CHAT_LIST_ITEM_TITLE = "div[aria-label='Chat list'] span[title]"
_QR_CANVAS_SELECTOR = "canvas[aria-label], div[data-testid='qrcode']"


async def _get_browser(config: Optional[dict] = None) -> Any:
    try:
        from .browser_tools import get_or_create_browser_controller
        return await get_or_create_browser_controller(config or {})
    except Exception as exc:
        logger.warning("[whatsapp] Could not get browser controller: %s", exc)
        return None


async def _ensure_web_open(bc: Any, method: str) -> Optional[str]:
    """
    Ensure WhatsApp Web is open and logged in.
    Returns None on success, error string on failure.
    """
    # If CDP-attached: check if there's already a WhatsApp tab open (logged-in session)
    if method == "web_cdp":
        existing = bc._pages.get(_WA_TAB)
        if existing and not existing.is_closed():
            current_url = existing.url or ""
            if "web.whatsapp.com" in current_url and "qr" not in current_url.lower():
                logger.info("[whatsapp] Reusing existing WhatsApp Web tab (already logged in)")
                await existing.bring_to_front()
                return None

    # Navigate to WhatsApp Web
    await bc.navigate(_WA_BASE_URL, expected_domain="web.whatsapp.com", tab=_WA_TAB)
    await bc.wait_for(network_idle=True, timeout_ms=8_000, tab=_WA_TAB)

    qr_check = await bc.check_element_exists(_QR_CANVAS_SELECTOR, tab=_WA_TAB)
    if isinstance(qr_check, str):
        if qr_check.startswith("Error") or qr_check.startswith("Check failed"):
            return f"Could not confirm WhatsApp Web login state: {qr_check}"
        if not qr_check.startswith("0 match"):
            return (
                "WhatsApp Web needs a QR scan to log in. "
                "Please scan the QR code in the browser window, then try again."
            )
    return None


async def _send_via_web(phone: Optional[str], contact_name: Optional[str], message: str, config: Optional[dict] = None, method: str = "web_managed") -> str:
    """Send via WhatsApp Web (CDP or managed Chromium)."""
    bc = await _get_browser(config)
    if not bc:
        return "WhatsApp Web: browser controller unavailable."

    if phone:
        # Click-to-chat URL -- no need to log in first for number-based sends
        url = f"{_WA_BASE_URL}/send?phone={phone}&text={urllib.parse.quote(message)}"
        await bc.navigate(url, expected_domain="web.whatsapp.com", tab=_WA_TAB)
        await bc.wait_for(network_idle=True, timeout_ms=10_000, tab=_WA_TAB)
    else:
        err = await _ensure_web_open(bc, method)
        if err:
            return f"WhatsApp Web: {err}"
        # Search by contact name
        await bc.click_element(selector=_SEARCH_SELECTOR, tab=_WA_TAB)
        await bc.fill_input(_SEARCH_SELECTOR, contact_name or "", tab=_WA_TAB)
        await bc.wait_for(selector=_CHAT_LIST_ITEM_TITLE, timeout_ms=5_000, tab=_WA_TAB)
        click_res = await bc.click_element(text=contact_name, tab=_WA_TAB)
        if isinstance(click_res, str) and "Could not find" in click_res:
            return f"Could not find a WhatsApp chat for '{contact_name}'."

    wait_res = await bc.wait_for(selector=_COMPOSE_SELECTOR, timeout_ms=8_000, tab=_WA_TAB)
    if isinstance(wait_res, str) and "[TIMEOUT]" in wait_res:
        return f"WhatsApp chat for '{phone or contact_name}' did not load in time."

    await bc.fill_input(_COMPOSE_SELECTOR, message, tab=_WA_TAB)
    await bc.press_key("Enter", selector=_COMPOSE_SELECTOR, tab=_WA_TAB)
    return f"Message sent via WhatsApp Web to {phone or contact_name}"


async def _read_via_web(phone: Optional[str], contact_name: Optional[str], limit: int, config: Optional[dict] = None, method: str = "web_managed") -> list[dict]:
    bc = await _get_browser(config)
    if not bc:
        return []

    if phone:
        await bc.navigate(f"{_WA_BASE_URL}/send?phone={phone}", expected_domain="web.whatsapp.com", tab=_WA_TAB)
        await bc.wait_for(network_idle=True, timeout_ms=10_000, tab=_WA_TAB)
    else:
        err = await _ensure_web_open(bc, method)
        if err:
            logger.warning("[whatsapp] read_messages: %s", err)
            return []

    try:
        msgs_raw = await bc.run_js(
            "Array.from(document.querySelectorAll(\"div.copyable-text\"))"
            f".slice(-{limit}).map(el => ({{"
            "  meta: el.getAttribute('data-pre-plain-text') || '',"
            "  text: (el.querySelector('span.selectable-text')?.innerText || '').trim(),"
            "}}).filter(m => m.text)",
            tab=_WA_TAB,
        )
        if isinstance(msgs_raw, list):
            return [{"from": m.get("meta", "").strip("[] ") or "Unknown", "text": m.get("text", ""), "timestamp": 0} for m in msgs_raw]
        if isinstance(msgs_raw, str) and msgs_raw.startswith("["):
            import json
            parsed = json.loads(msgs_raw)
            if isinstance(parsed, list):
                return [{"from": m.get("meta", "").strip("[] ") or "Unknown", "text": m.get("text", ""), "timestamp": 0} for m in parsed]
    except Exception as exc:
        logger.error("[whatsapp] read_messages error: %s", exc)
    return []


# ---------------------------------------------------------------------------
# Standalone Tool Handlers (called by tool_registry)
# ---------------------------------------------------------------------------

async def whatsapp_send_message(
    recipient: str,
    message: str,
    confirmed: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Send a WhatsApp message to a phone number or saved contact.

    Makima automatically detects whether WhatsApp Desktop is installed and uses
    the best available method -- no configuration needed.
    """
    from ..core.capability_probe import get_capability

    clean_rec = (recipient or "").strip()
    clean_msg = (message or "").strip()

    if _active_adapter is not None:
        return await _active_adapter.send_message(clean_rec, clean_msg, confirmed=confirmed)

    if not clean_rec:
        return {"status": "error", "message": "No recipient provided (phone number or contact name)."}
    if not clean_msg:
        return {"status": "error", "message": "No message content provided."}

    if not confirmed and not kwargs.get("bypass_confirmation"):
        return {
            "status": "needs_confirmation",
            "message": (
                f"Confirmation Required: Send this WhatsApp message?\n"
                f"  To: {clean_rec}\n"
                f"  Message: \"{clean_msg}\"\n\n"
                f"Re-call whatsapp_send_message with confirmed=True to send."
            ),
            "recipient": clean_rec,
            "text": clean_msg,
            "requires_confirmation": True,
        }

    # ── Auto-detect best strategy ──
    wa_cap = get_capability("whatsapp")
    phone = _normalize_phone(clean_rec)

    if not wa_cap.available:
        return {
            "status": "error",
            "message": (
                "WhatsApp is not available on this system.\n\n"
                "To enable WhatsApp:\n"
                "  Option A (Recommended): Install WhatsApp Desktop from https://www.whatsapp.com/download\n"
                "  Option B: Open Chrome with: chrome.exe --remote-debugging-port=9222\n"
                "             then go to web.whatsapp.com and log in\n"
                "  Option C: Make sure Playwright is installed: pip install playwright && playwright install"
            ),
        }

    logger.info("[whatsapp] Using strategy: %s (reason: %s)", wa_cap.method, wa_cap.note)

    if wa_cap.method == "uri_scheme":
        if not phone:
            # URI scheme requires a phone number -- try to handle contact name gracefully
            return {
                "status": "error",
                "message": (
                    f"WhatsApp Desktop is installed but requires a phone number (not a contact name) "
                    f"for sending. Please provide the phone number with country code, e.g. +919876543210."
                ),
            }
        result = await _send_via_uri_scheme(phone, clean_msg)
        if "could not be opened" in result.lower():
            return {"status": "error", "message": result}
        return {"status": "success", "message": result, "recipient": clean_rec, "method": "uri_scheme"}

    elif wa_cap.method in ("web_cdp", "web_managed"):
        config = kwargs.get("config", {})
        result = await _send_via_web(phone, None if phone else clean_rec, clean_msg, config=config, method=wa_cap.method)
        if "failed" in result.lower() or "error" in result.lower() or "could not" in result.lower():
            return {"status": "error", "message": result}
        return {"status": "success", "message": result, "recipient": clean_rec, "method": wa_cap.method}

    return {"status": "error", "message": f"Unknown WhatsApp method: {wa_cap.method}"}


async def whatsapp_read_messages(
    recipient: str,
    limit: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    """Read recent WhatsApp messages from a contact or phone number."""
    clean_rec = (recipient or "").strip()
    if not clean_rec:
        return {"status": "error", "message": "No recipient provided."}

    if _active_adapter is not None:
        return await _active_adapter.read_messages(clean_rec, limit=limit)

    from ..core.capability_probe import get_capability
    wa_cap = get_capability("whatsapp")
    phone = _normalize_phone(clean_rec)

    if not wa_cap.available:
        return {"status": "error", "message": "WhatsApp not available. " + wa_cap.note}

    if wa_cap.method == "uri_scheme":
        # WhatsApp Desktop URI scheme cannot read messages -- needs web
        return {
            "status": "error",
            "message": (
                "Reading messages requires WhatsApp Web (the desktop app URI scheme only supports sending). "
                "Open Chrome with --remote-debugging-port=9222 and go to web.whatsapp.com to enable message reading."
            ),
        }

    config = kwargs.get("config", {})
    msgs = await _read_via_web(phone, None if phone else clean_rec, limit=limit, config=config, method=wa_cap.method)
    return {
        "status": "success",
        "recipient": clean_rec,
        "count": len(msgs),
        "messages": msgs,
        "method": wa_cap.method,
    }


async def whatsapp_search_contacts(
    query: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Search WhatsApp contacts by name or validate a phone number."""
    clean_q = (query or "").strip()
    if not clean_q:
        return {"status": "error", "message": "No search query provided."}

    if _active_adapter is not None:
        contacts = await _active_adapter.search_contacts(clean_q)
        return {"status": "success", "query": clean_q, "count": len(contacts), "contacts": contacts}

    phone = _normalize_phone(clean_q)
    if phone:
        return {
            "status": "success",
            "query": clean_q,
            "count": 1,
            "contacts": [{"name": f"+{phone}", "id": phone, "platform": "whatsapp"}],
        }

    from ..core.capability_probe import get_capability
    wa_cap = get_capability("whatsapp")
    if not wa_cap.available or wa_cap.method == "uri_scheme":
        return {
            "status": "partial",
            "query": clean_q,
            "count": 0,
            "contacts": [],
            "note": "Contact name search requires WhatsApp Web. Provide a phone number for direct lookup.",
        }

    config = kwargs.get("config", {})
    bc = await _get_browser(config)
    if not bc:
        return {"status": "error", "message": "Browser not available for contact search."}

    err = await _ensure_web_open(bc, wa_cap.method)
    if err:
        return {"status": "error", "message": err}

    try:
        await bc.click_element(selector=_SEARCH_SELECTOR, tab=_WA_TAB)
        await bc.fill_input(_SEARCH_SELECTOR, clean_q, tab=_WA_TAB)
        await bc.wait_for(selector=_CHAT_LIST_ITEM_TITLE, timeout_ms=5_000, tab=_WA_TAB)
        titles_res = await bc.run_js(
            f"Array.from(document.querySelectorAll(\"{_CHAT_LIST_ITEM_TITLE}\"))"
            ".slice(0, 8).map(e => e.getAttribute('title')).filter(Boolean)",
            tab=_WA_TAB,
        )
        contacts = []
        if isinstance(titles_res, list):
            contacts = [{"name": t, "id": t, "platform": "whatsapp"} for t in titles_res]
        elif isinstance(titles_res, str) and titles_res.startswith("["):
            import json
            parsed = json.loads(titles_res)
            if isinstance(parsed, list):
                contacts = [{"name": t, "id": t, "platform": "whatsapp"} for t in parsed]
        return {"status": "success", "query": clean_q, "count": len(contacts), "contacts": contacts}
    except Exception as exc:
        return {"status": "error", "message": f"Contact search failed: {exc}"}


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------

def register_whatsapp_tools(tool_registry: Any, services: Any = None) -> None:
    """Register WhatsApp automation tools into Makima's ToolRegistry."""
    if not tool_registry:
        return

    agent_hints = ["messaging", "commander", "automation", "general"]
    task_tags = ["whatsapp", "messaging", "chat", "send", "contacts", "read"]

    def _safe_reg(name: str, func: Any, desc: str, schema: dict, is_dest: bool = False):
        try:
            if hasattr(tool_registry, "register_tool"):
                tool_registry.register_tool(
                    name=name, description=desc, func=func, schema=schema,
                    category="communication", agent_hints=agent_hints,
                    task_tags=task_tags, priority=1, is_destructive=is_dest,
                )
            elif hasattr(tool_registry, "register"):
                tool_registry.register(name=name, func=func, description=desc, schema=schema, category="communication")
            logger.info("Registered WhatsApp tool: %s", name)
        except Exception as exc:
            logger.warning("Failed to register WhatsApp tool '%s': %s", name, exc)

    _safe_reg(
        name="whatsapp_send_message",
        func=whatsapp_send_message,
        desc=(
            "Send a WhatsApp message to a phone number or saved contact. "
            "Automatically uses WhatsApp Desktop (if installed), Chrome session, or browser automation. "
            "Requires confirmed=True before sending."
        ),
        schema={
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "Phone number with country code (e.g. '+919876543210') or contact name.",
                },
                "message": {"type": "string", "description": "Message text to send."},
                "confirmed": {
                    "type": "boolean",
                    "description": "Must be True to confirm sending.",
                    "default": False,
                },
            },
            "required": ["recipient", "message"],
        },
        is_dest=True,
    )

    _safe_reg(
        name="whatsapp_read_messages",
        func=whatsapp_read_messages,
        desc="Read recent WhatsApp messages from a contact or phone number via WhatsApp Web.",
        schema={
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Contact name or phone number."},
                "limit": {"type": "integer", "description": "Messages to retrieve (default 10).", "default": 10},
            },
            "required": ["recipient"],
        },
        is_dest=False,
    )

    _safe_reg(
        name="whatsapp_search_contacts",
        func=whatsapp_search_contacts,
        desc="Search WhatsApp contacts by name, or validate a phone number as a WhatsApp contact.",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or phone number to search."},
            },
            "required": ["query"],
        },
        is_dest=False,
    )
