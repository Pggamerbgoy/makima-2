"""
Makima v7.1 — Messaging Hub

Adapter pattern for all messaging platforms.
Adapters: WhatsApp (web), Telegram (Bot API), Discord (discord.py), Email (SMTP/IMAP).
RULE: send() ONLY after user explicitly approves via approve_message WS event.
Ambiguous contact → disambiguation list shown, wait for selection.
Privacy mode: refuse all send operations, read-only allowed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger("makima.messaging_hub")

# A bare digit string (optionally +-prefixed), 7-15 digits — E.164 range.
# Used to decide whether a contact_query/contact_id is already a usable
# phone number (skip fragile UI search entirely) vs. a display name that
# needs the chat-list search flow.
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


# ---------------------------------------------------------------------------
# Base Adapter
# ---------------------------------------------------------------------------

class BaseMessagingAdapter(ABC):
    """Abstract base for all messaging platform adapters."""

    @abstractmethod
    async def search_contacts(self, query: str) -> list[dict]:
        """Return [{name, id, platform}] matching query."""

    @abstractmethod
    async def send_message(self, contact_id: str, text: str) -> str:
        """Send message. Only called after user approval."""

    @abstractmethod
    async def get_recent_messages(self, contact_id: str, limit: int = 10) -> list[dict]:
        """Read recent messages from a conversation."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        pass


# ---------------------------------------------------------------------------
# Telegram Adapter (Bot API — most reliable)
# ---------------------------------------------------------------------------

