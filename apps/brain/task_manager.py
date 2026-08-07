"""Makima v7.1 — TaskManager

SQLite-backed task/to-do manager with priorities, due dates, tags,
and natural-language task creation via LLM entity extraction.

Spec:
- CRUD: create_task, update_task, complete_task, delete_task, list_tasks
- Filter: by status (pending/done), priority (low/med/high/urgent), tag, due date
- Recurring: optional recurrence rule (daily/weekly/monthly) auto-creates next task on completion
- Persistence: SQLite WAL mode, same pattern as EternalMemory
- WS events: task_created, task_updated, task_completed, task_deleted
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("makima.task_manager")

VALID_PRIORITIES = ("low", "med", "high", "urgent")
VALID_STATUSES = ("pending", "in_progress", "done", "cancelled")
VALID_RECURRENCE = ("none", "daily", "weekly", "monthly", "yearly")


@dataclass
class Task:
    id: str
    title: str
    description: str
    priority: str
    status: str
    tags: list[str]
    due_at: Optional[float]
    recurrence: str
    created_at: float
    updated_at: float
    completed_at: Optional[float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "status": self.status,
            "tags": self.tags,
            "due_at": self.due_at,
            "recurrence": self.recurrence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


class TaskManager:
    def __init__(self, config: dict[str, Any] | None = None, ws_broadcast=None):
        cfg = config or {}
        task_cfg = cfg.get("task_manager", {}) if isinstance(cfg, dict) else {}
        base_path = os.path.expanduser(task_cfg.get("db_path", "~/.makima/tasks.sqlite"))
        self.db_path = Path(base_path)
        self.ws_broadcast = ws_broadcast
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self._ensure_db()
        logger.info("TaskManager started")

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
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                priority TEXT DEFAULT 'med',
                status TEXT DEFAULT 'pending',
                tags TEXT DEFAULT '[]',
                due_at REAL,
                recurrence TEXT DEFAULT 'none',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at)")
        conn.commit()
        self._conn = conn

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            title=row["title"],
            description=row["description"] or "",
            priority=row["priority"],
            status=row["status"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            due_at=row["due_at"],
            recurrence=row["recurrence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    async def create_task(
        self,
        title: str,
        description: str = "",
        priority: str = "med",
        tags: list[str] | None = None,
        due_at: float | None = None,
        recurrence: str = "none",
    ) -> str:
        title = str(title).strip()
        if not title:
            raise ValueError("Task title cannot be empty")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Priority must be one of {VALID_PRIORITIES}")
        if recurrence not in VALID_RECURRENCE:
            raise ValueError(f"Recurrence must be one of {VALID_RECURRENCE}")

        now = time.time()
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        task = Task(
            id=task_id,
            title=title,
            description=str(description),
            priority=priority,
            status="pending",
            tags=tags or [],
            due_at=due_at,
            recurrence=recurrence,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )

        async with self._lock:
            try:
                self._conn.execute(
                    """INSERT INTO tasks(id, title, description, priority, status, tags,
                       due_at, recurrence, created_at, updated_at, completed_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task.id, task.title, task.description, task.priority, task.status,
                     json.dumps(task.tags), task.due_at, task.recurrence,
                     task.created_at, task.updated_at, task.completed_at),
                )
                self._conn.commit()
            except Exception as e:
                logger.error("create_task failed: %s", e)
                return f"Error creating task: {e}"

        await self._broadcast("task_created", task.as_dict())
        return json.dumps(task.as_dict(), default=str)

    async def update_task(
        self,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        tags: list[str] | None = None,
        due_at: float | None = None,
        recurrence: str | None = None,
        status: str | None = None,
    ) -> str:
        if priority and priority not in VALID_PRIORITIES:
            raise ValueError(f"Priority must be one of {VALID_PRIORITIES}")
        if status and status not in VALID_STATUSES:
            raise ValueError(f"Status must be one of {VALID_STATUSES}")
        if recurrence and recurrence not in VALID_RECURRENCE:
            raise ValueError(f"Recurrence must be one of {VALID_RECURRENCE}")

        now = time.time()
        updates = []
        params = []
        for field, val in [("title", title), ("description", description),
                           ("priority", priority), ("status", status),
                           ("due_at", due_at), ("recurrence", recurrence)]:
            if val is not None:
                updates.append(f"{field} = ?")
                params.append(val)
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags))
        if not updates:
            return "No fields to update"

        updates.append("updated_at = ?")
        params.append(now)
        params.append(task_id)

        async with self._lock:
            try:
                self._conn.execute(
                    f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                self._conn.commit()
                row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if row:
                    task = self._row_to_task(row)
                    await self._broadcast("task_updated", task.as_dict())
                    return json.dumps(task.as_dict(), default=str)
                return f"Task {task_id} not found"
            except Exception as e:
                logger.error("update_task failed: %s", e)
                return f"Error updating task: {e}"

    async def complete_task(self, task_id: str) -> str:
        now = time.time()
        async with self._lock:
            try:
                row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if not row:
                    return f"Task {task_id} not found"
                self._conn.execute(
                    "UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, task_id),
                )
                self._conn.commit()

                updated_row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                task = self._row_to_task(updated_row)
                await self._broadcast("task_completed", task.as_dict())

                if task.recurrence != "none":
                    next_due = self._next_recurrence(task.due_at or now, task.recurrence)
                    await self.create_task(
                        title=task.title,
                        description=task.description,
                        priority=task.priority,
                        tags=task.tags,
                        due_at=next_due,
                        recurrence=task.recurrence,
                    )
                return json.dumps(task.as_dict(), default=str)
            except Exception as e:
                logger.error("complete_task failed: %s", e)
                return f"Error completing task: {e}"

    async def delete_task(self, task_id: str) -> str:
        async with self._lock:
            try:
                row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if not row:
                    return f"Task {task_id} not found"
                task = self._row_to_task(row)
                self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                self._conn.commit()
                await self._broadcast("task_deleted", task.as_dict())
                return f"Deleted task: {task.title}"
            except Exception as e:
                logger.error("delete_task failed: %s", e)
                return f"Error deleting task: {e}"

    async def list_tasks(
        self,
        status: str | None = None,
        priority: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> str:
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if priority:
            conditions.append("priority = ?")
            params.append(priority)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        async with self._lock:
            try:
                rows = self._conn.execute(
                    f"SELECT * FROM tasks {where} ORDER BY "
                    "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
                    "WHEN 'med' THEN 2 WHEN 'low' THEN 3 END, "
                    "COALESCE(due_at, 9999999999) ASC LIMIT ?",
                    params,
                ).fetchall()
                tasks = [self._row_to_task(r).as_dict() for r in rows]
                return json.dumps(tasks, default=str)
            except Exception as e:
                logger.error("list_tasks failed: %s", e)
                return f"Error listing tasks: {e}"

    async def get_overdue_tasks(self) -> str:
        now = time.time()
        async with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE status != 'done' AND status != 'cancelled' "
                    "AND due_at IS NOT NULL AND due_at < ? ORDER BY due_at ASC",
                    (now,),
                ).fetchall()
                tasks = [self._row_to_task(r).as_dict() for r in rows]
                return json.dumps(tasks, default=str)
            except Exception as e:
                logger.error("get_overdue_tasks failed: %s", e)
                return f"Error: {e}"

    def _next_recurrence(self, current_due: float, recurrence: str) -> float:
        import datetime
        dt = datetime.datetime.fromtimestamp(current_due)
        if recurrence == "daily":
            dt += datetime.timedelta(days=1)
        elif recurrence == "weekly":
            dt += datetime.timedelta(weeks=1)
        elif recurrence == "monthly":
            month = dt.month + 1
            year = dt.year
            if month > 12:
                month = 1
                year += 1
            dt = dt.replace(year=year, month=month)
        elif recurrence == "yearly":
            dt = dt.replace(year=dt.year + 1)
        return dt.timestamp()

    async def _broadcast(self, event_type: str, data: dict) -> None:
        if self.ws_broadcast:
            try:
                from . import ws_protocol
                await self.ws_broadcast(ws_protocol.WSMessage(
                    v=ws_protocol.PROTOCOL_VERSION,
                    type=event_type,
                    payload=data,
                ))
            except Exception:
                pass
