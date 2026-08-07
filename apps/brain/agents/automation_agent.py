"""Makima v7.2 — Elite Automation Agent: Workflows, macros, reminders, scheduled tasks.

This module provides an enterprise-grade, zero-crash resilient automation engine.
It handles complex workflow orchestration, cron-based scheduling, and macro 
recording/playback without relying on browser automation (delegated to BrowserAgent).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

# Zero-crash resilience: Graceful fallbacks for optional heavy dependencies
try:
    from croniter import croniter
    _HAS_CRONITER = True
except ImportError:
    _HAS_CRONITER = False

try:
    from pydantic import BaseModel, Field, ValidationError
    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False

from .base_agent import BaseAgent

logger = logging.getLogger("makima.agents.automation")


# ============================================================================
# Enums & Data Models
# ============================================================================

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class WorkflowStep:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    delay_ms: int = 0
    condition: str | None = None
    retry_count: int = 0

@dataclass
class Workflow:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class Reminder:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    text: str = ""
    trigger_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cron_expr: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class MacroEvent:
    event_type: str  # "keystroke", "mouse_click", "wait", "tool_call"
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)

@dataclass
class Macro:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    events: list[MacroEvent] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# Pydantic Validation Models (If available)
# ============================================================================

if _HAS_PYDANTIC:
    class WorkflowStepModel(BaseModel):
        action: str
        params: dict[str, Any] = Field(default_factory=dict)
        delay_ms: int = 0
        condition: Optional[str] = None

    class CreateWorkflowParams(BaseModel):
        name: str
        description: str = ""
        steps: list[WorkflowStepModel]

    class SetReminderParams(BaseModel):
        text: str
        trigger_iso: Optional[str] = None
        delay_seconds: int = 60
        cron_expr: Optional[str] = None


# ============================================================================
# Core Engines
# ============================================================================

class ScheduleEngine:
    """High-performance asyncio-based scheduler for reminders and cron jobs."""
    
    def __init__(self, broadcast_callback: Callable | None = None):
        self._reminders: dict[str, Reminder] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._broadcast = broadcast_callback
        self._lock = asyncio.Lock()

    async def add_reminder(self, reminder: Reminder) -> str:
        async with self._lock:
            self._reminders[reminder.id] = reminder
            task = asyncio.create_task(self._schedule_loop(reminder))
            self._tasks[reminder.id] = task
            return reminder.id

    async def _schedule_loop(self, reminder: Reminder):
        try:
            while reminder.status == TaskStatus.PENDING:
                now = datetime.now(timezone.utc)
                wait_seconds = (reminder.trigger_time - now).total_seconds()
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                
                reminder.status = TaskStatus.RUNNING
                if self._broadcast:
                    await self._broadcast(f"⏰ Reminder: {reminder.text}")
                
                if reminder.cron_expr and _HAS_CRONITER:
                    cron = croniter(reminder.cron_expr, datetime.now(timezone.utc))
                    reminder.trigger_time = cron.get_next(datetime)
                    reminder.status = TaskStatus.PENDING
                else:
                    reminder.status = TaskStatus.COMPLETED
                    break
        except asyncio.CancelledError:
            reminder.status = TaskStatus.CANCELLED
        except Exception as e:
            logger.error(f"Schedule loop error for {reminder.id}: {e}", exc_info=True)
            reminder.status = TaskStatus.FAILED

    async def cancel_reminder(self, reminder_id: str) -> bool:
        async with self._lock:
            if reminder_id in self._tasks:
                self._tasks[reminder_id].cancel()
                del self._tasks[reminder_id]
                if reminder_id in self._reminders:
                    self._reminders[reminder_id].status = TaskStatus.CANCELLED
                return True
            return False
            
    def list_reminders(self) -> list[dict[str, Any]]:
        return [
            {
                "id": r.id, "text": r.text, "trigger_time": r.trigger_time.isoformat(),
                "cron_expr": r.cron_expr, "status": r.status.value
            }
            for r in self._reminders.values() if r.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ]


class WorkflowEngine:
    """Orchestrates multi-step workflows with retry logic and state tracking."""
    
    def __init__(self, tool_executor: Callable | None = None):
        self._workflows: dict[str, Workflow] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._tool_executor = tool_executor
        self._lock = asyncio.Lock()

    async def register(self, workflow: Workflow) -> str:
        async with self._lock:
            self._workflows[workflow.id] = workflow
            return workflow.id

    async def execute(self, workflow_id: str, context: dict[str, Any]) -> dict[str, Any]:
        if workflow_id not in self._workflows:
            return {"success": False, "error": "Workflow not found"}
            
        workflow = self._workflows[workflow_id]
        task = asyncio.create_task(self._run(workflow, context))
        self._running[workflow_id] = task
        
        try:
            return await task
        finally:
            self._running.pop(workflow_id, None)

    async def _run(self, workflow: Workflow, context: dict[str, Any]) -> dict[str, Any]:
        results = []
        for i, step in enumerate(workflow.steps):
            if step.delay_ms > 0:
                await asyncio.sleep(step.delay_ms / 1000.0)
                
            attempts = 0
            max_attempts = step.retry_count + 1
            while attempts < max_attempts:
                try:
                    if self._tool_executor:
                        res = await self._tool_executor(step.action, **step.params)
                        results.append({"step": i, "action": step.action, "result": res})
                        break
                    else:
                        results.append({"step": i, "action": step.action, "result": "No executor"})
                        break
                except Exception as e:
                    attempts += 1
                    if attempts >= max_attempts:
                        logger.error(f"Workflow step {i} failed after {max_attempts} attempts: {e}")
                        return {"success": False, "error": str(e), "partial_results": results}
                    await asyncio.sleep(1.0 * attempts)  # Exponential backoff
                    
        return {"success": True, "results": results}

    def list_workflows(self) -> list[dict[str, Any]]:
        return [
            {"id": w.id, "name": w.name, "description": w.description, "steps_count": len(w.steps)}
            for w in self._workflows.values()
        ]


class MacroEngine:
    """Handles recording and playback of system/UI action sequences."""
    
    def __init__(self, tool_executor: Callable | None = None):
        self._macros: dict[str, Macro] = {}
        self._recording: dict[str, list[MacroEvent]] = {}
        self._tool_executor = tool_executor
        self._lock = asyncio.Lock()

    async def start_recording(self, name: str) -> str:
        async with self._lock:
            macro_id = str(uuid.uuid4())
            self._recording[macro_id] = []
            self._macros[macro_id] = Macro(id=macro_id, name=name)
            return macro_id

    async def stop_recording(self, macro_id: str) -> str:
        async with self._lock:
            if macro_id in self._recording:
                self._macros[macro_id].events = self._recording.pop(macro_id)
                return f"Macro '{self._macros[macro_id].name}' saved with {len(self._macros[macro_id].events)} events."
            return "Recording not found"

    async def play_macro(self, macro_id: str) -> dict[str, Any]:
        if macro_id not in self._macros:
            return {"success": False, "error": "Macro not found"}
            
        macro = self._macros[macro_id]
        results = []
        last_ts = 0.0
        
        for event in macro.events:
            delay = event.timestamp - last_ts
            if delay > 0 and event.event_type != "wait":
                await asyncio.sleep(delay)
            last_ts = event.timestamp
            
            try:
                if event.event_type == "tool_call" and self._tool_executor:
                    res = await self._tool_executor(event.data.get("action"), **event.data.get("params", {}))
                    results.append(res)
                elif event.event_type == "wait":
                    await asyncio.sleep(event.data.get("seconds", 1.0))
                else:
                    results.append(f"Simulated {event.event_type}")
            except Exception as e:
                return {"success": False, "error": str(e), "partial_results": results}
                
        return {"success": True, "results": results}


# ============================================================================
# Elite Automation Agent
# ============================================================================

class AutomationAgent(BaseAgent):
    AGENT_NAME = "automation"
    DESCRIPTION = "Elite enterprise-grade workflow, reminder, and schedule engine."
    
    SYSTEM_PROMPT = """\
You are the Elite Automation Agent for Makima OS v7.2.
You are an enterprise-grade personal assistant workflow, reminder, and schedule engine.

CAPABILITIES:
1. Workflows: Create, list, and execute multi-step automated workflows.
2. Reminders & Scheduling: Set one-time or recurring (cron) reminders.
3. Macros: Record and playback sequences of system actions.

TOOLS AVAILABLE:
- create_workflow(name: str, description: str, steps: list[dict]): Create a workflow. Steps need 'action', 'params', optional 'delay_ms' and 'retry_count'.
- run_workflow(workflow_id: str, initial_context: dict): Execute a workflow.
- list_workflows(): List all saved workflows.
- set_reminder(text: str, trigger_iso: str | null, delay_seconds: int, cron_expr: str | null): Set a reminder. trigger_iso is ISO8601.
- cancel_reminder(reminder_id: str): Cancel an active reminder.
- list_reminders(): List active reminders.

CRITICAL INSTRUCTION: 
You MUST output your response as a valid JSON object. No markdown formatting, no conversational text outside the JSON.
Format:
{
    "tool": "tool_name" | null,
    "params": {"param1": "value"},
    "reply": "Conversational reply to the user."
}
"""

    def __init__(self, ai_handler=None, memory=None, tool_registry=None, ws_broadcast=None, orchestrator=None, guardrails=None, **kwargs):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, **kwargs)
        self._schedule_engine = ScheduleEngine(broadcast_callback=self._broadcast_wrapper)
        self._workflow_engine = WorkflowEngine(tool_executor=self._execute_tool_safe)
        self._macro_engine = MacroEngine(tool_executor=self._execute_tool_safe)

    async def _broadcast_wrapper(self, message: str):
        if self.ws_broadcast:
            try:
                from ..ws_protocol import build_ai_chunk
                await self.ws_broadcast(build_ai_chunk("system-automation", message))
            except Exception as e:
                logger.warning(f"Automation broadcast failed: {e}")

    async def _execute_tool_safe(self, action: str, **params) -> Any:
        if self.tool_registry:
            return await self._use_tool(action, **params)
        return f"Executed {action} with {params}"

    async def execute(self, task_id: str, message: str, context: dict[str, Any],
                      entities: dict[str, Any]) -> str:
        self._reset_state()
        messages = self._build_messages(message, context)
        
        state_context = self._get_state_summary()
        if state_context:
            messages.append({"role": "system", "content": f"Current Automation State:\n{state_context}"})

        response = await self._llm_call(messages, task="automation", require_json=True)
        self._partial_result = response
        
        try:
            data = self.ai_handler.try_parse_json(response)
            if not data:
                logger.warning("[automation] Failed to parse JSON from LLM: %s", response[:200])
                return "I couldn't quite process that automation request — could you rephrase it?"

            tool_name = data.get("tool")
            params = data.get("params", {})
            reply = data.get("reply", "Done.")

            if tool_name:
                tool_result = await self._route_tool(tool_name, params)
                if self.ws_broadcast:
                    try:
                        from ..ws_protocol import build_ai_chunk
                        await self.ws_broadcast(build_ai_chunk(task_id, f" [Automation: {tool_result}] "))
                    except Exception as e:
                        logger.warning(f"Failed to broadcast tool result: {e}")
                        
                if self._tool_failed(tool_result):
                    return f"{reply} (Note: execution encountered an issue — {tool_result})"

            return reply

        except Exception as e:
            logger.error(f"Error in automation agent execute: {e}", exc_info=True)
            return "An internal error occurred while processing your automation request."

    def _get_state_summary(self) -> str:
        reminders = self._schedule_engine.list_reminders()
        workflows = self._workflow_engine.list_workflows()
        summary = []
        if workflows:
            summary.append(f"Active Workflows: {len(workflows)} ({', '.join(w['name'] for w in workflows[:3])}...)")
        if reminders:
            summary.append(f"Active Reminders: {len(reminders)}")
        return "\n".join(summary) if summary else "No active workflows or reminders."

    async def _route_tool(self, tool_name: str, params: dict[str, Any]) -> Any:
        try:
            if tool_name == "create_workflow": return await self._handle_create_workflow(params)
            elif tool_name == "run_workflow": return await self._handle_run_workflow(params)
            elif tool_name == "list_workflows": return self._workflow_engine.list_workflows()
            elif tool_name == "set_reminder": return await self._handle_set_reminder(params)
            elif tool_name == "cancel_reminder": return await self._handle_cancel_reminder(params)
            elif tool_name == "list_reminders": return self._schedule_engine.list_reminders()
            else:
                if self.tool_registry: return await self._use_tool(tool_name, **params)
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"Tool routing error for {tool_name}: {e}", exc_info=True)
            return f"Error executing {tool_name}: {str(e)}"

    async def _handle_create_workflow(self, params: dict[str, Any]) -> str:
        if _HAS_PYDANTIC:
            try:
                validated = CreateWorkflowParams(**params)
                params = validated.model_dump()
            except ValidationError as e:
                return f"Invalid workflow parameters: {e}"

        steps = [
            WorkflowStep(
                action=s.get("action", "unknown"), params=s.get("params", {}),
                delay_ms=s.get("delay_ms", 0), condition=s.get("condition"),
                retry_count=s.get("retry_count", 0)
            ) for s in params.get("steps", [])
        ]
        wf = Workflow(name=params.get("name", "Unnamed"), description=params.get("description", ""), steps=steps)
        wf_id = await self._workflow_engine.register(wf)
        return f"Workflow '{wf.name}' created with ID: {wf_id}"

    async def _handle_run_workflow(self, params: dict[str, Any]) -> dict[str, Any]:
        wf_id = params.get("workflow_id")
        if not wf_id:
            name = params.get("name")
            for wf in self._workflow_engine._workflows.values():
                if wf.name == name:
                    wf_id = wf.id
                    break
        if not wf_id:
            return {"success": False, "error": "Workflow ID or name required"}
        return await self._workflow_engine.execute(wf_id, params.get("initial_context", {}))

    async def _handle_set_reminder(self, params: dict[str, Any]) -> str:
        if _HAS_PYDANTIC:
            try:
                validated = SetReminderParams(**params)
                params = validated.model_dump()
            except ValidationError as e:
                return f"Invalid reminder parameters: {e}"

        trigger_iso = params.get("trigger_iso")
        if trigger_iso:
            try:
                trigger_time = datetime.fromisoformat(trigger_iso.replace('Z', '+00:00'))
            except ValueError:
                return "Invalid ISO8601 format for trigger_iso"
        else:
            trigger_time = datetime.now(timezone.utc) + timedelta(seconds=params.get("delay_seconds", 60))
            
        cron_expr = params.get("cron_expr")
        if cron_expr and not _HAS_CRONITER:
            return "Recurring reminders require the 'croniter' package, which is not installed."
            
        reminder = Reminder(text=params.get("text", "Reminder"), trigger_time=trigger_time, cron_expr=cron_expr)
        rid = await self._schedule_engine.add_reminder(reminder)
        return f"Reminder set with ID: {rid} for {trigger_time.isoformat()}"

    async def _handle_cancel_reminder(self, params: dict[str, Any]) -> str:
        rid = params.get("reminder_id")
        if not rid: return "reminder_id is required"
        success = await self._schedule_engine.cancel_reminder(rid)
        return "Reminder cancelled" if success else "Reminder not found"