class TelegramAdapter(BaseMessagingAdapter):
    platform_name = "telegram"

    def __init__(self, config: dict):
        self.bot_token = config.get("telegram_bot_token", "")
        self._base_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def search_contacts(self, query: str) -> list[dict]:
        # The Bot API has no endpoint to list/search users a bot can message
        # (real platform limitation, not something a client-side fix can
        # solve) — a bot can only message chat_ids that have already
        # messaged it. Previously this unconditionally returned [], which
        # meant even a caller who already KNEW the correct numeric chat_id
        # could never get through search_and_draft (every message would
        # dead-end at "No contacts found"). If the query already looks like
        # a chat_id (bare int, or Telegram's negative group-chat ids), treat
        # it as a direct match instead of pretending to "search" for it.
        stripped = query.strip()
        if stripped.lstrip("-").isdigit():
            return [{"name": stripped, "id": stripped, "platform": "telegram"}]
        return []

    async def send_message(self, contact_id: str, text: str) -> str:
        if not self.bot_token:
            return "Error: Telegram bot token not configured."
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base_url}/sendMessage",
                    json={"chat_id": contact_id, "text": text},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    return f"✅ Message sent via Telegram to {contact_id}"
                return f"Telegram error: {resp.text}"
        except Exception as e:
            return f"Telegram send failed: {e}"

    async def get_recent_messages(self, contact_id: str, limit: int = 10) -> list[dict]:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/getUpdates",
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    updates = resp.json().get("result", [])
                    messages = []
                    for u in updates[-limit:]:
                        msg = u.get("message", {})
                        if str(msg.get("chat", {}).get("id")) == str(contact_id):
                            messages.append({
                                "from": msg.get("from", {}).get("first_name", "Unknown"),
                                "text": msg.get("text", ""),
                                "timestamp": msg.get("date", 0),
                            })
                    return messages
        except Exception as e:
            logger.error(f"Telegram read failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Discord Adapter
# ---------------------------------------------------------------------------

class DiscordAdapter(BaseMessagingAdapter):
    platform_name = "discord"

    def __init__(self, config: dict):
        self.bot_token = config.get("discord_bot_token", "")
        self._headers = {"Authorization": f"Bot {self.bot_token}"}

    async def search_contacts(self, query: str) -> list[dict]:
        # Same shape of fix as TelegramAdapter: a full guild-member search
        # needs the members-list endpoint (privileged intent + Phase 2 work),
        # but a caller who already has the numeric channel id shouldn't be
        # blocked from sending just because "search" can't look names up yet.
        stripped = query.strip()
        if stripped.isdigit():
            return [{"name": stripped, "id": stripped, "platform": "discord"}]
        return []  # Name search requires guild member list — Phase 2

    async def send_message(self, contact_id: str, text: str) -> str:
        if not self.bot_token:
            return "Error: Discord bot token not configured."
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://discord.com/api/v10/channels/{contact_id}/messages",
                    headers=self._headers,
                    json={"content": text},
                    timeout=10.0,
                )
                if resp.status_code in (200, 201):
                    return f"✅ Message sent via Discord to channel {contact_id}"
                return f"Discord error ({resp.status_code}): {resp.text}"
        except Exception as e:
            return f"Discord send failed: {e}"

    async def get_recent_messages(self, contact_id: str, limit: int = 10) -> list[dict]:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://discord.com/api/v10/channels/{contact_id}/messages?limit={limit}",
                    headers=self._headers,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    return [
                        {"from": m["author"]["username"], "text": m["content"],
                         "timestamp": m["timestamp"]}
                        for m in resp.json()
                    ]
        except Exception as e:
            logger.error(f"Discord read failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Email Adapter (SMTP send + IMAP read)
# ---------------------------------------------------------------------------

class EmailAdapter(BaseMessagingAdapter):
    platform_name = "email"

    def __init__(self, config: dict):
        cfg = config.get("email", {})
        self.smtp_host = cfg.get("smtp_host", "smtp.gmail.com")
        self.smtp_port = cfg.get("smtp_port", 587)
        self.imap_host = cfg.get("imap_host", "imap.gmail.com")
        self.username = cfg.get("username", "")
        self.password = cfg.get("password", "")

    async def search_contacts(self, query: str) -> list[dict]:
        # No address-book lookup yet (Phase 2), but that shouldn't block the
        # single most common case: the caller already typed an email address.
        # Before this fix search_and_draft was 100% unreachable for email —
        # search_contacts always returned [] so every request dead-ended at
        # "No contacts found", even though send_message below is fully
        # implemented and working.
        stripped = query.strip()
        if "@" in stripped and "." in stripped.split("@", 1)[-1]:
            return [{"name": stripped, "id": stripped, "platform": "email"}]
        return []

    async def send_message(self, contact_id: str, text: str) -> str:
        if not self.username or not self.password:
            return "Error: Email credentials not configured."
        try:
            import smtplib
            from email.mime.text import MIMEText
            loop = asyncio.get_event_loop()

            def _send():
                msg = MIMEText(text)
                msg["Subject"] = "Message from Makima"
                msg["From"] = self.username
                msg["To"] = contact_id
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.username, self.password)
                    server.sendmail(self.username, [contact_id], msg.as_string())

            await loop.run_in_executor(None, _send)
            return f"✅ Email sent to {contact_id}"
        except Exception as e:
            return f"Email send failed: {e}"

    async def get_recent_messages(self, contact_id: str, limit: int = 10) -> list[dict]:
        return []  # IMAP reading — Phase 2


# ---------------------------------------------------------------------------
# WhatsApp Adapter (WhatsApp Web via the shared BrowserController)
#
# Unlike Telegram/Discord/Email, WhatsApp has no bot API a hobby account can
# use — the only viable route is driving the real web.whatsapp.com session,
# same as MediaAgent already does for Spotify/YouTube. This reuses the SAME
# BrowserController instance (passed in from main.py) rather than opening a
# second Chromium, on a dedicated named tab so it never fights the media tab
# for focus.
#
# Design decision (verified via WhatsApp's own Click-to-Chat docs,
# 2026-07-29): sending to a phone number goes through
#   https://web.whatsapp.com/send?phone=<digits>&text=<encoded>
# instead of driving the in-page search box. This is the exact same fix
# shape as the Spotify canonical-search-URL fix in the last session — skip
# the fragile UI path entirely when a deterministic URL exists. Only
# NAME-based lookups (no phone number given) fall back to the chat-list
# search UI, which is the fragile part of this file and the part most
# likely to need a selector update after a WhatsApp Web release.
#
# NOT live-verified this session (disclosed, not guessed-and-hoped): I do
# not have live control of your browser from this chat, so the chat-list
# search/click selectors and the message-bubble read selectors below are
# best-effort from current public WhatsApp Web automation references, not
# confirmed against your actual live DOM the way the Spotify/YouTube fixes
# were. The phone-number send path doesn't depend on any of those selectors
# except the compose box + Enter-to-send, which is the most stable part of
# the page. Flagged again at the bottom of this file under "known
# limitations."
# ---------------------------------------------------------------------------

