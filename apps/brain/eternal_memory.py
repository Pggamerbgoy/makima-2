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
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .embeddings import DEFAULT_MODEL, EmbeddingProvider

logger = logging.getLogger("makima.eternal_memory")


@dataclass
class MemoryConfig:
    path: str
    write_buffer_s: float = 3.0
    wal: bool = True


class EternalMemory:
    """Async SQLite-backed conversation memory with buffered writes."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        mem_cfg = cfg.get("memory", {}) if isinstance(cfg, dict) else {}

        base_path = os.path.expanduser(mem_cfg.get("sqlite_path", mem_cfg.get("path", "~/.makima/memory.sqlite")))
        self.db_path = Path(base_path)
        self.write_buffer_s = float(mem_cfg.get("flush_interval_s", mem_cfg.get("write_buffer_s", 3.0)))

        self._queue: asyncio.Queue[tuple[str, str, float, str | None]] = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None

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
                asyncio.create_task(self.backfill_embeddings())
            logger.info("EternalMemory started, db=%s", self.db_path)
        except Exception as e:
            logger.error("EternalMemory start failed: %s", e)

    async def stop(self) -> None:
        """Flush queue, cancel writer task, save vector index, close connection."""
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None
        try:
            await self.flush()
        except Exception as e:
            logger.error("flush on stop failed: %s", e)
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
              message TEXT NOT NULL
            )
            """
        )
        # Migrate: add conversation_id column if missing
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation)").fetchall()}
            if "conversation_id" not in columns:
                conn.execute("ALTER TABLE conversation ADD COLUMN conversation_id TEXT")
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
        except Exception as e:
            logger.error("save_turn queue failed: %s", e)

    async def flush(self) -> None:
        """Block until all queued turns are flushed to SQLite."""
        try:
            await self._queue.join()
        except Exception as e:
            logger.error("flush failed: %s", e)

    async def _writer_loop(self) -> None:
        """Background writer: batch-debounce queued turns."""
        while True:
            batch: list[tuple[str, str, float, str | None]] = []
            try:
                role, message, created_at, conversation_id = await self._queue.get()
                batch.append((role, message, created_at, conversation_id))
                await asyncio.sleep(self.write_buffer_s)

                while not self._queue.empty():
                    try:
                        item = self._queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break

                async with self._lock:
                    for r, m, c, cid in batch:
                        await self._write_immediately(r, m, c, cid)
                
                for _ in range(len(batch)):
                    self._queue.task_done()
            except asyncio.CancelledError:
                # Persist items already dequeued before cancellation
                try:
                    async with self._lock:
                        for r, m, c, cid in batch:
                            await self._write_immediately(r, m, c, cid)
                    for _ in range(len(batch)):
                        self._queue.task_done()
                except Exception:
                    pass
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

    def _load_vector_index(self) -> None:
        """Load persisted .npy index; prune rows whose conversation rows are gone."""
        try:
            if not self._vec_path.exists() or not self._vec_ids_path.exists():
                return
            mat = np.load(self._vec_path, allow_pickle=False)
            ids = list(np.load(self._vec_ids_path, allow_pickle=False).tolist())
            if mat.shape[0] != len(ids):
                logger.warning("vector index size mismatch (%s vs %s) — rebuilding", mat.shape[0], len(ids))
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
            logger.info("Vector index loaded: %d vectors", len(self._vec_ids))
        except Exception as e:
            logger.warning("_load_vector_index failed (%s) — starting fresh", e)
            self._vec_ids, self._vec_rows, self._vec_mat = [], [], None

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
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"SELECT id FROM conversation WHERE id IN ({placeholders})", ids
                ).fetchall()
                return {r[0] for r in rows}
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

    async def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Hybrid search: semantic (vector) top-k merged with keyword (LIKE) fallback.

        Returns list of turns with a `relevance_score` (cosine for vector hits,
        0.5 default for keyword-only hits). Never raises — degrades to keyword
        search when the embedding layer is unavailable.
        """
        if not query or not isinstance(query, str):
            return []
        k = max(1, min(int(k), 100))
        db_path = str(self.db_path)
        loop = asyncio.get_running_loop()

        def _fetch_by_ids(ids: list[int]) -> list[dict[str, Any]]:
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                try:
                    placeholders = ",".join("?" for _ in ids)
                    rows = conn.execute(
                        "SELECT id, conversation_id, created_at, role, message "
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
                        "SELECT id, conversation_id, created_at, role, message "
                        "FROM conversation WHERE message LIKE ? ORDER BY created_at DESC LIMIT ?",
                        (pattern, k),
                    ).fetchall()
                    return [dict(r) for r in rows]
                finally:
                    conn.close()
            except Exception as e:
                logger.error("_like_search failed: %s", e)
                return []

        # 1) Semantic retrieval (primary)
        vec_hits: list[dict[str, Any]] = []
        if self._embeddings and self._embeddings.available and self._vec_ids:
            try:
                qv = await loop.run_in_executor(None, self._embeddings.embed_one, query)
                if qv is not None:
                    mat = self._build_mat()
                    if mat is not None:
                        sims = mat @ qv  # both normalized → cosine
                        order = np.argsort(-sims)[:k]
                        hit_ids = [self._vec_ids[int(i)] for i in order]
                        sim_map = {self._vec_ids[int(i)]: float(sims[int(i)]) for i in order}
                        # Preserve similarity order (SQL IN clause loses it)
                        by_id = {r["id"]: r for r in await loop.run_in_executor(None, _fetch_by_ids, hit_ids)}
                        for rid in hit_ids:
                            if rid in by_id:
                                by_id[rid]["relevance_score"] = sim_map[rid]
                                vec_hits.append(by_id[rid])
            except Exception as e:
                logger.error("vector search failed for '%s': %s", query, e)

        # 2) Keyword fallback (always run — fills gaps / exact-substring hits)
        like_hits = await loop.run_in_executor(None, _like_search)

        # 3) Merge: vector hits first (higher quality), dedupe by id, fill to k
        seen: set[int] = set()
        merged: list[dict[str, Any]] = []
        for r in vec_hits + like_hits:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            r.setdefault("relevance_score", 0.5)
            merged.append(r)
        merged.sort(key=lambda r: r.get("relevance_score", 0.5), reverse=True)
        return merged[:k]

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
            return deleted
        except Exception as e:
            logger.error("delete_conversation executor failed: %s", e)
            return False

    def _prune_index(self, conversation_id: str) -> None:
        """Drop vectors whose conversation rows were deleted (live prune)."""
        try:
            keep = [i for i, rid in enumerate(self._vec_ids) if self._id_still_exists(rid)]
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

