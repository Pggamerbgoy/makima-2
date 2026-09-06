"""
Makima OS — Durable Task Engine (Module 4)
Location: apps/brain/core/durable_task_engine.py

Based on: Event Sourcing pattern + Temporal workflow concepts.
Enables tasks to survive process restarts, host crashes, reboots, and the 25-turn execution horizon.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from apps.brain.core.contracts import Task, TaskState
from apps.brain.core.persistence import EventStore

logger = logging.getLogger("makima.core.durable_task_engine")


@dataclass
class CheckpointedTask:
    """Represents a serialized task snapshot persisted in SQLite."""
    checkpoint_id: str
    task_id: str
    task_name: str
    original_prompt: str
    completed_steps: list[Any]
    remaining_steps: list[Any]
    context_snapshot: dict[str, Any]
    turn_count: int
    max_turns: int
    status: str  # "active" | "paused" | "completed" | "failed"
    created_at: float
    updated_at: float
    resume_after: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "original_prompt": self.original_prompt,
            "completed_steps": self.completed_steps,
            "remaining_steps": self.remaining_steps,
            "context_snapshot": self.context_snapshot,
            "turn_count": self.turn_count,
            "max_turns": self.max_turns,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resume_after": self.resume_after,
        }


@dataclass
class ResumedTask:
    """Represents an active task reconstructed from durable storage."""
    task: Task
    checkpoint: CheckpointedTask
    reconstructed_context: dict[str, Any]
    continuation_prompt: str

    @property
    def original_prompt(self) -> str:
        return self.checkpoint.original_prompt

    @property
    def remaining_steps(self) -> list[Any]:
        return self.checkpoint.remaining_steps

    @property
    def completed_steps(self) -> list[Any]:
        return self.checkpoint.completed_steps

    @property
    def task_id(self) -> str:
        return self.checkpoint.task_id



class DurableTaskEngine:
    """
    Durable Task Engine backed by SQLite (reusing EventStore connection and locks).
    Manages task checkpoints, crash recovery, and scheduled resumptions.
    """

    def __init__(
        self,
        event_store: EventStore,
        task_manager: Optional[Any] = None,
    ) -> None:
        self._event_store = event_store
        self._task_manager = task_manager
        self._conn = event_store._conn
        self._init_db_sync()

    async def start(self) -> None:
        """Start the durable task engine (ensures db schema initialized)."""
        self._init_db_sync()

    async def stop(self) -> None:
        """Stop the durable task engine cleanly."""
        pass

    def _init_db_sync(self) -> None:
        """Create the task_checkpoints table and indices synchronously."""
        try:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    checkpoint_id    TEXT PRIMARY KEY,
                    task_id          TEXT NOT NULL,
                    task_name        TEXT NOT NULL,
                    original_prompt  TEXT NOT NULL,
                    completed_steps  TEXT NOT NULL,    -- JSON list
                    remaining_steps  TEXT NOT NULL,    -- JSON list
                    context_snapshot TEXT NOT NULL,    -- JSON dict
                    turn_count       INTEGER DEFAULT 0,
                    max_turns        INTEGER DEFAULT 25,
                    status           TEXT DEFAULT 'active', -- active/paused/completed/failed
                    created_at       REAL NOT NULL,
                    updated_at       REAL NOT NULL,
                    resume_after     REAL              -- NULL or Unix timestamp
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_checkpoints_task
                ON task_checkpoints(task_id)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_checkpoints_status
                ON task_checkpoints(status)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_checkpoints_resume
                ON task_checkpoints(resume_after)
            """)
            self._conn.commit()
        except Exception as exc:
            logger.error("[DurableTaskEngine] Failed to initialize database schema: %s", exc)

    def _row_to_checkpoint(self, row: tuple) -> CheckpointedTask:
        """Convert a database row into a CheckpointedTask."""
        completed_steps = json.loads(row[4]) if row[4] else []
        remaining_steps = json.loads(row[5]) if row[5] else []
        context_snapshot = json.loads(row[6]) if row[6] else {}
        return CheckpointedTask(
            checkpoint_id=row[0],
            task_id=row[1],
            task_name=row[2],
            original_prompt=row[3],
            completed_steps=completed_steps,
            remaining_steps=remaining_steps,
            context_snapshot=context_snapshot,
            turn_count=row[7],
            max_turns=row[8],
            status=row[9],
            created_at=row[10],
            updated_at=row[11],
            resume_after=row[12],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Synchronous Core Methods (for crash recovery & executors)
    # ─────────────────────────────────────────────────────────────────────────

    def checkpoint_task_sync(
        self,
        task_id: str,
        prompt: str = "",
        completed_steps: Optional[list[Any]] = None,
        remaining_steps: Optional[list[Any]] = None,
        context: Optional[dict[str, Any]] = None,
        turn_count: int = 0,
        task_name: str = "",
        max_turns: int = 25,
        status: str = "active",
        resume_after: Optional[float] = None,
        original_prompt: str = "",
        resume_after_seconds: Optional[float] = None,
    ) -> CheckpointedTask:
        """Serialize task state to task_checkpoints table synchronously."""
        now = time.time()
        c_name = task_name or f"task_{task_id}"
        actual_prompt = original_prompt or prompt
        actual_resume = (now + resume_after_seconds) if resume_after_seconds is not None else resume_after
        c_completed = completed_steps if completed_steps is not None else []
        c_remaining = remaining_steps if remaining_steps is not None else []
        c_context = context if context is not None else {}

        # Clean context of non-serializable objects
        clean_context: dict[str, Any] = {}
        for k, v in c_context.items():
            if k in ("ws_broadcast", "_task_handles", "_lock"):
                continue
            try:
                json.dumps(v, default=str)
                clean_context[k] = v
            except Exception:
                clean_context[k] = str(v)

        existing = self.get_checkpoint_sync(task_id)
        if existing:
            cid = existing.checkpoint_id
            created_at = existing.created_at
            self._conn.execute(
                """
                UPDATE task_checkpoints
                SET task_name = ?, original_prompt = ?, completed_steps = ?,
                    remaining_steps = ?, context_snapshot = ?, turn_count = ?,
                    max_turns = ?, status = ?, updated_at = ?, resume_after = ?
                WHERE checkpoint_id = ?
                """,
                (
                    c_name,
                    actual_prompt,
                    json.dumps(c_completed, default=str),
                    json.dumps(c_remaining, default=str),
                    json.dumps(clean_context, default=str),
                    turn_count,
                    max_turns,
                    status,
                    now,
                    actual_resume,
                    cid,
                ),
            )
        else:
            cid = f"cp_{uuid.uuid4().hex[:12]}"
            created_at = now
            self._conn.execute(
                """
                INSERT INTO task_checkpoints
                (checkpoint_id, task_id, task_name, original_prompt, completed_steps,
                 remaining_steps, context_snapshot, turn_count, max_turns, status,
                 created_at, updated_at, resume_after)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid,
                    task_id,
                    c_name,
                    actual_prompt,
                    json.dumps(c_completed, default=str),
                    json.dumps(c_remaining, default=str),
                    json.dumps(clean_context, default=str),
                    turn_count,
                    max_turns,
                    status,
                    created_at,
                    now,
                    actual_resume,
                ),
            )
        self._conn.commit()

        # Emit durability audit event
        self._event_store.append_sync(
            "task_checkpointed",
            task_id=task_id,
            payload={
                "checkpoint_id": cid,
                "turn_count": turn_count,
                "status": status,
                "remaining_steps_count": len(remaining_steps),
            },
        )

        cp = CheckpointedTask(
            checkpoint_id=cid,
            task_id=task_id,
            task_name=c_name,
            original_prompt=prompt,
            completed_steps=completed_steps,
            remaining_steps=remaining_steps,
            context_snapshot=clean_context,
            turn_count=turn_count,
            max_turns=max_turns,
            status=status,
            created_at=created_at,
            updated_at=now,
            resume_after=resume_after,
        )
        logger.info(
            "[DurableTaskEngine] Checkpointed task %s (id=%s, turns=%d/%d, status=%s)",
            c_name, task_id, turn_count, max_turns, status,
        )
        return cp

    def get_checkpoint_sync(self, task_id: str) -> Optional[CheckpointedTask]:
        """Load latest checkpoint for task_id synchronously."""
        cur = self._conn.execute(
            """
            SELECT checkpoint_id, task_id, task_name, original_prompt,
                   completed_steps, remaining_steps, context_snapshot,
                   turn_count, max_turns, status, created_at, updated_at, resume_after
            FROM task_checkpoints
            WHERE task_id = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (task_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_checkpoint(row)

    def list_pending_tasks_sync(self) -> list[CheckpointedTask]:
        """Return all tasks with status IN ('active', 'paused') synchronously."""
        cur = self._conn.execute(
            """
            SELECT checkpoint_id, task_id, task_name, original_prompt,
                   completed_steps, remaining_steps, context_snapshot,
                   turn_count, max_turns, status, created_at, updated_at, resume_after
            FROM task_checkpoints
            WHERE status IN ('active', 'paused')
            ORDER BY updated_at ASC
            """
        )
        rows = cur.fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    def mark_completed_sync(self, task_id: str, final_result: Any = None) -> bool:
        """Mark task checkpoint as completed synchronously."""
        now = time.time()
        cur = self._conn.execute(
            "UPDATE task_checkpoints SET status = 'completed', updated_at = ? WHERE task_id = ?",
            (now, task_id),
        )
        self._conn.commit()
        self._event_store.append_sync(
            "task_durable_completed",
            task_id=task_id,
            payload={"final_result": str(final_result)[:200] if final_result else None},
        )
        return cur.rowcount > 0

    def mark_failed_sync(self, task_id: str, error: str = "") -> bool:
        """Mark task checkpoint as failed synchronously."""
        now = time.time()
        cur = self._conn.execute(
            "UPDATE task_checkpoints SET status = 'failed', updated_at = ? WHERE task_id = ?",
            (now, task_id),
        )
        self._conn.commit()
        self._event_store.append_sync(
            "task_durable_failed",
            task_id=task_id,
            payload={"error": error},
        )
        return cur.rowcount > 0

    # ─────────────────────────────────────────────────────────────────────────
    # Asynchronous Interface (Main Runtime Path)
    # ─────────────────────────────────────────────────────────────────────────

    async def checkpoint_task(
        self,
        task_id: str,
        prompt: str = "",
        completed_steps: Optional[list[Any]] = None,
        remaining_steps: Optional[list[Any]] = None,
        context: Optional[dict[str, Any]] = None,
        turn_count: int = 0,
        task_name: str = "",
        max_turns: int = 25,
        status: str = "active",
        resume_after: Optional[float] = None,
        original_prompt: str = "",
        resume_after_seconds: Optional[float] = None,
    ) -> CheckpointedTask:
        """Async wrapper for checkpoint_task_sync executing in SQLite thread pool."""
        async with self._event_store._get_lock():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                self.checkpoint_task_sync,
                task_id,
                prompt,
                completed_steps,
                remaining_steps,
                context,
                turn_count,
                task_name,
                max_turns,
                status,
                resume_after,
                original_prompt,
                resume_after_seconds,
            )

    async def get_checkpoint(self, task_id: str) -> Optional[CheckpointedTask]:
        """Async lookup of checkpoint."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_checkpoint_sync, task_id)

    async def list_pending_tasks(self) -> list[CheckpointedTask]:
        """Return all tasks with status='active' or 'paused'."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.list_pending_tasks_sync)

    async def schedule_resume(self, task_id: str, resume_after_seconds: float) -> bool:
        """Set resume_after timestamp for proactive orchestrator resumption."""
        resume_ts = time.time() + max(0.0, float(resume_after_seconds))
        async with self._event_store._get_lock():
            def _update():
                cur = self._conn.execute(
                    "UPDATE task_checkpoints SET resume_after = ?, status = 'paused', updated_at = ? WHERE task_id = ?",
                    (resume_ts, time.time(), task_id),
                )
                self._conn.commit()
                return cur.rowcount > 0

            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(None, _update)
            if ok:
                logger.info(
                    "[DurableTaskEngine] Scheduled resume for task %s in %.1fs (at %f)",
                    task_id, resume_after_seconds, resume_ts,
                )
            return ok

    async def mark_completed(self, task_id: str, final_result: Any = None) -> bool:
        """Async update status to 'completed'."""
        async with self._event_store._get_lock():
            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(None, self.mark_completed_sync, task_id, final_result)
            if self._task_manager:
                try:
                    await self._task_manager.complete_task(task_id, final_result=final_result)
                except Exception as e:
                    logger.debug("[DurableTaskEngine] Error syncing completion with TaskManager: %s", e)
            return ok

    async def mark_failed(self, task_id: str, error: str = "") -> bool:
        """Async update status to 'failed'."""
        async with self._event_store._get_lock():
            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(None, self.mark_failed_sync, task_id, error)
            if self._task_manager:
                try:
                    await self._task_manager.fail_task(task_id, error_message=error)
                except Exception as e:
                    logger.debug("[DurableTaskEngine] Error syncing failure with TaskManager: %s", e)
            return ok

    async def resume_task(self, task_id: str) -> Optional[ResumedTask]:
        """
        Load checkpoint from SQLite, reconstruct execution context and prompt,
        sync state with TaskManager, and return ResumedTask.
        """
        cp = await self.get_checkpoint(task_id)
        if not cp:
            logger.warning("[DurableTaskEngine] Cannot resume task %s — checkpoint not found", task_id)
            return None

        # Reconstruct canonical Task contract if TaskManager available
        task_obj: Optional[Task] = None
        if self._task_manager:
            task_obj = await self._task_manager.get_task(task_id)
            if not task_obj:
                task_obj = await self._task_manager.create_task(
                    request=cp.original_prompt,
                    goal=cp.task_name,
                    task_id=task_id,
                )
            await self._task_manager.set_state(task_id, TaskState.RUNNING)
        else:
            task_obj = Task(
                task_id=task_id,
                request_id=f"req_{task_id}",
                conversation_id=cp.context_snapshot.get("conversation_id", "default_session"),
                goal=cp.task_name,
                state=TaskState.RUNNING,
            )

        completed_summary = ""
        if cp.completed_steps:
            completed_summary = "\nCompleted steps so far:\n" + "\n".join(
                f"- {s if isinstance(s, str) else json.dumps(s)}" for s in cp.completed_steps
            )

        remaining_summary = ""
        if cp.remaining_steps:
            remaining_summary = "\nRemaining steps to execute:\n" + "\n".join(
                f"- {s if isinstance(s, str) else json.dumps(s)}" for s in cp.remaining_steps
            )

        continuation_prompt = (
            f"[RESUMED SESSION - Turn {cp.turn_count}/{cp.max_turns}]\n"
            f"Original Goal: {cp.original_prompt}\n"
            f"{completed_summary}\n"
            f"{remaining_summary}\n"
            f"Continue solving the goal directly from the remaining steps."
        )

        reconstructed_context = dict(cp.context_snapshot)
        reconstructed_context["is_resumed"] = True
        reconstructed_context["checkpoint_id"] = cp.checkpoint_id
        reconstructed_context["turn_count"] = cp.turn_count

        async with self._event_store._get_lock():
            def _mark_active():
                self._conn.execute(
                    "UPDATE task_checkpoints SET status = 'active', updated_at = ? WHERE checkpoint_id = ?",
                    (time.time(), cp.checkpoint_id),
                )
                self._conn.commit()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _mark_active)

        logger.info("[DurableTaskEngine] Resumed task %s (turn %d, %d remaining steps)", task_id, cp.turn_count, len(cp.remaining_steps))
        return ResumedTask(
            task=task_obj,
            checkpoint=cp,
            reconstructed_context=reconstructed_context,
            continuation_prompt=continuation_prompt,
        )