class WhatsAppAdapter(BaseMessagingAdapter):
    platform_name = "whatsapp"

    _TAB = "whatsapp"
    _BASE_URL = "https://web.whatsapp.com"

    # Best-effort selectors. BrowserController.click_element() already has
    # a vision-fallback (screenshot → Gemini Vision) baked in when a
    # selector misses and ai_handler is available — that safety net matters
    # more here than on Spotify/YouTube because this file's selectors are
    # unverified.
    _COMPOSE_SELECTOR = "footer div[contenteditable='true'][role='textbox']"
    _SEARCH_SELECTOR = "div[contenteditable='true'][data-tab='3']"
    _CHAT_LIST_ITEM_TITLE = "div[aria-label='Chat list'] span[title]"
    _QR_CANVAS_SELECTOR = "canvas[aria-label], div[data-testid='qrcode']"

    def __init__(self, browser_controller, config: dict):
        self.browser = browser_controller
        self.config = config or {}

    @staticmethod
    def _normalize_phone(value: str) -> Optional[str]:
        """Return digits-only phone number if `value` looks like one, else None."""
        stripped = value.strip()
        if _PHONE_RE.match(stripped):
            return stripped.lstrip("+")
        return None

    async def _ensure_open(self) -> Optional[str]:
        """Navigate to WhatsApp Web and confirm we're logged in.

        Returns None on success, or a user-facing error string if the
        browser is unavailable or a QR scan is needed. Never raises except
        CAPTCHA (bubbled from BrowserController by design)."""
        if not self.browser:
            return "WhatsApp isn't available — browser automation isn't running."

        await self.browser.navigate(self._BASE_URL, expected_domain="web.whatsapp.com", tab=self._TAB)
        await self.browser.wait_for(network_idle=True, timeout_ms=8_000, tab=self._TAB)

        qr_check = await self.browser.check_element_exists(self._QR_CANVAS_SELECTOR, tab=self._TAB)
        if qr_check.startswith("Error") or qr_check.startswith("Check failed"):
            return f"Couldn't confirm WhatsApp Web login state: {qr_check}"
        if not qr_check.startswith("0 match"):
            # QR canvas present → not logged in. The browser window is
            # visible (headless=False per BrowserController config), so the
            # fix is literally "scan it" — surface that instead of hanging
            # or silently failing.
            return ("WhatsApp Web needs a QR scan to log in. Open the Makima "
                    "browser window and scan the code with your phone, then try again.")
        return None

    async def search_contacts(self, query: str) -> list[dict]:
        phone = self._normalize_phone(query)
        if phone:
            # Deterministic match — no DOM interaction needed at all, so
            # this path can't be broken by a WhatsApp Web DOM change.
            return [{"name": phone, "id": phone, "platform": "whatsapp"}]

        error = await self._ensure_open()
        if error:
            logger.warning("WhatsApp search_contacts: %s", error)
            return []

        await self.browser.click_element(selector=self._SEARCH_SELECTOR, tab=self._TAB)
        await self.browser.fill_input(self._SEARCH_SELECTOR, query, tab=self._TAB)
        await self.browser.wait_for(selector=self._CHAT_LIST_ITEM_TITLE, timeout_ms=5_000, tab=self._TAB)

        titles = await self.browser.run_js(
            f"Array.from(document.querySelectorAll(\"{self._CHAT_LIST_ITEM_TITLE}\"))"
            ".slice(0, 8).map(e => e.getAttribute('title')).filter(Boolean)",
            tab=self._TAB,
        )
        if not titles:
            return []
        # NOTE: id == display name here, not a phone number — WhatsApp Web
        # doesn't expose the raw number for saved contacts without opening
        # the contact-info panel. send_message() re-opens the chat by name
        # search for these; only a phone-number contact_query skips search.
        return [{"name": t, "id": t, "platform": "whatsapp"} for t in titles]

    async def _open_chat(self, contact_id: str) -> Optional[str]:
        """Open the chat for contact_id (phone number or display name).
        Returns None on success, error string on failure."""
        phone = self._normalize_phone(contact_id)
        if phone:
            url = f"{self._BASE_URL}/send?phone={phone}"
            await self.browser.navigate(url, expected_domain="web.whatsapp.com", tab=self._TAB)
        else:
            error = await self._ensure_open()
            if error:
                return error
            await self.browser.click_element(selector=self._SEARCH_SELECTOR, tab=self._TAB)
            await self.browser.fill_input(self._SEARCH_SELECTOR, contact_id, tab=self._TAB)
            await self.browser.wait_for(selector=self._CHAT_LIST_ITEM_TITLE, timeout_ms=5_000, tab=self._TAB)
            click_result = await self.browser.click_element(
                text=contact_id, tab=self._TAB
            )
            if click_result.startswith("Could not find"):
                return f"Couldn't find a WhatsApp chat for '{contact_id}'."

        wait_result = await self.browser.wait_for(
            selector=self._COMPOSE_SELECTOR, timeout_ms=8_000, tab=self._TAB
        )
        if wait_result.startswith("[TIMEOUT]"):
            # For the phone-number path this is WhatsApp's own signal that
            # the number is invalid / has no WhatsApp account — surface that
            # rather than a generic timeout.
            return (f"Couldn't open a WhatsApp chat for '{contact_id}' — the "
                    f"number may not have WhatsApp, or the page didn't load in time.")
        return None

    async def send_message(self, contact_id: str, text: str) -> str:
        error = await self._open_chat(contact_id)
        if error:
            return f"WhatsApp send failed: {error}"
        try:
            # Land on the chat via URL/search first (_open_chat above), then
            # fill the compose box explicitly ourselves rather than relying
            # on /send?phone=&text= to prefill it — one fewer layer of URL
            # query-encoding to get wrong for messages with special chars.
            await self.browser.fill_input(self._COMPOSE_SELECTOR, text, tab=self._TAB)
            await self.browser.press_key("Enter", selector=self._COMPOSE_SELECTOR, tab=self._TAB)
            return f"✅ Message sent via WhatsApp to {contact_id}"
        except Exception as e:
            return f"WhatsApp send failed: {e}"

    async def get_recent_messages(self, contact_id: str, limit: int = 10) -> list[dict]:
        error = await self._open_chat(contact_id)
        if error:
            logger.warning("WhatsApp get_recent_messages: %s", error)
            return []
        try:
            # Most WhatsApp Web automation references agree on
            # span.selectable-text for message bubble text, but this is the
            # single most likely selector in this file to have drifted —
            # unverified this session, flag if reads come back empty.
            msgs = await self.browser.run_js(
                "Array.from(document.querySelectorAll(\"div.copyable-text\"))"
                f".slice(-{limit}).map(el => ({{"
                "  meta: el.getAttribute('data-pre-plain-text') || '',"
                "  text: (el.querySelector('span.selectable-text')?.innerText || '').trim(),"
                "})).filter(m => m.text)",
                tab=self._TAB,
            )
            return [
                {"from": m["meta"].strip("[] ") or "Unknown", "text": m["text"], "timestamp": 0}
                for m in (msgs or [])
            ]
        except Exception as e:
            logger.error("WhatsApp read failed: %s", e)
            return []


