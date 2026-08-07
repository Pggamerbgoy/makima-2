"""Makima v7.1 — ConversationSummarizer

Generates concise summaries of conversations using the LLM.
Supports per-turn summaries, full conversation summaries,
and sliding-window summaries for long conversations.

Spec:
- summarize_conversation(messages, max_words) -> str
- summarize_recent(history, n_turns, max_words) -> str
- auto_summary on conversation end (optional)
- Caches summaries in SQLite to avoid re-summarization
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("makima.conversation_summarizer")

DEFAULT_MAX_WORDS = 150


class ConversationSummarizer:
    def __init__(self, ai_handler=None, config: dict[str, Any] | None = None, ws_broadcast=None):
        self.ai_handler = ai_handler
        self.ws_broadcast = ws_broadcast
        cfg = config or {}
        sum_cfg = cfg.get("summarizer", {}) if isinstance(cfg, dict) else {}
        base_path = os.path.expanduser(sum_cfg.get("db_path", "~/.makima/summaries.sqlite"))
        self.db_path = Path(base_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        self.max_words = int(sum_cfg.get("max_words", DEFAULT_MAX_WORDS))

    async def start(self) -> None:
        await self._ensure_db()
        logger.info("ConversationSummarizer started")

    async def stop(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    async def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                conversation_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                turn_count INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.commit()
        self._conn = conn

    async def summarize_conversation(
        self,
        messages: list[dict[str, str]],
        conversation_id: str | None = None,
        max_words: int | None = None,
    ) -> str:
        if not messages:
            return "No messages to summarize."

        max_w = max_words or self.max_words

        # Build conversation text
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", msg.get("message", ""))
            if content:
                lines.append(f"{role}: {content}")
        full_text = "\n".join(lines)

        if not full_text.strip():
            return "Empty conversation."

        # Try LLM summarization
        summary = await self._llm_summarize(full_text, max_w)
        if not summary:
            # Fallback: extractive summary
            summary = self._extractive_summary(full_text, max_w)

        # Cache if conversation_id provided
        if conversation_id and summary:
            await self._cache_summary(conversation_id, summary, len(messages))

        return summary

    async def summarize_recent(
        self,
        history: list[dict[str, Any]],
        n_turns: int = 10,
        max_words: int | None = None,
    ) -> str:
        recent = history[-n_turns:] if len(history) > n_turns else history
        messages = []
        for h in recent:
            messages.append({
                "role": h.get("role", "unknown"),
                "content": h.get("content", h.get("message", "")),
            })
        return await self.summarize_conversation(messages, max_words=max_words)

    async def get_cached_summary(self, conversation_id: str) -> str | None:
        if not self._conn:
            return None
        try:
            row = self._conn.execute(
                "SELECT summary FROM summaries WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            return row["summary"] if row else None
        except Exception as e:
            logger.error("get_cached_summary failed: %s", e)
            return None

    async def _llm_summarize(self, text: str, max_words: int) -> str:
        if not self.ai_handler:
            return ""

        prompt = (
            f"Summarize the following conversation in {max_words} words or fewer. "
            f"Be concise and capture key topics, decisions, and action items.\n\n"
            f"{text}"
        )

        try:
            response = await self.ai_handler.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_words * 2,
                temperature=0.3,
            )
            if response and isinstance(response, dict):
                content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip() if content else ""
            elif response and isinstance(response, str):
                return response.strip()
            return ""
        except Exception as e:
            logger.warning("LLM summarization failed: %s", e)
            return ""

    def _extractive_summary(self, text: str, max_words: int) -> str:
        sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
        if not sentences:
            return text[:max_words * 6]

        result = []
        word_count = 0
        for sentence in sentences:
            words = sentence.split()
            if word_count + len(words) > max_words:
                break
            result.append(sentence.strip())
            word_count += len(words)

        return ". ".join(result) + ("." if result else "")

    async def _cache_summary(self, conversation_id: str, summary: str, turn_count: int) -> None:
        if not self._conn:
            return
        now = time.time()
        try:
            self._conn.execute(
                """INSERT OR REPLACE INTO summaries(conversation_id, summary, turn_count, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?)""",
                (conversation_id, summary, turn_count, now, now),
            )
            self._conn.commit()
        except Exception as e:
            logger.error("Cache summary failed: %s", e)
