"""
Makima OS v9.0 — Canonical Task Manager
Single authority for task lifecycle, state transitions, subtask trees, and cancellation.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from .contracts import Task, TaskState, UserRequest

logger = logging.getLogger("makima.task_manager")


class TaskManager:
    """
    Canonical manager for all task lifecycles in Makima OS.
    Eliminates fragmented task dictionaries and uncoordinated state transitions.
    """

    def __init__(self, default_timeout_s: float = 60.0) -> None:
        self.default_timeout_s = default_timeout_s
        self._tasks: dict[str, Task] = {}
        self._cancellation_events: dict[str, asyncio.Event] = {}
        self._task_handles: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    # ─────────────────────────────────────────────────────────────────────────
    # Task Creation & Registration
    # ─────────────────────────────────────────────────────────────────────────
    async def create_task(
        self,
        request: UserRequest | str,
        goal: str = "",
        intent: str = "general",
        grounded_slots: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
        timeout_s: float | None = None,
        task_id: str | None = None,
    ) -> Task:
        """Create and register a new stateful task."""
        if isinstance(request, str):
            req_id = f"req_{uuid.uuid4().hex[:8]}"
            req = UserRequest(
                request_id=req_id,
                conversation_id=req_id,
                raw_message=request,
            )
        else:
            req = request

        tid = task_id or f"task_{uuid.uuid4().hex[:12]}"
        task_goal = goal or req.raw_message

        task = Task(
            task_id=tid,
            request_id=req.request_id,
            conversation_id=req.conversation_id,
            goal=task_goal,
            intent=intent,
            grounded_slots=dict(grounded_slots or {}),
            state=TaskState.PENDING,
            parent_task_id=parent_task_id,
            timeout_s=timeout_s or self.default_timeout_s,
            created_at=time.time(),
        )

        async with self._lock:
            self._tasks[tid] = task
            self._cancellation_events[tid] = asyncio.Event()

            # Link to parent if present
            if parent_task_id and parent_task_id in self._tasks:
                parent = self._tasks[parent_task_id]
                if tid not in parent.subtask_ids:
                    parent.subtask_ids.append(tid)

        logger.debug("[task_manager] Created task %s (goal='%s', parent=%s)", tid, task_goal[:40], parent_task_id)
        return task

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle & State Transitions
    # ─────────────────────────────────────────────────────────────────────────
    async def set_state(
        self,
        task_id: str,
        state: TaskState,
        error_message: str | None = None,
        final_result: Any = None,
    ) -> bool:
        """Atomically transition a task's state."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                logger.warning("[task_manager] set_state called on unknown task %s", task_id)
                return False

            old_state = task.state
            # Terminal State Invariant Guard: CANCELLED or FAILED tasks must not be overwritten by COMPLETED/RUNNING
            if old_state in (TaskState.CANCELLED, TaskState.FAILED) and state not in (TaskState.CANCELLED, TaskState.FAILED):
                logger.debug("[task_manager] Ignoring state transition %s -> %s for terminal task %s", old_state, state, task_id)
                return False

            task.state = state
            if error_message:
                task.error_message = error_message
            if final_result is not None:
                task.final_result = final_result

            if state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
                task.completed_at = time.time()
                # Clean up cancellation event and handle
                self._cancellation_events.pop(task_id, None)
                self._task_handles.pop(task_id, None)

        logger.debug("[task_manager] Task %s state: %s -> %s", task_id, old_state, state)
        return True

    async def complete_task(self, task_id: str, final_result: Any = None) -> bool:
        # Pass final_result directly into set_state to avoid a second lock acquisition
        return await self.set_state(task_id, TaskState.COMPLETED, final_result=final_result)

    async def fail_task(self, task_id: str, error_message: str) -> bool:
        return await self.set_state(task_id, TaskState.FAILED, error_message=error_message)

    async def cancel_task(self, task_id: str, reason: str = "User cancelled") -> bool:
        """Signal cancellation for a task and its entire subtask tree (recursive)."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            # Collect all descendants via BFS without re-acquiring lock
            all_ids: list[str] = []
            queue = [task_id]
            while queue:
                current_id = queue.pop(0)
                all_ids.append(current_id)
                current_task = self._tasks.get(current_id)
                if current_task:
                    queue.extend(current_task.subtask_ids)

            cancelled_count = 0
            for tid in all_ids:
                t = self._tasks.get(tid)
                if not t:
                    continue

                # Trigger cancellation event
                event = self._cancellation_events.get(tid)
                if event:
                    event.set()

                # Cancel asyncio task handle if active
                handle = self._task_handles.get(tid)
                if handle and not handle.done():
                    handle.cancel()

                t.state = TaskState.CANCELLED
                t.error_message = reason
                t.completed_at = time.time()
                self._cancellation_events.pop(tid, None)
                self._task_handles.pop(tid, None)
                cancelled_count += 1

        logger.info(
            "[task_manager] Cancelled task %s + %d descendants (total %d)",
            task_id, cancelled_count - 1, cancelled_count,
        )
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Queries & Introspection
    # ─────────────────────────────────────────────────────────────────────────
    async def get_task(self, task_id: str) -> Task | None:
        async with self._lock:
            return self._tasks.get(task_id)

