"""
Makima v7.1 — Clipboard Handler

Receives WM_CLIPBOARDUPDATE events from C clipboard service via gRPC.
Stores last clipboard text (max 4000 chars, text only).
Never stores to EternalMemory unless user says "remember this".
Emits clipboard_changed WS event (opt-in).
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("makima.clipboard_handler")

class ClipboardHandler:
    def __init__(self, config: dict, clipboard_service=None, ws_broadcast=None):
        self.config = config.get("clipboard", {})
        self.enabled = self.config.get("enabled", True)
        self.max_chars = self.config.get("max_chars", 4000)
        self.emit_events = self.config.get("ws_events", False)
        self.privacy_mode = config.get("privacy", {}).get("mode", False)
        
        self.clipboard_service = clipboard_service
        self.ws_broadcast = ws_broadcast
        self._last_content: str = ""
        self._stream_task: Optional[asyncio.Task] = None
        
    async def start(self):
        if not self.enabled or self.privacy_mode:
            logger.info("Clipboard handler disabled (config or privacy mode)")
            return
            
        if self.clipboard_service:
            # Phase 2: Start gRPC stream listener
            # self._stream_task = asyncio.create_task(self._listen_stream())
            pass
            
    async def stop(self):
        if self._stream_task:
            self._stream_task.cancel()
            
    async def get_clipboard(self) -> Optional[str]:
        """Get current clipboard content for context injection."""
        if not self.enabled or self.privacy_mode:
            return None
            
        if not self.clipboard_service:
            return self._last_content or None
            
        try:
            # Prod: gRPC call to GetClipboard
            response = await self.clipboard_service.get_clipboard(max_chars=self.max_chars)
            self._last_content = response.get("text", "")
            return self._last_content if self._last_content else None
        except Exception as e:
            logger.error(f"Failed to read clipboard: {e}")
            return None

    async def _handle_change_event(self, text: str, total_chars: int):
        """Called when C service pushes an update."""
        self._last_content = text
        if self.emit_events and self.ws_broadcast:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.WSMessage(
                v=ws_protocol.PROTOCOL_VERSION,
                type=ws_protocol.ServerMessageType.CLIPBOARD_CHANGED,
                payload={"preview": text[:100], "chars": total_chars}
            ))
