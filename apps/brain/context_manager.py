"""
Makima OS v7.2 - Elite ContextManager & FAANG-Grade Query Rewriter Engine
Location: apps/brain/context_manager.py

Purpose:
Decoupled Pre-Processor sitting directly IN FRONT of IntentDetector.
Maintains a rolling conversation buffer and rewrites ambiguous/elliptical/pronoun-heavy queries
into standalone, explicit sentences BEFORE passing them to IntentDetector.
This protects Layer 1 Regex Fast-Pass (<5ms) performance while handling complex multi-turn context.
"""

import re
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("makima.brain.context_manager")


@dataclass
class ConversationTurn:
    turn_id: int
    user_query: str
    target_agent: str
    action: str
    extracted_subject: Optional[str] = None
    rewritten_query: Optional[str] = None
    timestamp: float = field(default_factory=asyncio.get_event_loop().time if asyncio._get_running_loop() else 0.0)


class ContextManager:
    """
    Production-grade ContextManager & Query Rewriter Engine.
    Employs a 2-Speed Cascade:
      Speed 1: Instant Regex Pronoun Hot-Swap (0ms)
      Speed 2: Fast Async LLM Query Rewriter (~30-80ms)
    """

    PRONOUN_TRIGGERS = re.compile(
        r"\b(it|that|this|the same|them|the report|the docs|the code|usko|isiko|woh|ye|pichla wala|pichli file|woh gana|us gana|usme|isme)\b",
        re.IGNORECASE
    )

    CONVERSATIONAL_ELLIPSIS_PATTERNS = [
        r"\b(what about|how about|and for|aur|what of|summarize|detail karo)\b",
        r"\b(aur|bhi|phir|dobara|wapas|next)\b"
    ]

    def __init__(self, max_buffer_size: int = 5):
        self.max_buffer_size = max_buffer_size
        self.history: List[Dict[str, Any]] = []

    def record_turn(
        self, 
        user_query: str, 
        target_agent: str, 
        action: str, 
        extracted_subject: Optional[str] = None,
        rewritten_query: Optional[str] = None
    ) -> None:
        """Appends a completed interaction turn to rolling history buffer."""
        turn_data = {
            "turn_id": len(self.history) + 1,
            "user_query": user_query,
            "target_agent": target_agent,
            "action": action,
            "extracted_subject": extracted_subject or self._extract_subject(user_query),
            "rewritten_query": rewritten_query
        }
        self.history.append(turn_data)
        if len(self.history) > self.max_buffer_size:
            self.history.pop(0)

    def _extract_subject(self, query: str) -> Optional[str]:
        """Extracts primary subject noun phrase from query."""
        match = re.search(r"(?:for|about|on|of|search|find|google|open|read|play|build|refactor)\s+([a-zA-Z0-9\._\-\s]{3,35})", query, re.I)
        if match:
            raw = match.group(1).strip()
            return self._clean_subject_phrase(raw)
        return self._clean_subject_phrase(query)

    def _clean_subject_phrase(self, text: str) -> str:
        """Strips search engines and verbs from subject phrase."""
        clean = re.sub(r"^(?:search|find|google|bing|open|look for|get|fetch|read|summarize)\s+", "", text, flags=re.I).strip()
        clean = re.sub(r"^(?:google|bing|duckduckgo)\s+(?:for\s+)?", "", clean, flags=re.I).strip()
        clean = re.sub(r"^(?:for|about|on|of|the|latest)\s+", "", clean, flags=re.I).strip()
        return clean.strip()

    def get_last_known_subject(self) -> Optional[str]:
        """Scans buffer backwards for the most recent active subject."""
        for turn in reversed(self.history):
            subj = turn.get("extracted_subject") or turn.get("user_query")
            if subj:
                clean_subj = self._clean_subject_phrase(str(subj))
                if clean_subj and len(clean_subj) >= 2:
                    return clean_subj
        return None

    async def rewrite_query(
        self, 
        current_query: str, 
        ai_handler: Optional[Any] = None
    ) -> Tuple[str, bool]:
        """
        Rewrites current_query using dialogue history.
        Returns: (rewritten_query, is_rewritten)
        """
        query_words = current_query.strip().split()
        has_pronoun = bool(self.PRONOUN_TRIGGERS.search(current_query))

        # Speed 0: Bypass Check (0ms latency penalty for explicit queries)
        if not has_pronoun and len(query_words) > 4 and not any(re.search(p, current_query, re.I) for p in self.CONVERSATIONAL_ELLIPSIS_PATTERNS):
            return current_query, False

        if not self.history:
            return current_query, False

        # Speed 1: Instant Fast-Path Regex Hot-Swap (0ms)
        last_subject = self.get_last_known_subject()
        if has_pronoun and last_subject:
            pronoun_match = self.PRONOUN_TRIGGERS.search(current_query)
            if pronoun_match:
                pronoun = pronoun_match.group(0)
                fast_rewritten = re.sub(
                    r"\b" + re.escape(pronoun) + r"\b", 
                    last_subject, 
                    current_query, 
                    flags=re.IGNORECASE
                )
                logger.info(f"[ContextManager] Speed 1 Fast-Path Hot-Swap '{pronoun}' -> '{last_subject}' (Rewritten: '{fast_rewritten}')")
                return fast_rewritten, True

        # Speed 2: Deep-Path LLM Query Rewriter (~30-80ms)
        if ai_handler and self.history:
            try:
                history_summary = "\n".join([f"Turn {t['turn_id']}: User='{t['user_query']}' -> Subject='{t.get('extracted_subject')}'" for t in self.history[-3:]])
                prompt = f"""You are the Makima OS Query Rewriter.
Given the chat history, rewrite the user's latest query so it is a standalone, fully understandable sentence.
Replace pronouns (it, that, usko, woh) or ellipsis with the actual subjects they refer to.
Do not answer the query. Output ONLY the rewritten sentence string.

History:
{history_summary}

Current Query: "{current_query}"

Rewritten Query:"""
                messages = [{"role": "user", "content": prompt}]
                if hasattr(ai_handler, "generate"):
                    if asyncio.iscoroutinefunction(ai_handler.generate):
                        llm_out = await ai_handler.generate(messages, task="query_rewriting")
                    else:
                        loop = asyncio.get_running_loop()
                        llm_out = await loop.run_in_executor(None, ai_handler.generate, messages)
                    
                    if isinstance(llm_out, dict):
                        rewritten_str = llm_out.get("content", "").strip()
                    else:
                        rewritten_str = str(llm_out).strip()

                    if rewritten_str and len(rewritten_str) > 3:
                        logger.info(f"[ContextManager] Speed 2 Deep-Path LLM Rewrote '{current_query}' -> '{rewritten_str}'")
                        return rewritten_str, True
            except Exception as e:
                logger.debug(f"[ContextManager] Deep-Path LLM Rewriter fallback: {e}")

        # Fallback to Fast-Path if deep-path LLM unavailable
        if last_subject:
            return f"{current_query} ({last_subject})", True
        return current_query, False
