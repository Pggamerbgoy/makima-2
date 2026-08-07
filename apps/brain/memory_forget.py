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
    def __init__(self, ai_handler, triple_store, vector_index, ws_broadcast=None):
        self.ai_handler = ai_handler
        self.triple_store = triple_store
        self.vector_index = vector_index
        self.ws_broadcast = ws_broadcast
        self._pending_confirmations: dict[str, dict] = {}

    async def handle_forget_command(self, task_id: str, message: str) -> str:
        """
        Handle a natural language "forget X" command.
        """
        if self.triple_store is None or self.vector_index is None:
            logger.warning("Forget requested while Rust graph memory is unavailable")
            return "Long-term graph memory is not available in this build, so there is nothing to forget there."

        # Step 1: Use LLM to extract the entity to forget
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
        if not parsed or not parsed.get("entity"):
            return "I couldn't figure out what you want me to forget. Could you be more specific?"
            
        entity = parsed["entity"]
        if parsed.get("is_ambiguous", False):
            return f"Which '{entity}' do you mean? Please be more specific."

        # Step 2: Query TripleStore
        # Note: PyO3 calls must run in executor
        loop = asyncio.get_event_loop()
        try:
            triples = await asyncio.wait_for(
                loop.run_in_executor(None, self.triple_store.query, entity, None, None),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            return "Memory search timed out while trying to forget."
        except Exception as e:
            logger.error(f"Failed to query TripleStore: {e}")
            return "An error occurred while accessing memory."

        if not triples:
            return f"I don't have any memories about '{entity}' to forget."

        # Step 3: Check cascade
        try:
            dependent_count = await asyncio.wait_for(
                loop.run_in_executor(None, self.triple_store.cascade_check, triples[0]["id"]),
                timeout=5.0
            )
        except Exception:
            dependent_count = 0

        if dependent_count > 0:
            # Requires confirmation
            self._pending_confirmations[task_id] = {
                "entity": entity,
                "triple_ids": [t["id"] for t in triples],
                "dependent_count": dependent_count
            }
            if self.ws_broadcast:
                from . import ws_protocol
                await self.ws_broadcast(ws_protocol.build_action_confirm(
                    task_id=task_id,
                    action="forget_cascade",
                    description=f"Forgetting '{entity}' will also delete {dependent_count} related memories. Proceed?",
                    risk_level="high"
                ))
            return f"Forgetting '{entity}' will also delete {dependent_count} related facts. Do you want to proceed?"

        # Step 4: Tombstone
        return await self._execute_forget(entity, [t["id"] for t in triples])

    async def confirm_forget(self, task_id: str) -> str:
        """Execute a pending forget action after confirmation."""
        pending = self._pending_confirmations.pop(task_id, None)
        if not pending:
            return "No pending forget action found."
        
        return await self._execute_forget(pending["entity"], pending["triple_ids"])

    async def _execute_forget(self, entity: str, triple_ids: list[str]) -> str:
        loop = asyncio.get_event_loop()
        success_count = 0
        
        for tid in triple_ids:
            try:
                # 1. Tombstone in SQLite
                await loop.run_in_executor(None, self.triple_store.tombstone, tid)
                # 2. Async HNSW deletion
                await loop.run_in_executor(None, self.vector_index.delete, tid)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to forget triple {tid}: {e}")
        
        return f"Successfully forgot {success_count} memory/memories about '{entity}'."
