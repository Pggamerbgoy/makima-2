"""Local reminder scheduler for Makima automation tasks.

Supports:
- One-shot reminders (delay_seconds)
- Recurring reminders: daily, weekly, monthly, custom interval
- Cron-like scheduling with hour/minute/day-of-week
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("makima.reminder_service")

VALID_RECURRENCE = ("none", "daily", "weekly", "monthly", "interval")


@dataclass
class Reminder:
    id: str
    text: str
    due_at: float
    recurrence: str = "none"
    interval_seconds: int = 0
    hour: int = -1
    minute: int = 0
    day_of_week: int = -1
    task: asyncio.Task | None = None
    last_fired: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "due_at": self.due_at,
            "recurrence": self.recurrence,
            "interval_seconds": self.interval_seconds,
            "hour": self.hour,
            "minute": self.minute,
            "day_of_week": self.day_of_week,
            "status": "scheduled",
            "last_fired": self.last_fired,
        }


class ReminderService:
    def __init__(self, ws_broadcast=None):
        self.ws_broadcast = ws_broadcast
        self._reminders: dict[str, Reminder] = {}

    async def start(self) -> None:
        logger.info("Reminder service started")

    async def stop(self) -> None:
        for reminder in self._reminders.values():
            if reminder.task and not reminder.task.done():
                reminder.task.cancel()
        self._reminders.clear()

    async def set_reminder(self, text: str, delay_seconds: int) -> str:
        text = str(text).strip()
        delay = int(delay_seconds)
        if not text:
            raise ValueError("Reminder text cannot be empty")
        if delay < 1 or delay > 60 * 60 * 24 * 365:
            raise ValueError("Reminder delay must be between 1 second and 1 year")

        reminder_id = f"rem-{uuid.uuid4().hex[:10]}"
        reminder = Reminder(reminder_id, text, time.time() + delay)
        reminder.task = asyncio.create_task(self._deliver(reminder))
        self._reminders[reminder_id] = reminder
        return f"Reminder set for {delay} seconds from now (id: {reminder_id})."

    async def set_recurring(
        self,
        text: str,
        recurrence: str = "daily",
        hour: int = 9,
        minute: int = 0,
        interval_seconds: int = 0,
        day_of_week: int = -1,
    ) -> str:
        text = str(text).strip()
        if not text:
            raise ValueError("Reminder text cannot be empty")
        if recurrence not in VALID_RECURRENCE:
            raise ValueError(f"Recurrence must be one of {VALID_RECURRENCE}")

        import datetime
        now = datetime.datetime.now()

        if recurrence == "interval" and interval_seconds > 0:
            due_at = time.time() + interval_seconds
        else:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                if recurrence == "daily":
                    target += datetime.timedelta(days=1)
                elif recurrence == "weekly":
                    target += datetime.timedelta(days=7)
                elif recurrence == "monthly":
                    month = target.month + 1
                    year = target.year
                    if month > 12:
                        month = 1
                        year += 1
                    target = target.replace(year=year, month=month)
            due_at = target.timestamp()

        reminder_id = f"rem-{uuid.uuid4().hex[:10]}"
        reminder = Reminder(
            id=reminder_id,
            text=text,
            due_at=due_at,
            recurrence=recurrence,
            interval_seconds=interval_seconds,
            hour=hour,
            minute=minute,
            day_of_week=day_of_week,
        )
        reminder.task = asyncio.create_task(self._deliver_recurring(reminder))
        self._reminders[reminder_id] = reminder
        return f"Recurring reminder ({recurrence}) set (id: {reminder_id}). Next: {time.ctime(due_at)}."

    async def cancel_reminder(self, reminder_id: str) -> str:
        reminder = self._reminders.pop(reminder_id, None)
        if not reminder:
            return f"No active reminder found with id {reminder_id}."
        if reminder.task and not reminder.task.done():
            reminder.task.cancel()
        return f"Cancelled reminder {reminder_id}."

    def list_reminders(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self._reminders.values() if not r.task or not r.task.done()]

    async def _deliver(self, reminder: Reminder) -> None:
        try:
            await asyncio.sleep(max(0, reminder.due_at - time.time()))
            await self._send_notification(reminder)
            logger.info("Delivered reminder %s", reminder.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder delivery failed for %s", reminder.id)
        finally:
            self._reminders.pop(reminder.id, None)

    async def _deliver_recurring(self, reminder: Reminder) -> None:
        try:
            while True:
                wait = max(0, reminder.due_at - time.time())
                await asyncio.sleep(wait)
                await self._send_notification(reminder)
                reminder.last_fired = time.time()

                import datetime
                now = datetime.datetime.now()
                if reminder.recurrence == "daily":
                    next_time = now.replace(hour=reminder.hour, minute=reminder.minute, second=0, microsecond=0) + datetime.timedelta(days=1)
                elif reminder.recurrence == "weekly":
                    next_time = now.replace(hour=reminder.hour, minute=reminder.minute, second=0, microsecond=0) + datetime.timedelta(weeks=1)
                elif reminder.recurrence == "monthly":
                    month = now.month + 1
                    year = now.year
                    if month > 12:
                        month = 1
                        year += 1
                    next_time = now.replace(year=year, month=month, hour=reminder.hour, minute=reminder.minute, second=0, microsecond=0)
                elif reminder.recurrence == "interval":
                    next_time = now + datetime.timedelta(seconds=reminder.interval_seconds)
                else:
                    break

                reminder.due_at = next_time.timestamp()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Recurring reminder failed: %s", reminder.id)

    async def _send_notification(self, reminder: Reminder) -> None:
        if self.ws_broadcast:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.WSMessage(
                v=ws_protocol.PROTOCOL_VERSION,
                type="notification_received",
                payload={
                    "source": "reminder",
                    "reminder_id": reminder.id,
                    "title": "Makima Reminder",
                    "message": reminder.text,
                    "recurrence": reminder.recurrence,
                },
            ))