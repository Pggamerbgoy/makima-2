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
import re
import time
from collections import OrderedDict
from typing import Any, Optional

from .base_agent import BaseAgent, agent_tool, TOOL_DISCIPLINE_BLOCK

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
        text = re.sub(r'[^\w\s]', '', text.lower())
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

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()

    async def invalidate_matching(self, entity_or_query: str) -> int:
        if not entity_or_query or not entity_or_query.strip():
            return 0
        cleaned = re.sub(r"[^\w\s]", "", entity_or_query.lower()).strip()
        tokens = [t for t in cleaned.split() if len(t) > 2]
        evicted = 0
        async with self._lock:
            keys = list(self._cache.keys())
            for k in keys:
                k_norm = k.lower()
                if cleaned in k_norm or any(t in k_norm for t in tokens):
                    del self._cache[k]
                    evicted += 1
        return evicted

    async def set(self, query: str, response: str) -> None:
        async with self._lock:
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[query] = {'response': response, 'timestamp': time.time()}


class MemoryScorer:
    """Calculates composite relevance scores using hybrid retrieval, importance, and semantic density."""
    
    @classmethod
    def calculate_score(cls, memory: dict[str, Any], query: str, current_time: float) -> float:
        # Relevance score from EternalMemory already includes hybrid retrieval & Ebbinghaus recency
        relevance = float(memory.get('relevance_score', 0.5))
        importance = float(memory.get('importance', 0.5))
        recency = float(memory.get('recency_factor', 1.0))
        
        # Semantic Density (bonus for informative memories)
        content = str(memory.get('content', ''))
        density_bonus = min(0.05, len(content) / 5000.0)
        
        # Composite Score: balanced weighted sum
        score = (relevance * 0.6) + (importance * 0.25) + (recency * 0.1) + density_bonus
        return round(min(1.0, max(0.0, score)), 4)


# ============================================================================
# ELITE MEMORY AGENT
# ============================================================================