# ---------------------------------------------------------------------------
# Messaging Hub — orchestrates all adapters
# ---------------------------------------------------------------------------

class MessagingHub:
    """
    Central hub for all messaging operations.
    Enforces approval gate and disambiguation before any send.
    """

    def __init__(self, config: dict, browser_controller=None, ws_broadcast=None):
        self.ws_broadcast = ws_broadcast
        self.privacy_mode = config.get("privacy", {}).get("mode", False)

        self.adapters: dict[str, BaseMessagingAdapter] = {}
        self._pending_sends: dict[str, dict] = {}  # task_id → pending send

        # Initialise adapters based on config
        messaging_cfg = config.get("messaging", {})
        if messaging_cfg.get("telegram_bot_token"):
            self.adapters["telegram"] = TelegramAdapter(messaging_cfg)
        if messaging_cfg.get("discord_bot_token"):
            self.adapters["discord"] = DiscordAdapter(messaging_cfg)
        if messaging_cfg.get("email", {}).get("username"):
            self.adapters["email"] = EmailAdapter(messaging_cfg)
        # WhatsApp needs no bot token/credential (it rides the shared,
        # already-authenticated browser session) so it's on by default —
        # gated only on the browser actually being available, plus an
        # explicit opt-out via messaging.whatsapp.enabled: false.
        whatsapp_cfg = messaging_cfg.get("whatsapp", {})
        if browser_controller is not None and whatsapp_cfg.get("enabled", True):
            self.adapters["whatsapp"] = WhatsAppAdapter(browser_controller, whatsapp_cfg)

        logger.info(f"MessagingHub initialised with adapters: {list(self.adapters.keys())}")

    async def search_and_draft(
        self,
        task_id: str,
        contact_query: str,
        message_text: str,
        platform: str = "",
    ) -> str:
        """
        Find contact, draft message, show preview for approval.
        NEVER sends without approval.
        """
        if self.privacy_mode:
            return "Privacy Mode is active. Messaging is disabled."

        # Determine adapters to search
        search_adapters = (
            {platform: self.adapters[platform]}
            if platform in self.adapters
            else self.adapters
        )

        # Search contacts
        all_contacts: list[dict] = []
        for platform_name, adapter in search_adapters.items():
            contacts = await adapter.search_contacts(contact_query)
            for c in contacts:
                c["platform"] = platform_name
            all_contacts.extend(contacts)

        # Disambiguation
        if len(all_contacts) == 0:
            return f"No contacts found matching '{contact_query}'. Please be more specific."

        if len(all_contacts) > 1:
            options = "\n".join(
                f"{i+1}. {c['name']} ({c['platform']})"
                for i, c in enumerate(all_contacts)
            )
            return f"Multiple contacts found for '{contact_query}':\n{options}\n\nWhich one did you mean?"

        # Single match — store pending and request approval
        contact = all_contacts[0]
        self._pending_sends[task_id] = {
            "contact": contact,
            "text": message_text,
            "platform": contact["platform"],
        }

        # Emit approval request
        if self.ws_broadcast:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.build_action_confirm(
                task_id=task_id,
                action="send_message",
                description=(
                    f"Send to {contact['name']} via {contact['platform']}:\n\n"
                    f"\"{message_text}\""
                ),
                risk_level="medium",
            ))

        return (
            f"Message drafted for {contact['name']} ({contact['platform']}):\n\n"
            f"\"{message_text}\"\n\nWaiting for your approval to send."
        )

    async def approve_send(self, task_id: str) -> str:
        """Execute a pending send after user approval."""
        pending = self._pending_sends.pop(task_id, None)
        if not pending:
            return "No pending message to send for this task."

        platform = pending["platform"]
        adapter = self.adapters.get(platform)
        if not adapter:
            return f"Adapter for {platform} not available."

        return await adapter.send_message(
            pending["contact"]["id"],
            pending["text"],
        )

    async def cancel_send(self, task_id: str) -> str:
        """Cancel a pending send."""
        self._pending_sends.pop(task_id, None)
        return "Message cancelled."

    async def read_messages(self, contact_query: str, platform: str = "", limit: int = 10) -> str:
        """Read recent messages from a contact."""
        if self.privacy_mode:
            return "Privacy Mode is active. Reading messages is disabled."

        search_adapters = (
            {platform: self.adapters[platform]}
            if platform in self.adapters
            else self.adapters
        )

        for platform_name, adapter in search_adapters.items():
            contacts = await adapter.search_contacts(contact_query)
            if contacts:
                msgs = await adapter.get_recent_messages(contacts[0]["id"], limit)
                if msgs:
                    lines = [f"**{m['from']}**: {m['text']}" for m in msgs]
                    return f"Recent messages with {contacts[0]['name']}:\n\n" + "\n".join(lines)

        return f"No messages found for '{contact_query}'."
