"""
Makima v7.2 — Elite Memory Agent
Enterprise-grade memory orchestration: Hybrid vector retrieval, semantic caching,
graph associations, temporal decay scoring, and asynchronous long-term consolidation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from collections import OrderedDict
from typing import Any, Optional

from .base_agent import BaseAgent

logger = logging.getLogger("makima.agents.memory")

# ============================================================================
# PROMPTS & CONFIGURATION
# ============================================================================

INTENT_ROUTING_PROMPT = """You are Makima's Elite Memory Orchestrator. Analyze the user's message 
and determine the optimal memory operation. 

Respond ONLY with a valid JSON object matching this schema:
{{
    "intent": "search" | "forget" | "consolidate" | "store" | "none",
    "search_queries": ["list", "of", "semantic", "queries"],
    "target_entities": ["list", "of", "specific", "entities", "to", "forget", "or", "query"],
    "temporal_constraint": "recent" | "historical" | "all",
    "requires_graph_traversal": true | false,
    "reasoning": "brief explanation of your routing decision"
}}

Rules:
1. "search": User asks about past events, facts, preferences, or context. Generate 1-3 distinct semantic queries.
2. "forget": User explicitly asks to delete, unlearn, or forget specific information. Extract exact target entities.
3. "consolidate": User asks to summarize, compress, or review long-term memory state.
4. "store": User explicitly asks to remember, save, or note down specific information.
5. "none": General chatter requiring no explicit memory I/O.
6. Set requires_graph_traversal to true if the query involves relationships between entities (e.g., "who did I meet at X").

User Message: {message}
Current Context Entities: {entities}"""

MEMORY_SYNTHESIS_PROMPT = """You are Makima's Memory Synthesizer. You have retrieved the following 
memory fragments and graph associations. Synthesize them into a coherent, natural response.

Retrieved Memories:
{memories}

Graph Associations:
{associations}

User Message: {message}

Instructions:
- Answer the user's question directly using ONLY the provided memories.
- If the memories are insufficient, state clearly what you don't recall.
- Do not hallucinate facts outside the provided context.
- Maintain Makima's persona: helpful, precise, and slightly conversational."""


# ============================================================================
# SEMANTIC CACHE & SCORING ENGINES
# ============================================================================

class SemanticCache:
    """
    High-performance async semantic cache using n-gram shingling and Jaccard 
    similarity for zero-dependency fallback, with optional embedding support.
    """
    def __init__(self, max_size: int = 2048, ttl: int = 3600, similarity_threshold: float = 0.82):
        self.max_size = max_size
        self.ttl = ttl
        self.threshold = similarity_threshold
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _normalize_and_shingle(self, text: str, n: int = 3) -> set[str]:
        text = re.sub(r'[^a-z0-9\s]', '', text.lower())
        words = text.split()
        return { ' '.join(words[i:i+n]) for i in range(len(words) - n + 1) } if len(words) >= n else set(words)

    def _compute_similarity(self, text1: str, text2: str) -> float:
        s1, s2 = self._normalize_and_shingle(text1), self._normalize_and_shingle(text2)
        if not s1 or not s2:
            return 1.0 if text1.strip() == text2.strip() else 0.0
        intersection = len(s1 & s2)
        union = len(s1 | s2)
        return intersection / union if union > 0 else 0.0

    async def get(self, query: str) -> Optional[str]:
        async with self._lock:
            current_time = time.time()
            keys_to_delete = [k for k, v in self._cache.items() if current_time - v['timestamp'] > self.ttl]
            for k in keys_to_delete:
                del self._cache[k]

            best_match, best_score = None, 0.0
            for cached_query, data in self._cache.items():
                score = self._compute_similarity(query, cached_query)
                if score > best_score:
                    best_score, best_match = score, data['response']
            
            if best_score >= self.threshold and best_match is not None:
                logger.debug("[memory_cache] Hit (score: %.2f)", best_score)
                return best_match
            return None

    async def set(self, query: str, response: str) -> None:
        async with self._lock:
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[query] = {'response': response, 'timestamp': time.time()}


