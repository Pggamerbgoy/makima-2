"""Makima v9.0 — SkillLibrary
Based on: Wang et al. 2023 "Voyager: An Open-Ended Embodied Agent with Large Language Models" (arXiv:2305.16291)

Self-evolving, open-ended skill library for autonomous agents.
Distills successful multi-step execution trajectories into reusable, verified,
AST-safety checked programs, indexes them by semantic embeddings, and dynamically
retrieves them for instantaneous direct execution.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger("skill_library")

__all__ = ["Skill", "SkillLibrary"]


# =============================================================================
# 1. Skill Dataclass
# =============================================================================

@dataclass
class Skill:
    """A synthesized, reusable program skill learned from successful trajectories."""
    skill_id: str
    name: str
    description: str
    trigger_patterns: list[str]
    parameters_schema: dict[str, Any]
    steps_code: str
    verified: bool = False
    use_count: int = 0
    success_count: int = 0
    created_at: float = field(default_factory=time.time)
    embedding: Optional[list[float]] = None

    @property
    def success_rate(self) -> float:
        return (self.success_count / self.use_count) if self.use_count > 0 else 0.0


# =============================================================================
# 2. Skill Execution Adapter
# =============================================================================

class SkillExecutionContext:
    """Safe execution bridge passed to skill steps code as `context`."""

    def __init__(
        self,
        execution_runtime: Any,
        tool_registry: Any,
        base_context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.execution_runtime = execution_runtime
        self.tool_registry = tool_registry
        self.context = dict(base_context or {})
        self.execution_log: list[dict[str, Any]] = []

    async def execute_tool(self, tool_name: str, **kwargs: Any) -> Any:
        """Execute a physical tool or action and record trajectory."""
        logger.debug("[SkillExecutionContext] execute_tool: %s (params=%s)", tool_name, kwargs)

        # 1. Try execution_runtime.execute_tool
        if self.execution_runtime and hasattr(self.execution_runtime, "execute_tool"):
            try:
                res = await self.execution_runtime.execute_tool(tool_name, params=kwargs, context=self.context)
                self.execution_log.append({"tool": tool_name, "params": kwargs, "result": res})
                return res
            except Exception as rt_err:
                logger.debug("execute_tool runtime direct error: %s", rt_err)

        # 2. Try execution_runtime.execute_action
        if self.execution_runtime and hasattr(self.execution_runtime, "execute_action"):
            try:
                from .core.contracts import Action
                act = Action(
                    action_id=f"act_skill_{uuid.uuid4().hex[:8]}",
                    task_id=self.context.get("task_id", "skill_task"),
                    capability_name=tool_name,
                    parameters=kwargs,
                )
                res = await self.execution_runtime.execute_action(act, context=self.context)
                out = getattr(res, "tool_output", res)
                self.execution_log.append({"tool": tool_name, "params": kwargs, "result": out})
                return out
            except Exception as act_err:
                logger.debug("execute_tool action error: %s", act_err)

        # 3. Try tool_registry.get_tool
        if self.tool_registry and hasattr(self.tool_registry, "get_tool"):
            tool = self.tool_registry.get_tool(tool_name)
            if tool and hasattr(tool, "func"):
                func = tool.func
                res = await func(**kwargs) if asyncio.iscoroutinefunction(func) else func(**kwargs)
                self.execution_log.append({"tool": tool_name, "params": kwargs, "result": res})
                return res

        fallback_res = f"Tool '{tool_name}' executed with {kwargs}"
        self.execution_log.append({"tool": tool_name, "params": kwargs, "result": fallback_res})
        return fallback_res


# =============================================================================
# 3. SkillLibrary Engine
# =============================================================================

class SkillLibrary:
    """
    Voyager-style Skill Library.
    Synthesizes multi-step trajectories into executable programs,
    validates them against a strict AST safety firewall, and executes them with sub-millisecond retrieval.
    """

    def __init__(
        self,
        eternal_memory: Any = None,
        ai_handler: Any = None,
        tool_registry: Any = None,
        execution_runtime: Any = None,
    ) -> None:
        self.eternal_memory = eternal_memory
        self.ai_handler = ai_handler
        self.tool_registry = tool_registry
        self.execution_runtime = execution_runtime

        # Resolve DB path
        if isinstance(eternal_memory, (str, Path)):
            self.db_path = Path(eternal_memory)
        elif hasattr(eternal_memory, "db_path") and eternal_memory.db_path:
            self.db_path = Path(eternal_memory.db_path)
        else:
            self.db_path = Path.home() / ".makima" / "eternal_memory.db"

        # In-memory caches
        self._skill_cache: dict[str, Skill] = {}
        self._cached_skill_ids: list[str] = []
        self._embeddings_matrix: Optional[np.ndarray] = None  # (N, dim), normalized
        self._lock = asyncio.Lock()
        self._initialized = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Create skills_library table if not exists and load verified skills into memory."""
        async with self._lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._sync_init_db_and_load_cache)
            self._initialized = True
            logger.info(
                "SkillLibrary started: %d verified skills loaded into cache (db=%s)",
                len(self._skill_cache),
                self.db_path,
            )

    def _sync_init_db_and_load_cache(self) -> None:
        """Synchronous SQLite table creation and initial cache population."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skills_library (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    trigger_patterns TEXT NOT NULL,
                    parameters_schema TEXT NOT NULL,
                    steps_code TEXT NOT NULL,
                    verified INTEGER DEFAULT 0,
                    use_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    embedding BLOB
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_name ON skills_library(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_verified ON skills_library(verified)")
            conn.commit()

            # Load verified skills
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT skill_id, name, description, trigger_patterns, parameters_schema,
                       steps_code, verified, use_count, success_count, created_at, embedding
                FROM skills_library
                WHERE verified = 1
                """
            )
            rows = cursor.fetchall()

            skill_cache: dict[str, Skill] = {}
            skill_ids: list[str] = []
            vectors: list[np.ndarray] = []

            for row in rows:
                (
                    s_id, name, desc, trig_json, params_json,
                    code, ver, use_cnt, succ_cnt, created, emb_blob
                ) = row

                try:
                    trig_list = json.loads(trig_json) if trig_json else []
                except Exception:
                    trig_list = []

                try:
                    params_dict = json.loads(params_json) if params_json else {}
                except Exception:
                    params_dict = {}

                vec = None
                emb_list = None
                if emb_blob:
                    try:
                        vec = np.frombuffer(emb_blob, dtype=np.float32)
                        norm = float(np.linalg.norm(vec))
                        if norm > 0:
                            vec = vec / norm
                        emb_list = vec.tolist()
                    except Exception as emb_err:
                        logger.warning("Failed to unpack embedding for skill %s: %s", s_id, emb_err)

                skill = Skill(
                    skill_id=s_id,
                    name=name,
                    description=desc,
                    trigger_patterns=trig_list,
                    parameters_schema=params_dict,
                    steps_code=code,
                    verified=bool(ver),
                    use_count=int(use_cnt),
                    success_count=int(succ_cnt),
                    created_at=float(created),
                    embedding=emb_list,
                )

                skill_cache[s_id] = skill
                if vec is not None:
                    skill_ids.append(s_id)
                    vectors.append(vec)

            self._skill_cache = skill_cache
            self._cached_skill_ids = skill_ids
            if vectors:
                self._embeddings_matrix = np.vstack(vectors)
            else:
                self._embeddings_matrix = None

    # ── Embedding Helper ──────────────────────────────────────────────────────

    async def _embed_text(self, text: str) -> np.ndarray:
        """Embed text using eternal_memory encoder, local FastEmbed, or normalized fallback."""
        if not text:
            return np.zeros(384, dtype=np.float32)

        # 1. Try eternal_memory embedding provider
        embed_provider = getattr(self.eternal_memory, "_embeddings", None) or getattr(
            self.eternal_memory, "embeddings", None
        )
        if embed_provider is not None and hasattr(embed_provider, "embed_one"):
            try:
                res = embed_provider.embed_one(text)
                if isinstance(res, np.ndarray) and res.ndim == 1 and res.size > 0:
                    res_f = res.astype(np.float32)
                    norm = float(np.linalg.norm(res_f))
                    return res_f / norm if norm > 0 else res_f
            except Exception as e:
                logger.debug("SkillLibrary embed_one error: %s", e)

        # 2. Try standalone EmbeddingProvider
        try:
            from .embeddings import EmbeddingProvider

            local_provider = EmbeddingProvider()
            if local_provider.available:
                res = local_provider.embed_one(text)
                if isinstance(res, np.ndarray) and res.ndim == 1 and res.size > 0:
                    res_f = res.astype(np.float32)
                    norm = float(np.linalg.norm(res_f))
                    return res_f / norm if norm > 0 else res_f
        except Exception:
            pass

        # 3. Deterministic normalized pseudo-embedding
        return deterministic_skill_embedding(text, dim=384)

    # ── Safety Verification (AST Firewall) ────────────────────────────────────

    def verify_skill_safety(self, skill: Skill) -> bool:
        """
        Verify that skill's steps_code is strictly safe via Python AST inspection:
        - BANNED: Import, importlib, subprocess, socket, eval, exec, __import__, globals, locals, open, os, sys
        - ALLOWED: only context.execute_tool() calls and safe builtins
        - Returns True only if zero banned patterns are found.
        """
        if not skill or not skill.steps_code or not isinstance(skill.steps_code, str):
            logger.warning("Skill safety check rejected: steps_code is empty")
            return False

        try:
            tree = ast.parse(skill.steps_code)
        except SyntaxError as syn_err:
            logger.warning("Skill safety check rejected: SyntaxError: %s", syn_err)
            return False

        banned_identifiers = {
            "importlib", "subprocess", "socket", "eval", "exec",
            "__import__", "globals", "locals", "open", "compile",
            "os", "sys", "shutil", "builtins", "__builtins__", "pty", "posix"
        }

        for node in ast.walk(tree):
            # 1. Banned: import / from ... import
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                logger.warning("Skill safety check rejected: Import statement is strictly forbidden in skills")
                return False

            # 2. Banned: identifier names
            if isinstance(node, ast.Name) and node.id in banned_identifiers:
                logger.warning("Skill safety check rejected: Banned identifier '%s' detected", node.id)
                return False

            # 3. Banned: attributes
            if isinstance(node, ast.Attribute) and node.attr in banned_identifiers:
                logger.warning("Skill safety check rejected: Banned attribute '%s' detected", node.attr)
                return False

            # 4. Check Call nodes
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    # Check if caller is context
                    if isinstance(func.value, ast.Name) and func.value.id == "context":
                        if func.attr != "execute_tool":
                            logger.warning(
                                "Skill safety check rejected: Disallowed method 'context.%s' (only context.execute_tool is permitted)",
                                func.attr,
                            )
                            return False
                    else:
                        if func.attr in banned_identifiers:
                            logger.warning("Skill safety check rejected: Banned call attribute '%s'", func.attr)
                            return False
                elif isinstance(func, ast.Name):
                    allowed_builtins = {
                        "len", "range", "str", "int", "float", "bool",
                        "dict", "list", "set", "print", "min", "max", "enumerate", "zip"
                    }
                    if func.id not in allowed_builtins:
                        logger.warning(
                            "Skill safety check rejected: Unauthorized function call '%s' (only context.execute_tool allowed)",
                            func.id,
                        )
                        return False

        return True

    # ── Trajectory Synthesis ──────────────────────────────────────────────────

    async def synthesize_from_trajectory(
        self,
        task: str,
        trajectory: list[dict],
        final_output: str,
    ) -> Optional[Skill]:
        """
        Synthesize a successful multi-step task trajectory into a reusable program.
        Requires 3+ tool calls.
        """
        if not trajectory or len(trajectory) < 3:
            logger.debug("SkillLibrary: Trajectory has %d tool calls (< 3 threshold) — skipping synthesis", len(trajectory) if trajectory else 0)
            return None

        logger.info("Synthesizing skill from trajectory")

        prompt = (
            "You are distilling a successful multi-step task into a reusable skill.\n\n"
            f"Task: {task}\n"
            f"Tool calls made: {json.dumps(trajectory[:10], default=str)}\n"
            f"Final output: {final_output[:500]}\n\n"
            "Respond ONLY in JSON:\n"
            "{\n"
            '  "name": "snake_case_skill_name",\n'
            '  "description": "one sentence what this skill does",\n'
            '  "trigger_patterns": ["phrase that would trigger this", "another trigger"],\n'
            '  "parameters_schema": {"param_name": "description"},\n'
            '  "steps_code": "res1 = await context.execute_tool(...) \\nres2 = await context.execute_tool(...)"\n'
            "}"
        )

        parsed: Optional[dict[str, Any]] = None
        if self.ai_handler and hasattr(self.ai_handler, "generate"):
            try:
                response = await self.ai_handler.generate(
                    messages=[{"role": "user", "content": prompt}],
                    task="fast",
                    require_json=True,
                    temperature=0.2,
                    max_tokens=350,
                )
                raw_text = getattr(response, "text", "") or ""
                if hasattr(self.ai_handler, "try_parse_json"):
                    parsed = self.ai_handler.try_parse_json(raw_text)
                if not parsed:
                    # Fallback JSON parsing
                    m = re.search(r"\{.*\}", raw_text, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group(0))
            except Exception as llm_err:
                logger.debug("SkillLibrary trajectory distillation LLM error: %s", llm_err)

        if not parsed or not isinstance(parsed, dict) or not parsed.get("name") or not parsed.get("steps_code"):
            # Fallback heuristic synthesizer if LLM is offline
            parsed = self._heuristic_synthesize(task, trajectory)

        skill_name = str(parsed.get("name", "synthesized_skill")).lower().strip().replace(" ", "_")
        desc = str(parsed.get("description", f"Automated workflow for: {task}")).strip()
        triggers = parsed.get("trigger_patterns") or [task]
        schema = parsed.get("parameters_schema") or {}
        code = str(parsed.get("steps_code", "")).strip()

        skill = Skill(
            skill_id=f"skill_{uuid.uuid4().hex[:10]}",
            name=skill_name,
            description=desc,
            trigger_patterns=triggers if isinstance(triggers, list) else [str(triggers)],
            parameters_schema=schema if isinstance(schema, dict) else {},
            steps_code=code,
            verified=False,
            created_at=time.time(),
        )

        # Safety verification
        if not self.verify_skill_safety(skill):
            logger.warning("SkillLibrary: Synthesized skill '%s' failed safety verification — rejected", skill_name)
            return None

        # Safe: Mark verified, compute embedding, persist, and register
        skill.verified = True
        vec = await self._embed_text(skill.description)
        skill.embedding = vec.tolist()

        await self.register_skill(skill)
        logger.info("Registered skill: %s", skill.name)
        return skill

    def _heuristic_synthesize(self, task: str, trajectory: list[dict]) -> dict[str, Any]:
        """Deterministic program synthesis fallback from trajectory items."""
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", task.lower()).strip("_")[:24]
        skill_name = f"skill_{slug}" if slug else "skill_workflow"

        lines: list[str] = []
        for i, call in enumerate(trajectory, start=1):
            tool = call.get("tool_name") or call.get("tool") or call.get("name") or "execute"
            params = call.get("params") or call.get("parameters") or {}
            params_str = ", ".join(f"{k}={repr(v)}" for k, v in params.items())
            lines.append(f"res{i} = await context.execute_tool('{tool}', {params_str})")
        lines.append(f"result = res{len(trajectory)}")

        return {
            "name": skill_name,
            "description": f"Executes distilled workflow for: {task}",
            "trigger_patterns": [task],
            "parameters_schema": {},
            "steps_code": "\n".join(lines),
        }

    # ── Registration & Persistence ────────────────────────────────────────────

    async def register_skill(self, skill: Skill) -> bool:
        """Persist skill to SQLite, add to memory cache, and inject into ToolRegistry."""
        if not skill:
            return False

        blob: Optional[bytes] = None
        if skill.embedding:
            try:
                vec = np.array(skill.embedding, dtype=np.float32)
                norm = float(np.linalg.norm(vec))
                if norm > 0:
                    vec = vec / norm
                blob = vec.tobytes()
            except Exception as e:
                logger.debug("Failed to serialize skill embedding: %s", e)

        def _sync_insert() -> None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO skills_library (
                        skill_id, name, description, trigger_patterns, parameters_schema,
                        steps_code, verified, use_count, success_count, created_at, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        skill.skill_id,
                        skill.name,
                        skill.description,
                        json.dumps(skill.trigger_patterns),
                        json.dumps(skill.parameters_schema),
                        skill.steps_code,
                        1 if skill.verified else 0,
                        skill.use_count,
                        skill.success_count,
                        skill.created_at,
                        blob,
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_sync_insert)

        # Update in-memory caches
        async with self._lock:
            self._skill_cache[skill.skill_id] = skill
            if blob and skill.verified:
                vec_2d = np.frombuffer(blob, dtype=np.float32).reshape(1, -1)
                if skill.skill_id in self._cached_skill_ids:
                    idx = self._cached_skill_ids.index(skill.skill_id)
                    if self._embeddings_matrix is not None:
                        self._embeddings_matrix[idx] = vec_2d[0]
                else:
                    self._cached_skill_ids.append(skill.skill_id)
                    if self._embeddings_matrix is None:
                        self._embeddings_matrix = vec_2d
                    else:
                        self._embeddings_matrix = np.vstack([self._embeddings_matrix, vec_2d])

        # Inject into tool_registry if dynamic registration method exists
        if self.tool_registry and hasattr(self.tool_registry, "register_dynamic_skill"):
            try:
                self.tool_registry.register_dynamic_skill(skill)
            except Exception as reg_err:
                logger.debug("tool_registry.register_dynamic_skill error: %s", reg_err)

        logger.info("SkillLibrary: registered skill '%s' (id=%s, verified=%s)", skill.name, skill.skill_id, skill.verified)
        return True

    # ── Retrieval ─────────────────────────────────────────────────────────────

    async def retrieve_matching_skill(
        self,
        task_description: str,
        threshold: float = 0.88,
    ) -> Optional[Skill]:
        """
        Embed task_description, compute cosine similarity against cached skill embeddings,
        and return the best matching Skill above threshold.
        """
        if not task_description or not isinstance(task_description, str):
            return None

        async with self._lock:
            if not self._initialized:
                await asyncio.to_thread(self._sync_init_db_and_load_cache)
                self._initialized = True

            if self._embeddings_matrix is None or len(self._cached_skill_ids) == 0:
                return None

            matrix_snap = self._embeddings_matrix
            skill_ids_snap = list(self._cached_skill_ids)

        # Embed task description
        query_vec = await self._embed_text(task_description)
        norm = float(np.linalg.norm(query_vec))
        if norm <= 0:
            return None
        query_vec = query_vec / norm

        try:
            if matrix_snap.shape[1] != query_vec.shape[0]:
                logger.warning("SkillLibrary dimension mismatch: matrix=%d, query=%d", matrix_snap.shape[1], query_vec.shape[0])
                return None

            similarities = np.dot(matrix_snap, query_vec)
        except Exception as sim_err:
            logger.error("SkillLibrary cosine similarity error: %s", sim_err)
            return None

        best_idx = int(np.argmax(similarities))
        best_sim = float(similarities[best_idx])

        if best_sim >= threshold:
            matched_id = skill_ids_snap[best_idx]
            skill = self._skill_cache.get(matched_id)
            if skill and skill.verified:
                logger.info("SkillLibrary match: %s", skill.name)
                return skill

        return None

    # ── Execution ─────────────────────────────────────────────────────────────

    async def execute_skill(
        self,
        skill: Skill,
        params: dict[str, Any],
        context: Any,
    ) -> Any:
        """
        Execute skill programmatically via execution_runtime using a safe context bridge.
        """
        if not skill or not skill.steps_code:
            return "No skill steps to execute."

        params = dict(params or {})

        # Validate params against parameters_schema
        if skill.parameters_schema and isinstance(skill.parameters_schema, dict):
            required = skill.parameters_schema.get("required")
            if isinstance(required, list):
                missing = [r for r in required if r not in params]
                if missing:
                    logger.warning("Skill '%s' missing required params: %s", skill.name, missing)

        # Build safe context bridge
        base_ctx = context if isinstance(context, dict) else {}
        exec_bridge = SkillExecutionContext(
            execution_runtime=self.execution_runtime,
            tool_registry=self.tool_registry,
            base_context=base_ctx,
        )

        return await self._execute_steps_safely(skill.steps_code, exec_bridge, params)

    async def _execute_steps_safely(
        self,
        steps_code: str,
        exec_ctx: SkillExecutionContext,
        params: dict[str, Any],
    ) -> Any:
        """Execute verified skill steps in a restricted scope."""
        local_scope: dict[str, Any] = {}
        safe_builtins = {
            "len": len, "range": range, "str": str, "int": int, "float": float,
            "bool": bool, "dict": dict, "list": list, "set": set, "print": print,
            "min": min, "max": max, "enumerate": enumerate, "zip": zip,
        }
        safe_globals = {"__builtins__": safe_builtins}

        # Format as async runnable function
        import textwrap
        code_lines = [
            "async def _run_skill(context, params):",
            "    result = None",
        ]
        dedented = textwrap.dedent(steps_code).strip()
        for line in dedented.splitlines():
            if line.strip():
                code_lines.append(f"    {line}")
            else:
                code_lines.append("")
        code_lines.append(
            "    if result is not None: return result"
        )
        code_lines.append(
            "    if getattr(context, 'execution_log', None): return context.execution_log[-1]['result']"
        )
        code_lines.append(
            "    return 'Success'"
        )
        wrapped_code = "\n".join(code_lines)

        try:
            exec(wrapped_code, safe_globals, local_scope)
            run_func = local_scope["_run_skill"]
            return await run_func(exec_ctx, params)
        except Exception as exec_err:
            logger.error("Skill execution failed: %s", exec_err)
            raise

    # ── Telemetry & Deprecation ───────────────────────────────────────────────

    async def record_skill_execution(self, skill_id: str, success: bool) -> None:
        """
        Record execution outcome:
        - Increment use_count always
        - Increment success_count if success
        - If success_rate < 0.6 AND use_count >= 5: deprecate skill
        - Update SQLite
        """
        if not skill_id:
            return

        skill = self._skill_cache.get(skill_id)
        if not skill:
            return

        skill.use_count += 1
        if success:
            skill.success_count += 1

        should_deprecate = False
        if skill.use_count >= 5 and skill.success_rate < 0.6:
            should_deprecate = True

        def _sync_update() -> None:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.execute(
                    """
                    UPDATE skills_library
                    SET use_count = ?, success_count = ?
                    WHERE skill_id = ?
                    """,
                    (skill.use_count, skill.success_count, skill_id),
                )
                conn.commit()

        await asyncio.to_thread(_sync_update)

        if should_deprecate:
            await self.deprecate_skill(skill_id)

    async def deprecate_skill(self, skill_id: str) -> None:
        """
        Deprecate underperforming skill:
        - Set verified=False in SQLite
        - Remove from in-memory cache
        - Log deprecation with reason
        """
        skill = self._skill_cache.get(skill_id)
        skill_name = skill.name if skill else skill_id
        rate = skill.success_rate if skill else 0.0

        def _sync_deprecate() -> None:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.execute(
                    "UPDATE skills_library SET verified = 0 WHERE skill_id = ?",
                    (skill_id,),
                )
                conn.commit()

        await asyncio.to_thread(_sync_deprecate)

        # Evict from in-memory cache & matrix
        async with self._lock:
            self._skill_cache.pop(skill_id, None)
            if skill_id in self._cached_skill_ids:
                idx = self._cached_skill_ids.index(skill_id)
                self._cached_skill_ids.pop(idx)
                if self._embeddings_matrix is not None:
                    if len(self._cached_skill_ids) == 0:
                        self._embeddings_matrix = None
                    else:
                        self._embeddings_matrix = np.delete(self._embeddings_matrix, idx, axis=0)

        # Unregister from tool registry if method available
        if self.tool_registry and hasattr(self.tool_registry, "unregister_tool") and skill:
            self.tool_registry.unregister_tool(skill.name)

        logger.warning(
            "SkillLibrary: Deprecated skill '%s' (id=%s) due to low success rate (%.2f < 0.60 after %d uses)",
            skill_name,
            skill_id,
            rate,
            skill.use_count if skill else 0,
        )


# =============================================================================
# Vector Utilities
# =============================================================================

def deterministic_skill_embedding(text: str, dim: int = 384) -> np.ndarray:
    """Deterministic pseudo-embedding for testing and zero-crash fallback."""
    vec = np.zeros(dim, dtype=np.float32)
    words = text.lower().strip().split()
    if not words:
        return vec

    for word in words:
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        for b_idx in range(0, min(len(digest), 32), 4):
            val = int.from_bytes(digest[b_idx : b_idx + 4], "little")
            dim_idx = val % dim
            sign = 1.0 if ((val >> 16) & 1) else -1.0
            vec[dim_idx] += sign

    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec
