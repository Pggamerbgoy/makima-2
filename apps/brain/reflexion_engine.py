"""Makima v9.0 — ReflexionEngine
Based on: Shinn et al. 2023 "Reflexion: Language Agents with Verbal Reinforcement Learning" (arXiv:2303.11366)

Self-reflective failure critique engine. Evaluates agent execution failures,
synthesizes concrete verbal lessons via fast LLM critique extraction, embeds the lessons
into semantic vectors, and dynamically retrieves relevant lessons at runtime to prevent
repeated agent failures.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional

import numpy as np

logger = logging.getLogger("reflexion_engine")

__all__ = ["ReflexionEngine"]


class ReflexionEngine:
    """
    Reflexion Engine for language agents with verbal reinforcement learning.
    Stores and retrieves failure critiques and actionable lessons using semantic vector search.
    """

    def __init__(self, eternal_memory: Any = None, ai_handler: Any = None) -> None:
        self.eternal_memory = eternal_memory
        self.ai_handler = ai_handler

        # Resolve DB path from eternal_memory or use default
        if isinstance(eternal_memory, (str, Path)):
            self.db_path = Path(eternal_memory)
        elif hasattr(eternal_memory, "db_path") and eternal_memory.db_path:
            self.db_path = Path(eternal_memory.db_path)
        else:
            self.db_path = Path.home() / ".makima" / "eternal_memory.db"

        # In-memory numpy embedding cache for sub-millisecond retrieval
        self._trace_ids: list[str] = []
        self._critiques: list[str] = []
        self._domains: list[str] = []
        self._embeddings_matrix: Optional[np.ndarray] = None  # Shape: (N, dim), normalized
        self._background_tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._initialized = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Create reflexion_traces table if not exists and load embeddings into cache."""
        async with self._lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._sync_init_db_and_load_cache)
            self._initialized = True
            logger.info(
                "ReflexionEngine started: %d traces loaded into in-memory cache (db=%s)",
                len(self._trace_ids),
                self.db_path,
            )

    def _sync_init_db_and_load_cache(self) -> None:
        """Synchronous SQLite table creation and initial cache population."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflexion_traces (
                    id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    task_pattern TEXT NOT NULL,
                    action_taken TEXT NOT NULL,
                    error_observed TEXT NOT NULL,
                    critique TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    avoided_count INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_applied REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reflexion_domain ON reflexion_traces(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reflexion_hit ON reflexion_traces(hit_count)")
            conn.commit()

            # Load existing traces into memory cache
            cursor = conn.cursor()
            cursor.execute("SELECT id, domain, critique, embedding FROM reflexion_traces")
            rows = cursor.fetchall()

            trace_ids: list[str] = []
            critiques: list[str] = []
            domains: list[str] = []
            vectors: list[np.ndarray] = []

            for row in rows:
                t_id, domain, crit, blob = row
                try:
                    vec = np.frombuffer(blob, dtype=np.float32)
                    norm = float(np.linalg.norm(vec))
                    if norm > 0:
                        vec = vec / norm
                    trace_ids.append(t_id)
                    domains.append(domain)
                    critiques.append(crit)
                    vectors.append(vec)
                except Exception as load_err:
                    logger.warning("Failed to unpack embedding for trace %s: %s", t_id, load_err)

            self._trace_ids = trace_ids
            self._domains = domains
            self._critiques = critiques
            if vectors:
                self._embeddings_matrix = np.vstack(vectors)
            else:
                self._embeddings_matrix = None

    # ── Embedding Helper ──────────────────────────────────────────────────────

    async def _embed_text(self, text: str) -> np.ndarray:
        """Embed text using eternal_memory encoder, local FastEmbed, or normalized fallback."""
        if not text:
            return np.zeros(384, dtype=np.float32)

        # 1. Try eternal_memory's embedding provider
        embed_provider = getattr(self.eternal_memory, "_embeddings", None) or getattr(
            self.eternal_memory, "embeddings", None
        )
        if embed_provider is not None and hasattr(embed_provider, "embed_one"):
            try:
                res = embed_provider.embed_one(text)
                if inspect_vec(res):
                    return normalize_vec(res)
            except Exception as e:
                logger.debug("EternalMemory embed_one error: %s", e)

        # 2. Try standalone EmbeddingProvider if available
        try:
            from .embeddings import EmbeddingProvider

            local_provider = EmbeddingProvider()
            if local_provider.available:
                res = local_provider.embed_one(text)
                if inspect_vec(res):
                    return normalize_vec(res)
        except Exception:
            pass

        # 3. Deterministic pseudo-embedding fallback (ensures hermetic unit tests & zero crash)
        return deterministic_text_embedding(text, dim=384)

    # ── Core Reflexion Methods ────────────────────────────────────────────────

    async def store_reflection(
        self,
        task: str = "",
        action: str = "",
        error: str = "",
        domain: str = "general",
        **kwargs: Any,
    ) -> Optional[str]:
        """
        Synthesize concrete failure critique, compute embedding, persist trace, and update cache.
        """
        # Accommodate legacy / alternate parameter naming gracefully
        task_str = str(task or kwargs.get("task_pattern") or kwargs.get("task_description") or "").strip()
        action_str = str(action or kwargs.get("action_taken") or kwargs.get("trajectory") or "").strip()
        error_str = str(error or kwargs.get("error_observed") or kwargs.get("error_text") or "").strip()
        domain_str = str(domain or kwargs.get("category") or "general").strip()

        if not task_str and not action_str and not error_str:
            logger.warning("ReflexionEngine.store_reflection called with empty parameters")
            return None

        # 1. Call ai_handler with fast model (temp=0.2) to synthesize actionable critique
        prompt = (
            "In one or two sentences, what concrete lesson should an AI agent learn from this failure?\n"
            f"Task: {task_str}\n"
            f"Action taken: {action_str}\n"
            f"Error: {error_str}\n"
            "Lesson:"
        )

        critique = ""
        if self.ai_handler and hasattr(self.ai_handler, "generate"):
            try:
                response = await self.ai_handler.generate(
                    messages=[{"role": "user", "content": prompt}],
                    task="fast",
                    temperature=0.2,
                    max_tokens=120,
                )
                if response and getattr(response, "text", None):
                    critique = response.text.strip()
                    if critique.lower().startswith("lesson:"):
                        critique = critique[7:].strip()
            except Exception as gen_err:
                logger.debug("ReflexionEngine LLM critique synthesis fallback: %s", gen_err)

        if not critique:
            critique = f"When doing {task_str or 'this task'}, avoid {action_str or 'the failing action'} because {error_str or 'it resulted in an error'}."

        # 2. Embed critique via FastEmbed ONNX encoder or deterministic fallback
        vec = await self._embed_text(f"{task_str} {critique}")
        blob = vec.astype(np.float32).tobytes()

        # 3. Store in reflexion_traces table
        reflection_id = f"ref_{uuid.uuid4().hex[:12]}"
        now = time.time()

        def _sync_insert() -> None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.execute(
                    """
                    INSERT INTO reflexion_traces (
                        id, domain, task_pattern, action_taken, error_observed,
                        critique, embedding, hit_count, avoided_count, created_at, last_applied
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        reflection_id,
                        domain_str,
                        task_str,
                        action_str,
                        error_str,
                        critique,
                        blob,
                        now,
                        now,
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_sync_insert)

        # 4. Update in-memory cache
        async with self._lock:
            self._trace_ids.append(reflection_id)
            self._domains.append(domain_str)
            self._critiques.append(critique)
            vec_2d = vec.reshape(1, -1)
            if self._embeddings_matrix is None:
                self._embeddings_matrix = vec_2d
            else:
                self._embeddings_matrix = np.vstack([self._embeddings_matrix, vec_2d])

        logger.info("Stored reflection %s", reflection_id)
        return reflection_id

    async def record_failure(
        self,
        task: str = "",
        action: str = "",
        error: str = "",
        domain: str = "general",
        **kwargs: Any,
    ) -> Optional[str]:
        """Convenience alias for store_reflection."""
        return await self.store_reflection(task=task, action=action, error=error, domain=domain, **kwargs)

    async def get_relevant_reflections(
        self,
        task_description: str,
        top_k: int = 3,
        threshold: float = 0.65,
        **kwargs: Any,
    ) -> List[str]:
        """
        Embed task_description, compute cosine similarity against cached embeddings,
        and return top_k critiques above threshold as formatted strings:
        'Lesson 1: When doing X, avoid Y because Z.'
        """
        if not task_description or not isinstance(task_description, str):
            return []

        async with self._lock:
            if not self._initialized:
                await asyncio.to_thread(self._sync_init_db_and_load_cache)
                self._initialized = True

            if self._embeddings_matrix is None or len(self._trace_ids) == 0:
                return []

            # Snapshot references under lock
            emb_matrix = self._embeddings_matrix
            critiques = list(self._critiques)

        # 1. Embed task_description
        query_vec = await self._embed_text(task_description)
        norm = float(np.linalg.norm(query_vec))
        if norm <= 0:
            return []
        query_vec = query_vec / norm

        # 2. Cosine similarity against cached embeddings (dot product of L2-normalized vectors)
        try:
            # Handle dimension mismatch if DB had embeddings from another model/dim
            if emb_matrix.shape[1] != query_vec.shape[0]:
                logger.warning(
                    "ReflexionEngine dimension mismatch: matrix=%d, query=%d",
                    emb_matrix.shape[1],
                    query_vec.shape[0],
                )
                return []

            similarities = np.dot(emb_matrix, query_vec)
        except Exception as sim_err:
            logger.error("ReflexionEngine cosine similarity calculation error: %s", sim_err)
            return []

        # 3. Filter by threshold and take top_k
        above_threshold_indices = [
            int(idx) for idx, sim in enumerate(similarities) if sim >= threshold
        ]
        if not above_threshold_indices:
            return []

        # Sort indices by similarity descending
        above_threshold_indices.sort(key=lambda idx: float(similarities[idx]), reverse=True)
        top_indices = above_threshold_indices[:top_k]

        # 4. Return top_k critiques formatted as 'Lesson N: ...'
        results: list[str] = []
        for i, idx in enumerate(top_indices, start=1):
            crit = critiques[idx].strip()
            if crit.lower().startswith("lesson"):
                # Clean up if already begins with Lesson
                crit_clean = crit.split(":", 1)[-1].strip() if ":" in crit[:12] else crit
                results.append(f"Lesson {i}: {crit_clean}")
            else:
                results.append(f"Lesson {i}: {crit}")

        return results

    async def record_reflection_outcome(self, reflection_id: str, succeeded: bool) -> None:
        """
        Record the outcome when a reflection was applied:
        - If succeeded: increment avoided_count
        - Always: increment hit_count, update last_applied
        """
        if not reflection_id:
            return

        now = time.time()
        avoid_inc = 1 if succeeded else 0

        def _sync_update() -> None:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.execute(
                    """
                    UPDATE reflexion_traces
                    SET hit_count = hit_count + 1,
                        avoided_count = avoided_count + ?,
                        last_applied = ?
                    WHERE id = ?
                    """,
                    (avoid_inc, now, reflection_id),
                )
                conn.commit()

        await asyncio.to_thread(_sync_update)

    async def clear_old_reflections(self, days: int = 30) -> int:
        """
        Delete rows where hit_count == 0 AND age > days.
        Reload in-memory cache and return count deleted.
        """
        cutoff = time.time() - (float(days) * 86400.0)

        def _sync_delete() -> int:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM reflexion_traces
                    WHERE hit_count == 0 AND created_at < ?
                    """,
                    (cutoff,),
                )
                deleted_count = cursor.rowcount
                conn.commit()
                return int(deleted_count)

        deleted = await asyncio.to_thread(_sync_delete)

        # Synchronize in-memory cache
        async with self._lock:
            await asyncio.to_thread(self._sync_init_db_and_load_cache)

        logger.info("ReflexionEngine: cleared %d stale zero-hit reflections", deleted)
        return deleted

    # ── Signal Ingestion Adapters (Unified Feedback Gateway) ──────────────────

    def _spawn_tracked(self, coro: Any) -> asyncio.Task:
        """Spawn background task with strong reference tracking to prevent GC."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def on_negative_feedback(
        self,
        user_message: str,
        agent_response: str = "",
        agent_name: str = "unknown",
        **kwargs: Any,
    ) -> None:
        """Called on thumbs-down or explicit negative user feedback ('galat hai / wrong')."""
        self._spawn_tracked(
            self.store_reflection(
                task=user_message,
                action=f"Agent '{agent_name}' generated: {agent_response[:300]}",
                error=kwargs.get("error") or "User signaled unsatisfactory response / negative feedback",
                domain=kwargs.get("domain", "conversational"),
            )
        )

    async def on_routing_correction(
        self,
        user_message: str,
        wrong_intent: str,
        correct_intent: str,
        **kwargs: Any,
    ) -> None:
        """Called when the user corrects which agent or intent ran."""
        self._spawn_tracked(
            self.store_reflection(
                task=user_message,
                action=f"Routed to '{wrong_intent}'",
                error=f"User corrected route: should be '{correct_intent}'",
                domain="routing",
            )
        )

    async def on_tool_failure(
        self,
        agent_name: str,
        tool_name: str,
        params: Any = None,
        error: str = "",
        user_message: str = "",
        **kwargs: Any,
    ) -> None:
        """Called when a tool execution fails repeatedly or permanently."""
        self._spawn_tracked(
            self.store_reflection(
                task=user_message or f"Execute tool '{tool_name}'",
                action=f"{tool_name}(params={params})",
                error=str(error or "Execution failed"),
                domain=kwargs.get("domain", "tool"),
            )
        )

    async def on_agent_failure(
        self,
        agent_name: str,
        user_message: str = "",
        error_text: str = "",
        **kwargs: Any,
    ) -> None:
        """Called on high-level agent failure/crash."""
        self._spawn_tracked(
            self.store_reflection(
                task=user_message or f"Agent '{agent_name}' task",
                action=f"Agent '{agent_name}' execution",
                error=str(error_text or "Agent failed"),
                domain=kwargs.get("domain", "agent"),
            )
        )

    async def enqueue_signal(self, signal: dict[str, Any]) -> bool:
        """Generic signal receiver for backward compatibility with older pipeline callers."""
        if not isinstance(signal, dict):
            return False
        # Do not reflect on successful executions
        if signal.get("is_success") is True or signal.get("signal_type") == "tool_success" or signal.get("type") == "tool_success":
            return True

        sig_type = signal.get("type") or signal.get("signal_type") or "general"
        task = signal.get("task") or signal.get("user_message") or f"Execute tool '{signal.get('tool_name', '')}'"
        action = signal.get("action") or f"{signal.get('tool_name', '')}(params={signal.get('params', {})})"
        error = signal.get("error") or signal.get("error_text") or signal.get("what_went_wrong") or signal.get("rule_text", "Execution failed")
        domain = signal.get("domain") or signal.get("category") or signal.get("agent_name") or "tool"

        self._spawn_tracked(
            self.store_reflection(
                task=str(task),
                action=str(action),
                error=str(error),
                domain=str(domain),
            )
        )
        return True

    enqueue_learning_signal = enqueue_signal
    record_signal = enqueue_signal


# ── Internal Vector Utilities ─────────────────────────────────────────────────

def inspect_vec(v: Any) -> bool:
    """Check if value is a valid 1D numpy array."""
    return isinstance(v, np.ndarray) and v.ndim == 1 and v.size > 0


def normalize_vec(v: np.ndarray) -> np.ndarray:
    """L2 normalize vector."""
    v_f = v.astype(np.float32)
    norm = float(np.linalg.norm(v_f))
    return v_f / norm if norm > 0 else v_f


def deterministic_text_embedding(text: str, dim: int = 384) -> np.ndarray:
    """
    Deterministic pseudo-embedding for testing and zero-crash fallback.
    Produces repeatable normalized vectors from text tokens.
    """
    vec = np.zeros(dim, dtype=np.float32)
    words = text.lower().strip().split()
    if not words:
        return vec

    for word in words:
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        for b_idx in range(0, min(len(digest), 32), 4):
            val = int.from_bytes(digest[b_idx : b_idx + 4], "little")
            dim_idx = val % dim
            sign = 1.0 if ((val >> 16) & 1) else -1.0
            vec[dim_idx] += sign

    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec
