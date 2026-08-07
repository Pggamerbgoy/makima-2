"""Makima v7.2 — Elite Messaging Agent: Multi-Channel Router & Broadcast Engine.
Orchestrates WhatsApp, Telegram, Discord, Email, and SMS via the Messaging Hub.
Enforces strict approval workflows, contact disambiguation, and privacy controls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

from .base_agent import BaseAgent

logger = logging.getLogger("makima.agents.messaging")

# ──────────────────────────────────────────────────────────────────────────────
# Prompts & Constants
# ──────────────────────────────────────────────────────────────────────────────

MESSAGING_TOOL_PROMPT = """You are Makima's Elite Messaging & Broadcast Agent.
You orchestrate WhatsApp, Telegram, Discord, Email, and SMS via the centralized Messaging Hub.

Analyze the user's intent and respond ONLY with a valid JSON object matching this schema:
{{
    "action": "search_and_draft" | "read" | "broadcast" | "none",
    "platform": "whatsapp" | "telegram" | "discord" | "email" | "sms" | "all" | "",
    "contact_queries": ["contact name 1", "contact name 2"],
    "message_text": "The exact drafted message text",
    "reply": "A brief, conversational status update for the user"
}}

Strict Operational Rules:
1. NEVER execute a send. Always use "search_and_draft" or "broadcast" to prepare a preview.
2. If a contact is ambiguous, include the ambiguous query in "contact_queries" and let the system handle disambiguation.
3. For "broadcast", use when the user wants to send the same message to multiple distinct contacts or groups.
4. For "read", fetch recent messages. Limit "contact_queries" to 1 for read operations.
5. In Privacy Mode, the system will intercept. Do not attempt to bypass.
6. Ensure "message_text" is well-formatted for the target platform (e.g., no markdown for SMS).

User Message: {message}
Context: {context}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Enums & Data Models
# ──────────────────────────────────────────────────────────────────────────────

