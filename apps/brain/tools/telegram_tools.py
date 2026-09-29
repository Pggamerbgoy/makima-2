"""
Makima OS -- Telegram Tools
Location: apps/brain/tools/telegram_tools.py

Direct Telegram messaging tool using the Telegram Bot API.
Enables Makima to send messages, alerts, and fallback notifications to Telegram chats.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("makima.tools.telegram")


async def telegram_send_message(
    chat_id: str,
    message: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Send a message to a Telegram chat, user, or group via the configured Telegram Bot.

    Parameters:
        chat_id: Telegram Chat ID, username (e.g. '@channel_username'), or user ID.
        message: The message text to send.
    """
    clean_chat = str(chat_id or "").strip()
    clean_msg = str(message or "").strip()

    if not clean_chat:
        return {"status": "error", "message": "No chat_id provided for Telegram message."}
    if not clean_msg:
        return {"status": "error", "message": "No message content provided."}

    token = (
        os.environ.get("TELEGRAM_BOT_TOKEN")
        or os.environ.get("MAKIMA_TELEGRAM_TOKEN")
        or ""
    ).strip()

    if not token:
        return {
            "status": "error",
            "message": (
                "Telegram Bot Token is not configured.\n"
                "Please add TELEGRAM_BOT_TOKEN in .env or via Settings -> Connectors -> Telegram."
            ),
        }

    try:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": clean_chat,
            "text": clean_msg,
            "parse_mode": "HTML",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()

            if data.get("ok"):
                msg_id = data.get("result", {}).get("message_id")
                return {
                    "status": "success",
                    "message": f"Message delivered to Telegram chat {clean_chat} (message_id: {msg_id}).",
                    "chat_id": clean_chat,
                    "message_id": msg_id,
                }

            # If HTML parsing fails, retry once with plain text
            err_desc = data.get("description", "Unknown Telegram error")
            if "can't parse entities" in err_desc.lower():
                payload.pop("parse_mode", None)
                resp2 = await client.post(url, json=payload)
                data2 = resp2.json()
                if data2.get("ok"):
                    msg_id = data2.get("result", {}).get("message_id")
                    return {
                        "status": "success",
                        "message": f"Message delivered to Telegram chat {clean_chat} (message_id: {msg_id}).",
                        "chat_id": clean_chat,
                        "message_id": msg_id,
                    }
                err_desc = data2.get("description", err_desc)

            if token and token in err_desc:
                err_desc = err_desc.replace(token, "[REDACTED_TELEGRAM_TOKEN]")
            return {
                "status": "error",
                "message": f"Telegram API error: {err_desc}",
                "chat_id": clean_chat,
            }
    except Exception as exc:
        err_msg = str(exc)
        if token and token in err_msg:
            err_msg = err_msg.replace(token, "[REDACTED_TELEGRAM_TOKEN]")
        logger.error("[telegram] Failed to send message to %s: %s", clean_chat, err_msg)
        return {
            "status": "error",
            "message": f"Network or Telegram client error: {err_msg}",
            "chat_id": clean_chat,
        }


def register_telegram_tools(tool_registry: Any, services: Any = None) -> None:
    """Register Telegram tools into Makima ToolRegistry."""
    agent_hints = ["messaging", "commander", "automation", "general"]
    task_tags = ["telegram", "messaging", "chat", "send", "notify"]

    schema = {
        "type": "object",
        "properties": {
            "chat_id": {
                "type": "string",
                "description": "Telegram user ID, chat ID (e.g. '123456789'), or channel/group username ('@username').",
            },
            "message": {
                "type": "string",
                "description": "Message text to deliver to the chat.",
            },
        },
        "required": ["chat_id", "message"],
    }

    desc = (
        "Send a message to a Telegram user, group, or channel via the Telegram Bot API. "
        "Use as an automatic fallback when WhatsApp or local desktop messengers fail."
    )

    try:
        if hasattr(tool_registry, "register_tool"):
            tool_registry.register_tool(
                name="telegram_send_message",
                description=desc,
                func=telegram_send_message,
                schema=schema,
                category="communication",
                agent_hints=agent_hints,
                task_tags=task_tags,
                priority=2,
                is_destructive=False,
            )
        elif hasattr(tool_registry, "register"):
            tool_registry.register(
                name="telegram_send_message",
                func=telegram_send_message,
                description=desc,
                schema=schema,
                category="communication",
            )
        logger.info("Registered Telegram tool: telegram_send_message")
    except Exception as exc:
        logger.warning("Failed to register Telegram tool: %s", exc)