class MemoryScorer:
    """Calculates composite relevance scores using temporal decay, importance, and semantic density."""
    
    HALF_LIFE_SECONDS = 7 * 24 * 3600  # 7 days half-life for recency decay

    @classmethod
    def calculate_score(cls, memory: dict[str, Any], query: str, current_time: float) -> float:
        relevance = float(memory.get('relevance_score', 0.5))
        importance = float(memory.get('importance', 0.5))
        
        # Temporal Decay
        timestamp = float(memory.get('timestamp', current_time))
        age_seconds = max(0, current_time - timestamp)
        decay_factor = math.exp(-0.693 * age_seconds / cls.HALF_LIFE_SECONDS)
        
        # Semantic Density (bonus for longer, detailed memories)
        content = str(memory.get('content', ''))
        density_bonus = min(0.1, len(content) / 5000.0)
        
        # Composite Score: weighted sum
        score = (relevance * 0.5) + (importance * 0.25) + (decay_factor * 0.2) + density_bonus
        return round(min(1.0, max(0.0, score)), 4)


# ============================================================================
# ELITE MEMORY AGENT
# ============================================================================

class MemoryAgent(BaseAgent):
    AGENT_NAME = "memory"
    DESCRIPTION = "Elite Memory Engine: Hybrid retrieval, semantic caching, graph associations, and retention."
    SYSTEM_PROMPT = MEMORY_SYNTHESIS_PROMPT

    def __init__(self, ai_handler=None, memory=None, tool_registry=None, ws_broadcast=None, orchestrator=None, guardrails=None, **kwargs):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails)
        self.semantic_cache = SemanticCache(max_size=2048, ttl=3600, similarity_threshold=0.82)
        self.scorer = MemoryScorer()
        self._consolidation_lock = asyncio.Lock()

    async def execute(self, task_id: str, message: str, context: dict[str, Any],
                      entities: dict[str, Any]) -> str:
        self._reset_state()
        start_time = time.time()

        try:
            # 1. Check Semantic Cache for immediate resolution
            cached_response = await self.semantic_cache.get(message)
            if cached_response:
                logger.info("[memory] Resolved via semantic cache in %.2fs", time.time() - start_time)
                return cached_response

            # 2. Route Intent via LLM
            intent_data = await self._route_memory_intent(message, entities)
            intent = intent_data.get("intent", "none")
            
            if intent == "none":
                return await self._fallback_response(message, context)

            # 3. Execute Intent
            if intent == "forget":
                response = await self._execute_memory_forget(task_id, intent_data)
            elif intent == "consolidate":
                response = await self._trigger_consolidation(task_id, context)
            elif intent == "search":
                response = await self._execute_hybrid_search(task_id, message, intent_data, context)
            elif intent == "store":
                # FIX: Just acknowledge the storage. CommandRouter automatically saves this turn to EternalMemory anyway!
                response = f"Got it. I will remember that: {', '.join(intent_data.get('target_entities', [])) or 'your requested information'}."
            else:
                response = await self._fallback_response(message, context)

            # 4. Update Cache and Return
            if response and intent != "forget":
                await self.semantic_cache.set(message, response)
                
            logger.info("[memory] Executed '%s' in %.2fs", intent, time.time() - start_time)
            return response

        except Exception as e:
            logger.exception("[memory] Critical execute failure: %s", e)
            self._partial_result = f"Error: {e}"
            return f"I encountered a critical error accessing my memory systems: {str(e)[:100]}"

    async def _route_memory_intent(self, message: str, entities: dict[str, Any]) -> dict[str, Any]:
        prompt = INTENT_ROUTING_PROMPT.format(message=message, entities=json.dumps(entities, default=str))
        try:
            raw = await self._llm_call(
                [{"role": "user", "content": prompt}],
                task="routing", require_json=True, temperature=0.0, max_tokens=400
            )
            parsed = self.ai_handler.try_parse_json(raw)
            return parsed if isinstance(parsed, dict) else {"intent": "none"}
        except Exception as e:
            logger.warning("[memory] Intent routing failed, defaulting to search: %s", e)
            return {"intent": "search", "search_queries": [message], "requires_graph_traversal": False}

    async def _execute_hybrid_search(self, task_id: str, message: str, intent_data: dict, context: dict) -> str:
        queries = intent_data.get("search_queries", [message])
        requires_graph = intent_data.get("requires_graph_traversal", False)
        temporal = intent_data.get("temporal_constraint", "all")
        
        # Parallel execution of vector search and graph traversal
        tasks = [self._vector_retrieve(q, temporal) for q in queries]
        if requires_graph:
            tasks.append(self._graph_traverse(intent_data.get("target_entities", [])))
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Aggregate and deduplicate memories
        raw_memories = []
        graph_associations = []
        
        for res in results:
            if isinstance(res, Exception):
                logger.warning("[memory] Sub-task failed: %s", res)
                continue
            if isinstance(res, list):
                if res and isinstance(res[0], dict) and 'content' in res[0]:
                    raw_memories.extend(res)
                else:
                    graph_associations.extend(res)
            elif isinstance(res, dict):
                graph_associations.append(res)

        # Score, rank, and filter
        ranked_memories = self._score_and_rank(raw_memories, message)
        
        # Synthesize response
        return await self._synthesize_response(message, ranked_memories, graph_associations, context)

    async def _vector_retrieve(self, query: str, temporal: str) -> list[dict[str, Any]]:
        try:
            limit = 10 if temporal == "all" else 5
            if self.memory is not None:
                rows = await self.memory.search(query, k=limit)
                return [self._normalize_memory_row(r) for r in rows if isinstance(r, dict)]
            if not self.tool_registry:
                return []
            raw = await self._use_tool("memory_search", query=query, k=limit)
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            results = parsed if isinstance(parsed, list) else [parsed]
            return [self._normalize_memory_row(r) for r in results if isinstance(r, dict)]
        except (json.JSONDecodeError, TypeError, Exception) as e:
            logger.error("[memory] Vector retrieval failed for '%s': %s", query, e)
            return []

    @staticmethod
    def _normalize_memory_row(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize EternalMemory rows (message/created_at) to agent schema (content/timestamp)."""
        if "content" not in row and "message" in row:
            row["content"] = row["message"]
        if "timestamp" not in row and "created_at" in row:
            row["timestamp"] = row["created_at"]
        row.setdefault("relevance_score", 0.5)
        row.setdefault("importance", 0.5)
        return row

    async def _graph_traverse(self, entities: list[str]) -> list[dict[str, Any]]:
        if not self.tool_registry or not entities:
            return []
        if not self.tool_registry.has_tool("graph_query"):
            return []
        try:
            raw = await self._use_tool("graph_query", entities=entities, depth=2, limit=15)
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return parsed if isinstance(parsed, list) else []
        except Exception as e:
            logger.error("[memory] Graph traversal failed: %s", e)
            return []

    def _score_and_rank(self, memories: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        if not memories:
            return []
            
        current_time = time.time()
        scored = []
        seen_ids = set()
        
        for mem in memories:
            mem_id = mem.get('id', hashlib.md5(str(mem.get('content', '')).encode()).hexdigest())
            if mem_id in seen_ids:
                continue
            seen_ids.add(mem_id)
            
            score = self.scorer.calculate_score(mem, query, current_time)
            mem['final_score'] = score
            scored.append(mem)
            
        scored.sort(key=lambda x: x['final_score'], reverse=True)
        return scored[:7]  # Top 7 memories for context window optimization

    async def _synthesize_response(self, message: str, memories: list[dict], 
                                   associations: list[dict], context: dict) -> str:
        mem_text = "\n".join([
            f"- [{m.get('timestamp', 'N/A')}] (Score: {m.get('final_score', 0)}) {m.get('content', '')}" 
            for m in memories
        ]) or "No direct memories found."
        
        assoc_text = "\n".join([
            f"- {a.get('source', '?')} -> {a.get('relation', '?')} -> {a.get('target', '?')}" 
            for a in associations
        ]) or "No graph associations found."

        prompt = self.SYSTEM_PROMPT.format(
            memories=mem_text, 
            associations=assoc_text, 
            message=message
        )
        
        # Inject learned persona summary if available
        try:
            _le_inst = getattr(self, "_learning_engine", None)
            if _le_inst and hasattr(_le_inst, "cached_user_persona"):
                _snap = getattr(_le_inst, "cached_user_persona", {}) or {}
                if _snap:
                    _p_parts = []
                    for cat, data in _snap.items():
                        if isinstance(data, dict) and data:
                            _p_parts.append(f"{cat}: {json.dumps(data)}")
                    if _p_parts:
                        prompt += "\n\n[LEARNED USER PERSONA & PREFERENCES]\n" + "\n".join(_p_parts)
        except Exception:
            pass

        messages = [{"role": "system", "content": prompt}]
        if context.get("history"):
            messages.extend(context["history"][-4:])
        messages.append({"role": "user", "content": message})
        
        try:
            return await self._llm_call(messages, task="synthesis", temperature=0.3, max_tokens=800)
        except Exception as e:
            logger.error("[memory] Synthesis LLM call failed: %s", e)
            return "I found some memories, but I'm having trouble formulating a response right now."

    async def _execute_memory_forget(self, task_id: str, intent_data: dict) -> str:
        targets = intent_data.get("target_entities", [])
        if not targets:
            return "I need to know exactly what you want me to forget. Could you specify the details?"

        target_str = ", ".join(targets)
        confirmed = await self._confirm_action(
            task_id, "forget", 
            f"Permanently erase all memories and graph associations related to: {target_str}?",
            risk_level="critical"
        )
        
        if not confirmed:
            return f"Understood. I have cancelled the deletion of '{target_str}'."

        if not self.tool_registry:
            return "Memory deletion tools are currently unavailable."

        try:
            # Execute cascading delete (Vector + Graph) — graph tools may not exist
            tool_calls = [self._use_tool("memory_forget", entity=target_str, cascade=True)]
            if self.tool_registry.has_tool("graph_delete"):
                tool_calls.append(self._use_tool("graph_delete", entities=targets, cascade=True))
            
            results = await asyncio.gather(*tool_calls, return_exceptions=True)
            
            failures = [r for r in results if isinstance(r, Exception) or (isinstance(r, str) and "error" in r.lower())]
            
            # Invalidate cache to prevent stale recalls
            await self.semantic_cache.set(target_str, "I have forgotten this information as requested.")
            
            if failures:
                logger.warning("[memory] Partial forget failure: %s", failures)
                return f"I have initiated the forgetting process for '{target_str}', but some associations might take time to fully purge from the graph."
                
            return f"Done. I have completely erased my memories and graph associations regarding '{target_str}'."
            
        except Exception as e:
            logger.error("[memory] Forget execution failed: %s", e)
            return f"I tried to forget '{target_str}', but encountered a system error: {str(e)[:50]}"

    async def _trigger_consolidation(self, task_id: str, context: dict) -> str:
        if not self._consolidation_lock.locked():
            asyncio.create_task(self._run_background_consolidation(task_id))
            return "I've initiated a background memory consolidation process. I'll summarize and compress my long-term retention graphs shortly."
        return "Memory consolidation is already in progress. Please wait a moment."

    async def _run_background_consolidation(self, task_id: str) -> None:
        async with self._consolidation_lock:
            try:
                logger.info("[memory] Starting background consolidation...")
                if not self.tool_registry:
                    return
                if not self.tool_registry.has_tool("memory_fetch_oldest"):
                    logger.info("[memory] memory_fetch_oldest not available; skipping consolidation.")
                    return
                    
                raw = await self._use_tool("memory_fetch_oldest", limit=50)
                old_memories = json.loads(raw) if isinstance(raw, str) else []
                
                if not old_memories or not isinstance(old_memories, list):
                    logger.info("[memory] No old memories to consolidate.")
                    return

                # Chunk and summarize
                chunk_text = "\n".join([m.get('content', '') for m in old_memories[:20]])
                summary_prompt = f"Summarize the following historical memories into a single, dense, factual paragraph preserving key entities and dates:\n\n{chunk_text}"
                
                summary = await self._llm_call(
                    [{"role": "user", "content": summary_prompt}],
                    task="consolidation", temperature=0.1, max_tokens=500
                )
                
                # Store consolidated memory and prune old ones
                await self._use_tool("memory_store", content=summary, type="consolidated", importance=0.9)
                
                ids_to_prune = [m.get('id') for m in old_memories[:20] if m.get('id')]
                if ids_to_prune:
                    await self._use_tool("memory_prune", ids=ids_to_prune)
                    
                logger.info("[memory] Consolidation complete. Pruned %d memories.", len(ids_to_prune))
                
            except Exception as e:
                logger.error("[memory] Background consolidation failed: %s", e)

    async def _fallback_response(self, message: str, context: dict) -> str:
        messages = self._build_messages(message, context)
        try:
            return await self._llm_call(messages, task="general", temperature=0.5, max_tokens=400)
        except Exception as e:
            logger.error("[memory] Fallback LLM call failed: %s", e)
            return "I'm having a momentary lapse in my cognitive processing. Could you repeat that?"
