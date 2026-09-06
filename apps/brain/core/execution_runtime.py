"""
Makima OS v9.2 — Canonical Transactional Execution Runtime
Single authority for transactional physical tool execution, policy evaluation, 
single-pass parameter normalization, timeout & retry enforcement, state capture, 
invariant verification, and Saga auto-compensation.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Callable, Optional, Union

from .contracts import Action, ActionExecutionContext, ExecutionResult, Task, MEDIA_KEYWORDS
from ..core.invariant_verifier import InvariantVerifier, VerificationResult

logger = logging.getLogger("execution_runtime")

# Domain-specific default execution timeouts (in seconds)
DOMAIN_TIMEOUT_DEFAULTS: dict[str, float] = {
    "system": 20.0,
    "media": 25.0,
    "browser": 45.0,
    "cognitive": 30.0,
    "comm": 25.0,
    "devops": 60.0,
    "security": 30.0,
    "default": 30.0,
}


def _resolve_filesystem_engine() -> Any:
    """Lazy resolver for FilesystemEngine singleton to avoid cyclic/re-imports."""
    try:
        from ..agents.filesystem_engine import get_filesystem_engine
        return get_filesystem_engine()
    except Exception as exc:
        logger.debug("[execution_runtime] FilesystemEngine unavailable: %s", exc)
        return None


def _resolve_world_state() -> Any:
    """Lazy resolver for WorldState singleton."""
    try:
        from .world_state import get_world_state
        return get_world_state()
    except Exception as exc:
        logger.debug("[execution_runtime] WorldState unavailable: %s", exc)
        return None


class ExecutionRuntime:
    """
    Canonical transactional runtime for physical action execution in Makima OS.
    Unified single authority replacing separate ToolRuntime + ExecutionRuntime wrappers.
    """
    _resolve_filesystem_engine = staticmethod(_resolve_filesystem_engine)
    _resolve_world_state = staticmethod(_resolve_world_state)

    def __init__(
        self,
        tool_registry: Any = None,
        invariant_verifier: Optional[InvariantVerifier] = None,
        learning_coordinator: Any = None,
        guardrails: Any = None,
        recovery_manager: Any = None,
        saga_recovery: Any = None,
        kernel: Any = None,
    ) -> None:
        self.tool_registry = tool_registry
        if self.tool_registry and hasattr(self.tool_registry, "set_execution_runtime"):
            self.tool_registry.set_execution_runtime(self)
        self.verifier = invariant_verifier or InvariantVerifier()
        self.learning_coordinator = learning_coordinator
        self.reflexion_engine = learning_coordinator
        self.guardrails = guardrails
        self.saga_recovery = saga_recovery or recovery_manager
        self.recovery_manager = self.saga_recovery
        self.kernel = kernel
        self._background_tasks: set[asyncio.Task] = set()

    def _determine_timeout(self, action: Action, tool_meta: Any = None) -> float:
        """Calculate the timeout deadline for the given Action."""
        # 1. Action-level explicit override
        if hasattr(action, "timeout_s") and action.timeout_s and action.timeout_s > 0:
            return float(action.timeout_s)
        if isinstance(action.parameters, dict) and "timeout_s" in action.parameters:
            try:
                return float(action.parameters["timeout_s"])
            except (ValueError, TypeError):
                pass

        # 2. Tool metadata policy override
        if tool_meta and getattr(tool_meta, "timeout_s", None):
            try:
                return float(tool_meta.timeout_s)
            except (ValueError, TypeError):
                pass

        # 3. Tool name / capability domain heuristic
        cap = (action.capability_name or "").lower()
        if any(k in cap for k in ("browser", "scrape", "navigate", "page", "cdp")):
            return DOMAIN_TIMEOUT_DEFAULTS["browser"]
        if any(k in cap for k in ("docker", "deploy", "build", "ci", "git_clone")):
            return DOMAIN_TIMEOUT_DEFAULTS["devops"]
        if any(k in cap for k in MEDIA_KEYWORDS):
            return DOMAIN_TIMEOUT_DEFAULTS["media"]
        if any(k in cap for k in ("security", "audit", "vuln")):
            return DOMAIN_TIMEOUT_DEFAULTS["security"]
        if any(k in cap for k in ("msg", "whatsapp", "telegram", "email", "discord")):
            return DOMAIN_TIMEOUT_DEFAULTS["comm"]
        if any(k in cap for k in ("file", "window", "process", "desktop", "os", "clipboard")):
            return DOMAIN_TIMEOUT_DEFAULTS["system"]

        return DOMAIN_TIMEOUT_DEFAULTS["default"]

    COMMON_PARAM_ALIASES: dict[str, list[str]] = {
        "path": ["file_path", "filepath", "target_path", "filename", "file", "target_file", "path_str", "targetpath"],
        "file_path": ["path", "filepath", "target_path", "filename", "file", "target_file", "path_str", "targetpath"],
        "content": ["text", "body", "data", "message", "script", "code", "file_content"],
        "text": ["content", "body", "data", "message", "script", "code"],
        "query": ["search_query", "q", "text", "search_text", "search", "keyword", "keywords", "prompt", "querystr", "searchquery", "query_str", "term"],
        "search_query": ["query", "q", "text", "search_text", "search", "keyword", "querystr", "searchquery", "query_str", "term"],
        "url": ["uri", "link", "target_url", "web_url", "address", "urllink", "website", "href", "site"],
        "code": ["script", "source_code", "command", "snippet"],
        "message": ["text", "message_text", "body", "content", "msg"],
        "timeout": ["timeout_s", "timeout_seconds"],
        "timeout_s": ["timeout", "timeout_seconds"],
        "source_path": ["src", "src_path", "source", "source_file"],
        "target_folder_or_path": ["dst", "dest", "destination", "target", "destination_path", "target_path"],
        "selector": ["css_selector", "xpath_selector", "target", "element"],
    }

    FILE_TOOLS: frozenset[str] = frozenset({
        "read_file", "write_file", "delete_file", "move_file", "copy_file", "rename_file",
        "profile_dataset", "execute_query", "statistical_test", "generate_chart", "export_dataset",
        "parse_document", "compile_pdf", "create_spreadsheet", "create_presentation", "convert_document",
    })

    @staticmethod
    def _is_retriable(error_msg: Optional[str], retryable_keywords: tuple[str, ...]) -> bool:
        """Check if an execution failure message matches configured retriable keywords."""
        if not error_msg or not retryable_keywords:
            return False
        err_lower = str(error_msg).lower()
        return any(k.lower() in err_lower for k in retryable_keywords)

    @staticmethod
    def _validate_params(
        schema: dict[str, Any],
        params: dict[str, Any],
        critical_params: Optional[tuple[str, ...] | list[str]] = None,
    ) -> str:
        """Validate parameters against standard tool JSON-Schema subset and SAGE critical parameters."""
        if critical_params:
            # Verify that at least one critical parameter slot contains real, non-empty data
            has_critical = False
            for cp in critical_params:
                val = params.get(cp)
                if val is not None and val != "" and val != [] and val != {}:
                    has_critical = True
                    break
            if not has_critical:
                return f"missing required specification: at least one of {list(critical_params)} must be provided with non-empty data"

        if not schema or not isinstance(schema, dict):
            return ""
        properties = schema.get("properties") or {}
        missing = [name for name in schema.get("required", []) if name not in params]
        if missing:
            return f"missing required parameter(s): {', '.join(missing)}"

        type_names = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        for name, value in params.items():
            definition = properties.get(name) or {}
            expected = type_names.get(definition.get("type"))
            if expected and (isinstance(value, bool) and expected is not bool or
                             not isinstance(value, expected)):
                return f"parameter '{name}' must be of type {definition.get('type')}"
            if "enum" in definition and value not in definition["enum"]:
                return f"parameter '{name}' must be one of {definition['enum']}"
            if isinstance(value, str):
                if "minLength" in definition and len(value) < definition["minLength"]:
                    return f"parameter '{name}' is shorter than minLength"
                if "maxLength" in definition and len(value) > definition["maxLength"]:
                    return f"parameter '{name}' exceeds maxLength"
        return ""

    def _normalize_tool_parameters(
        self,
        tool_name_or_action: Union[str, Action, None] = None,
        params: Optional[dict[str, Any]] = None,
        handler: Optional[Callable] = None,
        schema: Optional[dict[str, Any]] = None,
        *,
        tool_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Universal Single-Pass Parameter Normalization:
        1. Clean whitespace & extract key-values
        2. Schema-driven alias remapping & fuzzy mapping
        3. Safe primitive type coercion (int, float, bool, str)
        4. Volume intent words translation (badhao/kam -> delta=+10/-10)
        5. Relative path grounding to Desktop / Workspace root
        6. Local handler signature kwargs adaptation
        """
        effective_name = tool_name or ""
        if hasattr(tool_name_or_action, "capability_name") and hasattr(tool_name_or_action, "parameters"):
            effective_name = str(tool_name_or_action.capability_name or "")
            params = dict(tool_name_or_action.parameters or {})
        elif isinstance(tool_name_or_action, str) and not effective_name:
            effective_name = tool_name_or_action
        params = dict(params or {})
        tool_name = effective_name

        normalized: dict[str, Any] = {}
        for k, v in params.items():
            if k is not None and str(k).strip():
                clean_k = str(k).strip()
                normalized[clean_k] = v

        properties = (schema.get("properties") if isinstance(schema, dict) else {}) or {}

        # 1. Alias normalization
        for canonical, aliases in self.COMMON_PARAM_ALIASES.items():
            if canonical not in normalized:
                for alias in aliases:
                    if alias in normalized:
                        normalized[canonical] = normalized[alias]
                        break

        # 2. Schema-driven type coercion
        if properties:
            for param_key, param_val in list(normalized.items()):
                target_def = properties.get(param_key)
                if not target_def or not isinstance(target_def, dict):
                    continue
                target_type = target_def.get("type")
                if target_type == "integer" and isinstance(param_val, str):
                    try:
                        normalized[param_key] = int(float(param_val.strip()))
                    except (ValueError, TypeError):
                        pass
                elif target_type == "number" and isinstance(param_val, str):
                    try:
                        normalized[param_key] = float(param_val.strip())
                    except (ValueError, TypeError):
                        pass
                elif target_type == "boolean" and isinstance(param_val, str):
                    v_low = param_val.strip().lower()
                    if v_low in ("true", "1", "yes", "on"):
                        normalized[param_key] = True
                    elif v_low in ("false", "0", "no", "off"):
                        normalized[param_key] = False
                elif target_type == "string" and not isinstance(param_val, (dict, list, str)) and param_val is not None:
                    normalized[param_key] = str(param_val)

        # 3. Volume intent normalization
        if tool_name in ("set_volume", "adjust_volume", "volume", "media_set_volume"):
            _INCREASE_WORDS = frozenset(("increase", "raise", "up", "loud", "louder", "badhao", "badha", "higher", "more", "tez", "zyada"))
            _DECREASE_WORDS = frozenset(("decrease", "lower", "down", "dheere", "kam", "quiet", "quieter", "softer", "less", "reduce"))
            raw_level = normalized.get("level")
            if isinstance(raw_level, str) and not raw_level.strip().lstrip("+-").isdigit():
                dir_str = raw_level.strip().lower()
                if dir_str in _INCREASE_WORDS:
                    normalized.setdefault("delta", 10)
                    normalized.pop("level", None)
                elif dir_str in _DECREASE_WORDS:
                    normalized.setdefault("delta", -10)
                    normalized.pop("level", None)
                else:
                    normalized.pop("level", None)
            if "delta" in normalized and isinstance(normalized["delta"], str):
                try:
                    normalized["delta"] = int(float(normalized["delta"].strip()))
                except (ValueError, TypeError):
                    pass

        # 4. Path grounding for file tools
        if tool_name in self.FILE_TOOLS or any(k in tool_name for k in ("file", "doc", "chart", "dataset", "pdf", "sheet")):
            from .known_folders import resolve_known_folder
            desktop_path = resolve_known_folder("desktop") or os.path.expanduser("~/Desktop")
            workspace_root = getattr(self, "workspace_root", None) or os.getcwd()

            path_keys = ("path", "file_path", "output_path", "source_path", "target_folder_or_path", "filename", "dst", "src")
            for pk in path_keys:
                val = normalized.get(pk)
                if isinstance(val, str) and val.strip():
                    clean_val = val.strip().strip("'\"")
                    if not os.path.isabs(clean_val) and not any(clean_val.startswith(p) for p in ("http://", "https://", "ms-", "file://")):
                        if clean_val.lower().startswith("desktop") or "desktop" in clean_val.lower():
                            rel_sub = re.sub(r'^(?:desktop[\\/]|desktop\s*)', '', clean_val, flags=re.I).strip()
                            resolved = os.path.normpath(os.path.join(desktop_path, rel_sub))
                            normalized[pk] = resolved
                        else:
                            resolved = os.path.normpath(os.path.join(workspace_root, clean_val))
                            normalized[pk] = resolved

        # 5. Handler signature adaptation
        if handler:
            try:
                sig = inspect.signature(handler)
                has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                expected_params = list(sig.parameters.keys())
                if not has_var_kw:
                    dropped = [k for k in normalized if k not in expected_params]
                    if dropped:
                        logger.debug(
                            "[execution_runtime] Dropping params %s not in handler signature for '%s'",
                            dropped, tool_name,
                        )
                    normalized = {k: v for k, v in normalized.items() if k in expected_params}
            except Exception:
                pass

        return normalized

    def _extract_artifacts(self, tool_name: str, params: dict[str, Any], raw_output: Any) -> dict[str, Any]:
        """Extract structured domain artifacts (files, URLs, dataset handles, entities) from execution."""
        artifacts: dict[str, Any] = {}
        out_str = str(raw_output or "")

        # A. File extraction
        files: list[str] = []
        for pk in ("path", "file_path", "output_path", "destination_path", "target_folder_or_path", "filename"):
            if pk in params and isinstance(params[pk], str) and params[pk].strip():
                p_val = params[pk].strip()
                if os.path.isabs(p_val) or any(ext in p_val for ext in (".", "\\", "/")):
                    files.append(p_val)

        path_matches = re.findall(r'(?:[a-zA-Z]:[\\/][^\r\n<>:"|?*]+|/(?:Users|home|tmp|var|opt|code)[^\r\n<>:"|?*]+)', out_str)
        for pm in path_matches:
            clean_pm = pm.strip().rstrip(".,;)'\"]")
            if clean_pm and clean_pm not in files:
                files.append(clean_pm)

        if files:
            artifacts["files"] = files
            artifacts["created_files"] = files

        # B. URL extraction
        urls: list[str] = []
        if "url" in params and isinstance(params["url"], str):
            urls.append(params["url"].strip())
        if "urls" in params and isinstance(params["urls"], list):
            urls.extend([str(u).strip() for u in params["urls"] if u])

        found_urls = re.findall(r'https?://[^\s<>"\')]+', out_str)
        for u in found_urls:
            clean_u = u.strip().rstrip(".,;)")
            if clean_u not in urls:
                urls.append(clean_u)

        if urls:
            artifacts["urls"] = urls
            artifacts["extracted_urls"] = urls

        # C. Structured output envelope
        if isinstance(raw_output, dict):
            for k in ("schema", "columns", "row_count", "metrics", "chart_path", "download_path", "entity"):
                if k in raw_output:
                    artifacts[k] = raw_output[k]

        return artifacts

    async def execute_action(
        self,
        action: Action,
        context: Optional[ActionExecutionContext] = None,
        local_tool_map: Optional[dict[str, Callable]] = None,
    ) -> ExecutionResult:
        """
        Execute an Action through the canonical unified transactional lifecycle:
        1. Preflight & Policy Authorization (Guardrails)
        2. Pre-State Capture (WorldState)
        3. Single-Pass Parameter Normalization, Validation, and Path Grounding
        4. Physical Execution with Dynamic Timeout & Exponential Retry Backoff
        5. Reversibility & Structured Snapshot Metadata Extraction
        6. Post-State Capture & Invalidation
        7. Invariant Verification & Saga Auto-Compensation
        8. Learning & Telemetry Signal Emission
        """
        t0 = time.perf_counter()
        exec_id = f"exec_{uuid.uuid4().hex[:10]}"
        ctx = context or ActionExecutionContext(
            task_id=action.task_id,
            execution_id=exec_id,
        )
        ctx.task_id = action.task_id
        ctx.execution_id = exec_id

        # ── 1. Policy & Preflight Authorization ───────────────────────────────
        if self.guardrails and hasattr(self.guardrails, "check_action"):
            try:
                allowed, reason = await self.guardrails.check_action(
                    capability=action.capability_name,
                    parameters=action.parameters,
                    risk_level=action.risk_level,
                )
                if not allowed:
                    duration_ms = (time.perf_counter() - t0) * 1000
                    logger.warning(
                        "[execution_runtime] Action '%s' blocked by policy: %s",
                        action.capability_name,
                        reason,
                    )
                    return ExecutionResult(
                        execution_id=exec_id,
                        action_id=action.action_id,
                        task_id=action.task_id,
                        is_verified=False,
                        evidence_tier="PERMISSION_DENIED",
                        duration_ms=duration_ms,
                        error=f"Permission denied: {reason}",
                    )
            except Exception as guard_err:
                logger.warning("[execution_runtime] Guardrail check error: %s", guard_err)

        # ── 2. Pre-State Capture ──────────────────────────────────────────────
        pre_state: dict[str, Any] = {}
        ws = _resolve_world_state()
        if ws:
            try:
                pre_state = await ws.get_snapshot("os")
                ctx.observed_pre_state = pre_state
            except Exception as state_err:
                logger.debug("[execution_runtime] Pre-state capture error: %s", state_err)

        # ── 3. Resolve Tool & Handler ─────────────────────────────────────────
        tool_name = action.capability_name
        tool_meta = None
        handler = (local_tool_map.get(tool_name) if local_tool_map else None)

        if not handler and self.tool_registry and hasattr(self.tool_registry, "_tools"):
            tool_meta = self.tool_registry._tools.get(tool_name)
            if tool_meta:
                handler = tool_meta.func

        schema = getattr(tool_meta, "schema", None) if tool_meta else None
        critical_params = getattr(tool_meta, "critical_parameters", ()) if tool_meta else ()

        # ── 4. Single-Pass Parameter Normalization & Validation ───────────────
        params = self._normalize_tool_parameters(
            tool_name=tool_name,
            params=dict(action.parameters or {}),
            handler=handler,
            schema=schema,
        )

        validation_error = self._validate_params(schema or {}, params, critical_params=critical_params)
        if validation_error:
            duration_ms = (time.perf_counter() - t0) * 1000
            res_obj = ExecutionResult(
                execution_id=exec_id,
                action_id=action.action_id,
                task_id=action.task_id,
                is_verified=False,
                evidence_tier="INVALID_PARAMETERS",
                duration_ms=duration_ms,
                error=f"Invalid parameters for tool '{tool_name}': {validation_error}",
            )
            if getattr(self, "saga_recovery", None):
                recovery = await self.saga_recovery.handle_failure(
                    action, res_obj, ctx
                )
                if recovery.recovered and recovery.result is not None:
                    logger.info("Recovery succeeded")
                    return recovery.result
                res_obj.metadata["recovery_report"] = recovery.diagnostic_report
                res_obj.metadata["recovery_strategy"] = recovery.strategy_used
            return res_obj

        # Context injection if handler expects it
        if handler:
            try:
                sig = inspect.signature(handler)
                if "context" in sig.parameters:
                    params["context"] = ctx
            except Exception:
                pass

        # ── 5. Physical Tool Invocation with Dynamic Timeout & Retry Backoff ──
        timeout_s = self._determine_timeout(action, tool_meta)
        attempts = max(1, 1 + getattr(tool_meta, "max_retries", 0))
        retry_backoff_s = getattr(tool_meta, "retry_backoff_s", 1.0)
        retryable_keywords = getattr(tool_meta, "retryable_keywords", ())

        raw_output: Any = None
        tool_error: Optional[str] = None
        is_timeout: bool = False

        if not handler:
            tool_error = f"Tool '{tool_name}' not found in registry"
            logger.error("[execution_runtime] %s", tool_error)
        else:
            for attempt in range(1, attempts + 1):
                is_timeout = False
                tool_error = None
                try:
                    if inspect.iscoroutinefunction(handler):
                        raw_output = await asyncio.wait_for(handler(**params), timeout=timeout_s)
                    else:
                        res_val = handler(**params)
                        if inspect.isawaitable(res_val):
                            raw_output = await asyncio.wait_for(res_val, timeout=timeout_s)
                        else:
                            raw_output = res_val
                    break  # Success — break out of retry loop
                except asyncio.TimeoutError:
                    is_timeout = True
                    tool_error = f"Execution timed out after {timeout_s:.1f}s"
                    logger.warning("[execution_runtime] Tool '%s' attempt %d timed out after %.1fs", tool_name, attempt, timeout_s)
                except TypeError as te:
                    tool_error = f"Parameter mismatch: {te}"
                    logger.error("[execution_runtime] Tool '%s' parameter mismatch: %s", tool_name, te)
                    break  # TypeErrors are non-retriable code bugs
                except Exception as exc:
                    if isinstance(exc, RuntimeError) and "CAPTCHA detected" in str(exc):
                        raise
                    tool_error = str(exc)
                    logger.warning("[execution_runtime] Tool '%s' attempt %d failed: %s", tool_name, attempt, exc)

                if attempt < attempts and self._is_retriable(tool_error, retryable_keywords):
                    backoff = retry_backoff_s * attempt
                    logger.info("[execution_runtime] Tool '%s' retrying in %.1fs (attempt %d/%d)...", tool_name, backoff, attempt + 1, attempts)
                    await asyncio.sleep(backoff)
                    continue
                break

        # Record metrics on tool_meta if available
        if tool_meta:
            tool_meta.call_count += 1
            if tool_error:
                tool_meta.failure_count += 1

        # ── 6. Reversibility & Structured Snapshot Metadata ───────────────────
        res_str = str(raw_output or "")

        if isinstance(raw_output, dict):
            if "snapshot_id" in raw_output:
                ctx.snapshot_id = raw_output.get("snapshot_id")
                ctx.reversibility = "snapshot"
            if "compensation_tool" in raw_output:
                ctx.compensation_tool = str(raw_output["compensation_tool"])
            if "compensation_params" in raw_output and isinstance(raw_output["compensation_params"], dict):
                ctx.compensation_params = raw_output["compensation_params"]

        if tool_name in ("write_file", "move_file", "rename_file", "delete_file", "organize_desktop"):
            ctx.reversibility = "snapshot"
            if not ctx.snapshot_id and "[Snapshot: " in res_str:
                sm = re.search(r'\[Snapshot: (snap_[a-zA-Z0-9_.]+)\]', res_str)
                if sm:
                    ctx.snapshot_id = sm.group(1)

            if tool_name == "move_file" and not ctx.compensation_tool:
                ctx.compensation_tool = "restore_move_transaction"
                src_p = params.get("source_path") or params.get("src")
                dst_p = params.get("target_folder_or_path") or params.get("dst")
                if src_p and dst_p:
                    try:
                        fe = _resolve_filesystem_engine()
                        if fe:
                            resolved_src = fe._resolve(str(src_p))
                            resolved_dst = fe._resolve(str(dst_p))
                            dst_t = (
                                os.path.join(resolved_dst, os.path.basename(resolved_src))
                                if os.path.isdir(resolved_dst)
                                else resolved_dst
                            )
                            ctx.compensation_params = {
                                "source_path": resolved_src,
                                "destination_path": dst_t,
                                "src_snapshot_id": ctx.snapshot_id,
                                "dst_existed_before": False,
                            }
                    except Exception as fe_err:
                        logger.debug("[execution_runtime] move_file compensation resolve error: %s", fe_err)

            elif tool_name == "organize_desktop" and not ctx.compensation_tool:
                ctx.compensation_tool = "rollback_organize_desktop"
                if "[Manifest: " in res_str:
                    mm = re.search(r'\[Manifest: ({.*})\]', res_str)
                    if mm:
                        try:
                            ctx.compensation_params = json.loads(mm.group(1))
                        except Exception:
                            ctx.compensation_params = {}
            elif not ctx.compensation_tool:
                ctx.compensation_tool = "restore_snapshot"
                t_path = params.get("path") or params.get("file_path") or params.get("filename")
                ctx.compensation_params = {
                    "target_path": str(t_path) if t_path else "",
                    "snapshot_id": ctx.snapshot_id,
                }

        # ── 7. Post-State Capture & Invalidation ──────────────────────────────
        post_state: dict[str, Any] = {}
        if ws:
            try:
                ws.invalidate("os")
                ws.invalidate("process")
                ws.invalidate("window")
                post_state = await ws.get_snapshot("os", force=True)
                ctx.observed_post_state = post_state
            except Exception as state_err:
                logger.debug("[execution_runtime] Post-state capture error: %s", state_err)

        # ── 8. Invariant Verification & Saga Auto-Compensation ────────────────
        verification: VerificationResult
        if is_timeout:
            verification = VerificationResult(
                status="TIMEOUT",
                reason=tool_error or "Execution timed out",
                expected_state=action.expected_state,
                observed_state=post_state,
                confidence=0.0,
            )
        elif tool_error:
            verification = VerificationResult(
                status="VERIFIED_FAILURE",
                reason=tool_error,
                expected_state=action.expected_state,
                observed_state=post_state,
                confidence=0.0,
            )
        else:
            try:
                verification = await self.verifier.verify(
                    tool_name=tool_name,
                    params=params,
                    tool_result=raw_output,
                )
            except Exception as verif_err:
                logger.warning("[execution_runtime] Invariant verification error: %s", verif_err)
                verification = VerificationResult(
                    status="UNVERIFIABLE",
                    reason=f"Verification fallback: {verif_err}",
                    confidence=0.5,
                )

        ctx.verification_passed = verification.status in ("VERIFIED_SUCCESS", "UNVERIFIABLE")
        ctx.verification_status = verification.status
        ctx.verification_confidence = getattr(verification, "confidence", 1.0 if verification.status == "VERIFIED_SUCCESS" else 0.5)
        duration_ms = (time.perf_counter() - t0) * 1000
        if verification.status == "VERIFIED_FAILURE" and not tool_error:
            tool_error = f"[Physical Verification Failed]: {verification.reason}"
        is_success = ctx.verification_passed and not tool_error and not is_timeout
        is_verified = (verification.status == "VERIFIED_SUCCESS") and is_success
        rollback_done = False

        # Saga Rollback on failure
        if not is_success and ctx.reversibility == "snapshot":
            fe = _resolve_filesystem_engine()
            if fe:
                try:
                    restored_ok = False
                    restored_err = ""
                    comp_func = getattr(fe, ctx.compensation_tool, None) if ctx.compensation_tool else None

                    if comp_func:
                        if ctx.compensation_tool == "restore_snapshot":
                            snap_id = ctx.compensation_params.get("snapshot_id") or ctx.snapshot_id
                            tgt = ctx.compensation_params.get("target_path") or ""
                            if snap_id and tgt:
                                restored_ok, restored_err = await fe.restore_snapshot(snap_id, tgt)
                        elif ctx.snapshot_id and "target_path" in ctx.compensation_params:
                            snap_id = ctx.compensation_params.get("snapshot_id") or ctx.snapshot_id
                            tgt = ctx.compensation_params.get("target_path") or ""
                            if snap_id and tgt:
                                restored_ok, restored_err = await fe.restore_snapshot(snap_id, tgt)
                        else:
                            restored_ok, restored_err = await comp_func(ctx.compensation_params)
                    elif ctx.snapshot_id and ctx.compensation_params.get("target_path"):
                        restored_ok, restored_err = await fe.restore_snapshot(
                            ctx.snapshot_id,
                            ctx.compensation_params["target_path"],
                        )

                    rollback_done = bool(restored_ok)
                    ctx.rollback_result = {
                        "attempted": True,
                        "success": restored_ok,
                        "verified": restored_ok,
                        "error": restored_err,
                    }

                    if restored_ok:
                        tool_error = (
                            f"Invariant verification failed: {verification.reason} "
                            f"[Auto-compensated: Rollback verified]"
                        )
                        logger.info(
                            "[execution_runtime] Auto-compensation succeeded for '%s' (snapshot: %s)",
                            tool_name,
                            ctx.snapshot_id,
                        )
                    else:
                        tool_error = (
                            f"Invariant verification failed: {verification.reason} "
                            f"[Rollback failed: {restored_err}]"
                        )
                        logger.critical(
                            "[execution_runtime] DOUBLE FAULT: Action '%s' failed AND rollback failed: %s",
                            tool_name,
                            restored_err,
                        )
                except Exception as comp_exc:
                    ctx.rollback_result = {
                        "attempted": True,
                        "success": False,
                        "verified": False,
                        "error": str(comp_exc),
                    }
                    logger.critical(
                        "[execution_runtime] DOUBLE FAULT exception during rollback of '%s': %s",
                        tool_name,
                        comp_exc,
                    )

        # ── 9. Learning & Telemetry Signal Emission ───────────────────────────
        if self.learning_coordinator and hasattr(self.learning_coordinator, "enqueue_signal"):
            try:
                signal = {
                    "signal_type": "tool_failure" if not is_success else "tool_success",
                    "type": "tool_failure" if not is_success else "tool_success",
                    "task_id": action.task_id,
                    "action_id": action.action_id,
                    "tool_name": tool_name,
                    "capability": tool_name,
                    "agent_name": getattr(ctx, "agent_name", "") or getattr(action, "agent_name", "") or "",
                    "is_success": is_success,
                    "duration_ms": duration_ms,
                    "error": tool_error or (verification.reason if not is_success else None),
                    "error_text": tool_error or (verification.reason if not is_success else ""),
                    "params": params,
                    "timestamp": time.time(),
                }
                sig_task = asyncio.create_task(self.learning_coordinator.enqueue_signal(signal))
                self._background_tasks.add(sig_task)
                sig_task.add_done_callback(self._background_tasks.discard)
            except Exception as sig_err:
                logger.debug("[execution_runtime] Learning signal emit error: %s", sig_err)

        if self.kernel and hasattr(self.kernel, "record_telemetry"):
            try:
                domain = "system"
                if "browser" in tool_name or "scrape" in tool_name:
                    domain = "browser"
                elif "media" in tool_name or "spotify" in tool_name:
                    domain = "media"
                self.kernel.record_telemetry(domain=domain, latency_ms=duration_ms, is_error=not is_success)
            except Exception as kern_err:
                logger.debug("[execution_runtime] Kernel telemetry error: %s", kern_err)

        # ── 10. Assemble Result & Recovery Strategy ───────────────────────────
        evidence_tier_val = "TIMEOUT" if is_timeout else getattr(verification, "status", "UNVERIFIABLE")
        artifacts = self._extract_artifacts(tool_name, params, raw_output)
        ctx.artifacts = artifacts

        res_obj = ExecutionResult(
            execution_id=exec_id,
            action_id=action.action_id,
            task_id=action.task_id,
            pre_state_snapshot=pre_state,
            tool_output=raw_output,
            post_state_snapshot=post_state,
            is_verified=is_verified,
            evidence_tier=evidence_tier_val,
            duration_ms=duration_ms,
            error=tool_error or (verification.reason if not is_success else None),
            rollback_performed=rollback_done,
            artifacts=artifacts,
        )

        if not is_success:
            if getattr(self, "saga_recovery", None):
                recovery = await self.saga_recovery.handle_failure(
                    action, res_obj, ctx
                )
                if recovery.recovered and recovery.result is not None:
                    logger.info("Recovery succeeded")
                    return recovery.result
                res_obj.metadata["recovery_report"] = recovery.diagnostic_report
                res_obj.metadata["recovery_strategy"] = recovery.strategy_used
                if recovery.compensated_actions:
                    res_obj.metadata["compensated_actions"] = recovery.compensated_actions
        else:
            if getattr(self, "saga_recovery", None):
                self.saga_recovery.log_action_success(action)
                if ctx.snapshot_id or ctx.compensation_tool:
                    self.saga_recovery.register_compensation(
                        task_id=action.task_id,
                        action_id=action.action_id,
                        snapshot_id=ctx.snapshot_id,
                        compensation_tool=ctx.compensation_tool,
                        compensation_params=ctx.compensation_params,
                        tool_name=action.capability_name,
                    )

        return res_obj

    async def execute_tool(
        self,
        name: str,
        params: Optional[dict[str, Any]] = None,
        context: Any = None,
        task_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Convenience method to execute a tool by name and return raw output."""
        merged_params = dict(params or {})
        merged_params.update(kwargs)
        t_id = task_id or getattr(context, "task_id", "") or "default_task"
        action = Action(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            task_id=t_id,
            capability_name=name,
            parameters=merged_params,
        )
        res = await self.execute_action(action, context=context if isinstance(context, ActionExecutionContext) else None)
        if not res.is_verified and res.error:
            return f"Error executing tool {name}: {res.error}"
        if isinstance(res.tool_output, (dict, list)):
            return json.dumps(res.tool_output, default=str)
        return str(res.tool_output if res.tool_output is not None else "")
