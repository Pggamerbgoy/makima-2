"""Makima v7.1 — EntityExtractor

Spec (from makima_v7_improved_plan.md / HANDOFF_GUIDE):
- Daemon thread runs after every conversation turn
- LLM triple extraction: (subject, predicate, object, confidence)
- min confidence 0.7 for storage
- 60s thread timeout
- conflict detection for 1-to-1 predicates

This repo snapshot may not yet have full Rust triple store / graph conflict
resolution wired. So this module provides a safe implementation that:
- runs triple extraction via AIHandler (best-effort)
- emits WS events for conflicts (graph_conflict) via ws_protocol
- never blocks reply pipeline (async safe; runs in daemon thread)

Non-negotiable rules:
- Never crash the brain: catch/log errors and skip extraction.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("makima.entity_extractor")


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    confidence: float


class EntityExtractor:
    """Daemon entity/triple extraction service."""

    def __init__(
        self,
        ai_handler: Any,
        eternal_memory: Any = None,
        ws_broadcast: Optional[Any] = None,
        min_confidence: float = 0.7,
        thread_timeout_s: float = 60.0,
    ):
        self.ai_handler = ai_handler
        self.eternal_memory = eternal_memory
        self.ws_broadcast = ws_broadcast
        self.min_confidence = float(min_confidence)
        self.thread_timeout_s = float(thread_timeout_s)

        self._q: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(
            target=self._loop, name="EntityExtractor", daemon=True
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()

    def submit_turn(
        self, turn_text: str, context: Optional[dict[str, Any]] = None
    ) -> None:
        if not turn_text:
            return
        ctx = context or {}
        try:
            self._q.put((turn_text, ctx), block=False)
        except Exception:
            pass

    def _loop(self) -> None:
        # Create a single persistent event loop for this daemon thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while not self._stop.is_set():
            try:
                turn_text, ctx = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                # Per-turn timeout: run extraction on the persistent loop
                raw = loop.run_until_complete(
                    asyncio.wait_for(
                        self._extract_triples_async(turn_text),
                        timeout=self.thread_timeout_s,
                    )
                )
                if raw:
                    loop.run_until_complete(self._store_or_emit(raw))
            except asyncio.TimeoutError:
                logger.debug("Entity extraction timed out (P7)")
                # Task timed out but loop continues — no cleanup needed
            except Exception as e:
                logger.error("EntityExtractor failed: %s", e)

        loop.close()

    async def _extract_triples_async(self, turn_text: str) -> list[Triple]:
        """Async extraction call. Runs on the daemon thread's persistent event loop."""
        await asyncio.sleep(2.0)  # Yield API burst window to interactive user commands
        prompt = self._build_prompt(turn_text)

        try:
            resp = await self.ai_handler.generate(
                messages=[{"role": "user", "content": prompt}],
                task="entity_extraction",
                require_json=True,
                temperature=0.2,
                max_tokens=256,
            )
            raw = getattr(resp, "text", "")
        except Exception as e:
            logger.debug("Triple extraction failed: %s", e)
            return []

        parsed = self.ai_handler.try_parse_json(raw)
        if not parsed:
            return []

        items = parsed.get("triples", [])
        out: list[Triple] = []
        for it in items:
            try:
                conf = float(it.get("confidence", 0.0))
                if conf < self.min_confidence:
                    continue
                out.append(
                    Triple(
                        subject=str(it.get("subject", "")),
                        predicate=str(it.get("predicate", "")),
                        object=str(it.get("object", "")),
                        confidence=conf,
                    )
                )
            except Exception:
                continue
        return out

    def _build_prompt(self, turn_text: str) -> str:
        return (
            "You are Makima's Entity Extractor.\n"
            "Extract factual triples from the user's turn.\n\n"
            "Return ONLY valid JSON:\n"
            "{\n"
            "  \"triples\": [\n"
            "    {\"subject\": \"...\", \"predicate\": \"...\", \"object\": \"...\", \"confidence\": 0.0-1.0}\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Only extract explicit or strongly implied facts.\n"
            "- Predicate is a single stable string (snake_case).\n"
            "- Use 0-1 confidence.\n"
            "- If nothing to extract, return {\"triples\": []}.\n\n"
            f"User turn:\n{turn_text}\n"
        )

    async def _store_or_emit(self, triples: list[Triple]) -> None:
        # Conflict detection for 1-to-1 predicates (simplified)
        one_to_one_predicates = {
            "favorite_color",
            "favorite_food",
            "preferred_app",
            "timezone",
        }

        if self.ws_broadcast:
            for t in triples:
                if t.predicate not in one_to_one_predicates:
                    continue
                try:
                    from . import ws_protocol

                    msg = ws_protocol.WSMessage(
                        v=ws_protocol.PROTOCOL_VERSION,
                        type=ws_protocol.ServerMessageType.GRAPH_CONFLICT,
                        payload={
                            "subject": t.subject,
                            "predicate": t.predicate,
                            "new_object": t.object,
                            "confidence": t.confidence,
                        },
                    )
                    await self.ws_broadcast(msg)
                except Exception:
                    continue

        # Storage to triple store not available in this snapshot.
        # Keep this as a no-op to avoid crashing.



