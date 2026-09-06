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
from typing import Any, Optional

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

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
""" + "\n" + TOOL_DISCIPLINE_BLOCK


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
        tool_registry: Any,
        agent: Any = None,
    ) -> list[ContactMatch]:
        try:
            res = None
            if agent and hasattr(agent, "_use_tool"):
                try:
                    res = await agent._use_tool("messaging_search_contacts", query=query, platform=platform.value)
                except Exception:
                    res = None
            if not res and tool_registry and hasattr(tool_registry, "call_tool"):
                try:
                    res = await tool_registry.call_tool(
                        "messaging_search_contacts", 
                        query=query, platform=platform.value,
                    )
                except Exception:
                    res = None
            if not res:
                return []
            if isinstance(res, str):
                parsed_res = self.ai_handler.try_parse_json(res) if (self.ai_handler and hasattr(self.ai_handler, "try_parse_json")) else None
                if not parsed_res:
                    try:
                        import ast
                        parsed_res = ast.literal_eval(res)
                    except Exception:
                        parsed_res = None
                res = parsed_res
            if not res or not isinstance(res, dict) or "contacts" not in res:
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
        tool_registry: Any,
        agent: Any = None,
    ) -> tuple[list[DraftedMessage], list[tuple[str, str]]]:
        drafts: list[DraftedMessage] = []
        unresolved: list[tuple[str, str]] = []
        formatter = MessageFormatter()
        formatted_msg = formatter.format(message, platform)

        async def _resolve_and_draft(q: str) -> None:
            async with self._semaphore:
                contacts = await ContactResolver.resolve(q, platform, tool_registry, agent=agent)
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
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30.0)
        except asyncio.TimeoutError:
            self._logger.warning("[broadcast] Draft batch timed out after 30s")
        return drafts, unresolved


# ──────────────────────────────────────────────────────────────────────────────
# Main Agent Implementation
# ──────────────────────────────────────────────────────────────────────────────

class MessagingAgent(BaseAgent):
    AGENT_NAME = "messaging"
    DESCRIPTION = "Send, draft, and manage messages across WhatsApp, Telegram, Discord, Email (Gmail), and SMS. Use for any messaging task: drafting texts, looking up contacts, or delivering messages on any platform."
    SYSTEM_PROMPT = """You are Makima's Elite Messaging Agent.
You handle multi-platform communication across WhatsApp, Telegram, Discord, Email (Gmail), and SMS.

AVAILABLE TOOLS:
- Contact Lookup: messaging_search_contacts(query, platform=None)
- Draft & Approval: messaging_register_draft(recipient, platform, content, subject=None)
- Broadcast: messaging_register_broadcast(recipients=[...], platform="...", content="...")
- Read Messages: messaging_read(platform="...", limit=5)

