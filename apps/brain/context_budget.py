"""Makima v7.1 — ContextBudgetAllocator

Spec:
- Backend-aware token allocation
- Splits: history 40%, memory 25%, graph 15%, screen 10%, system 10%
- Never drop system slot (personality + tool manifest)
- Rank_and_trim drops lowest until within budget

This snapshot provides:
- allocate(backend, attached_files_tokens)
- rank_and_trim(snippets, budget)

Snippets must have: text (or content) and relevance_score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


BACKEND_CONTEXT_LIMITS = {
    "groq_llama_70b": 131072,
    "gemini_2_pro": 1000000,
    "claude_openrouter": 200000,
    "gpt4o": 128000,
    "ollama_local": 8192,
    "cerebras": 131072,
}

BUDGET_SPLITS = {
    "history": 0.40,
    "memory": 0.25,
    "graph": 0.15,
    "screen": 0.10,
    "system": 0.10,
}


@dataclass
class Snippet:
    text: str
    relevance_score: float = 0.0
    slot: str = "memory"


class ContextBudgetAllocator:
    def allocate(self, backend: str, attached_files_tokens: int = 0) -> Dict[str, int]:
        limit = BACKEND_CONTEXT_LIMITS.get(backend, 128000)
        usable = int(limit * 0.80) - int(attached_files_tokens)
        usable = max(0, usable)
        return {k: int(usable * v) for k, v in BUDGET_SPLITS.items()}

    def rank_and_trim(self, snippets: List[Snippet], budget: Dict[str, int]) -> List[Snippet]:
        # Group by slot and keep system intact.
        keep: List[Snippet] = []

        def approx_tokens(s: Snippet) -> int:
            # crude token estimator (works for budgeting, not exact)
            return max(1, int(len(s.text) / 4))

        # Never drop system slot snippets
        system_snips = [s for s in snippets if s.slot == "system"]
        keep.extend(sorted(system_snips, key=lambda x: x.relevance_score, reverse=True))

        for slot in ("history", "memory", "graph", "screen"):
            slot_snips = [s for s in snippets if s.slot == slot]
            slot_snips = sorted(slot_snips, key=lambda x: x.relevance_score, reverse=True)

            total = 0
            for s in slot_snips:
                t = approx_tokens(s)
                if total + t > budget.get(slot, 0):
                    break
                keep.append(s)
                total += t

        return keep