class Platform(str, Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    EMAIL = "email"
    SMS = "sms"
    UNKNOWN = "unknown"


class Action(str, Enum):
    SEARCH_AND_DRAFT = "search_and_draft"
    READ = "read"
    BROADCAST = "broadcast"
    NONE = "none"


@dataclass
class ContactMatch:
    id: str
    name: str
    platform: Platform
    score: float
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DraftedMessage:
    platform: Platform
    contacts: list[ContactMatch]
    content: str
    formatted_content: str
    requires_approval: bool = True


@dataclass
class BroadcastResult:
    success: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Message Formatter (Platform-Specific Adaptation)
# ──────────────────────────────────────────────────────────────────────────────

class MessageFormatter:
    """Adapts raw text into platform-specific formatting (HTML, Markdown, Plain)."""

    @staticmethod
    def format(text: str, platform: Platform) -> str:
        if not text:
            return ""
        if platform == Platform.TELEGRAM:
            return MessageFormatter._to_telegram_html(text)
        if platform == Platform.DISCORD:
            return MessageFormatter._to_discord_md(text)
        if platform == Platform.EMAIL:
            return MessageFormatter._to_email_html(text)
        if platform == Platform.WHATSAPP:
            return MessageFormatter._to_whatsapp_md(text)
        if platform == Platform.SMS:
            return MessageFormatter._to_plain_text(text)
        return text

    @staticmethod
    def _to_telegram_html(text: str) -> str:
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        escaped = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', escaped)
        escaped = re.sub(r'\*(.*?)\*', r'<b>\1</b>', escaped)
        escaped = re.sub(r'__(.*?)__', r'<i>\1</i>', escaped)
        escaped = re.sub(r'_(.*?)_', r'<i>\1</i>', escaped)
        escaped = re.sub(r'`(.*?)`', r'<code>\1</code>', escaped)
        return escaped

    @staticmethod
    def _to_discord_md(text: str) -> str:
        return text  # Discord natively supports standard Markdown

    @staticmethod
    def _to_email_html(text: str) -> str:
        paragraphs = text.split('\n\n')
        html_parts = [f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip()]
        return "\n".join(html_parts) or "<p></p>"

    @staticmethod
    def _to_whatsapp_md(text: str) -> str:
        text = re.sub(r'\*\*(.*?)\*\*', r'*\1*', text)
        return text

    @staticmethod
    def _to_plain_text(text: str) -> str:
        return re.sub(r'(\*\*|__|\*|_|`|~)', '', text)


# ──────────────────────────────────────────────────────────────────────────────
# Contact Resolver & Broadcast Engine
# ──────────────────────────────────────────────────────────────────────────────

class ContactResolver:
    """Handles fuzzy matching and disambiguation of contacts across platforms."""

    @staticmethod
    async def resolve(
        query: str, 
        platform: Platform, 
        tool_registry: Any
    ) -> list[ContactMatch]:
        if not tool_registry:
            return []
        try:
            res = await tool_registry.execute(
                "messaging_search_contacts", 
                {"query": query, "platform": platform.value}
            )
            if not res or "contacts" not in res:
                return []
            
            matches = []
            for c in res["contacts"]:
                matches.append(ContactMatch(
                    id=c.get("id", ""),
                    name=c.get("name", "Unknown"),
                    platform=platform,
                    score=float(c.get("score", 0.0)),
                    raw_data=c
                ))
            return sorted(matches, key=lambda x: x.score, reverse=True)
        except Exception as e:
            logger.error("[resolver] Contact resolution failed: %s", e)
            return []


class BroadcastEngine:
    """High-performance async fan-out engine for multi-contact messaging."""

    def __init__(self, max_concurrency: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._logger = logging.getLogger("makima.messaging.broadcast")

    async def draft_batch(
        self, 
        queries: list[str], 
        message: str, 
        platform: Platform,
        tool_registry: Any
    ) -> tuple[list[DraftedMessage], list[tuple[str, str]]]:
        drafts: list[DraftedMessage] = []
        unresolved: list[tuple[str, str]] = []
        formatter = MessageFormatter()
        formatted_msg = formatter.format(message, platform)

        async def _resolve_and_draft(q: str) -> None:
            async with self._semaphore:
                contacts = await ContactResolver.resolve(q, platform, tool_registry)
                if not contacts:
                    unresolved.append((q, "no contact found"))
                    return
                best = contacts[0]
                if best.score < 0.5:
                    unresolved.append((q, f"low confidence match '{best.name}' ({best.score:.0%})"))
                    return
                if len(contacts) > 1 and best.score < 0.95:
                    unresolved.append((q, f"ambiguous — {len(contacts)} matches, best '{best.name}' ({best.score:.0%})"))
                    return
                drafts.append(DraftedMessage(
                    platform=platform,
                    contacts=[best],
                    content=message,
                    formatted_content=formatted_msg
                ))

        tasks = [asyncio.create_task(_resolve_and_draft(q)) for q in queries]
        await asyncio.gather(*tasks, return_exceptions=True)
        return drafts, unresolved


# ──────────────────────────────────────────────────────────────────────────────
# Main Agent Implementation
# ──────────────────────────────────────────────────────────────────────────────

class MessagingAgent(BaseAgent):
    AGENT_NAME = "messaging"
    DESCRIPTION = "Enterprise-grade multi-channel messaging, routing, and broadcast engine."
    SYSTEM_PROMPT = """You are Makima's Elite Messaging Agent.
You handle WhatsApp, Telegram, Discord, Email, and SMS.
CRITICAL RULES:
1. NEVER send a message without explicit user approval (approve_message event).
2. If contact name is ambiguous, ALWAYS show disambiguation list.
3. Draft the message first, show preview, wait for approval.
4. In Privacy Mode, refuse all messaging operations."""

    def __init__(
        self,
        ai_handler: Any = None,
        memory: Any = None,
        tool_registry: Any = None,
        ws_broadcast: Any = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        **kwargs: Any
    ) -> None:
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, **kwargs)
        self._broadcast_engine = BroadcastEngine(max_concurrency=5)
        self._last_action_time: float = 0.0
        self._rate_limit_cooldown: float = 1.5  # Seconds between heavy actions

    # ── State & Security Guards ───────────────────────────────────────────────

    def _is_privacy_mode(self) -> bool:
        try:
            cfg = (self.orchestrator.config or {}) if self.orchestrator else {}
            return bool(cfg.get("privacy_mode", False))
        except Exception:
            return False

    def _is_rate_limited(self) -> bool:
        return (time.perf_counter() - self._last_action_time) < self._rate_limit_cooldown

    def _privacy_rejection(self) -> str:
        return "🔒 Messaging is disabled in Privacy Mode. Disable Privacy Mode to read or send messages."

    def _rate_limit_rejection(self) -> str:
        return "⏳ Please wait a moment before sending another messaging command."

    def _parse_platform(self, p_str: str) -> Platform:
        try:
            return Platform(p_str.lower())
        except ValueError:
            return Platform.UNKNOWN

    # ── LLM & Tool Helpers ────────────────────────────────────────────────────

    async def _extract_intent(self, message: str, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        context_str = json.dumps(context.get("messaging_context", {}), default=str)[:500]
        tool_prompt = MESSAGING_TOOL_PROMPT.format(message=message, context=context_str)
        
        decision = await self._llm_call(
            [{"role": "user", "content": tool_prompt}],
            task="general", require_json=True, temperature=0.1, max_tokens=400,
        )
        self._partial_result = decision
        
        parsed = self.ai_handler.try_parse_json(decision)
        if not parsed or not isinstance(parsed, dict):
            logger.warning("[messaging] Failed to parse LLM JSON intent.")
            return None
        return parsed

    async def _safe_tool_call(self, tool_name: str, **kwargs: Any) -> Optional[dict[str, Any]]:
        if not self.tool_registry:
            logger.warning("[messaging] Tool registry unavailable.")
            return None
        try:
            result = await self._use_tool(tool_name, **kwargs)
            if isinstance(result, str):
                parsed = self.ai_handler.try_parse_json(result)
                return parsed if parsed else {"raw": result}
            return result
        except Exception as e:
            logger.error("[messaging] Tool '%s' failed: %s", tool_name, e)
            return {"error": str(e)}

    # ── Action Handlers ───────────────────────────────────────────────────────

    def _build_disambiguation_response(self, matches: list[ContactMatch], platform: Platform) -> str:
        lines = [f"🔍 Multiple contacts found for your query on {platform.value.capitalize()}. Please clarify:"]
        for i, m in enumerate(matches[:5], 1):
            lines.append(f"{i}. **{m.name}** (Match: {m.score:.0%})")
        lines.append("\nReply with the number or the exact name to proceed.")
        return "\n".join(lines)

    async def _handle_search_and_draft(
        self, task_id: str, platform: Platform, queries: list[str], text: str, reply: str
    ) -> str:
        if not queries:
            return "Please specify who you want to message."
            
        matches = await ContactResolver.resolve(queries[0], platform, self.tool_registry)
        if not matches:
            return f"I couldn't find any contacts matching '{queries[0]}' on {platform.value}."
            
        if len(matches) > 1 and matches[0].score < 0.95:
            return self._build_disambiguation_response(matches, platform)
            
        target = matches[0]
        formatted_text = MessageFormatter.format(text, platform)
        
        draft_payload = {
            "task_id": task_id, "platform": platform.value, "contact_id": target.id,
            "contact_name": target.name, "raw_text": text, "formatted_text": formatted_text,
            "status": "pending_approval"
        }
        reg_result = await self._safe_tool_call("messaging_register_draft", **draft_payload)
        if not reg_result or "error" in reg_result:
            detail = (reg_result or {}).get("error", "draft registry unavailable")
            return (f"⚠️ I could not register the draft for {target.name}: {detail}. "
                    f"No message will be sent — please retry or check the messaging service.")
            
        return (
            f"📝 **Draft Message Preview**\n"
            f"**To:** {target.name} ({platform.value.capitalize()})\n"
            f"**Message:**\n{formatted_text}\n\n"
            f"Reply with 'approve' or 'send' to dispatch this message."
        )

    async def _handle_broadcast(
        self, task_id: str, platform: Platform, queries: list[str], text: str, reply: str
    ) -> str:
        if not queries:
            return "Please specify the contacts or groups for the broadcast."
            
        drafts, unresolved = await self._broadcast_engine.draft_batch(queries, text, platform, self.tool_registry)
        if not drafts:
            notes = "; ".join(f"'{q}' — {why}" for q, why in unresolved)
            return (f"I couldn't resolve any of the specified contacts for the broadcast"
                    f" ({notes}). No message will be sent.")

        preview_lines = [f"📢 **Broadcast Preview** ({len(drafts)} recipients)\n"]
        for d in drafts:
            preview_lines.append(f"• **{d.contacts[0].name}** ({d.platform.value})")

        if unresolved:
            preview_lines.append(f"\n⚠️ **Could not include ({len(unresolved)}):**")
            for q, why in unresolved:
                preview_lines.append(f"• {q} — {why}")

        preview_lines.append(f"\n**Message:**\n{drafts[0].formatted_content}\n")
        preview_lines.append("Reply with 'approve broadcast' to send to all listed recipients.")
        
        # Register batch draft
        reg_result = await self._safe_tool_call(
            "messaging_register_broadcast", 
            task_id=task_id, 
            drafts=[{"contact_id": d.contacts[0].id, "name": d.contacts[0].name} for d in drafts],
            message=text, platform=platform.value
        )
        if not reg_result or "error" in reg_result:
            detail = (reg_result or {}).get("error", "broadcast registry unavailable")
            return (f"⚠️ I could not register the broadcast ({len(drafts)} recipients): {detail}. "
                    f"No message will be sent — please retry or check the messaging service.")
        return "\n".join(preview_lines)

    async def _handle_read(self, task_id: str, platform: Platform, queries: list[str]) -> str:
        query = queries[0] if queries else ""
        result = await self._safe_tool_call(
            "messaging_read", contact_query=query, platform=platform.value, limit=10
        )
        if not result or "error" in result:
            return f"Failed to read messages: {result.get('error', 'Unknown error')}"
            
        messages = result.get("messages", [])
        if not messages:
            return f"No recent messages found for '{query}' on {platform.value}."
            
        lines = [f"📬 **Recent Messages ({platform.value.capitalize()})**\n"]
        for msg in messages[:5]:
            sender = msg.get("sender", "Unknown")
            content = msg.get("content", "")[:100]
            lines.append(f"**{sender}:** {content}")
        return "\n".join(lines)

    # ── Main Execution Contract ───────────────────────────────────────────────

    async def execute(self, task_id: str, message: str, context: dict[str, Any],
                      entities: dict[str, Any]) -> str:
        self._reset_state()
        start_time = time.perf_counter()

        if self._is_privacy_mode():
            return self._privacy_rejection()
        if self._is_rate_limited():
            return self._rate_limit_rejection()

        try:
            intent = await self._extract_intent(message, context)
            if not intent:
                messages = self._build_messages(message, context)
                return await self._llm_call(messages, task="general")

            action = intent.get("action", Action.NONE.value)
            platform = self._parse_platform(intent.get("platform", ""))
            
            contact_queries = intent.get("contact_queries", [])
            if isinstance(contact_queries, str):
                contact_queries = [contact_queries]
                
            message_text = intent.get("message_text", "")
            reply = intent.get("reply", "")

            self._last_action_time = time.perf_counter()

            if action == Action.SEARCH_AND_DRAFT.value:
                return await self._handle_search_and_draft(task_id, platform, contact_queries, message_text, reply)
            elif action == Action.BROADCAST.value:
                return await self._handle_broadcast(task_id, platform, contact_queries, message_text, reply)
            elif action == Action.READ.value:
                return await self._handle_read(task_id, platform, contact_queries)
            else:
                return reply or "I cannot process that messaging request."

        except Exception as e:
            logger.exception("[messaging] Critical failure in execute: %s", e)
            self._partial_result = f"Error: {e}"
            return f"I encountered a system error while processing your messaging request: {e}"
        finally:
            elapsed = time.perf_counter() - start_time
            logger.debug("[messaging] Execution completed in %.2fms", elapsed * 1000)
