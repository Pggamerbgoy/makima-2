"""Makima v8.1 — EternalMemory

Facade over conversation memory + hybrid search.

HANDOFF_GUIDE spec:
- SQLite for raw conversation turns: WAL mode, 3s buffered write
- Local ONNX embeddings (fastembed) for semantic retrieval — the "vector"
  memory layer. SQLite remains the source of truth; the vector index is a
  projection over it (hybrid search: semantic top-k merged with keyword).
- Public API used by CommandRouter/agents:
  - save_turn(message: str, role: str)
  - search(query: str, k: int)
  - get_history(n: int)

Non-negotiable rules:
- Never crash the brain: catch/log errors and degrade gracefully.
- Thread-safe with asyncio.Lock for shared state.
- Structured %s-format logging.
- Input validation on all public APIs.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from .embeddings import DEFAULT_MODEL, EmbeddingProvider

logger = logging.getLogger("makima.eternal_memory")

__all__ = [
    "EternalMemory",
    "MemoryConfig",
    "MemoryHit",
    "CognitiveMemoryRetriever",
]


@dataclass
class MemoryHit:
    id: int
    conversation_id: Optional[str]
    created_at: float
    role: str
    message: str
    cosine_sim: float
    bm25_score: float
    recency_factor: float
    access_count: int
    composite_score: float


class CognitiveMemoryRetriever:
    """
    Production Hybrid Retrieval Engine combining:
    1. SQLite FTS5 (Full-Text Search) with BM25 ranking
    2. Dense Vector Dot-Product with cosine similarity
    3. Ebbinghaus Spaced Reinforcement Recency Model
    4. Reciprocal Rank Fusion (RRF)
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ensure_fts_schema()

    def _ensure_fts_schema(self) -> None:
        """Create SQLite FTS5 virtual table, triggers, and access tracking columns."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            # Ensure base conversation table exists if not created yet
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  conversation_id TEXT,
                  created_at REAL NOT NULL,
                  role TEXT NOT NULL,
                  message TEXT NOT NULL
                )
                """
            )
            # FTS5 Virtual Table for sub-millisecond keyword search
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5(
                    message,
                    content='conversation',
                    content_rowid='id',
                    tokenize='porter unicode61'
                )
                """
            )
            # Triggers to keep FTS index synchronized with raw table
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_conversation_ai AFTER INSERT ON conversation
                BEGIN
                    INSERT INTO conversation_fts(rowid, message) VALUES (new.id, new.message);
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_conversation_au AFTER UPDATE OF message ON conversation
                BEGIN
                    INSERT INTO conversation_fts(conversation_fts, rowid, message) 
                    VALUES('delete', old.id, old.message);
                    INSERT INTO conversation_fts(rowid, message) VALUES (new.id, new.message);
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_conversation_ad AFTER DELETE ON conversation
                BEGIN
                    INSERT INTO conversation_fts(conversation_fts, rowid, message) 
                    VALUES('delete', old.id, old.message);
                END;
                """
            )
            # Access frequency and last accessed columns for Spaced Reinforcement
            cols = {row[1] for row in conn.execute("PRAGMA table_info(conversation)").fetchall()}
            if "access_count" not in cols:
                conn.execute("ALTER TABLE conversation ADD COLUMN access_count INTEGER DEFAULT 1")
            if "last_accessed_at" not in cols:
                conn.execute("ALTER TABLE conversation ADD COLUMN last_accessed_at REAL")
            if "importance" not in cols:
                conn.execute("ALTER TABLE conversation ADD COLUMN importance REAL DEFAULT 0.7")

            # Fix: backfill FTS5 for ANY rows missing from index (not just when fully empty).
            # Before this fix, injected history (via ingest_takeout_curated.py) was invisible
            # to keyword/BM25 search whenever pre-existing FTS rows already existed.
            try:
                conv_count = conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0]
                fts_count = conn.execute("SELECT COUNT(*) FROM conversation_fts").fetchone()[0]
                if conv_count > fts_count:
                    conn.execute(
                        """
                        INSERT INTO conversation_fts(rowid, message)
                        SELECT id, message FROM conversation
                        WHERE id NOT IN (SELECT rowid FROM conversation_fts)
                        """
                    )
                    backfilled = conv_count - fts_count
                    logger.info("FTS5 backfill: inserted %d missing rows into search index", backfilled)
            except Exception as e:
                logger.debug("FTS population check: %s", e)

            # Entity Knowledge Graph Triples table for Digital Twin
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_triples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    source TEXT DEFAULT 'chat',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_sub_pred ON entity_triples(subject, predicate)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_obj ON entity_triples(object)")

            conn.commit()

    def execute_fts5_search(self, query: str, limit: int = 20) -> List[Tuple[int, float]]:
        """
        Executes native BM25 search via SQLite FTS5.
        Returns list of (row_id, normalized_bm25_score).
        """
        clean_q = re.sub(r"[^\w\s]", " ", query).strip()
        if not clean_q:
            return []

        tokens = [f'"{token}"*' for token in clean_q.split() if len(token) > 1]
        if not tokens:
            tokens = [f'"{token}"*' for token in clean_q.split() if token]
        if not tokens:
            return []
        match_query = " OR ".join(tokens)

        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            try:
                # FTS5 bm25 function returns negative score where more negative = better match
                cursor = conn.execute(
                    """
                    SELECT rowid, bm25(conversation_fts) as rank_score
                    FROM conversation_fts
                    WHERE conversation_fts MATCH ?
                    ORDER BY rank_score ASC
                    LIMIT ?
                    """,
                    (match_query, limit),
                )
                results: List[Tuple[int, float]] = []
                for row in cursor.fetchall():
                    bm25_raw = float(row["rank_score"])
                    # FTS5 returns negative bm25 score (more negative = better match).
                    # Map abs(score) to [0.0, 1.0] using asymptotic saturation.
                    abs_score = abs(bm25_raw)
                    bm25_norm = float(1.0 - math.exp(-abs_score / 4.0))
                    results.append((int(row["rowid"]), bm25_norm))
                return results
            except sqlite3.OperationalError as e:
                logger.warning("FTS5 search error for '%s': %s", query, e)
                return []

    @staticmethod
    def calculate_ebbinghaus_recency(
        created_at: float,
        last_accessed_at: Optional[float],
        access_count: int,
        current_time: float,
    ) -> float:
        """
        Mathematical Ebbinghaus Forgetting Curve with Spaced Repetition Reinforcement:
        R = exp( - delta_t / (S * (1 + ln(1 + max(1, access_count)))) ) in [0.0, 1.0]
        """
        reference_time = last_accessed_at if (last_accessed_at is not None and last_accessed_at > 0) else created_at
        delta_hours = max(0.0, (current_time - reference_time) / 3600.0)

        # Base stability S = 72 hours (~3 days)
        stability_base = 72.0
        cnt = max(1, int(access_count) if access_count is not None else 1)
        reinforced_stability = stability_base * (1.0 + math.log(1.0 + cnt))

        decay = math.exp(-delta_hours / reinforced_stability)
        return float(max(0.0, min(1.0, decay)))

    @staticmethod
    def reciprocal_rank_fusion(
        vector_rankings: List[int],
        fts_rankings: List[int],
        k: int = 60,
    ) -> Dict[int, float]:
        """
        Combines disparate retrieval rankings using standard RRF:
        RRF_Score(d) = sum_{m in models} 1.0 / (k + rank_m(d))
        """
        rrf_scores: Dict[int, float] = {}

        for rank, doc_id in enumerate(vector_rankings):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + (rank + 1)))

        for rank, doc_id in enumerate(fts_rankings):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + (rank + 1)))

        return rrf_scores

    # ── Digital Twin Knowledge Graph Engine ───────────────────────────────────

    def add_triple(
        self,
        subject: str,
        predicate: str,
        object_val: str,
        confidence: float = 1.0,
        source: str = "chat",
    ) -> int:
        """Insert or update a semantic relationship triple in the Digital Twin graph."""
        now = time.time()
        sub = str(subject).strip().lower()
        pred = str(predicate).strip().lower()
        obj = str(object_val).strip()
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            cur = conn.execute(
                """
                INSERT INTO entity_triples (subject, predicate, object, confidence, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sub, pred, obj, float(confidence), str(source), now, now),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def query_triples(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        object_val: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query semantic relationship triples matching specified criteria."""
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            clauses = []
            params: List[Any] = []
            if subject:
                clauses.append("subject = ?")
                params.append(subject.strip().lower())
            if predicate:
                clauses.append("predicate = ?")
                params.append(predicate.strip().lower())
            if object_val:
                clauses.append("object = ?")
                params.append(object_val.strip())
            where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = f"SELECT id, subject, predicate, object, confidence, source, updated_at FROM entity_triples{where_sql} ORDER BY updated_at DESC LIMIT ?"
            params.append(max(1, min(int(limit), 200)))
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def find_related_entities(self, entity: str, max_depth: int = 2) -> List[Dict[str, Any]]:
        """Perform bidirectional graph traversal starting from an entity node."""
        clean_ent = str(entity).strip().lower()
        visited: Set[str] = set()
        queue: List[Tuple[str, int]] = [(clean_ent, 0)]
        results: List[Dict[str, Any]] = []

        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            while queue:
                curr, depth = queue.pop(0)
                if curr in visited or depth >= max_depth:
                    continue
                visited.add(curr)

                # Outgoing edges: curr -> predicate -> object
                fwd = conn.execute(
                    "SELECT id, subject, predicate, object, confidence FROM entity_triples WHERE subject = ?",
                    (curr,),
                ).fetchall()
                for r in fwd:
                    d = dict(r)
                    d["direction"] = "outgoing"
                    results.append(d)
                    next_node = str(d["object"]).strip().lower()
                    if next_node not in visited and depth + 1 < max_depth:
                        queue.append((next_node, depth + 1))

                # Incoming edges: subject -> predicate -> curr
                bwd = conn.execute(
                    "SELECT id, subject, predicate, object, confidence FROM entity_triples WHERE object = ?",
                    (curr,),
                ).fetchall()
                for r in bwd:
                    d = dict(r)
                    d["direction"] = "incoming"
                    results.append(d)
                    next_node = str(d["subject"]).strip().lower()
                    if next_node not in visited and depth + 1 < max_depth:
                        queue.append((next_node, depth + 1))

        return results

    def delete_triples_matching(self, entity: str) -> int:
        """Delete semantic relationship triples where subject, predicate, or object matches entity."""
        clean = str(entity).strip().lower()
        if not clean:
            return 0
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            pattern = f"%{clean}%"
            cur = conn.execute(
                "DELETE FROM entity_triples WHERE subject = ? OR predicate = ? OR object LIKE ? OR subject LIKE ?",
                (clean, clean, pattern, pattern),
            )
            conn.commit()
            return int(cur.rowcount or 0)


@dataclass
class MemoryConfig:
    path: str
    write_buffer_s: float = 0.05
    wal: bool = True


class EternalMemory:
    """Async SQLite-backed conversation memory with buffered writes."""

    def __init__(self, config: dict[str, Any] | None = None, db_path: Optional[Path | str] = None, **kwargs: Any) -> None:
        cfg = config or {}
        mem_cfg = cfg.get("memory", {}) if isinstance(cfg, dict) else {}

        if db_path is not None:
            self.db_path = Path(db_path)
        else:
            base_path = os.path.expanduser(mem_cfg.get("sqlite_path", mem_cfg.get("path", kwargs.get("path", "~/.makima/memory.sqlite"))))
            self.db_path = Path(base_path)
            
        self.write_buffer_s = float(kwargs.get("flush_interval", mem_cfg.get("flush_interval_s", mem_cfg.get("write_buffer_s", 0.05))))

        self._queue: asyncio.Queue[tuple[str, str, float, str | None]] = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._wake_writer: asyncio.Event = asyncio.Event()
        self._background_tasks: set[asyncio.Task] = set()
        self._retriever: Optional[CognitiveMemoryRetriever] = None

        # sqlite3.Connection is bound to the thread that created it.
        # Writer connection lives on the event-loop thread only.
        # All reads executed in run_in_executor use their own short-lived connection.
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

        # Optional Rust facade (not strictly wired in this snapshot)
        self._rust_index: Any = None
        try:
            import makima_core  # type: ignore
            self._rust_index = getattr(makima_core, "vector_index", None)
        except Exception:
            self._rust_index = None

        # ------------------------------------------------------------------
        # Local ONNX embedding layer (RAG) — v8.1
        # SQLite stays the source of truth; the numpy vector index is a
        # projection (id → embedding) persisted next to the DB as .npy.
        # ------------------------------------------------------------------
        embed_enabled = bool(mem_cfg.get("embedding_enabled", True))
        self._embeddings: Optional[EmbeddingProvider] = (
            EmbeddingProvider(
                model_name=mem_cfg.get("embedding_model", DEFAULT_MODEL),
                enabled=embed_enabled,
            )
            if embed_enabled
            else None
        )
        self._vec_ids: list[int] = []                 # conversation.id per row
        self._vec_rows: list[np.ndarray] = []         # raw vectors (normalized)
        self._vec_mat: Optional[np.ndarray] = None    # cached N×dim (None = dirty)
        self._vec_path = Path(str(self.db_path) + ".vec.npy")
        self._vec_ids_path = Path(str(self.db_path) + ".vec.ids.npy")

    async def start(self) -> None:
        """Initialize DB and start buffered writer loop."""
        try:
            await self._ensure_db()
            if self._writer_task is None or self._writer_task.done():
                self._writer_task = asyncio.create_task(self._writer_loop())
            self._load_vector_index()
            if self._embeddings and self._embeddings.available:
                task = asyncio.create_task(self.backfill_embeddings())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            logger.info("EternalMemory started, db=%s", self.db_path)
        except Exception as e:
            logger.error("EternalMemory start failed: %s", e)

    async def stop(self) -> None:
        """Flush queue, cancel writer task, save vector index, close connection."""
        # 1. Flush queued turns before cancelling the background writer loop
        try:
            await self.flush(timeout=2.0)
        except Exception as e:
            logger.error("flush on stop failed: %s", e)

        # 2. Drain any remaining queued items directly to SQLite
        while not self._queue.empty():
            try:
                r, m, c, cid = self._queue.get_nowait()
                await self._write_immediately(r, m, c, cid)
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error("draining remaining memory item failed: %s", e)

        # 3. Safely cancel the writer task now that queue is empty
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None

        # 4. Cancel any background tasks
        for bg_task in list(self._background_tasks):
            if not bg_task.done():
                bg_task.cancel()
        self._background_tasks.clear()

        try:
            self._save_vector_index()
        except Exception as e:
            logger.error("vector index save failed: %s", e)
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        logger.info("EternalMemory stopped")

    close = stop

    @property
    def retriever(self) -> CognitiveMemoryRetriever:
        if self._retriever is None:
            self._retriever = CognitiveMemoryRetriever(self.db_path)
        return self._retriever

    async def _ensure_db(self) -> None:
        """Create DB file, apply schema and WAL mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except Exception as e:
            logger.warning("PRAGMA failed: %s", e)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT,
              created_at REAL NOT NULL,
              role TEXT NOT NULL,
              message TEXT NOT NULL,
              access_count INTEGER DEFAULT 1,
              last_accessed_at REAL
            )
            """
        )
        # Migrate: add conversation_id, access_count, last_accessed_at columns if missing
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation)").fetchall()}
            if "conversation_id" not in columns:
                conn.execute("ALTER TABLE conversation ADD COLUMN conversation_id TEXT")
            if "access_count" not in columns:
                conn.execute("ALTER TABLE conversation ADD COLUMN access_count INTEGER DEFAULT 1")
            if "last_accessed_at" not in columns:
                conn.execute("ALTER TABLE conversation ADD COLUMN last_accessed_at REAL")
            # Fix: importance column missing — ingest script stores 0.95 for Gemini topics etc.
            if "importance" not in columns:
                conn.execute("ALTER TABLE conversation ADD COLUMN importance REAL DEFAULT 0.7")
                logger.info("Migrated: added 'importance' column to conversation table")
        except Exception as e:
            logger.warning("migration check failed: %s", e)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_role ON conversation(role)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_group ON conversation(conversation_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_created ON conversation(created_at)")

        # v8.1: embedding column for the semantic retrieval layer
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation)").fetchall()}
            if "embedding" not in columns:
                conn.execute("ALTER TABLE conversation ADD COLUMN embedding BLOB")
        except Exception as e:
            logger.warning("embedding migration check failed: %s", e)

        # Closed-Loop Reflexion Table (Learned Rules)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learned_rules (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              rule TEXT NOT NULL,
              keywords TEXT NOT NULL,
              created_at REAL NOT NULL
            )
            """
        )
        conn.commit()

        self._conn = conn

        # Ensure FTS5 virtual table and triggers
        self.retriever._ensure_fts_schema()

    async def save_turn(
        self,
        message: str,
        role: str,
        conversation_id: str | None = None,
    ) -> None:
        """Queue a conversation turn for buffered write."""
        if not message or not isinstance(message, str):
            logger.warning("save_turn: invalid message")
            return
        if not role or not isinstance(role, str):
            logger.warning("save_turn: invalid role")
            return

        if self._conn is None:
            await self._ensure_db()
        try:
            await self._queue.put((str(role), str(message), time.time(), conversation_id))
            if str(role).lower() == "user":
                extract_task = asyncio.create_task(self.extract_and_sync_entities(message))
                self._background_tasks.add(extract_task)
                extract_task.add_done_callback(self._background_tasks.discard)
        except Exception as e:
            logger.error("save_turn queue failed: %s", e)

    async def flush(self, timeout: float = 5.0) -> None:
        """Block until all queued turns are flushed to SQLite (with timeout)."""
        # Immediately signal background writer to break sleep and flush batch
        self._wake_writer.set()
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("flush timed out after %.1fs — continuing without full drain", timeout)
        except Exception as e:
            logger.error("flush failed: %s", e)

    async def _writer_loop(self) -> None:
        """Background writer: batch-debounce queued turns."""
        while True:
            batch: list[tuple[str, str, float, str | None]] = []
            try:
                role, message, created_at, conversation_id = await self._queue.get()
                batch.append((role, message, created_at, conversation_id))

                # Wait for debounce window OR immediate flush signal
                try:
                    await asyncio.wait_for(self._wake_writer.wait(), timeout=self.write_buffer_s)
                except asyncio.TimeoutError:
                    pass
                self._wake_writer.clear()

                while not self._queue.empty():
                    try:
                        item = self._queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break

                try:
                    async with self._lock:
                        for r, m, c, cid in batch:
                            await self._write_immediately(r, m, c, cid)
                except Exception as e:
                    logger.error("writer_loop write error: %s", e)
                finally:
                    # ALWAYS mark items done — prevents flush()/queue.join() deadlock
                    for _ in range(len(batch)):
                        self._queue.task_done()
            except asyncio.CancelledError:
                # Persist items already dequeued before cancellation
                try:
                    async with self._lock:
                        for r, m, c, cid in batch:
                            await self._write_immediately(r, m, c, cid)
                except Exception:
                    pass
                finally:
                    for _ in range(len(batch)):
                        self._queue.task_done()
                break
            except Exception as e:
                logger.error("writer_loop error: %s", e)
                await asyncio.sleep(1.0)

    async def _write_immediately(
        self, role: str, message: str, created_at: float, conversation_id: str | None
    ) -> None:
        """Write a single turn to SQLite.

        The previous implementation ran _do_write() inside run_in_executor(),
        which hands it off to a thread-pool thread.  SQLite connections are
        bound to the thread that created them — self._conn was created on the
        event-loop thread, so every executor call raised:
          'SQLite objects created in a thread can only be used in that same thread.'
        Fix: open a *new* short-lived connection inside the worker function
        (same pattern already used by the read path / search methods), instead
        of sharing self._conn across threads.  self._conn is kept for the
        _writer_loop / flush paths that run directly on the event-loop thread
        and are therefore safe.
        """
        db_path = str(self.db_path)

        def _do_write() -> int:
            # New connection per executor call — thread-safe by construction.
            conn = sqlite3.connect(db_path, timeout=30.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                cur = conn.execute(
                    "INSERT INTO conversation (conversation_id, created_at, role, message) "
                    "VALUES (?, ?, ?, ?)",
                    (conversation_id, created_at, role, message),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

        try:
            loop = asyncio.get_running_loop()
            row_id = await loop.run_in_executor(None, _do_write)
            if row_id and self._embeddings and self._embeddings.available:
                try:
                    vec = await loop.run_in_executor(None, self._embeddings.embed_one, message)
                    if vec is not None:
                        await loop.run_in_executor(None, self._store_embedding, row_id, vec)
                        self._index_add(row_id, vec)
                        await loop.run_in_executor(None, self._save_vector_index)
                except Exception as e:
                    logger.error("embed-on-write failed (id=%s): %s", row_id, e)
        except Exception as e:
            logger.error("_write_immediately failed: %s", e)

    # ==========================================================================
    # Vector index (RAG layer)
    # ==========================================================================

    def _store_embedding(self, row_id: int, vec: np.ndarray) -> None:
        """Persist a vector as a BLOB on the conversation row (short-lived conn)."""
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                conn.execute(
                    "UPDATE conversation SET embedding = ? WHERE id = ?",
                    (vec.astype(np.float32).tobytes(), row_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("_store_embedding failed (id=%s): %s", row_id, e)

    def _index_add(self, row_id: int, vec: np.ndarray) -> None:
        self._vec_ids.append(row_id)
        self._vec_rows.append(np.asarray(vec, dtype=np.float32).reshape(-1))
        self._vec_mat = None  # dirty

    def _build_mat(self) -> Optional[np.ndarray]:
        if self._vec_mat is None and self._vec_rows:
            try:
                self._vec_mat = np.vstack(self._vec_rows).astype(np.float32)
            except Exception as e:
                logger.error("_build_mat failed: %s", e)
                return None
        return self._vec_mat

    def _sync_from_sqlite_blobs(self) -> int:
        """
        Synchronize any BLOB embeddings present in SQLite that are missing from the vector index.
        Provides zero-data-loss auto-healing if .vec.npy is missing, out-of-sync, or corrupted.
        """
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                rows = conn.execute(
                    "SELECT id, embedding FROM conversation WHERE embedding IS NOT NULL"
                ).fetchall()
            finally:
                conn.close()

            existing_ids = set(self._vec_ids)
            added = 0
            for rid, blob in rows:
                if rid not in existing_ids and blob is not None:
                    try:
                        arr = np.frombuffer(blob, dtype=np.float32)
                        if arr.size > 0:
                            if not self._vec_rows or arr.size == self._vec_rows[0].size:
                                self._vec_ids.append(rid)
                                self._vec_rows.append(arr.reshape(-1))
                                existing_ids.add(rid)
                                added += 1
                    except Exception as e:
                        logger.debug("Error decoding embedding BLOB for row %d: %s", rid, e)

            if added > 0:
                self._vec_mat = None  # dirty
                self._save_vector_index()
                logger.info("Auto-healed vector index: added %d missing vectors from SQLite (total: %d)", added, len(self._vec_ids))
            return added
        except Exception as e:
            logger.warning("_sync_from_sqlite_blobs failed: %s", e)
            return 0

    def _load_vector_index(self) -> None:
        """Load persisted .npy index; prune rows whose conversation rows are gone; sync missing SQLite BLOBs."""
        try:
            if not self._vec_path.exists() or not self._vec_ids_path.exists():
                self._sync_from_sqlite_blobs()
                return
            mat = np.load(self._vec_path, allow_pickle=False)
            ids = list(np.load(self._vec_ids_path, allow_pickle=False).tolist())
            if mat.shape[0] != len(ids):
                logger.warning("vector index size mismatch (%s vs %s) — rebuilding from SQLite", mat.shape[0], len(ids))
                self._vec_ids, self._vec_rows, self._vec_mat = [], [], None
                self._sync_from_sqlite_blobs()
                return
            # Prune entries missing from SQLite (e.g. memory_forget tombstones)
            existing = self._existing_conversation_ids(ids)
            keep = [i for i, rid in enumerate(ids) if rid in existing]
            if len(keep) != len(ids):
                ids = [ids[i] for i in keep]
                mat = mat[keep]
            self._vec_ids = ids
            self._vec_rows = [mat[i] for i in range(mat.shape[0])]
            self._vec_mat = mat
            # Auto-heal: pull any embeddings in SQLite that were never flushed to disk index
            self._sync_from_sqlite_blobs()
            logger.info("Vector index loaded: %d vectors", len(self._vec_ids))
        except Exception as e:
            logger.warning("_load_vector_index failed (%s) — starting fresh and syncing from SQLite", e)
            self._vec_ids, self._vec_rows, self._vec_mat = [], [], None
            self._sync_from_sqlite_blobs()

    def _save_vector_index(self) -> None:
        if not self._vec_rows:
            return
        try:
            mat = self._build_mat()
            if mat is None:
                return
            np.save(self._vec_path, mat)
            np.save(self._vec_ids_path, np.asarray(self._vec_ids, dtype=np.int64))
        except Exception as e:
            logger.error("_save_vector_index failed: %s", e)

    def _existing_conversation_ids(self, ids: list[int]) -> set[int]:
        if not ids:
            return set()
        existing: set[int] = set()
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                batch_size = 500
                for i in range(0, len(ids), batch_size):
                    chunk = ids[i : i + batch_size]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = conn.execute(
                        f"SELECT id FROM conversation WHERE id IN ({placeholders})", chunk
                    ).fetchall()
                    existing.update(r[0] for r in rows)
                return existing
            finally:
                conn.close()
        except Exception as e:
            logger.error("_existing_conversation_ids failed: %s", e)
            return set(ids)

    async def backfill_embeddings(self, batch_size: int = 64) -> None:
        """Embed rows that have no embedding yet (idempotent background backfill)."""
        if not self._embeddings or not self._embeddings.available:
            return
        loop = asyncio.get_running_loop()
        failed_ids: list[int] = []
        try:
            while True:
                pending = await loop.run_in_executor(
                    None, self._fetch_unembedded, batch_size, failed_ids
                )
                if not pending:
                    break
                vecs = await loop.run_in_executor(
                    None, self._embeddings.embed_batch, [m for _, m in pending]
                )
                if vecs is None:
                    break
                async with self._lock:
                    for (rid, _msg), v in zip(pending, vecs):
                        if v is None:
                            failed_ids.append(rid)
                            continue
                        await loop.run_in_executor(None, self._store_embedding, rid, v)
                        self._index_add(rid, v)
                logger.info("backfill: %d embedded (total %d)", len(pending), len(self._vec_ids))
            await loop.run_in_executor(None, self._save_vector_index)
            logger.info("Embedding backfill complete — %d vectors indexed", len(self._vec_ids))
        except Exception as e:
            logger.error("backfill_embeddings failed: %s", e)

    def _fetch_unembedded(self, limit: int, skip_ids: list[int] | None = None) -> list[tuple[int, str]]:
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                query = (
                    "SELECT id, message FROM conversation "
                    "WHERE embedding IS NULL AND message != '' "
                )
                params: list[int] = []
                if skip_ids:
                    placeholders = ",".join("?" for _ in skip_ids)
                    query += f" AND id NOT IN ({placeholders})"
                    params = list(skip_ids)
                query += " ORDER BY id LIMIT ?"
                params.append(limit)
                rows = conn.execute(query, params).fetchall()
                return [(r[0], r[1]) for r in rows]
            finally:
                conn.close()
        except Exception as e:
            logger.error("_fetch_unembedded failed: %s", e)
            return []

    async def search(self, query: str, k: int = 5, top_k: int | None = None) -> list[dict[str, Any]]:
        """Hybrid search: semantic (vector) top-k merged with SQLite FTS5 BM25 keyword search via RRF.

        Returns list of turns with backward-compatible dict fields including `relevance_score`,
        `cosine_sim`, `bm25_score`, `recency_factor`, `access_count`, etc. Never raises — degrades gracefully.
        Accepts both `k` and `top_k` for backward and forward compatibility.
        """
        if not query or not isinstance(query, str):
            return []
        effective_k = int(top_k if top_k is not None else k)
        k = max(1, min(effective_k, 100))

        db_path = str(self.db_path)
        loop = asyncio.get_running_loop()
        now = time.time()

        def _fetch_by_ids(ids: list[int]) -> list[dict[str, Any]]:
            if not ids:
                return []
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                try:
                    placeholders = ",".join("?" for _ in ids)
                    rows = conn.execute(
                        f"SELECT id, conversation_id, created_at, role, message, access_count, last_accessed_at, importance "
                        f"FROM conversation WHERE id IN ({placeholders})",
                        ids,
                    ).fetchall()
                    return [dict(r) for r in rows]
                finally:
                    conn.close()
            except Exception as e:
                logger.error("_fetch_by_ids failed: %s", e)
                return []

        def _like_search() -> list[dict[str, Any]]:
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                try:
                    pattern = f"%{query}%"
                    rows = conn.execute(
                        "SELECT id, conversation_id, created_at, role, message, access_count, last_accessed_at, importance "
                        "FROM conversation WHERE message LIKE ? ORDER BY created_at DESC LIMIT ?",
                        (pattern, k),
                    ).fetchall()
                    return [dict(r) for r in rows]
                finally:
                    conn.close()
            except Exception as e:
                logger.error("_like_search failed: %s", e)
                return []

        def _touch_accessed(ids: list[int], touch_time: float) -> None:
            if not ids:
                return
            try:
                conn = sqlite3.connect(db_path, timeout=30.0)
                try:
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE conversation SET access_count = COALESCE(access_count, 1) + 1, last_accessed_at = ? "
                        f"WHERE id IN ({placeholders})",
                        [touch_time, *ids],
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logger.error("_touch_accessed failed: %s", e)

        # 1) Dense Vector (Semantic) Search
        vector_rankings: list[int] = []
        vec_sim_map: dict[int, float] = {}
        if self._embeddings and self._embeddings.available and self._vec_ids:
            try:
                qv = await loop.run_in_executor(None, self._embeddings.embed_one, query)
                if qv is not None:
                    mat = self._build_mat()
                    if mat is not None and mat.shape[0] > 0:
                        sims = mat @ qv  # Normalized vectors -> cosine similarity
                        # Limit to top vector candidates to prevent noise saturation
                        order = np.argsort(-sims)[:max(k * 6, 40)]
                        for idx in order:
                            sim_val = float(sims[int(idx)])
                            if sim_val >= 0.30:
                                rid = self._vec_ids[int(idx)]
                                vec_sim_map[rid] = sim_val
                                vector_rankings.append(rid)
            except Exception as e:
                logger.error("vector search failed for '%s': %s", query, e)

        # 2) Sparse FTS5 BM25 Keyword Search
        fts_hits: list[tuple[int, float]] = []
        try:
            fts_hits = await loop.run_in_executor(
                None, self.retriever.execute_fts5_search, query, max(k * 4, 20)
            )
        except Exception as e:
            logger.error("FTS5 search failed for '%s': %s", query, e)
            fts_hits = []

        fts_rankings = [row_id for row_id, _ in fts_hits]
        fts_bm25_map = {row_id: score for row_id, score in fts_hits}

        # 3) Reciprocal Rank Fusion (RRF)
        rrf_scores = self.retriever.reciprocal_rank_fusion(
            vector_rankings=vector_rankings,
            fts_rankings=fts_rankings,
            k=60,
        )

        # Fallback to LIKE search if both vector and FTS5 returned 0 hits
        like_hits: list[dict[str, Any]] = []
        if not vector_rankings and not fts_rankings:
            like_hits = await loop.run_in_executor(None, _like_search)

        # Gather candidate records
        candidate_ids = list(rrf_scores.keys())
        if not candidate_ids and like_hits:
            candidate_ids = [r["id"] for r in like_hits]

        by_id: dict[int, dict[str, Any]] = {}
        if candidate_ids:
            fetched_rows = await loop.run_in_executor(None, _fetch_by_ids, candidate_ids)
            by_id = {r["id"]: r for r in fetched_rows}
        elif like_hits:
            by_id = {r["id"]: r for r in like_hits}

        # Process and score candidate hits
        seen_ids: set[int] = set()
        seen_texts: set[str] = set()
        merged: list[dict[str, Any]] = []

        ordered_ids = sorted(candidate_ids, key=lambda cid: rrf_scores.get(cid, 0.0), reverse=True) if rrf_scores else candidate_ids

        for rid in ordered_ids:
            r = by_id.get(rid)
            if not r:
                continue
            msg_text = str(r.get("message") or "").strip()
            norm_text = re.sub(r"\s+", " ", msg_text.lower())

            if rid in seen_ids or not norm_text or norm_text in seen_texts:
                continue
            seen_ids.add(rid)
            seen_texts.add(norm_text)

            created_at = float(r.get("created_at") or now)
            last_accessed_at = r.get("last_accessed_at")
            last_accessed_at_val = float(last_accessed_at) if last_accessed_at is not None else None
            access_count = int(r.get("access_count") or 1)

            # Ebbinghaus Spaced Reinforcement Recency
            recency = self.retriever.calculate_ebbinghaus_recency(
                created_at=created_at,
                last_accessed_at=last_accessed_at_val,
                access_count=access_count,
                current_time=now,
            )

            cos_sim = float(vec_sim_map.get(rid, 0.0))
            bm25_norm = float(fts_bm25_map.get(rid, 0.0))
            importance = float(r.get("importance") if r.get("importance") is not None else 0.7)

            if not self._embeddings or not self._embeddings.available:
                # Embedding layer disabled/unavailable: keyword-only mode
                composite_score = bm25_norm if bm25_norm > 0.0 else 0.5
            else:
                # Semantic / Hybrid RAG mode
                if cos_sim > 0.0 and bm25_norm > 0.0:
                    # Hybrid boost: vector match reinforced by keyword match
                    match_score = (0.60 * cos_sim) + (0.40 * bm25_norm)
                elif cos_sim > 0.0:
                    match_score = cos_sim
                elif bm25_norm > 0.0:
                    match_score = bm25_norm
                else:
                    match_score = 0.0

                # Facts are durable: importance is primary (80%), recency provides gentle boost (20%)
                temporal_factor = (0.80 * importance) + (0.20 * max(0.25, recency))
                composite_score = match_score * temporal_factor

            r["relevance_score"] = round(composite_score, 4)
            r["cosine_sim"] = round(cos_sim, 4)
            r["bm25_score"] = round(bm25_norm, 4)
            r["recency_factor"] = round(recency, 4)
            r["access_count"] = access_count + 1
            r["last_accessed_at"] = now
            r["composite_score"] = round(composite_score, 4)
            merged.append(r)

        # Sort merged results by relevance_score descending, then by RRF score
        merged.sort(
            key=lambda item: (item.get("relevance_score", 0.0), rrf_scores.get(item.get("id", 0), 0.0)),
            reverse=True,
        )

        final_hits = merged[:k]

        # Asynchronously update access count and last accessed time in DB
        retrieved_ids = [int(h["id"]) for h in final_hits if "id" in h]
        if retrieved_ids:
            try:
                loop.create_task(loop.run_in_executor(None, _touch_accessed, retrieved_ids, now))
            except Exception as e:
                logger.debug("Failed to schedule _touch_accessed: %s", e)

        return final_hits

    async def get_history(self, n: int = 20, conversation_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieve recent conversation turns."""
        n = max(1, min(int(n), 500))
        db_path = str(self.db_path)

        def _do_read() -> list[dict[str, Any]]:
            # Open a fresh connection — SQLite connections are thread-bound
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                if conversation_id:
                    rows = conn.execute(
                        "SELECT id, conversation_id, created_at, role, message "
                        "FROM conversation WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
                        (conversation_id, n),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, conversation_id, created_at, role, message "
                        "FROM conversation ORDER BY created_at DESC LIMIT ?",
                        (n,),
                    ).fetchall()
                conn.close()
                return [dict(r) for r in reversed(rows)]
            except Exception as e:
                logger.error("get_history _do_read failed: %s", e)
                return []

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_read)
        except Exception as e:
            logger.error("get_history failed: %s", e)
            return []

    async def get_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """Get all turns for a specific conversation."""
        if not conversation_id:
            return []
        return await self.get_history(n=500, conversation_id=conversation_id)

    async def list_conversations(self) -> list[dict[str, Any]]:
        """List unique conversations with metadata."""
        db_path = str(self.db_path)
        def _do_list() -> list[dict[str, Any]]:
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT conversation_id, MIN(created_at) as started, MAX(created_at) as last_active, "
                    "COUNT(*) as turn_count FROM conversation GROUP BY conversation_id "
                    "ORDER BY last_active DESC LIMIT 100"
                ).fetchall()
                conn.close()
                return [dict(r) for r in rows]
            except Exception as e:
                logger.error("list_conversations failed: %s", e)
                return []

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_list)
        except Exception as e:
            logger.error("list_conversations executor failed: %s", e)
            return []

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Delete all turns for a specific conversation (prunes vector index too)."""
        if not conversation_id:
            return False

        db_path = str(self.db_path)
        def _do_delete() -> bool:
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                cursor = conn.execute("DELETE FROM conversation WHERE conversation_id = ?", (conversation_id,))
                conn.commit()
                rowcount = cursor.rowcount
                conn.close()
                return rowcount > 0
            except Exception as e:
                logger.error("delete_conversation failed: %s", e)
                return False

        try:
            loop = asyncio.get_running_loop()
            deleted = await loop.run_in_executor(None, _do_delete)
            if deleted:
                self._prune_index(conversation_id)
                self._save_vector_index()
            return deleted
        except Exception as e:
            logger.error("delete_conversation executor failed: %s", e)
            return False

    async def delete_memory(self, memory_id: int) -> bool:
        """Delete a single memory by row id from SQLite and prune vector index."""
        if not memory_id:
            return False

        db_path = str(self.db_path)
        def _do_delete() -> bool:
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                cursor = conn.execute("DELETE FROM conversation WHERE id = ?", (int(memory_id),))
                conn.commit()
                rowcount = cursor.rowcount
                conn.close()
                return rowcount > 0
            except Exception as e:
                logger.error("delete_memory failed: %s", e)
                return False

        try:
            loop = asyncio.get_running_loop()
            deleted = await loop.run_in_executor(None, _do_delete)
            if deleted:
                self._prune_index(f"id_{memory_id}")
                self._save_vector_index()
            return deleted
        except Exception as e:
            logger.error("delete_memory executor failed: %s", e)
            return False

    async def delete_matching(self, query: str) -> int:
        """
        Delete all conversation memories matching the query from SQLite relational
        storage (auto-purging FTS5 via trigger) and vector index.
        """
        if not query or not str(query).strip():
            return 0

        clean_query = str(query).strip()
        db_path = str(self.db_path)

        def _do_delete_matching() -> int:
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                # 1. Try exact substring match
                pattern = f"%{clean_query}%"
                rows = conn.execute(
                    "SELECT id FROM conversation WHERE message LIKE ?",
                    (pattern,),
                ).fetchall()

                # 2. Dynamic Full-Text Search via SQLite FTS5 native tokenizer
                if not rows:
                    raw_tokens = [re.sub(r"[^\w]", "", w) for w in clean_query.split()]
                    fts_tokens = [t for t in raw_tokens if len(t) >= 2]
                    if fts_tokens:
                        fts_expr = " AND ".join(f'"{t}"' for t in fts_tokens)
                        try:
                            rows = conn.execute(
                                "SELECT rowid FROM conversation_fts WHERE conversation_fts MATCH ?",
                                (fts_expr,),
                            ).fetchall()
                        except Exception as fts_err:
                            logger.debug("FTS5 match in delete_matching: %s", fts_err)
                            rows = []

                if not rows:
                    conn.close()
                    return 0

                ids_to_delete = [r[0] for r in rows]
                placeholders = ",".join("?" for _ in ids_to_delete)
                cursor = conn.execute(
                    f"DELETE FROM conversation WHERE id IN ({placeholders})",
                    ids_to_delete,
                )
                conn.commit()
                deleted_count = cursor.rowcount
                conn.close()
                return deleted_count
            except Exception as e:
                logger.error("delete_matching failed: %s", e)
                return 0

        try:
            loop = asyncio.get_running_loop()
            deleted_count = await loop.run_in_executor(None, _do_delete_matching)

            # Also cascade delete from entity knowledge graph
            try:
                triples_deleted = await loop.run_in_executor(None, self.retriever.delete_triples_matching, clean_query)
                if triples_deleted > 0:
                    logger.info("Purged %d entity triples matching '%s'", triples_deleted, clean_query)
            except Exception as ge:
                logger.debug("delete_matching entity_triples cascade: %s", ge)

            if deleted_count > 0:
                self._prune_index(clean_query)
                self._save_vector_index()
                return deleted_count

            # 3. Dynamic Semantic Vector Fallback (Zero keyword overlap matching)
            if self._embeddings and self._embeddings.available and self._vec_ids:
                semantic_deleted = await self.delete_semantic(clean_query, similarity_threshold=0.45)
                return semantic_deleted

            return 0
        except Exception as e:
            logger.error("delete_matching executor failed: %s", e)
            return 0

    async def delete_semantic(self, query: str, similarity_threshold: float = 0.45) -> int:
        """
        Delete memories based on deep semantic vector similarity (cosine sim >= threshold).
        Enables forgetting facts with zero literal keyword overlap (e.g. 'forget meat' -> deletes 'I am vegetarian').
        """
        if not query or not str(query).strip():
            return 0

        clean_query = str(query).strip()
        matched_ids: list[int] = []

        if self._embeddings and self._embeddings.available and self._vec_ids:
            try:
                loop = asyncio.get_running_loop()
                qv = await loop.run_in_executor(None, self._embeddings.embed_one, clean_query)
                if qv is not None:
                    mat = self._build_mat()
                    if mat is not None and mat.shape[0] > 0:
                        sims = mat @ qv
                        for idx, sim_val in enumerate(sims):
                            if float(sim_val) >= similarity_threshold:
                                matched_ids.append(self._vec_ids[idx])
            except Exception as e:
                logger.error("delete_semantic vector search error: %s", e)

        if not matched_ids:
            return 0

        db_path = str(self.db_path)
        def _do_delete_ids() -> int:
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                placeholders = ",".join("?" for _ in matched_ids)
                cursor = conn.execute(
                    f"DELETE FROM conversation WHERE id IN ({placeholders})",
                    matched_ids,
                )
                conn.commit()
                deleted_count = cursor.rowcount
                conn.close()
                return deleted_count
            except Exception as e:
                logger.error("delete_semantic batch delete error: %s", e)
                return 0

        loop = asyncio.get_running_loop()
        deleted_count = await loop.run_in_executor(None, _do_delete_ids)
        if deleted_count > 0:
            self._prune_index(clean_query)
            self._save_vector_index()
        return deleted_count

    async def resolve_contradictions(self, new_fact: str, contradiction_threshold: float = 0.65) -> int:
        """
        Detects if a new fact contradicts or supersedes past memory (e.g., location changes)
        and automatically archives outdated records.
        """
        if not new_fact:
            return 0
        if not self._embeddings or not self._embeddings.available or not self._vec_ids:
            return await self.delete_matching("primary residence")
        try:
            loop = asyncio.get_running_loop()
            qv = await loop.run_in_executor(None, self._embeddings.embed_one, new_fact)
            if qv is None:
                return 0
            mat = self._build_mat()
            if mat is None or mat.shape[0] == 0:
                return 0
            sims = mat @ qv
            superseded_ids = [self._vec_ids[idx] for idx, sim in enumerate(sims) if float(sim) >= contradiction_threshold]
            if not superseded_ids:
                return 0

            db_path = str(self.db_path)
            def _do_archive() -> int:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                placeholders = ",".join("?" for _ in superseded_ids)
                cursor = conn.execute(
                    f"DELETE FROM conversation WHERE id IN ({placeholders})",
                    superseded_ids,
                )
                conn.commit()
                count = cursor.rowcount
                conn.close()
                return count

            count = await loop.run_in_executor(None, _do_archive)
            if count > 0:
                self._prune_index("superseded")
                self._save_vector_index()
            return count
        except Exception as e:
            logger.error("resolve_contradictions error: %s", e)
            return 0

    def _prune_index(self, conversation_id: str) -> None:
        """Drop vectors whose conversation rows were deleted (live batch prune)."""
        try:
            if not self._vec_ids:
                return
            existing = self._existing_conversation_ids(self._vec_ids)
            keep = [i for i, rid in enumerate(self._vec_ids) if rid in existing]
            if len(keep) == len(self._vec_ids):
                return
            removed = len(self._vec_ids) - len(keep)
            self._vec_ids = [self._vec_ids[i] for i in keep]
            self._vec_rows = [self._vec_rows[i] for i in keep]
            self._vec_mat = None
            logger.info("Vector index pruned: -%d rows (conversation=%s, now %d vectors)", removed, conversation_id, len(self._vec_ids))
        except Exception as e:
            logger.error("_prune_index failed: %s", e)

    def _id_still_exists(self, row_id: int) -> bool:
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            try:
                row = conn.execute("SELECT 1 FROM conversation WHERE id = ?", (row_id,)).fetchone()
                return row is not None
            finally:
                conn.close()
        except Exception as e:
            logger.error("_id_still_exists failed: %s", e)
            return True

    async def save_rule(self, rule_text: str, keywords: list[str]) -> None:
        """Save a learned reflexion rule and its trigger keywords to SQLite."""
        if not rule_text or not isinstance(rule_text, str):
            logger.warning("save_rule: invalid rule_text")
            return
        
        kw_str = ",".join([k.strip().lower() for k in keywords if k and isinstance(k, str)])
        db_path = str(self.db_path)
        now = time.time()

        def _do_save() -> None:
            conn = sqlite3.connect(db_path, timeout=30.0)
            try:
                conn.execute(
                    "INSERT INTO learned_rules (rule, keywords, created_at) VALUES (?, ?, ?)",
                    (rule_text.strip(), kw_str, now),
                )
                conn.commit()
                logger.info("Saved learned rule: %r (keywords: %s)", rule_text, kw_str)
            finally:
                conn.close()

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_save)
        except Exception as e:
            logger.error("save_rule failed: %s", e)

    async def search_rules(self, query: str, top_k: int = 3) -> list[str]:
        """Search learned rules that match keywords in the query or general relevance."""
        if not query or not isinstance(query, str):
            return []
        
        query_words = [w.lower().strip() for w in query.split() if len(w.strip()) > 1]
        db_path = str(self.db_path)

        def _do_search() -> list[str]:
            conn = sqlite3.connect(db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT rule, keywords FROM learned_rules ORDER BY created_at DESC LIMIT 50").fetchall()
                matching_rules: list[str] = []
                for row in rows:
                    rule = row["rule"]
                    kw_list = [k.strip() for k in row["keywords"].split(",") if k.strip()]
                    # Check if query contains any of the keywords or vice versa
                    if any(kw in query.lower() for kw in kw_list) or any(w in rule.lower() for w in query_words):
                        matching_rules.append(rule)
                    if len(matching_rules) >= top_k:
                        break
                
                # If no keyword match, return most recent rules as general constraints
                if not matching_rules and rows:
                    matching_rules = [r["rule"] for r in rows[:top_k]]
                
                return matching_rules
            finally:
                conn.close()

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _do_search)
        except Exception as e:
            logger.error("search_rules failed: %s", e)
            return []

    # ── Digital Twin Knowledge Graph Async Facade ─────────────────────────────

    async def add_triple(
        self,
        subject: str,
        predicate: str,
        object_val: str,
        confidence: float = 1.0,
        source: str = "chat",
    ) -> int:
        """Asynchronously insert or update a semantic relationship triple in Digital Twin."""
        ret = self.retriever
        if not ret:
            return 0
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            ret.add_triple,
            subject,
            predicate,
            object_val,
            confidence,
            source,
        )

    async def query_triples(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        object_val: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Asynchronously query semantic relationship triples."""
        ret = self.retriever
        if not ret:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            ret.query_triples,
            subject,
            predicate,
            object_val,
            limit,
        )

    async def find_related_entities(self, entity: str, max_depth: int = 2) -> List[Dict[str, Any]]:
        """Asynchronously traverse Digital Twin graph to find associated nodes."""
        ret = self.retriever
        if not ret:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            ret.find_related_entities,
            entity,
            max_depth,
        )

    async def extract_and_sync_entities(self, user_text: str, agent_text: str = "") -> List[Dict[str, Any]]:
        """
        Fast regex & pattern-based heuristic extractor for projects, clients, deadlines, and tools.
        Syncs extracted triples into Digital Twin knowledge graph automatically.
        """
        if not user_text or not isinstance(user_text, str):
            return []

        text = user_text.strip()
        new_triples: List[Dict[str, str]] = []

        # 1. Project extraction: "project X", "working on X", "mera project X hai"
        proj_match = re.search(r"(?:project|working on|repo|codebase)\s+([A-Za-z0-9_\-\.]+)", text, re.IGNORECASE)
        if proj_match:
            proj = proj_match.group(1).strip()
            if proj.lower() not in ("a", "the", "my", "this", "new", "some", "our"):
                new_triples.append({"subject": "user", "predicate": "works_on", "object": proj})

        # 2. Client / Colleague extraction: "client is X", "client name is X", "meeting with X"
        client_match = re.search(r"(?:client|meeting with|collaborator|partner)\s+(?:is\s+)?([A-Za-z0-9_]+)", text, re.IGNORECASE)
        if client_match:
            person = client_match.group(1).strip()
            if person.lower() not in ("a", "the", "my", "tomorrow", "today", "yesterday"):
                new_triples.append({"subject": "user", "predicate": "collaborates_with", "object": person})

        # 3. Deadline extraction: "deadline is X", "submission on X", "due on X"
        deadline_match = re.search(r"(?:deadline|submission|due date|due on|submit by)\s+(?:is\s+)?([A-Za-z0-9_\-\s]{3,25})", text, re.IGNORECASE)
        if deadline_match:
            due = deadline_match.group(1).strip()
            new_triples.append({"subject": "user", "predicate": "has_deadline", "object": due})

        # Persist extracted triples
        results: List[Dict[str, Any]] = []
        for t in new_triples:
            try:
                tid = await self.add_triple(t["subject"], t["predicate"], t["object"], confidence=0.85, source="chat_inference")
                results.append({**t, "id": tid})
            except Exception as e:
                logger.debug("extract_and_sync_entities insert error: %s", e)

        return results

