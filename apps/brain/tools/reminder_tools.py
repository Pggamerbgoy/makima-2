"""
Makima OS — Reminder & Routine Tools (DurableTaskEngine-backed).

Real scheduling primitives for chat/voice/agents:
  - set_reminder   — one-shot (delay_s) or repeating (repeat_s) reminder
  - list_reminders — pending reminders with fire times
  - cancel_reminder — cancel by reminder id

No cron library needed: one-shot uses resume_after timestamps, repeats
re-arm on every fire (see ProactiveOrchestrator resume loop). The engine
(DurableTaskEngine) is resolved lazily per call so boot order never matters.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

logger = logging.getLogger("makima.tools.reminder")

_DTE_SERVICE_NAME = "durable_task_engine"
_MAX_DELAY_S = 30 * 24 * 3600.0  # 30 days
_MIN_REPEAT_S = 60.0


def _get_dte(services: Any) -> Any | None:
    try:
        if services is None:
            return None
        get = getattr(services, "get", None)
        if callable(get):
            return get(_DTE_SERVICE_NAME)
        if isinstance(services, dict):
            return services.get(_DTE_SERVICE_NAME)
    except Exception as _exc:
        logger.debug("suppressed: %s", _exc)
    return None


def _resolve_services(services: Any, kwargs: Any, func: Any) -> Any:
    """Resolve services via arg → call-context → registration-time stash (late binding)."""
    if services is not None:
        return services
    try:
        ctx = (kwargs or {}).get("context") if isinstance(kwargs, dict) else None
        if isinstance(ctx, dict) and ctx.get("services") is not None:
            return ctx["services"]
    except Exception as _exc:
        logger.debug("suppressed: %s", _exc)
    try:
        return getattr(func, "_makima_services", None)
    except Exception as _exc:
        logger.debug("suppressed: %s", _exc)
        return None


def _ctx_dict(context: Any) -> dict[str, Any]:
    """Coerce the context arg to a plain dict (dict via SDK path, ToolContext via registry)."""
    if isinstance(context, dict):
        try:
            return dict(context)
        except Exception as _exc:
            logger.debug("suppressed: %s", _exc)
    return {}


def _fmt_in(delay_s: float) -> str:
    if delay_s < 90:
        return f"{int(delay_s)}s"
    if delay_s < 5400:
        return f"{delay_s / 60:.1f} min"
    if delay_s < 172800:
        return f"{delay_s / 3600:.1f} h"
    return f"{delay_s / 86400:.1f} days"


async def set_reminder(
    text: str,
    delay_s: float = 600,
    repeat_s: float = 0,
    name: str = "",
    context: dict[str, Any] | None = None,
    services: Any = None,
    **kwargs: Any,
) -> str:
    """Set a one-shot or repeating reminder. Fires via the proactive resume loop."""
    services = _resolve_services(services, kwargs, set_reminder)
    dte = _get_dte(services)
    if dte is None:
        return "[reminder] Scheduler unavailable (DurableTaskEngine not running)."

    clean = (text or "").strip()
    if not clean:
        return "[reminder] No reminder text provided."
    try:
        delay = float(delay_s)
    except (TypeError, ValueError):
        return "[reminder] delay_s must be a number of seconds."
    if delay <= 0:
        return "[reminder] delay_s must be positive."
    if delay > _MAX_DELAY_S:
        return "[reminder] delay_s exceeds the 30-day maximum."
    try:
        repeat = float(repeat_s or 0)
    except (TypeError, ValueError):
        repeat = 0.0
    if repeat and repeat < _MIN_REPEAT_S:
        return f"[reminder] repeat_s must be 0 or >= {_MIN_REPEAT_S:.0f}s."

    task_id = f"rem_{uuid.uuid4().hex[:8]}"
    ctx = _ctx_dict(context)
    ctx.update({
        "reminder": True,
        "repeat_interval_s": repeat or None,
        "conversation_id": ctx.get("conversation_id", "default_session"),
    })
    title = (name or clean[:60]).strip()
    try:
        await dte.checkpoint_task(
            task_id=task_id,
            prompt=clean,
            task_name=title,
            context=ctx,
            resume_after_seconds=delay,
            status="paused" if delay > 0 else "active",
            original_prompt=clean,
        )
    except Exception as e:
        logger.warning("[reminder] set_reminder failed: %s", e)
        return f"[reminder] Could not schedule: {e}"
    suffix = f", repeats every {_fmt_in(repeat)}" if repeat else ""
    return f"[reminder] Set '{title}' (id={task_id}) — fires in {_fmt_in(delay)}{suffix}."


async def list_reminders(services: Any = None, **kwargs: Any) -> str:
    """List pending reminders with fire times."""
    services = _resolve_services(services, kwargs, list_reminders)
    dte = _get_dte(services)
    if dte is None:
        return "[reminder] Scheduler unavailable (DurableTaskEngine not running)."
    try:
        pending = await dte.list_pending_tasks()
    except Exception as e:
        logger.warning("[reminder] list failed: %s", e)
        return f"[reminder] Could not list: {e}"
    now = time.time()
    rows = []
    for cp in pending or []:
        ra = getattr(cp, "resume_after", None)
        if not ra:
            continue
        ctx = getattr(cp, "context_snapshot", None) or {}
        rep = ctx.get("repeat_interval_s") if isinstance(ctx, dict) else None
        rows.append(
            f"• {cp.task_id} — '{cp.task_name}' — fires in {_fmt_in(max(0.0, ra - now))}"
            + (f" (repeats every {_fmt_in(rep)})" if rep else "")
        )
    if not rows:
        return "[reminder] No pending reminders."
    return "[reminder] Pending:\n" + "\n".join(rows)


async def cancel_reminder(reminder_id: str, services: Any = None, **kwargs: Any) -> str:
    """Cancel a pending reminder by id."""
    services = _resolve_services(services, kwargs, cancel_reminder)
    dte = _get_dte(services)
    if dte is None:
        return "[reminder] Scheduler unavailable (DurableTaskEngine not running)."
    rid = (reminder_id or "").strip()
    if not rid:
        return "[reminder] No reminder id provided."
    try:
        cp = await dte.get_checkpoint(rid)
        if cp is None:
            return f"[reminder] No reminder found with id '{rid}'."
        ok = await dte.mark_completed(rid, final_result="cancelled by user")
    except Exception as e:
        logger.warning("[reminder] cancel failed: %s", e)
        return f"[reminder] Could not cancel: {e}"
    return f"[reminder] Cancelled '{cp.task_name}' (id={rid})." if ok else f"[reminder] Cancel failed for id '{rid}'."


_REMINDER_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "set_reminder",
        "description": "Set a one-shot or repeating reminder that fires later via the proactive loop. Use when the user says 'remind me', 'har 10 min me', 'kal subah yaad dilao'.",
        "func": set_reminder,
        "schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to remind about."},
                "delay_s": {"type": "number", "description": "Seconds from now when it fires (e.g. 600 = 10 min)."},
                "repeat_s": {"type": "number", "description": "Repeat interval in seconds (0 = one-shot, min 60)."},
                "name": {"type": "string", "description": "Short title for the reminder."},
            },
            "required": ["text"],
        },
        "category": "automation",
        "agent_hints": ["automation", "messaging", "general"],
        "task_tags": ["reminder", "schedule", "routine", "automation"],
        "priority": 1,
        "is_destructive": False,
    },
    {
        "name": "list_reminders",
        "description": "List pending reminders with fire times. Use when the user asks what reminders exist.",
        "func": list_reminders,
        "schema": {"type": "object", "properties": {}},
        "category": "automation",
        "agent_hints": ["automation", "general"],
        "task_tags": ["reminder", "schedule", "list", "automation"],
        "priority": 1,
        "is_destructive": False,
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending reminder by its id (see list_reminders).",
        "func": cancel_reminder,
        "schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "Reminder id, e.g. rem_a1b2c3d4."},
            },
            "required": ["reminder_id"],
        },
        "category": "automation",
        "agent_hints": ["automation", "general"],
        "task_tags": ["reminder", "schedule", "cancel", "automation"],
        "priority": 1,
        "is_destructive": False,
    },
)


def register_reminder_tools(tool_registry: Any, services: Any = None) -> None:
    """Register reminder/routine tools into Makima's ToolRegistry."""
    registered = 0
    for spec in _REMINDER_SPECS:
        try:
            if hasattr(tool_registry, "register_tool"):
                tool_registry.register_tool(
                    name=spec["name"],
                    description=spec["description"],
                    func=spec["func"],
                    schema=spec["schema"],
                    category=spec.get("category", "automation"),
                    agent_hints=spec.get("agent_hints", []),
                    task_tags=spec.get("task_tags", []),
                    priority=spec.get("priority", 1),
                    is_destructive=spec.get("is_destructive", False),
                )
                registered += 1
            elif hasattr(tool_registry, "register"):
                tool_registry.register(
                    name=spec["name"],
                    func=spec["func"],
                    description=spec["description"],
                    schema=spec["schema"],
                    category=spec.get("category", "automation"),
                )
                registered += 1
        except Exception as e:
            logger.warning("Failed to register reminder tool '%s': %s", spec["name"], e)
    # Stash services on the tool funcs so runtime calls resolve the engine lazily.
    for spec in _REMINDER_SPECS:
        try:
            setattr(spec["func"], "_makima_services", services)
        except Exception as _exc:
            logger.debug("suppressed: %s", _exc)
    logger.info("Successfully registered %d reminder tools into ToolRegistry.", registered)