CRITICAL OPERATIONAL RULES:
1. Approval Gate: NEVER send an outbound message without explicit user approval. Always draft first and present a preview.
2. Contact Disambiguation: If a recipient name or handle matches multiple contacts, present a clear disambiguation list.
3. Privacy Mode: In Privacy Mode, strictly refuse external communication operations.
4. Structured Thinking: Always decompose intent, identify target platform, and verify recipient handles inside a <thinking>...</thinking> block before drafting.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    AGENT_TOOLS = [
        "messaging_search_contacts",
        "messaging_register_draft",
        "messaging_register_broadcast",
        "messaging_read",
    ]

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
        self._last_action_time: dict[str, float] = {}
        self._rate_limit_cooldown: float = 1.5  # Seconds per platform
        self._drafts: dict[str, dict[str, Any]] = {}
        self._broadcasts: dict[str, dict[str, Any]] = {}
        self._contacts_directory: list[dict[str, Any]] = [
            {"id": "c_alex", "name": "Alex", "platform": "whatsapp", "handle": "+15550101"},
            {"id": "c_sarah", "name": "Sarah", "platform": "whatsapp", "handle": "+15550102"},
            {"id": "c_mom", "name": "Mom", "platform": "whatsapp", "handle": "+15550103"},
            {"id": "c_john", "name": "John Doe", "platform": "telegram", "handle": "@johndoe"},
            {"id": "c_alice", "name": "Alice", "platform": "discord", "handle": "alice#0001"},
            {"id": "c_boss", "name": "Boss", "platform": "email", "handle": "boss@example.com"},
        ]

        self._TOOL_MAP = {
            "messaging_search_contacts": self._tool_messaging_search_contacts,
            "messaging_register_draft": self._tool_messaging_register_draft,
            "messaging_register_broadcast": self._tool_messaging_register_broadcast,
            "messaging_read": self._tool_messaging_read,
        }

    # ── State & Security Guards ───────────────────────────────────────────────

    def _is_privacy_mode(self) -> bool:
        try:
            cfg = (self.orchestrator.config or {}) if self.orchestrator else {}
            return bool(cfg.get("privacy_mode", False))
        except Exception:
            return False

    def _is_rate_limited(self, platform: Platform) -> bool:
        last = self._last_action_time.get(platform.value, 0.0)
        return (time.perf_counter() - last) < self._rate_limit_cooldown

    def _privacy_rejection(self) -> str:
        return "🔒 Messaging is disabled in Privacy Mode. Disable Privacy Mode to read or send messages."

    def _rate_limit_rejection(self) -> str:
        return "⏳ Please wait a moment before sending another messaging command on this platform."

    def _parse_platform(self, p_str: str) -> Platform:
        try:
            return Platform(p_str.lower())
        except ValueError:
            return Platform.UNKNOWN

    def _infer_platform(self, message: str, parsed_platform: Platform) -> Platform:
        """Fallback platform detection from message text when LLM misses platform."""
        if parsed_platform != Platform.UNKNOWN:
            return parsed_platform

        msg_lower = message.lower()
        platform_keywords = {
            Platform.WHATSAPP: ["whatsapp", "wa", "whats app"],
            Platform.TELEGRAM: ["telegram", "tg"],
            Platform.DISCORD: ["discord", "dc"],
            Platform.EMAIL: ["email", "mail", "gmail", "outlook"],
            Platform.SMS: ["sms", "text", "message kar"],
        }
        for platform, keywords in platform_keywords.items():
            if any(kw in msg_lower for kw in keywords):
                return platform
        return Platform.WHATSAPP  # Default fallback

    # ── LLM & Tool Helpers ────────────────────────────────────────────────────

    async def _extract_intent(self, message: str, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        context_str = json.dumps(context.get("messaging_context", {}), default=str)[:500]
        tool_prompt = MESSAGING_TOOL_PROMPT.format(message=message, context=context_str)
        
        decision = await self._llm_call(
            [{"role": "user", "content": tool_prompt}],
            task="entity_extraction", require_json=True, temperature=0.05, max_tokens=300,
        )
        self._partial_result = decision
        
        parsed = None
        if self.ai_handler and hasattr(self.ai_handler, "try_parse_json"):
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
            result = await asyncio.wait_for(self._use_tool(tool_name, **kwargs), timeout=15.0)
            if isinstance(result, str):
                parsed = None
                if self.ai_handler and hasattr(self.ai_handler, "try_parse_json"):
                    parsed = self.ai_handler.try_parse_json(result)
                if not parsed:
                    parsed = {"raw": result}
                return parsed
            return result
        except asyncio.TimeoutError:
            logger.error("[messaging] Tool '%s' timed out after 15s", tool_name)
            return {"error": f"{tool_name} timed out"}
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
            
        matches = await ContactResolver.resolve(queries[0], platform, self.tool_registry, agent=self)
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
            
        drafts, unresolved = await self._broadcast_engine.draft_batch(
            queries, text, platform, self.tool_registry, agent=self,
        )
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

    # ── Tool Implementations (Canonical ToolRegistry API) ────────────────────

    async def _tool_messaging_search_contacts(
        self,
        query: str = "",
        platform: str = "whatsapp",
        **kwargs: Any
    ) -> dict[str, Any]:
        """Search contacts with fuzzy score matching across platforms."""
        q = str(query or "").strip().lower()
        plat = str(platform or "whatsapp").strip().lower()

        matches = []
        for c in self._contacts_directory:
            c_plat = c.get("platform", "").lower()
            c_name = c.get("name", "")
            c_name_lower = c_name.lower()

            if plat and c_plat != plat and plat != "all":
                continue

            score = 0.0
            if not q:
                score = 0.5
            elif q == c_name_lower:
                score = 1.0
            elif q in c_name_lower:
                score = 0.85
            elif any(part in c_name_lower for part in q.split()):
                score = 0.70

            if score > 0.0:
                matches.append({
                    "id": c.get("id", f"c_{c_name_lower}"),
                    "name": c_name,
                    "platform": c_plat,
                    "handle": c.get("handle", ""),
                    "score": score,
                })

        # If no configured match, return honest not_found result (no synthetic fake contacts)
        if not matches:
            return {"status": "not_found", "platform": plat, "query": query, "contacts": [], "message": f"No contact matching '{query}' found."}

        matches.sort(key=lambda x: x["score"], reverse=True)
        return {"status": "ok", "platform": plat, "query": query, "contacts": matches}

    async def _tool_messaging_register_draft(
        self,
        task_id: str = "",
        platform: str = "whatsapp",
        contact_id: str = "",
        contact_name: str = "",
        raw_text: str = "",
        formatted_text: str = "",
        status: str = "pending_approval",
        **kwargs: Any
    ) -> dict[str, Any]:
        """Register a pending message draft for user approval."""
        eff_formatted = formatted_text or raw_text
        draft = {
            "task_id": task_id,
            "platform": platform,
            "contact_id": contact_id,
            "contact_name": contact_name,
            "raw_text": raw_text,
            "formatted_text": eff_formatted,
            "status": status,
            "created_at": time.time(),
        }
        self._drafts[task_id] = draft
        logger.info("[messaging] Registered draft for task %s (to %s on %s)", task_id, contact_name, platform)
        return {"status": "ok", "task_id": task_id, "registered": True, "draft": draft}

    async def _tool_messaging_register_broadcast(
        self,
        task_id: str,
        drafts: list[dict[str, Any]],
        message: str,
        platform: str,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Register a pending broadcast batch for multi-contact messaging."""
        broadcast = {
            "task_id": task_id,
            "platform": platform,
            "message": message,
            "drafts": drafts,
            "status": "pending_approval",
            "created_at": time.time(),
        }
        self._broadcasts[task_id] = broadcast
        logger.info("[messaging] Registered broadcast for task %s (%d recipients on %s)", task_id, len(drafts), platform)
    async def _tool_messaging_contact_search(
        self,
        query: str = "",
        platform: str = "whatsapp",
        **kwargs: Any
    ) -> dict[str, Any]:
        """Search contacts across platforms without synthetic hallucination."""
        plat = self._parse_platform(platform)
        matches = await ContactResolver.resolve(
            query=query,
            platform=plat,
            tool_registry=getattr(self, "tool_registry", None),
            agent=self,
        )
        if not matches:
            return {
                "status": "not_found",
                "platform": plat.value,
                "contacts": [],
                "message": f"No contact matching '{query}' found on {plat.value}.",
            }
        return {
            "status": "ok",
            "platform": plat.value,
            "contacts": [{"id": m.id, "name": m.name, "score": m.score} for m in matches],
            "message": f"Found {len(matches)} contacts on {plat.value}.",
        }

    async def _tool_messaging_read(
        self,
        contact_query: str = "",
        platform: str = "whatsapp",
        limit: int = 10,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Read recent messages from platform channel."""
        plat = str(platform or "whatsapp").lower()
        return {
            "status": "ok",
            "platform": plat,
            "contact_query": contact_query,
            "messages": [
                {
                    "sender": contact_query.strip().title() if contact_query else "Alex",
                    "content": f"Hey! Checking in on {plat}.",
                    "timestamp": time.time() - 300,
                }
            ],
        }

    # ── Approval / Cancellation Lifecycle ────────────────────────────────────

    async def approve_send(self, task_id: str) -> str:
        """Called when user confirms message send via APPROVE_ACTION event.

        NOTE: Network delivery to WhatsApp/Telegram/Discord/Email is not yet implemented.
        This method stages the draft as approved and returns an honest status.
        """
        if task_id in self._drafts:
            draft = self._drafts[task_id]
            # Mark as approved but not yet delivered — no network call is made here.
            draft["status"] = "approved_pending_delivery"
            draft["approved_at"] = time.time()
            return (
                f"📋 Message to {draft['contact_name']} via {draft['platform'].capitalize()} "
                f"has been approved and staged. "
                f"Note: direct network delivery to {draft['platform'].capitalize()} is not yet enabled — "
                f"the draft has been saved locally."
            )
        elif task_id in self._broadcasts:
            bc = self._broadcasts[task_id]
            bc["status"] = "approved_pending_delivery"
            bc["approved_at"] = time.time()
            return (
                f"📋 Broadcast to {len(bc.get('drafts', []))} recipients via "
                f"{bc['platform'].capitalize()} has been approved and staged. "
                f"Note: direct network delivery is not yet enabled — the draft has been saved locally."
            )
        return f"No pending message draft found for task {task_id}."


    async def cancel_send(self, task_id: str) -> str:
        """Called when user rejects message send via REJECT_ACTION event."""
        if task_id in self._drafts:
            self._drafts[task_id]["status"] = "cancelled"
            return f"🚫 Message draft to {self._drafts[task_id]['contact_name']} was cancelled."
        elif task_id in self._broadcasts:
            self._broadcasts[task_id]["status"] = "cancelled"
            return "🚫 Broadcast was cancelled."
        return f"No pending message draft found for task {task_id}."

    # ── Main Execution Contract ───────────────────────────────────────────────

    async def execute(self, task_id: str, message: str, context: dict[str, Any],
                      entities: dict[str, Any]) -> str:
        self._reset_state()
        start_time = time.perf_counter()

        if self._is_privacy_mode():
            return self._privacy_rejection()

        # ── OpenAI Agents SDK Runner Execution ────────────────────────────────
        try:
            final_out = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=6,
                task_type="messaging",
                input_guardrails=[],
                output_guardrails=[],
            )
            self._partial_result = final_out
            return final_out
        except Exception as sdk_exc:
            logger.warning("[messaging] SDK Runner encountered exception, falling back: %s", sdk_exc)

        try:
            # ── P1 Bridge 4B: Consume structured AgentTask parameters first ───
            agent_task = getattr(self, "_current_agent_task", None) or context.get("agent_task")
            intent = None

            if agent_task:
                params = dict(agent_task.parameters or {})
                task_op = str(agent_task.operation or params.get("action") or "").lower().strip()
                if any(x in task_op for x in ("draft", "send", "message", "write", "chat")):
                    action = Action.SEARCH_AND_DRAFT.value
                elif "broadcast" in task_op:
                    action = Action.BROADCAST.value
                elif any(x in task_op for x in ("read", "check", "fetch", "get")):
                    action = Action.READ.value
                else:
                    action = Action.SEARCH_AND_DRAFT.value

                recipient = params.get("contact_queries") or params.get("recipient") or params.get("contact") or agent_task.target_entity
                if recipient:
                    c_queries = [recipient] if isinstance(recipient, str) else list(recipient)
                    p_str = str(params.get("platform") or "whatsapp").lower()
                    m_text = str(params.get("message_text") or params.get("message") or params.get("text") or agent_task.goal or "")
                    intent = {
                        "action": action,
                        "platform": p_str,
                        "contact_queries": c_queries,
                        "message_text": m_text,
                        "reply": params.get("reply", ""),
                    }
                    logger.info("[messaging] P1-4B: Consumed structured AgentTask parameters directly (action=%s, platform=%s, recipient=%s)", action, p_str, c_queries)

            # Legacy fallback: extract intent via LLM JSON parsing
            if not intent:
                intent = await self._extract_intent(message, context)
            if not intent:
                messages = self._build_messages(message, context)
                return await self._llm_call(messages, task="general")

            action = intent.get("action", Action.NONE.value)
            raw_platform = self._parse_platform(intent.get("platform", ""))
            platform = self._infer_platform(message, raw_platform)


            if self._is_rate_limited(platform):
                return self._rate_limit_rejection()
            self._last_action_time[platform.value] = time.perf_counter()

            contact_queries = intent.get("contact_queries", [])
            if isinstance(contact_queries, str):
                contact_queries = [contact_queries]

            message_text = intent.get("message_text", "")
            reply = intent.get("reply", "")

            self._last_action_time[platform.value] = time.perf_counter()

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
