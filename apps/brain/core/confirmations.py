"""
Makima OS v9.2 — Action Confirmation Lifecycle
Decoupled authority for pending action approvals, rejections, and event triggers.
Replaces BaseAgent._pending_confirmations and BaseAgent.resolve_confirmation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

logger = logging.getLogger("makima.confirmations")


class ActionConfirmationManager:
    """Manages asynchronous wait events for user confirmation of dangerous/sensitive actions."""

    _pending_confirmations: ClassVar[dict[str, tuple[asyncio.Event, str, dict[str, bool]]]] = {}

    @classmethod
    def register(cls, key: str, entry: tuple[asyncio.Event, str, dict[str, bool]]) -> None:
        cls._pending_confirmations[key] = entry

    @classmethod
    def pop(cls, key: str, default: Any = None) -> Any:
        return cls._pending_confirmations.pop(key, default)

    @classmethod
    async def resolve_confirmation(cls, confirm_id: str, approved: bool) -> bool:
        entry = cls._pending_confirmations.get(confirm_id)
        if not entry:
            # Suffix or exact prefix match to avoid loose substring collisions
            for k, val in list(cls._pending_confirmations.items()):
                if k == confirm_id or k.endswith(f"_{confirm_id}") or confirm_id.endswith(f"_{k}"):
                    entry = val
                    break
        if not entry:
            logger.warning(
                "[ActionConfirmationManager] No pending confirmation found for: %s. Active pending keys: %s",
                confirm_id,
                list(cls._pending_confirmations.keys()),
            )
            return False
        event, _, res = entry
        res["approved"] = approved
        event.set()
        return True


# Direct module exports for convenient access
_pending_confirmations = ActionConfirmationManager._pending_confirmations
resolve_confirmation = ActionConfirmationManager.resolve_confirmation
