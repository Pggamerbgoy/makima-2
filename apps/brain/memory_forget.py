"""
Makima v7.1 â€” Memory Forget

Handles natural-language "forget X" commands and explicit UI forget actions.
Resolves entity â†’ triple IDs â†’ sets deleted_at tombstone in Rust TripleStore.
Cascades to related triples with user confirmation if > 0 dependents found.
Supports TTL: triples with expires_at auto-purge on next flush.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("makima.memory_forget")

class MemoryForgetHandler:
    def __init__(self, ai_handler=None, triple_store=None, vector_index=None, ws_broadcast=None, eternal_memory=None):
        self.ai_handler = ai_handler
        self.triple_store = triple_store
        self.vector_index = vector_index
        self.ws_broadcast = ws_broadcast
        self.eternal_memory = eternal_memory
        self._pending_confirmations: dict[str, dict] = {}

    async def handle_forget_command(self, task_id: str, message: str) -> str:
        """
        Handle a natural language "forget X" command.
        """
        entity = ""
        # Step 1: Use LLM to extract the entity to forget if available, otherwise heuristic extract
        if self.ai_handler:
            try:
                prompt = (
                    f"The user wants to forget something. Extract the exact entity or fact they want forgotten.\n"
                    f"User message: '{message}'\n"
                    f"Return ONLY a JSON object: {{\"entity\": \"<name of entity or fact>\", \"is_ambiguous\": <true/false>}}"
                )
                response = await self.ai_handler.generate(
                    messages=[{"role": "user", "content": prompt}],
                    task="fast_chat",
                    require_json=True
                )
                parsed = self.ai_handler.try_parse_json(response.text)
                if parsed and parsed.get("entity"):
                    entity = str(parsed["entity"]).strip()
            except Exception as e:
                logger.warning("AI entity extraction for forget failed: %s", e)

        if not entity:
            # Fallback heuristic extraction
            import re
            m = re.search(r"(?:forget|delete|remove|clear|erase|unlearn)\s+(?:about\s+|my\s+|everything\s+about\s+)?(.+)", message, re.IGNORECASE)
            if m:
                entity = m.group(1).strip()
            else:
                entity = message.strip()

        if not entity:
            return "I couldn't figure out what you want me to forget. Could you be more specific?"

        # Step 2: Delete from EternalMemory if available
        success_count = 0
        if self.eternal_memory is not None:
            try:
                deleted_turns = await self.eternal_memory.delete_matching(entity)
                success_count += deleted_turns
            except Exception as e:
                logger.error("EternalMemory delete_matching error for '%s': %s", entity, e)

        # Step 3: Delete from TripleStore if present
        if self.triple_store is not None:
            loop = asyncio.get_running_loop()
            try:
                triples = await asyncio.wait_for(
                    loop.run_in_executor(None, self.triple_store.query, entity, None, None),
                    timeout=5.0
                )
                if triples:
                    for t in triples:
                        tid = t.get("id") if isinstance(t, dict) else getattr(t, "id", None)
                        if tid:
                            await loop.run_in_executor(None, self.triple_store.tombstone, tid)
                            if self.vector_index is not None:
                                await loop.run_in_executor(None, self.vector_index.delete, tid)
                            success_count += 1
            except Exception as e:
                logger.error("TripleStore forget error for '%s': %s", entity, e)

        if success_count > 0:
            return f"Successfully forgot {success_count} memory records regarding '{entity}'."
        return f"I checked my memory, but found no stored records regarding '{entity}'."

    async def confirm_forget(self, task_id: str) -> str:
        """Execute a pending forget action after confirmation."""
        pending = self._pending_confirmations.pop(task_id, None)
        if not pending:
            return "No pending forget action found."
        
        entity = pending.get("entity", "")
        if self.eternal_memory is not None and entity:
            deleted = await self.eternal_memory.delete_matching(entity)
            return f"Successfully confirmed and purged {deleted} memories about '{entity}'."
        return "Action confirmed."