class MemoryAgent(BaseAgent):
    AGENT_NAME = "memory"
    DESCRIPTION = "Elite Memory Engine: Hybrid retrieval, semantic caching, graph associations, and retention."
    SYSTEM_PROMPT = """You are Makima's Elite Memory Agent.
You manage long-term episodic recall, user preference retention, semantic caching, and memory lifecycle operations.

CORE CAPABILITIES & TOOLS:
- Memory Search: memory_search(query, limit=5, threshold=0.7) [hybrid vector & semantic recall]
- Memory Store: memory_store(content="<exact fact/preference/requirement>", category="fact|preference|requirement") [persist important user facts]
- Memory Forget: memory_forget(entity="...", query="...") [permanently purge obsolete/requested facts]
- Graph Traversal: graph_query(entity="...") [explore knowledge associations]

GROUNDING & INTEGRITY RULES:
1. Grounded Recall: Base your answers strictly on retrieved memory records and context.
2. Grounded Store: When the user asks you to remember or store something, store ONLY the exact fact, rule, or preference stated by the user. Do NOT hallucinate or inject unrelated entities or past conversational history.
3. Honest Uncertainty: If a memory is missing or confidence is low, state plainly that you don't recall it rather than fabricating facts.
4. Structured Thinking: Analyze user inquiry, entity associations, and memory relevance inside a <thinking>...</thinking> block before synthesizing responses or performing lifecycle operations.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    def __init__(self, ai_handler=None, memory=None, tool_registry=None, ws_broadcast=None, orchestrator=None, guardrails=None, **kwargs):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails)
        self.semantic_cache = SemanticCache(max_size=2048, ttl=3600, similarity_threshold=0.82)
        self.scorer = MemoryScorer()
        self._consolidation_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

        # ── Bridge unregistered tool names → EternalMemory public API ────
        # memory_agent calls _use_tool("memory_search", ...) etc. inside
        # _vector_retrieve / _trigger_consolidation / _execute_memory_forget.
        # Without _TOOL_MAP these raise KeyError("Tool not found: X") at
        # runtime. We bridge to self.memory where possible; graph-layer ops
        # (require Rust graph store) degrade gracefully.
        self._TOOL_MAP: dict[str, Any] = {
            "memory_search": self._bridge_memory_search,
            "memory_store": self._bridge_memory_store,
            "memory_fetch_oldest": self._bridge_memory_fetch_oldest,
            "memory_forget": self._bridge_memory_forget,
            # Graph-layer ops: Rust store not present → safe no-op strings
            "graph_query": self._bridge_graph_unavailable,
            "graph_delete": self._bridge_graph_unavailable,
            "memory_prune": self._bridge_graph_unavailable,
        }

    # ── EternalMemory declarative agent tools ───────────────────────────────

    @agent_tool(
        name="memory_forget",
        description="Permanently delete stored conversation memories matching a specific entity, topic, or fact.",
        category="memory",
        is_destructive=True,
        schema={
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "The entity, topic, or fact to delete"},
                "query": {"type": "string", "description": "Alternative query string to match for deletion"},
            },
            "required": [],
        },
    )
    async def _bridge_memory_forget(self, entity: str = "", query: str = "", **_: Any) -> str:
        target = entity or query
        if not target:
            return "No target entity or query specified to forget."
        if self.memory is None:
            return "Memory layer unavailable."
        try:
            count = await self.memory.delete_matching(target)
            await self.semantic_cache.invalidate_matching(target)
            return f"Successfully deleted {count} memory records matching '{target}'."
        except Exception as e:
            logger.error("[memory] bridge_memory_forget failed: %s", e)
            return f"Failed to delete memories: {e}"

    @agent_tool(
        name="memory_search",
        description="Search past user memories and conversation history using hybrid semantic vector and BM25 keyword search.",
        category="memory",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query or entity to look up"},
                "k": {"type": "integer", "description": "Max memory records to retrieve (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    )
    async def _bridge_memory_search(self, query: str = "", k: int = 5, **_: Any) -> str:
        if self.memory is None:
            return "[]"
        try:
            rows = await self.memory.search(query, k=int(k))
            return json.dumps(rows, default=str)
        except Exception as e:
            logger.error("[memory] bridge_memory_search failed: %s", e)
            return "[]"

    @agent_tool(
        name="memory_store",
        description="Store a piece of user information, preference, fact, or requirement into long-term memory.",
        category="memory",
        schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The exact fact, preference, or requirement to store"},
                "category": {"type": "string", "description": "fact | preference | requirement | context", "default": "fact"},
                "importance": {"type": "number", "description": "Importance score between 0.0 and 1.0", "default": 0.8},
            },
            "required": ["content"],
        },
    )
    async def _bridge_memory_store(self, content: str = "", category: str = "fact",
                                   importance: float = 0.8, auto_resolve: bool = True, **kwargs: Any) -> str:
        actual_content = str(content or kwargs.get("text") or kwargs.get("fact") or kwargs.get("preference") or kwargs.get("requirement") or "").strip()
        if not actual_content:
            return "No content provided to store."
        if self.memory is None:
            return "Memory layer unavailable"
        try:
            clean_fact = re.sub(r"^(?:ye\s+)?(?:yaad\s+(?:rakho|karo)|remember\s+that|save|store|note\s+down)\s+(?:ki\s+)?", "", actual_content, flags=re.IGNORECASE).strip()
            if not clean_fact:
                clean_fact = actual_content
            if auto_resolve and hasattr(self.memory, "resolve_contradictions"):
                superseded = await self.memory.resolve_contradictions(clean_fact)
                if superseded > 0:
                    logger.info("[memory] Auto-superseded %d contradictory memories for new fact", superseded)
            await self.memory.save_turn(clean_fact, role="user")
            await self.semantic_cache.clear()
            return f"Stored successfully: '{clean_fact}'"
        except Exception as e:
            logger.error("[memory] bridge_memory_store failed: %s", e)
            return f"Store failed: {e}"

    @agent_tool(
        name="memory_fetch_oldest",
        description="Fetch historical conversation turns for background summarization and memory consolidation.",
        category="memory",
        schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of turns to fetch", "default": 50},
            },
            "required": [],
        },
    )
    async def _bridge_memory_fetch_oldest(self, limit: int = 50, **_: Any) -> str:
        if self.memory is None:
            return "[]"
        try:
            rows = await self.memory.get_history(n=int(limit))
            return json.dumps(rows, default=str)
        except Exception as e:
            logger.error("[memory] bridge_memory_fetch_oldest failed: %s", e)
            return "[]"

    async def _bridge_graph_unavailable(self, **_: Any) -> str:
        # Rust graph store / triple-store not built into this deployment.
        # Return an empty result so callers can degrade gracefully.
        return json.dumps({"status": "unavailable", "results": [], "note": "Graph layer requires Rust data-core binary"})

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

            # ── OpenAI Agents SDK Runner Execution ────────────────────────────
            try:
                final_out = await self.run_sdk_execution(
                    task_id=task_id,
                    message=message,
                    context=context,
                    max_turns=6,
                    task_type="memory",
                    input_guardrails=[],
                    output_guardrails=[],
                )
                self._partial_result = final_out
                if final_out and "forget" not in message.lower():
                    await self.semantic_cache.set(message, final_out)
                logger.info("[memory] Executed via SDK Runner in %.2fs", time.time() - start_time)
                return final_out
            except Exception as sdk_exc:
                logger.warning("[memory] SDK Runner encountered exception, falling back: %s", sdk_exc)

            # 2. Route Intent via LLM (or AgentTask direct consumption)
            agent_task = getattr(self, "_current_agent_task", None) or (context.get("agent_task") if isinstance(context, dict) else None)
            if agent_task and hasattr(agent_task, "operation") and agent_task.operation:
                op = str(agent_task.operation or "").lower().strip()
                params = dict(agent_task.parameters or {})
                if "forget" in op:
                    intent_data = {"intent": "forget", "target_entities": [agent_task.target_entity] if agent_task.target_entity else []}
                elif "consolidate" in op:
                    intent_data = {"intent": "consolidate"}
                elif any(k in op for k in ("search", "recall", "find")):
                    intent_data = {"intent": "search", "query": agent_task.target_entity or message, "depth": params.get("depth", "standard")}
                elif any(k in op for k in ("store", "save", "remember")):
                    intent_data = {"intent": "store", "target_entities": [agent_task.target_entity] if agent_task.target_entity else []}
                else:
                    intent_data = await self._route_memory_intent(message, entities)
            else:
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
                raw_target = intent_data.get("target_entities") or []
                store_text = raw_target[0] if raw_target else message
                clean_store_text = re.sub(r"^(?:ye\s+)?(?:yaad\s+(?:rakho|karo)|remember\s+that|save|store|note\s+down)\s+(?:ki\s+)?", "", store_text, flags=re.IGNORECASE).strip()
                if not clean_store_text:
                    clean_store_text = message
                if self.memory and hasattr(self.memory, "save_turn"):
                    try:
                        if hasattr(self.memory, "resolve_contradictions"):
                            superseded = await self.memory.resolve_contradictions(clean_store_text)
                            if superseded > 0:
                                logger.info("[memory] Auto-superseded %d contradictory memories for new fact", superseded)
                        await self.memory.save_turn(clean_store_text, role="user")
                        await self.semantic_cache.clear()
                    except Exception as e:
                        logger.warning("[memory] Failed to persist memory turn: %s", e)
                response = f"Got it. I will remember that: {clean_store_text}."
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
            if isinstance(raw, str):
                parsed = self.ai_handler.try_parse_json(raw) if (self.ai_handler and hasattr(self.ai_handler, "try_parse_json")) else None
                if parsed is None:
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = []
            else:
                parsed = raw
            results = parsed if isinstance(parsed, list) else [parsed]
            return [self._normalize_memory_row(r) for r in results if isinstance(r, dict)]
        except Exception as e:
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
        if not entities:
            return []

        # 1. Direct EternalMemory Digital Twin Knowledge Graph traversal
        if self.memory and hasattr(self.memory, "find_related_entities"):
            all_assoc: list[dict[str, Any]] = []
            for ent in entities:
                try:
                    rel = await self.memory.find_related_entities(ent, max_depth=2)
                    if rel:
                        all_assoc.extend(rel)
                except Exception as ge:
                    logger.debug("[memory] Digital Twin graph traversal error for '%s': %s", ent, ge)
            return all_assoc

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

        prompt = MEMORY_SYNTHESIS_PROMPT.format(
            memories=mem_text, 
            associations=assoc_text, 
            message=message
        )
        
        # Inject learned persona summary if available
        # TODO: ReflexionEngine will handle this

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

        if not self.tool_registry and not self.memory and not hasattr(self, "_TOOL_MAP"):
            return "Memory deletion tools are currently unavailable."

        try:
            # Execute cascading delete (Vector + Graph) — graph tools may not exist
            tool_calls = [self._use_tool("memory_forget", entity=target_str, cascade=True)]
            if self.tool_registry and hasattr(self.tool_registry, "has_tool") and self.tool_registry.has_tool("graph_delete"):
                tool_calls.append(self._use_tool("graph_delete", entities=targets, cascade=True))
            
            results = await asyncio.gather(*tool_calls, return_exceptions=True)
            
            failures = [r for r in results if isinstance(r, Exception) or (isinstance(r, str) and "error" in r.lower())]
            
            # Invalidate cache to prevent stale recalls
            await self.semantic_cache.invalidate_matching(target_str)
            
            if failures:
                logger.warning("[memory] Partial forget failure: %s", failures)
                return f"I have initiated the forgetting process for '{target_str}', but some associations might take time to fully purge from the graph."
                
            return f"Done. I have completely erased my memories and graph associations regarding '{target_str}'."
            
        except Exception as e:
            logger.error("[memory] Forget execution failed: %s", e)
            return f"I tried to forget '{target_str}', but encountered a system error: {str(e)[:50]}"

    async def _trigger_consolidation(self, task_id: str, context: dict) -> str:
        if not self._consolidation_lock.locked():
            t = asyncio.create_task(self._run_background_consolidation(task_id))
            self._background_tasks.add(t)
            t.add_done_callback(self._background_tasks.discard)
            return "I've initiated a background memory consolidation process. I'll summarize and compress my long-term retention graphs shortly."
        return "Memory consolidation is already in progress. Please wait a moment."

    async def _run_background_consolidation(self, task_id: str) -> None:
        async with self._consolidation_lock:
            try:
                logger.info("[memory] Starting background consolidation...")
                old_memories: list[dict[str, Any]] = []

                if self.memory is not None and hasattr(self.memory, "get_history"):
                    old_memories = await self.memory.get_history(n=50)
                elif self.tool_registry and self.tool_registry.has_tool("memory_fetch_oldest"):
                    raw = await self._use_tool("memory_fetch_oldest", limit=50)
                    if isinstance(raw, str):
                        parsed = self.ai_handler.try_parse_json(raw) if (self.ai_handler and hasattr(self.ai_handler, "try_parse_json")) else None
                        if parsed is None:
                            try:
                                parsed = json.loads(raw)
                            except Exception:
                                parsed = []
                    else:
                        parsed = raw or []
                    old_memories = parsed if isinstance(parsed, list) else []

                if not old_memories or not isinstance(old_memories, list):
                    logger.info("[memory] No old memories to consolidate.")
                    return

                # Chunk and summarize
                chunk_text = "\n".join([str(m.get('message') or m.get('content') or '') for m in old_memories[:20]])
                if not chunk_text.strip():
                    return

                summary_prompt = f"Summarize the following historical memories into a single, dense, factual paragraph preserving key entities and dates:\n\n{chunk_text}"

                summary = await self._llm_call(
                    [{"role": "user", "content": summary_prompt}],
                    task="consolidation", temperature=0.1, max_tokens=500
                )

                if summary and self.memory is not None and hasattr(self.memory, "save_turn"):
                    await self.memory.save_turn(f"[Consolidated Memory] {summary}", role="assistant")

                # Prune consolidated historical memories to keep DB lean
                ids_to_prune = [m.get('id') for m in old_memories[:20] if m.get('id')]
                if ids_to_prune and self.memory is not None and hasattr(self.memory, "delete_memory"):
                    for mid in ids_to_prune:
                        try:
                            await self.memory.delete_memory(mid)
                        except Exception:
                            pass

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
