"""
Makima OS — Long-Term Memory Tools
Location: apps/brain/tools/memory_tools.py

Restores and standardizes Makima's EternalMemory subsystem tools
(formerly in MemoryAgent: memory_store, memory_search, memory_forget, memory_fetch_oldest),
exposing them to ToolRegistry and OpenAI Agents SDK FunctionTools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from .types import Tool, ToolDefinition, ToolPolicy

logger = logging.getLogger("makima.tools.memory")

_services: Optional[Any] = None
_memory_instance: Optional[Any] = None
_mem_lock: Optional[asyncio.Lock] = None


def _get_mem_lock() -> asyncio.Lock:
    global _mem_lock
    if _mem_lock is None:
        _mem_lock = asyncio.Lock()
    return _mem_lock


def set_memory_service(services: Any) -> None:
    """Set the service container reference for memory tool resolution."""
    global _services, _memory_instance
    _services = services
    if services is not None:
        mem = services.get("memory") or services.get("eternal_memory")
        if mem is not None:
            _memory_instance = mem


async def _get_memory() -> Any:
    """Resolve or lazily initialize the EternalMemory instance."""
    global _memory_instance
    if _memory_instance is not None:
        return _memory_instance

    lock = _get_mem_lock()
    async with lock:
        if _memory_instance is not None:
            return _memory_instance

        if _services is not None:
            mem = _services.get("memory") or _services.get("eternal_memory")
            if mem is not None:
                _memory_instance = mem
                return _memory_instance

        try:
            from ..eternal_memory import EternalMemory
            mem = EternalMemory()
            await mem.start()
            _memory_instance = mem
            logger.info("Initialized fallback EternalMemory instance for memory_tools")
            return _memory_instance
        except Exception as e:
            logger.error("Failed to initialize EternalMemory: %s", e)
            raise


# =============================================================================
# Canonical Memory Agent Tools (Restored from MemoryAgent)
# =============================================================================

async def memory_store(
    content: str = "",
    category: str = "fact",
    importance: float = 0.8,
    auto_resolve: bool = True,
    **kwargs: Any,
) -> str:
    """
    Store a piece of user information, preference, fact, or requirement into long-term memory.
    """
    actual_content = str(
        content or kwargs.get("text") or kwargs.get("fact") or kwargs.get("preference") or kwargs.get("requirement") or ""
    ).strip()
    if not actual_content:
        return "No content provided to store."

    try:
        mem = await _get_memory()
    except Exception as e:
        return f"Memory layer unavailable: {e}"

    try:
        # Strip common natural language prefixes like "ye yaad karo ki", "remember that", etc.
        clean_fact = re.sub(
            r"^(?:ye\s+)?(?:yaad\s+(?:rakho|karo)|remember\s+that|save|store|note\s+down)\s+(?:ki\s+)?",
            "",
            actual_content,
            flags=re.IGNORECASE,
        ).strip()
        if not clean_fact:
            clean_fact = actual_content

        if auto_resolve and hasattr(mem, "resolve_contradictions"):
            superseded = await mem.resolve_contradictions(clean_fact)
            if superseded and superseded > 0:
                logger.info("[memory] Auto-superseded %d contradictory memories for new fact", superseded)

        # Also store to rule index if categorized as rule / instruction
        if category in ("rule", "instruction", "preference") and hasattr(mem, "save_rule"):
            kw = [w.lower() for w in clean_fact.replace(".", " ").replace(",", " ").split() if len(w) > 3][:6]
            await mem.save_rule(clean_fact, keywords=kw)

        await mem.save_turn(clean_fact, role="user")
        if hasattr(mem, "flush"):
            try:
                await mem.flush(timeout=1.0)
            except Exception:
                pass
        return f"Stored successfully: '{clean_fact}'"
    except Exception as e:
        logger.error("[memory] memory_store failed: %s", e)
        return f"Store failed: {e}"


async def memory_search(
    query: str = "",
    k: int = 5,
    **kwargs: Any,
) -> str:
    """
    Search past user memories and conversation history using hybrid semantic vector and BM25 keyword search.
    """
    clean_query = str(query or kwargs.get("text") or kwargs.get("q") or "").strip()
    if not clean_query:
        return "[]"

    try:
        mem = await _get_memory()
    except Exception as e:
        return f"[]"

    try:
        rows = await mem.search(clean_query, k=int(k))
        return json.dumps(rows, default=str)
    except Exception as e:
        logger.error("[memory] memory_search failed: %s", e)
        return "[]"


async def memory_forget(
    entity: str = "",
    query: str = "",
    **kwargs: Any,
) -> str:
    """
    Permanently delete stored conversation memories matching a specific entity, topic, or fact.
    """
    target = str(entity or query or kwargs.get("topic") or kwargs.get("text") or "").strip()
    if not target:
        return "No target entity or query specified to forget."

    try:
        mem = await _get_memory()
    except Exception as e:
        return f"Memory layer unavailable: {e}"

    try:
        count = await mem.delete_matching(target)
        return f"Successfully forgot {count} memories matching '{target}'."
    except Exception as e:
        logger.error("[memory] memory_forget failed: %s", e)
        return f"Forget failed: {e}"


async def memory_fetch_oldest(
    limit: int = 50,
    **kwargs: Any,
) -> str:
    """
    Fetch oldest memories for consolidation, review, or compaction.
    """
    try:
        mem = await _get_memory()
    except Exception as e:
        return "[]"

    try:
        if hasattr(mem, "get_history"):
            rows = await mem.get_history(n=int(limit))
            return json.dumps(rows, default=str)
        return "[]"
    except Exception as e:
        logger.error("[memory] memory_fetch_oldest failed: %s", e)
        return "[]"


# =============================================================================
# Modern Composite & Knowledge Graph Tools
# =============================================================================

async def recall_memory(
    query: str = "",
    limit: int = 5,
    include_rules: bool = True,
    include_triples: bool = True,
    **kwargs: Any,
) -> str:
    """
    High-level memory recall returning a rich composite bundle of vector memories,
    learned rules, and knowledge graph facts.
    """
    clean_query = str(query or kwargs.get("text") or kwargs.get("q") or "").strip()
    if not clean_query:
        return "[Memory Error]: Query string cannot be empty."

    k = max(1, min(int(limit), 25))
    try:
        mem = await _get_memory()
    except Exception as e:
        return f"[Memory Error]: Memory subsystem unavailable: {e}"

    results: Dict[str, Any] = {
        "query": clean_query,
        "memories": [],
        "rules": [],
        "knowledge_graph": [],
    }

    try:
        turns = await mem.search(query=clean_query, k=k)
        if isinstance(turns, list):
            results["memories"] = [
                {
                    "id": t.get("id"),
                    "role": t.get("role", "memory"),
                    "content": t.get("message", ""),
                    "score": round(float(t.get("relevance_score") or t.get("cosine_sim") or 0.0), 3),
                }
                for t in turns
                if isinstance(t, dict)
            ]
    except Exception as e:
        logger.warning("recall_memory search failed: %s", e)

    if include_rules and hasattr(mem, "search_rules"):
        try:
            matched_rules = await mem.search_rules(query=clean_query, top_k=3)
            if isinstance(matched_rules, list):
                results["rules"] = [r for r in matched_rules if isinstance(r, str)]
        except Exception as e:
            logger.warning("recall_memory rule search failed: %s", e)

    if include_triples and hasattr(mem, "query_triples"):
        try:
            triples = await mem.query_triples(subject=clean_query, limit=5)
            if not triples:
                triples = await mem.query_triples(object_val=clean_query, limit=5)
            if isinstance(triples, list):
                results["knowledge_graph"] = [
                    {"subject": tr.get("subject"), "predicate": tr.get("predicate"), "object": tr.get("object")}
                    for tr in triples
                    if isinstance(tr, dict)
                ]
        except Exception as e:
            logger.warning("recall_memory graph query failed: %s", e)

    return json.dumps(results, ensure_ascii=False, indent=2)


async def remember_fact(
    fact: str = "",
    category: str = "fact",
    subject: str = "",
    predicate: str = "",
    object_val: str = "",
    **kwargs: Any,
) -> str:
    """Alias to memory_store with optional knowledge-graph triple extraction."""
    clean_fact = str(fact or kwargs.get("content") or kwargs.get("text") or "").strip()
    res = await memory_store(content=clean_fact, category=category, **kwargs)

    # If triple provided, also persist to knowledge graph
    if subject.strip() and predicate.strip() and object_val.strip():
        try:
            mem = await _get_memory()
            if hasattr(mem, "add_triple"):
                await mem.add_triple(
                    subject=subject.strip(),
                    predicate=predicate.strip(),
                    object_val=object_val.strip(),
                    confidence=1.0,
                    source="remember_fact_tool",
                )
        except Exception as e:
            logger.warning("remember_fact triple addition failed: %s", e)

    return res


async def query_knowledge_graph(
    entity: str = "",
    max_depth: int = 2,
    **kwargs: Any,
) -> str:
    """Traverse Makima's knowledge graph to find related entities and relationships."""
    clean_entity = str(entity or kwargs.get("name") or "").strip()
    if not clean_entity:
        return "[Memory Error]: Entity name cannot be empty."

    depth = max(1, min(int(max_depth), 4))
    try:
        mem = await _get_memory()
    except Exception as e:
        return f"[Memory Error]: Memory subsystem unavailable: {e}"

    connections: List[Dict[str, Any]] = []
    if hasattr(mem, "find_related_entities"):
        try:
            related = await mem.find_related_entities(entity=clean_entity, max_depth=depth)
            if isinstance(related, list):
                connections.extend(related)
        except Exception as e:
            logger.warning("find_related_entities failed: %s", e)

    if hasattr(mem, "query_triples"):
        try:
            sub_triples = await mem.query_triples(subject=clean_entity, limit=25)
            obj_triples = await mem.query_triples(object_val=clean_entity, limit=25)
            existing_pairs = {(c.get("subject"), c.get("predicate"), c.get("object")) for c in connections if isinstance(c, dict)}
            for tr in (sub_triples + obj_triples):
                if isinstance(tr, dict):
                    key = (tr.get("subject"), tr.get("predicate"), tr.get("object"))
                    if key not in existing_pairs:
                        existing_pairs.add(key)
                        connections.append(tr)
        except Exception as e:
            logger.warning("query_triples failed: %s", e)

    return json.dumps({"entity": clean_entity, "depth": depth, "relationships": connections}, ensure_ascii=False, indent=2)


