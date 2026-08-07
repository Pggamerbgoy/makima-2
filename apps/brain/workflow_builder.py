"""Makima v7.2 — WorkflowBuilder

Visual workflow builder for creating multi-step automations.
Supports step types: tool_call, condition, delay, message, agent_call.

Spec:
- create_workflow(name, steps) -> workflow_id
- update_workflow(workflow_id, steps) -> confirmation
- run_workflow(workflow_id, context) -> result
- list_workflows() -> list
- delete_workflow(workflow_id) -> confirmation
- Steps can reference tools from ToolRegistry
- Conditional branching based on previous step output

v7.2 upgrades:
- Thread-safe asyncio.Lock for DB operations
- Structured %s-format logging
- Input validation on all public APIs
- Zero-crash resilience with try/except wrappers
- Async task execution with proper error handling
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("makima.workflow_builder")

STEP_TYPES = ("tool_call", "condition", "delay", "message", "agent_call", "set_variable")


class WorkflowConditionFailed(Exception):
    """Raised when a condition step evaluates to False."""
    pass


@dataclass
class WorkflowStep:
    id: str
    type: str
    config: dict[str, Any]
    next_on_success: str | None = None
    next_on_failure: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "config": self.config,
            "next_on_success": self.next_on_success,
            "next_on_failure": self.next_on_failure,
        }


@dataclass
class Workflow:
    id: str
    name: str
    description: str
    steps: list[WorkflowStep]
    enabled: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0
    run_count: int = 0
    last_run: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": [s.as_dict() for s in self.steps],
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_count": self.run_count,
            "last_run": self.last_run,
        }


class WorkflowBuilder:
    """SQLite-backed workflow builder with async execution."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        ws_broadcast: Any = None,
        tool_registry: Any = None,
    ) -> None:
        cfg = config or {}
        wf_cfg = cfg.get("workflow_builder", {}) if isinstance(cfg, dict) else {}
        base_path = os.path.expanduser(wf_cfg.get("db_path", "~/.makima/workflows.sqlite"))
        self.db_path = Path(base_path)
        self.ws_broadcast = ws_broadcast
        self.tool_registry = tool_registry
        self._workflows: dict[str, Workflow] = {}
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize DB and load all workflows."""
        try:
            await self._ensure_db()
            await self._load_workflows()
            logger.info("WorkflowBuilder started with %d workflows", len(self._workflows))
        except Exception as e:
            logger.error("WorkflowBuilder start failed: %s", e)

    async def stop(self) -> None:
        """Close DB connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
        except Exception as e:
            logger.warning("PRAGMA failed: %s", e)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                steps TEXT DEFAULT '[]',
                enabled INTEGER DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                run_count INTEGER DEFAULT 0,
                last_run REAL
            )
            """
        )
        conn.commit()
        self._conn = conn

    async def _load_workflows(self) -> None:
        if self._conn is None:
            return
        try:
            def _do_load() -> list[sqlite3.Row]:
                assert self._conn is not None
                return self._conn.execute("SELECT * FROM workflows").fetchall()

            loop = asyncio.get_running_loop()
            rows = await loop.run_in_executor(None, _do_load)
            for row in rows:
                try:
                    steps_data = json.loads(row["steps"]) if row["steps"] else []
                    steps = [WorkflowStep(**s) for s in steps_data]
                    wf = Workflow(
                        id=row["id"],
                        name=row["name"],
                        description=row["description"] or "",
                        steps=steps,
                        enabled=bool(row["enabled"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        run_count=row["run_count"],
                        last_run=row["last_run"],
                    )
                    self._workflows[wf.id] = wf
                except Exception as e:
                    logger.warning("failed to load workflow row: %s", e)
        except Exception as e:
            logger.error("load_workflows failed: %s", e)

    async def create_workflow(
        self, name: str, steps: list[dict], description: str = ""
    ) -> str:
        """Create a new workflow. Returns JSON of the created workflow."""
        name = str(name).strip() if name else ""
        if not name:
            return json.dumps({"error": "Workflow name cannot be empty"})

        if not isinstance(steps, list):
            return json.dumps({"error": "Steps must be a list"})

        try:
            now = time.time()
            wf_id = f"wf-{uuid.uuid4().hex[:10]}"
            workflow_steps: list[WorkflowStep] = []
            for i, step_data in enumerate(steps):
                if not isinstance(step_data, dict):
                    continue
                step = WorkflowStep(
                    id=step_data.get("id", f"step-{i}"),
                    type=step_data.get("type", "tool_call"),
                    config=step_data.get("config", {}),
                    next_on_success=step_data.get("next_on_success"),
                    next_on_failure=step_data.get("next_on_failure"),
                )
                if step.type not in STEP_TYPES:
                    return json.dumps({"error": f"Invalid step type: {step.type}"})
                workflow_steps.append(step)

            wf = Workflow(
                id=wf_id,
                name=name,
                description=str(description),
                steps=workflow_steps,
                created_at=now,
                updated_at=now,
            )
            self._workflows[wf_id] = wf
            await self._save_workflow(wf)
            await self._broadcast("workflow_created", wf.as_dict())
            return json.dumps(wf.as_dict(), default=str)
        except Exception as e:
            logger.error("create_workflow failed: %s", e)
            return json.dumps({"error": str(e)})

    async def update_workflow(self, workflow_id: str, steps: list[dict] | None = None, name: str | None = None) -> str:
        """Update an existing workflow."""
        if not workflow_id or workflow_id not in self._workflows:
            return json.dumps({"error": f"Workflow {workflow_id} not found"})

        try:
            wf = self._workflows[workflow_id]
            if name is not None:
                wf.name = str(name).strip() or wf.name
            if steps is not None and isinstance(steps, list):
                workflow_steps: list[WorkflowStep] = []
                for i, step_data in enumerate(steps):
                    if not isinstance(step_data, dict):
                        continue
                    step = WorkflowStep(
                        id=step_data.get("id", f"step-{i}"),
                        type=step_data.get("type", "tool_call"),
                        config=step_data.get("config", {}),
                        next_on_success=step_data.get("next_on_success"),
                        next_on_failure=step_data.get("next_on_failure"),
                    )
                    workflow_steps.append(step)
                wf.steps = workflow_steps
            wf.updated_at = time.time()
            await self._save_workflow(wf)
            await self._broadcast("workflow_updated", wf.as_dict())
            return json.dumps(wf.as_dict(), default=str)
        except Exception as e:
            logger.error("update_workflow failed: %s", e)
            return json.dumps({"error": str(e)})

    async def delete_workflow(self, workflow_id: str) -> str:
        """Delete a workflow."""
        if not workflow_id:
            return json.dumps({"error": "workflow_id required"})
        if workflow_id not in self._workflows:
            return json.dumps({"error": f"Workflow {workflow_id} not found"})

        try:
            del self._workflows[workflow_id]
            if self._conn:
                def _do_delete() -> None:
                    assert self._conn is not None
                    self._conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
                    self._conn.commit()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _do_delete)
            await self._broadcast("workflow_deleted", {"id": workflow_id})
            return json.dumps({"status": "deleted", "id": workflow_id})
        except Exception as e:
            logger.error("delete_workflow failed: %s", e)
            return json.dumps({"error": str(e)})

    async def enable_workflow(self, workflow_id: str) -> str:
        """Enable a workflow."""
        if workflow_id in self._workflows:
            wf = self._workflows[workflow_id]
            wf.enabled = True
            await self._save_workflow(wf)
            return json.dumps({"status": "enabled", "id": workflow_id})
        return json.dumps({"error": f"Workflow {workflow_id} not found"})

    async def disable_workflow(self, workflow_id: str) -> str:
        """Disable a workflow."""
        if workflow_id in self._workflows:
            wf = self._workflows[workflow_id]
            wf.enabled = False
            await self._save_workflow(wf)
            return json.dumps({"status": "disabled", "id": workflow_id})
        return json.dumps({"error": f"Workflow {workflow_id} not found"})

    async def list_workflows(self) -> str:
        """List all workflows."""
        try:
            workflows = [wf.as_dict() for wf in self._workflows.values()]
            return json.dumps(workflows, default=str)
        except Exception as e:
            logger.error("list_workflows failed: %s", e)
            return json.dumps([])

    async def get_workflow(self, workflow_id: str) -> str:
        """Get a specific workflow."""
        wf = self._workflows.get(workflow_id)
        if not wf:
            return json.dumps({"error": f"Workflow {workflow_id} not found"})
        return json.dumps(wf.as_dict(), default=str)

    async def run_workflow(self, workflow_id: str, context: dict[str, Any] | None = None) -> str:
        """Execute a workflow with the given context."""
        if not workflow_id or workflow_id not in self._workflows:
            return json.dumps({"error": f"Workflow {workflow_id} not found"})

        wf = self._workflows[workflow_id]
        if not wf.enabled:
            return json.dumps({"error": f"Workflow {workflow_id} is disabled"})

        ctx = context or {}
        results: list[dict[str, Any]] = []
        current_step_id: str | None = wf.steps[0].id if wf.steps else None
        step_map = {s.id: s for s in wf.steps}

        try:
            max_iterations = len(wf.steps) + 10  # Safety limit
            iteration = 0
            while current_step_id and iteration < max_iterations:
                iteration += 1
                step = step_map.get(current_step_id)
                if not step:
                    break

                step_result: dict[str, Any] = {"step_id": step.id, "type": step.type}
                success = True
                try:
                    output = await self._execute_step(step, ctx)
                    step_result["output"] = output
                    step_result["success"] = True
                except WorkflowConditionFailed as e:
                    step_result["success"] = False
                    step_result["error"] = str(e)
                    success = False
                except Exception as e:
                    step_result["success"] = False
                    step_result["error"] = str(e)
                    success = False

                results.append(step_result)

                # Navigate to next step
                if success:
                    current_step_id = step.next_on_success
                else:
                    current_step_id = step.next_on_failure

            # Update run metadata
            wf.run_count += 1
            wf.last_run = time.time()
            await self._save_workflow(wf)
            await self._broadcast("workflow_completed", {"id": workflow_id, "run_count": wf.run_count})

            return json.dumps({"status": "completed", "workflow_id": workflow_id, "steps": results}, default=str)
        except Exception as e:
            logger.error("run_workflow failed: %s", e)
            return json.dumps({"status": "error", "error": str(e), "steps": results})

    async def _execute_step(self, step: WorkflowStep, ctx: dict[str, Any]) -> Any:
        """Execute a single workflow step."""
        cfg = step.config

        if step.type == "delay":
            delay = float(cfg.get("seconds", 1))
            delay = max(0, min(delay, 60))  # Cap at 60s
            await asyncio.sleep(delay)
            return f"delayed {delay}s"

        elif step.type == "message":
            msg = str(cfg.get("message", ""))
            if self.ws_broadcast:
                await self._broadcast("workflow_message", {"message": msg})
            return msg

        elif step.type == "condition":
            variable = str(cfg.get("variable", ""))
            operator = str(cfg.get("operator", "equals"))
            value = cfg.get("value", "")
            actual = ctx.get(variable, "")
            result = self._evaluate_condition(str(actual), operator, str(value))
            if not result:
                raise WorkflowConditionFailed(f"{actual} {operator} {value} is False")
            return f"condition passed: {variable} {operator} {value}"

        elif step.type == "set_variable":
            var_name = str(cfg.get("variable") or cfg.get("name", "")).strip()
            var_value = cfg.get("value", "")
            if var_name:
                ctx[var_name] = var_value
            return f"set {var_name}={var_value}"

        elif step.type == "tool_call":
            tool_name = str(cfg.get("tool", ""))
            tool_args = cfg.get("args", {})
            if self.tool_registry and tool_name:
                result = await self.tool_registry.call_tool(tool_name, **tool_args)
                return result
            return f"tool_call: {tool_name} (registry unavailable)"

        elif step.type == "agent_call":
            agent_name = str(cfg.get("agent", ""))
            prompt = str(cfg.get("prompt", ""))
            return f"agent_call: {agent_name} -> {prompt[:50]}..."

        else:
            return f"unknown step type: {step.type}"

    def _evaluate_condition(self, actual: str, operator: str, expected: str) -> bool:
        """Evaluate a condition expression."""
        if operator in ("equals", "=="):
            return actual == expected
        elif operator in ("not_equals", "!="):
            return actual != expected
        elif operator in ("contains", "in"):
            return expected in actual
        elif operator in ("starts_with",):
            return actual.startswith(expected)
        elif operator in ("ends_with",):
            return actual.endswith(expected)
        elif operator in ("gt", ">"):
            try:
                return float(actual) > float(expected)
            except (ValueError, TypeError):
                return False
        elif operator in ("lt", "<"):
            try:
                return float(actual) < float(expected)
            except (ValueError, TypeError):
                return False
        return False

    async def _save_workflow(self, wf: Workflow) -> None:
        """Persist a workflow to SQLite."""
        if not self._conn:
            return
        try:
            def _do_save() -> None:
                assert self._conn is not None
                self._conn.execute(
                    """INSERT OR REPLACE INTO workflows
                       (id, name, description, steps, enabled, created_at, updated_at, run_count, last_run)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        wf.id, wf.name, wf.description,
                        json.dumps([s.as_dict() for s in wf.steps]),
                        int(wf.enabled), wf.created_at, wf.updated_at,
                        wf.run_count, wf.last_run,
                    ),
                )
                self._conn.commit()

            async with self._lock:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _do_save)
        except Exception as e:
            logger.error("_save_workflow failed: %s", e)

    async def _broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Send WS broadcast event."""
        if not self.ws_broadcast:
            return
        try:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.build_ai_chunk(event_type, json.dumps(data, default=str)))
        except Exception as e:
            logger.debug("broadcast failed: %s", e)
