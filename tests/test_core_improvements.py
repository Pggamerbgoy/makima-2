r"""
Makima OS — Core Architectural Improvements Integration & Unit Test Suite
Target: tests/test_core_improvements.py

Comprehensive test suite covering:
1. TestPreferenceEngineSchemaGuard:
   - Verify that _schema_initialized starts False and becomes True after first query.
   - Verify that subsequent reads do not execute DDL statements.
2. TestExecutionRuntimeContextIsolation:
   - Test execute_actions_parallel with multiple concurrent actions.
   - Verify that each branch operates on an isolated context copy and execution IDs do not overwrite each other.
3. TestDesktopPathGroundingStrictPrefix:
   - Test _normalize_tool_parameters with paths:
     - desktop\my_file.txt -> grounded to Desktop.
     - src/desktop_view.py -> NOT grounded to Desktop, kept relative/workspace.
     - my_desktop_notes.txt -> NOT grounded to Desktop.
4. TestDurableTaskEnginePoisonPill:
   - Insert a task checkpoint with corrupted JSON in context_snapshot.
   - Call list_pending_tasks_sync().
   - Verify it logs a warning, skips/safely parses the corrupt task, and does NOT raise json.JSONDecodeError.
5. TestDurableTaskEngineAtomicCAS:
   - Checkpoint a task with status='paused'.
   - Run two concurrent resume_task() calls.
   - Verify that only ONE succeeds in setting status='active' and the second safely returns None.
6. TestSdkBridgeArrayItemsSchema:
   - Test to_sdk_function_tool with a function taking tags: list[str].
   - Verify that the generated JSON schema has "type": "array" and "items": {"type": "string"}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
from typing import Any, List

import pytest

from apps.brain.core.contracts import Action, ActionExecutionContext, ExecutionResult
from apps.brain.core.durable_task_engine import CheckpointedTask, DurableTaskEngine, ResumedTask
from apps.brain.core.execution_runtime import ExecutionRuntime
from apps.brain.core.persistence import EventStore
from apps.brain.core.preference_engine import PreferenceEngine
from apps.brain.core.sdk_bridge import to_sdk_function_tool
from apps.brain.core.task_manager import TaskManager
from apps.brain.tool_registry import ToolRegistry


# ─────────────────────────────────────────────────────────────────────────────
# 1. TestPreferenceEngineSchemaGuard
# ─────────────────────────────────────────────────────────────────────────────
class TestPreferenceEngineSchemaGuard:
    """
    Validates that PreferenceEngine defers table creation until the first query
    and strictly avoids re-running DDL statements on subsequent reads.
    """

    @pytest.mark.asyncio
    async def test_schema_initialized_starts_false_and_becomes_true(self):
        """Verify that _schema_initialized starts False and becomes True after first query."""
        conn = sqlite3.connect(":memory:")
        try:
            engine = PreferenceEngine(eternal_memory=conn)
            assert engine._schema_initialized is False, "_schema_initialized must start as False"

            # Execute first query (triggers schema initialization)
            prefs = await engine.get_all_preferences()
            assert engine._schema_initialized is True, "_schema_initialized must become True after first query"
            assert isinstance(prefs, list)

            # Verify table was actually created
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation'"
            )
            assert cursor.fetchone() is not None, "conversation table must exist in SQLite"
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_subsequent_reads_do_not_execute_ddl(self):
        """Verify that subsequent reads do not execute DDL statements (CREATE, ALTER, DROP)."""
        conn = sqlite3.connect(":memory:")
        try:
            engine = PreferenceEngine(eternal_memory=conn)

            # First query: initializes schema
            await engine.get_all_preferences()
            assert engine._schema_initialized is True

            # Track SQL statements executed during subsequent queries
            executed_ddl: list[str] = []
            ddl_keywords = ("CREATE TABLE", "CREATE INDEX", "ALTER TABLE", "DROP TABLE")

            def trace_callback(query: str):
                q_upper = query.upper()
                if any(kw in q_upper for kw in ddl_keywords):
                    executed_ddl.append(query)

            conn.set_trace_callback(trace_callback)

            # Perform multiple subsequent reads
            for _ in range(5):
                await engine.get_all_preferences()
                await engine.get_preference_for_tool("browser_navigate")
                await engine.get_preference_for_tool("code_interpreter")

            # Check that zero DDL statements were executed
            assert len(executed_ddl) == 0, (
                f"Subsequent reads must NOT execute DDL statements. Found: {executed_ddl}"
            )
        finally:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 2. TestExecutionRuntimeContextIsolation
# ─────────────────────────────────────────────────────────────────────────────
class TestExecutionRuntimeContextIsolation:
    """
    Validates that ExecutionRuntime.execute_actions_parallel provides branch context
    isolation so concurrent actions do not overwrite each other's execution IDs
    or mutate the parent context.
    """

    @pytest.mark.asyncio
    async def test_parallel_actions_context_isolation(self):
        """
        Test execute_actions_parallel with multiple concurrent actions.
        Verify each branch operates on an isolated context copy and execution IDs do not overwrite each other.
        """
        runtime = ExecutionRuntime()
        registry = ToolRegistry()

        observed_execution_ids: list[str] = []
        observed_contexts: list[ActionExecutionContext] = []

        async def concurrent_worker(duration: float = 0.03, context: ActionExecutionContext | None = None) -> dict[str, Any]:
            assert context is not None, "Context must be injected into worker"
            observed_execution_ids.append(context.execution_id)
            observed_contexts.append(context)
            await asyncio.sleep(duration)
            return {"status": "ok", "worker_exec_id": context.execution_id}

        registry.register_tool(
            "concurrent_worker",
            "Test worker for concurrency isolation",
            concurrent_worker,
            {"type": "object", "properties": {"duration": {"type": "number"}}},
            parallel_safe=True,
        )
        runtime.tool_registry = registry

        parent_ctx = ActionExecutionContext(
            task_id="parent_task_001",
            execution_id="parent_exec_original",
            consumer="test_runner",
            metadata={"orig": "data"},
        )

        actions = [
            Action(action_id="act_branch_1", task_id="subtask_1", capability_name="concurrent_worker", parameters={"duration": 0.04}),
            Action(action_id="act_branch_2", task_id="subtask_2", capability_name="concurrent_worker", parameters={"duration": 0.04}),
            Action(action_id="act_branch_3", task_id="subtask_3", capability_name="concurrent_worker", parameters={"duration": 0.04}),
        ]

        results = await runtime.execute_actions_parallel(actions, context=parent_ctx, max_concurrency=4)

        # 1. Verify parent context was preserved and not overwritten
        assert parent_ctx.execution_id == "parent_exec_original", (
            f"Parent execution_id should not be overwritten! Got: {parent_ctx.execution_id}"
        )
        assert parent_ctx.task_id == "parent_task_001"

        # 2. Verify all actions succeeded
        assert len(results) == 3
        for res in results:
            assert res.is_verified is True
            assert res.error is None

        # 3. Verify each action received a distinct execution ID
        result_exec_ids = [r.execution_id for r in results]
        assert len(set(result_exec_ids)) == 3, f"Execution IDs must be distinct: {result_exec_ids}"

        # 4. Verify workers observed their branch-specific execution IDs
        assert len(observed_execution_ids) == 3
        assert len(set(observed_execution_ids)) == 3, f"Observed IDs must be distinct: {observed_execution_ids}"
        assert set(observed_execution_ids) == set(result_exec_ids)

        # 5. Verify observed context objects are distinct instances in memory
        assert len(observed_contexts) == 3
        assert observed_contexts[0] is not observed_contexts[1]
        assert observed_contexts[1] is not observed_contexts[2]
        assert observed_contexts[0] is not parent_ctx


# ─────────────────────────────────────────────────────────────────────────────
# 3. TestDesktopPathGroundingStrictPrefix
# ─────────────────────────────────────────────────────────────────────────────
class TestDesktopPathGroundingStrictPrefix:
    """
    Validates strict prefix path grounding in ExecutionRuntime._normalize_tool_parameters:
    - desktop\\my_file.txt -> grounded to Desktop.
    - src/desktop_view.py -> NOT grounded to Desktop, kept relative/workspace.
    - my_desktop_notes.txt -> NOT grounded to Desktop.
    """

    def test_strict_desktop_prefix_grounding(self):
        from apps.brain.core.known_folders import resolve_known_folder

        runtime = ExecutionRuntime()
        desktop_dir = resolve_known_folder("desktop") or os.path.expanduser("~/Desktop")
        workspace_dir = os.getcwd()

        # 1. desktop\my_file.txt -> Grounded to Desktop
        p1 = runtime._normalize_tool_parameters(
            "write_file", {"path": r"desktop\my_file.txt"}
        )
        expected_p1 = os.path.normpath(os.path.join(desktop_dir, "my_file.txt"))
        assert os.path.normpath(p1["path"]) == expected_p1, (
            f"Expected desktop\\my_file.txt to ground to Desktop ({expected_p1}), got {p1['path']}"
        )

        # 2. desktop/my_file.txt -> Grounded to Desktop (forward slash)
        p1_fwd = runtime._normalize_tool_parameters(
            "write_file", {"path": "desktop/my_file.txt"}
        )
        assert os.path.normpath(p1_fwd["path"]) == expected_p1

        # 3. src/desktop_view.py -> NOT grounded to Desktop, kept in workspace
        p2 = runtime._normalize_tool_parameters(
            "write_file", {"path": "src/desktop_view.py"}
        )
        expected_p2 = os.path.normpath(os.path.join(workspace_dir, "src/desktop_view.py"))
        assert os.path.normpath(p2["path"]) == expected_p2, (
            f"Expected src/desktop_view.py to remain in workspace ({expected_p2}), got {p2['path']}"
        )
        assert os.path.normpath(desktop_dir) not in os.path.normpath(p2["path"]) or (
            workspace_dir.startswith(desktop_dir)
        )

        # 4. my_desktop_notes.txt -> NOT grounded to Desktop, kept in workspace
        p3 = runtime._normalize_tool_parameters(
            "write_file", {"path": "my_desktop_notes.txt"}
        )
        expected_p3 = os.path.normpath(os.path.join(workspace_dir, "my_desktop_notes.txt"))
        assert os.path.normpath(p3["path"]) == expected_p3, (
            f"Expected my_desktop_notes.txt to remain in workspace ({expected_p3}), got {p3['path']}"
        )

        # 5. desktop_view.py (starts with 'desktop_', not 'desktop/' or 'desktop\') -> Workspace
        p4 = runtime._normalize_tool_parameters(
            "write_file", {"path": "desktop_view.py"}
        )
        expected_p4 = os.path.normpath(os.path.join(workspace_dir, "desktop_view.py"))
        assert os.path.normpath(p4["path"]) == expected_p4


# ─────────────────────────────────────────────────────────────────────────────
# 4. TestDurableTaskEnginePoisonPill
# ─────────────────────────────────────────────────────────────────────────────
class TestDurableTaskEnginePoisonPill:
    """
    Validates poison pill resilience in DurableTaskEngine:
    - Insert a task checkpoint with corrupted JSON in context_snapshot.
    - Call list_pending_tasks_sync().
    - Verify it logs a warning, skips/safely parses the corrupt task, and does NOT raise json.JSONDecodeError.
    """

    def test_corrupted_context_snapshot_handled_gracefully(self, caplog):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = EventStore(db_path)
            engine = DurableTaskEngine(event_store=store)

            # Checkpoint a valid task first
            engine.checkpoint_task_sync(
                task_id="task_valid",
                prompt="Valid task prompt",
                status="active",
                context={"ok": True},
            )

            # Checkpoint a task that will receive corrupt JSON
            engine.checkpoint_task_sync(
                task_id="task_poison",
                prompt="Poison pill task prompt",
                status="paused",
                context={"test": 123},
            )

            # Deliberately corrupt the context_snapshot column in SQLite
            corrupt_raw_json = "{'bad_json': invalid, missing_braces:"
            engine._conn.execute(
                "UPDATE task_checkpoints SET context_snapshot = ? WHERE task_id = 'task_poison'",
                (corrupt_raw_json,),
            )
            engine._conn.commit()

            # Execute list_pending_tasks_sync with log capture
            with caplog.at_level(logging.WARNING):
                pending_tasks = engine.list_pending_tasks_sync()

            # 1. Verify NO json.JSONDecodeError was raised
            assert isinstance(pending_tasks, list)

            # 2. Verify warning was logged
            warning_messages = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
            assert any(
                "Failed to decode context_snapshot" in msg or "Corrupted context_snapshot" in msg or "task_poison" in msg
                for msg in warning_messages
            ), f"Expected warning log for corrupted task, found: {warning_messages}"

            # 3. Verify task was safely parsed with empty fallback context
            task_ids = [t.task_id for t in pending_tasks]
            assert "task_valid" in task_ids
            assert "task_poison" in task_ids

            poison_task = next(t for t in pending_tasks if t.task_id == "task_poison")
            assert poison_task.context_snapshot == {}, "Corrupted context should safely default to empty dict"
            assert poison_task.status == "paused"

            store.close()
        finally:
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except Exception:
                    pass


# ─────────────────────────────────────────────────────────────────────────────
# 5. TestDurableTaskEngineAtomicCAS
# ─────────────────────────────────────────────────────────────────────────────
class TestDurableTaskEngineAtomicCAS:
    """
    Validates atomic Compare-And-Swap (CAS) in DurableTaskEngine.resume_task:
    - Checkpoint a task with status='paused'.
    - Run two concurrent resume_task() calls.
    - Verify that only ONE succeeds in setting status='active' and the second safely returns None.
    """

    @pytest.mark.asyncio
    async def test_concurrent_resume_task_atomic_cas(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = EventStore(db_path)
            task_mgr = TaskManager()
            engine = DurableTaskEngine(event_store=store, task_manager=task_mgr)

            task_id = "task_cas_race_001"
            await engine.checkpoint_task(
                task_id=task_id,
                prompt="Perform financial calculation",
                completed_steps=["Step 1 complete"],
                remaining_steps=["Step 2 execute"],
                context={"turn": 5},
                turn_count=5,
                status="paused",
            )

            # Verify initial status is 'paused'
            initial_cp = await engine.get_checkpoint(task_id)
            assert initial_cp is not None
            assert initial_cp.status == "paused"

            # Execute two concurrent resume_task calls
            res1, res2 = await asyncio.gather(
                engine.resume_task(task_id),
                engine.resume_task(task_id),
            )

            # Exactly ONE call must succeed, the other must safely return None
            successes = [r for r in (res1, res2) if r is not None]
            failures = [r for r in (res1, res2) if r is None]

            assert len(successes) == 1, (
                f"Expected exactly 1 success in atomic CAS, got {len(successes)}: {res1}, {res2}"
            )
            assert len(failures) == 1, (
                f"Expected exactly 1 failure (None) in atomic CAS, got {len(failures)}: {res1}, {res2}"
            )

            resumed_task: ResumedTask = successes[0]
            assert isinstance(resumed_task, ResumedTask)
            assert resumed_task.task.task_id == task_id
            assert resumed_task.checkpoint.status == "active"

            # Verify persistent DB status is now 'active'
            final_cp = await engine.get_checkpoint(task_id)
            assert final_cp is not None
            assert final_cp.status == "active"

            # Tasks marked completed cannot be resumed
            await engine.mark_completed(task_id)
            res3 = await engine.resume_task(task_id)
            assert res3 is None, "Subsequent resume_task on completed task must return None"

            store.close()
        finally:
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except Exception:
                    pass


# ─────────────────────────────────────────────────────────────────────────────
# 6. TestSdkBridgeArrayItemsSchema
# ─────────────────────────────────────────────────────────────────────────────
class TestSdkBridgeArrayItemsSchema:
    """
    Validates parameter schema synthesis in to_sdk_function_tool:
    - Test to_sdk_function_tool with a function taking tags: list[str].
    - Verify that the generated JSON schema has "type": "array" and "items": {"type": "string"}.
    """

    def test_list_str_generates_array_and_items_schema(self):
        """Test to_sdk_function_tool with tags: list[str]."""

        def apply_tags(item_id: str, tags: list[str]) -> str:
            """Apply tags to an item."""
            return f"tagged {item_id}"

        fn_tool = to_sdk_function_tool("apply_tags", func=apply_tags)

        schema = fn_tool.params_json_schema
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"

        properties = schema.get("properties", {})
        assert "tags" in properties, "tags property must be generated in schema"
        tags_prop = properties["tags"]

        # Verify type is array
        assert tags_prop.get("type") == "array", f"Expected type 'array', got {tags_prop.get('type')}"

        # Verify items is {"type": "string"}
        assert tags_prop.get("items") == {"type": "string"}, (
            f"Expected items {{'type': 'string'}}, got {tags_prop.get('items')}"
        )

        # Verify item_id property
        assert "item_id" in properties
        assert properties["item_id"].get("type") == "string"

        # Verify required fields
        assert "tags" in schema.get("required", [])
        assert "item_id" in schema.get("required", [])

    def test_typing_list_and_primitives_schema(self):
        """Test with typing.List and other primitive list item types."""

        def process_scores(scores: List[int], flags: list[bool]) -> str:
            """Process numerical scores."""
            return "processed"

        fn_tool = to_sdk_function_tool("process_scores", func=process_scores)
        schema = fn_tool.params_json_schema
        props = schema.get("properties", {})

        assert props["scores"]["type"] == "array"
        assert props["scores"]["items"] == {"type": "integer"}

        assert props["flags"]["type"] == "array"
        assert props["flags"]["items"] == {"type": "boolean"}