# =============================================================================
# Registration Hook
# =============================================================================

def register_memory_tools(tool_registry: Any, services: Any = None) -> None:
    """Register all memory tools into Makima's ToolRegistry."""
    if not tool_registry:
        logger.warning("register_memory_tools: tool_registry is None — skipping")
        return

    if services is not None:
        set_memory_service(services)

    tool_specs: List[Dict[str, Any]] = [
        # Original canonical tools
        {
            "name": "memory_store",
            "func": memory_store,
            "description": "Store a piece of user information, preference, fact, or requirement into long-term memory.",
            "schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The exact fact, preference, or requirement to store"},
                    "category": {"type": "string", "description": "fact | preference | requirement | context", "default": "fact"},
                    "importance": {"type": "number", "description": "Importance score between 0.0 and 1.0", "default": 0.8},
                },
                "required": ["content"],
            },
            "category": "memory",
            "priority": 2,
            "is_destructive": False,
        },
        {
            "name": "memory_search",
            "func": memory_search,
            "description": "Search past user memories and conversation history using hybrid semantic vector and BM25 keyword search.",
            "schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query or entity to look up"},
                    "k": {"type": "integer", "description": "Max memory records to retrieve (default 5)", "default": 5},
                },
                "required": ["query"],
            },
            "category": "memory",
            "priority": 2,
            "is_destructive": False,
        },
        {
            "name": "memory_forget",
            "func": memory_forget,
            "description": "Permanently delete stored conversation memories matching a specific entity, topic, or fact.",
            "schema": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "The entity, topic, or fact to delete"},
                    "query": {"type": "string", "description": "Alternative query string to match for deletion"},
                },
                "required": [],
            },
            "category": "memory",
            "priority": 4,
            "is_destructive": True,
        },
        {
            "name": "memory_fetch_oldest",
            "func": memory_fetch_oldest,
            "description": "Fetch oldest historical memories for consolidation or inspection.",
            "schema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of memories to fetch", "default": 50},
                },
            },
            "category": "memory",
            "priority": 4,
            "is_destructive": False,
        },
        # High-level convenience aliases
        {
            "name": "recall_memory",
            "func": recall_memory,
            "description": "Search Makima's long-term memory for past conversations, user preferences, and knowledge graph facts.",
            "schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query or question"},
                    "limit": {"type": "integer", "description": "Max number of memory turns to return (default 5)"},
                },
                "required": ["query"],
            },
            "category": "memory",
            "priority": 2,
            "is_destructive": False,
        },
        {
            "name": "remember_fact",
            "func": remember_fact,
            "description": "Save a permanent fact, user preference, instruction, or knowledge relationship into long-term memory.",
            "schema": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact or preference to remember permanently"},
                    "category": {"type": "string", "description": "Category: 'fact', 'preference', 'rule'"},
                },
                "required": ["fact"],
            },
            "category": "memory",
            "priority": 2,
            "is_destructive": False,
        },
        {
            "name": "query_knowledge_graph",
            "func": query_knowledge_graph,
            "description": "Explore connections, attributes, and relationships for a specific entity in Makima's knowledge graph.",
            "schema": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "Entity name to inspect"},
                    "max_depth": {"type": "integer", "description": "Graph traversal depth (default 2)"},
                },
                "required": ["entity"],
            },
            "category": "memory",
            "priority": 3,
            "is_destructive": False,
        },
    ]

    registered_count = 0
    for spec in tool_specs:
        definition = ToolDefinition(
            name=spec["name"],
            description=spec["description"],
            parameters=spec["schema"],
        )
        policy = ToolPolicy(
            timeout_s=30.0,
            max_retries=1,
        )
        tool_obj = Tool(
            definition=definition,
            handler=spec["func"],
            policy=policy,
            category=spec.get("category", "memory"),
            priority=spec.get("priority", 3),
            is_destructive=spec.get("is_destructive", False),
        )

        if hasattr(tool_registry, "register"):
            tool_registry.register(tool_obj)
            registered_count += 1
        elif hasattr(tool_registry, "register_tool_obj"):
            tool_registry.register_tool_obj(tool_obj)
            registered_count += 1
        elif hasattr(tool_registry, "register_tool"):
            tool_registry.register_tool(
                name=spec["name"],
                description=spec["description"],
                func=spec["func"],
                schema=spec["schema"],
                category=spec.get("category", "memory"),
                priority=spec.get("priority", 3),
                is_destructive=spec.get("is_destructive", False),
            )
            registered_count += 1
        else:
            tool_registry[spec["name"]] = spec["func"]
            registered_count += 1

    logger.info("Successfully registered %d memory tools in ToolRegistry", registered_count)


__all__ = [
    "memory_store",
    "memory_search",
    "memory_forget",
    "memory_fetch_oldest",
    "recall_memory",
    "remember_fact",
    "query_knowledge_graph",
    "register_memory_tools",
    "set_memory_service",
]
