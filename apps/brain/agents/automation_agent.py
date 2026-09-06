"""Makima v7.2 — Elite Automation Agent: Workflows, macros, reminders, scheduled tasks.

This module provides an enterprise-grade, zero-crash resilient automation engine.
It handles complex workflow orchestration, cron-based scheduling, and macro 
recording/playback without relying on browser automation (delegated to BrowserAgent).
"""
from __future__ import annotations

import asyncio
import json
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

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

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
                trig = reminder.trigger_time
                if trig.tzinfo is None:
                    trig = trig.replace(tzinfo=timezone.utc)
                wait_seconds = (trig - now).total_seconds()
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
        finally:
            async with self._lock:
                self._tasks.pop(reminder.id, None)

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
        from ..core.invariant_verifier import ErrorPatternRegistry
        results = []
        for i, step in enumerate(workflow.steps):
            if step.delay_ms > 0:
                await asyncio.sleep(step.delay_ms / 1000.0)
                
            attempts = 0
            max_attempts = step.retry_count + 1
            step_failed = False
            last_err = ""
            while attempts < max_attempts:
                try:
                    if self._tool_executor:
                        step_params = step.params if isinstance(step.params, dict) else {}
                        res = await self._tool_executor(step.action, **step_params)
                        if isinstance(res, dict) and res.get("success") is False:
                            step_failed = True
                            last_err = str(res.get("error") or "Step reported failure")
                        elif hasattr(res, "is_verified") and not res.is_verified and getattr(res, "error", None):
                            step_failed = True
                            last_err = str(res.error)
                        elif hasattr(res, "success") and not getattr(res, "success"):
                            step_failed = True
                            last_err = str(getattr(res, "error", "Step reported failure"))
                        elif ErrorPatternRegistry.is_failure(str(res or "")):
                            step_failed = True
                            last_err = str(res)
                        else:
                            step_failed = False
                            results.append({"step": i, "action": step.action, "result": res})
                            break
                    else:
                        step_failed = True
                        last_err = "No executor available"
                        results.append({"step": i, "action": step.action, "result": "No executor"})
                        break
                except Exception as e:
                    step_failed = True
                    last_err = str(e)
                    
                attempts += 1
                if attempts < max_attempts:
                    await asyncio.sleep(1.0 * attempts)  # Exponential backoff
            
            if step_failed:
                logger.error(f"Workflow step {i} ({step.action}) failed after {attempts} attempts: {last_err}")
                return {"success": False, "failed_step": i, "action": step.action, "error": last_err, "partial_results": results}
                    
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
        from ..core.invariant_verifier import ErrorPatternRegistry
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
                    if isinstance(res, dict) and res.get("success") is False:
                        return {"success": False, "failed_event": event.event_type, "error": str(res.get("error") or "Tool failed"), "partial_results": results}
                    elif hasattr(res, "is_verified") and not res.is_verified and getattr(res, "error", None):
                        return {"success": False, "failed_event": event.event_type, "error": str(res.error), "partial_results": results}
                    elif hasattr(res, "success") and not getattr(res, "success"):
                        return {"success": False, "failed_event": event.event_type, "error": str(getattr(res, "error", "Tool failed")), "partial_results": results}
                    elif ErrorPatternRegistry.is_failure(str(res or "")):
                        return {"success": False, "failed_event": event.event_type, "error": str(res), "partial_results": results}
                    results.append(res)
                elif event.event_type == "wait":
                    await asyncio.sleep(event.data.get("seconds", 1.0))
                elif event.event_type == "mouse_click":
                    import pyautogui
                    x = event.data.get("x")
                    y = event.data.get("y")
                    btn = str(event.data.get("button", "left")).lower()
                    clicks = int(event.data.get("clicks", 1))
                    if x is not None and y is not None:
                        await asyncio.to_thread(pyautogui.click, x=int(x), y=int(y), clicks=clicks, button=btn)
                        results.append(f"Clicked {btn} at ({x}, {y}) [clicks={clicks}]")
                    else:
                        await asyncio.to_thread(pyautogui.click, clicks=clicks, button=btn)
                        results.append(f"Clicked {btn} [clicks={clicks}]")
                elif event.event_type in ("keystroke", "press_key", "hotkey"):
                    import pyautogui
                    keys = event.data.get("keys") or event.data.get("key") or event.data.get("text")
                    if isinstance(keys, list):
                        await asyncio.to_thread(pyautogui.hotkey, *keys)
                        results.append(f"Pressed hotkey: {'+'.join(str(k) for k in keys)}")
                    elif str(keys).lower() in pyautogui.KEYBOARD_KEYS:
                        await asyncio.to_thread(pyautogui.press, str(keys).lower())
                        results.append(f"Pressed key: {keys}")
                    else:
                        await asyncio.to_thread(pyautogui.write, str(keys), interval=0.01)
                        results.append(f"Typed text: {keys}")
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
    DESCRIPTION = "Schedules and manages reminders, timers, alarms, cron jobs, and recurring automated routines (STRICTLY NOT for opening, launching, or closing desktop apps or OS window controls)."
    CAPABILITIES = [
        "workflow_orchestration", "reminder_scheduling", "macro_recording", "cron_automation"
    ]
    AGENT_TOOLS = [
        "create_workflow", "run_workflow", "list_workflows",
        "set_reminder", "cancel_reminder", "list_reminders",
    ]
    
    SYSTEM_PROMPT = """You are Makima's Elite Automation Agent.
You manage personal assistant workflows, background macros, and one-time or recurring (cron) reminders.

AVAILABLE TOOLS:
- Workflows: create_workflow(name, description, steps=[...]), run_workflow(workflow_id, initial_context), list_workflows()
- Reminders: set_reminder(text, trigger_iso=None, delay_seconds=0, cron_expr=None), cancel_reminder(reminder_id), list_reminders()

EXECUTION GUIDELINES:
1. Workflow Steps: Each step requires 'action' and 'params', with optional 'delay_ms' and 'retry_count'.
2. Reminders: Support relative delays (delay_seconds), exact ISO8601 timestamps (trigger_iso), or standard cron syntax (cron_expr).
3. Output Discipline: Deliver final answers in natural, clean prose (with markdown formatting). Do NOT wrap final responses in {"tool": ...} JSON envelopes.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    def __init__(self, ai_handler=None, memory=None, tool_registry=None, ws_broadcast=None, orchestrator=None, guardrails=None, **kwargs):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, **kwargs)
        self._schedule_engine = ScheduleEngine(broadcast_callback=self._broadcast_wrapper)
        self._workflow_engine = WorkflowEngine(tool_executor=self._execute_tool_safe)
        self._macro_engine = MacroEngine(tool_executor=self._execute_tool_safe)
        self._TOOL_MAP = {
            "create_workflow": self._tool_create_workflow,
            "run_workflow": self._tool_run_workflow,
            "list_workflows": self._tool_list_workflows,
            "set_reminder": self._tool_set_reminder,
            "cancel_reminder": self._tool_cancel_reminder,
            "list_reminders": self._tool_list_reminders,
        }

    async def _tool_create_workflow(self, name: str = "Unnamed", description: str = "", steps: list = None, **kwargs: Any) -> str:
        params = {"name": name, "description": description, "steps": steps or []}
        params.update(kwargs)
        return await self._handle_create_workflow(params)

    async def _tool_run_workflow(self, workflow_id: str = "", name: str = "", initial_context: dict = None, **kwargs: Any) -> dict[str, Any]:
        params = {"workflow_id": workflow_id, "name": name, "initial_context": initial_context or {}}
        params.update(kwargs)
        return await self._handle_run_workflow(params)

    async def _tool_list_workflows(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._workflow_engine.list_workflows()

    async def _tool_set_reminder(self, text: str = "Reminder", trigger_iso: str = None, delay_seconds: int = 60, cron_expr: str = None, **kwargs: Any) -> str:
        params = {"text": text, "trigger_iso": trigger_iso, "delay_seconds": delay_seconds, "cron_expr": cron_expr}
        params.update(kwargs)
        return await self._handle_set_reminder(params)

    async def _tool_cancel_reminder(self, reminder_id: str = "", **kwargs: Any) -> str:
        params = {"reminder_id": reminder_id}
        params.update(kwargs)
        return await self._handle_cancel_reminder(params)

    async def _tool_list_reminders(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._schedule_engine.list_reminders()

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

        # ── Autonomous Safety Delegation ──────────────────────────────────────
        # If an OS app/window lifecycle command is dispatched here by mistake, forward to system_agent
        msg_lower = (message or "").lower().strip()
        if any(kw in msg_lower for kw in ("close ", "quit ", "kill ", "open ", "launch ", "start ", "minimize ", "maximize ", "focus ")) and not any(rem in msg_lower for rem in ("remind", "timer", "alarm", "schedule", "cron", "workflow", "macro")):
            if self.orchestrator:
                logger.info("[automation] Re-routing misdirected OS app/window command to system_agent: '%s'", message)
                sys_res = await self.orchestrator.dispatch(task_id, "system_agent", message, context, entities=entities)
                return str(getattr(sys_res, "result", sys_res) or "")

        # ── P1 Bridge 4B: Consume structured AgentTask parameters directly ────
        agent_task = getattr(self, "_current_agent_task", None) or (context.get("agent_task") if isinstance(context, dict) else None)
        if agent_task and hasattr(agent_task, "operation") and agent_task.operation:
            op = str(agent_task.operation or "").lower().strip()
            params = dict(agent_task.parameters or {})
            
            tool_name = None
            if any(k in op for k in ("set_reminder", "create_reminder", "reminder")):
                tool_name = "set_reminder"
            elif "list_reminder" in op or "show_reminder" in op:
                tool_name = "list_reminders"
            elif "cancel_reminder" in op or "delete_reminder" in op:
                tool_name = "cancel_reminder"
            elif "create_workflow" in op:
                tool_name = "create_workflow"
            elif "run_workflow" in op:
                tool_name = "run_workflow"
            elif "list_workflow" in op:
                tool_name = "list_workflows"

            if tool_name:
                logger.info("[automation] P1-4B: Direct execution of tool '%s'", tool_name)
                tool_result = await self._route_tool(tool_name, params)
                res_str = str(tool_result) if not isinstance(tool_result, (dict, list)) else json.dumps(tool_result, indent=2)
                self._partial_result = res_str
                return res_str

        # ── OpenAI Agents SDK Runner Execution ────────────────────────────────
        try:
            state_context = self._get_state_summary()
            extra_sys = f"Current Automation State:\n{state_context}" if state_context else ""
            final_out = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=6,
                task_type="automation",
                extra_system=extra_sys,
                input_guardrails=[],
                output_guardrails=[],
            )
            self._partial_result = final_out
            return final_out
        except Exception as sdk_exc:
            logger.warning("[automation] SDK Runner encountered exception, falling back: %s", sdk_exc)

        messages = self._build_messages(message, context)
        
        state_context = self._get_state_summary()
        if state_context:
            messages.append({"role": "system", "content": f"Current Automation State:\n{state_context}"})

        response = await self._llm_call(messages, task="automation", require_json=True)
        self._partial_result = response
        
        try:
            import inspect
            data = self.ai_handler.try_parse_json(response)
            if inspect.isawaitable(data):
                data = await data
            if not isinstance(data, dict):
                try:
                    data = json.loads(response)
                except Exception:
                    data = None
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
                    return f"Execution encountered an issue: {tool_result}"

                # Turn 2: Grounded observation synthesis (eliminates hallucinated times/IDs)
                messages.append({"role": "assistant", "content": json.dumps({"tool": tool_name, "params": params})})
                messages.append({"role": "user", "content": f"[SYSTEM] Tool '{tool_name}' executed successfully with result:\n{tool_result}\n\nProvide a concise, helpful confirmation grounded in this exact outcome."})
                try:
                    synthesis_resp = await self._llm_call(messages, task="automation", require_json=False)
                    if synthesis_resp and len(synthesis_resp.strip()) > 0:
                        return synthesis_resp.strip()
                except Exception as synth_err:
                    logger.debug("[automation] Turn 2 synthesis fallback: %s", synth_err)

                return str(tool_result)

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
        import time as _time

        task_id = getattr(self, "_current_task_id", "") or "task_default"
        if self.ws_broadcast:
            try:
                from ..ws_protocol import build_tool_call_started
                await self.ws_broadcast(build_tool_call_started(task_id, tool_name, params, agent=self.AGENT_NAME))
            except Exception:
                pass

        t0 = _time.perf_counter()
        try:
            result = await self._dispatch_known_tool(tool_name, params)
        except Exception as e:
            logger.error(f"Tool routing error for {tool_name}: {e}", exc_info=True)
            result = f"Error executing {tool_name}: {str(e)}"

        if self.ws_broadcast:
            try:
                from ..ws_protocol import build_tool_call_finished
                res_str = result if isinstance(result, str) else json.dumps(result, default=str)
                await self.ws_broadcast(build_tool_call_finished(
                    task_id, tool_name,
                    result=res_str,
                    duration_ms=(_time.perf_counter() - t0) * 1000.0,
                    is_success=not str(res_str).startswith(("Error executing", "Unknown tool")),
                    agent=self.AGENT_NAME,
                ))
            except Exception:
                pass
        return result

    async def _dispatch_known_tool(self, tool_name: str, params: dict[str, Any]) -> Any:
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
            try:
                raw_delay = params.get("delay_seconds", 60)
                delay_sec = int(float(raw_delay)) if raw_delay is not None else 60
            except (ValueError, TypeError):
                delay_sec = 60
            trigger_time = datetime.now(timezone.utc) + timedelta(seconds=max(0, delay_sec))
            
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
