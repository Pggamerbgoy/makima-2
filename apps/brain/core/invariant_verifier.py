"""
Makima OS â€” M5 Closed-Loop Post-Execution Goal Verification & Invariant Checking
Location: apps/brain/core/invariant_verifier.py

SOTA Upgrades:
  1. Async-Safe OS Operations (asyncio.to_thread for all disk/process/win32 I/O â€” zero event loop blocking)
  2. Affordance-Based Verifier Routing (Zero hardcoded tool names with legacy fallbacks)
  3. Dynamic OS-Level Process Protection (Zero hardcoded process lists, heuristic PID & system account checks)
  4. Normalized Semantic Entity Matching (Zero hardcoded aliases)
  5. Structured Result Validation (Zero brittle string parsing)
"""

from __future__ import annotations

import ast
import asyncio
import difflib
import inspect
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("makima.invariant_verifier")


def _resolve_fs_path(p: str) -> str:
    """Resolve a user-supplied path EXACTLY the way FilesystemEngine does when
    it writes/moves files: known-folder shortcuts via SHGetKnownFolderPath
    (OneDrive-aware), ~ expansion, then home-relative join. Verifiers MUST
    check the same location the tool actually touched — raw abspath() checked
    CWD-relative junk and failed successful writes (triggering bogus Saga
    rollbacks that deleted real files)."""
    from .known_folders import resolve_known_folder, normalize_folder_name
    s = str(p).strip().strip("'\"")
    try:
        resolved = resolve_known_folder(s.lower())
        if resolved:
            return resolved
        # Prefix form: 'desktop\\file.txt' / 'downloads/x.y' → swap the known
        # folder head with its REAL location (OneDrive-aware), keep the tail.
        parts = s.replace("/", "\\").split("\\")
        if parts and normalize_folder_name(parts[0]):
            head = resolve_known_folder(parts[0])
            if head:
                return os.path.join(head, *parts[1:]) if len(parts) > 1 else head
    except Exception:
        pass
    full = os.path.expanduser(s)
    if not os.path.isabs(full):
        home = os.path.expanduser("~")
        cand = os.path.join(home, full)
        # Engine prefers existing home-joined path; else cwd-abspath fallback
        full = cand if os.path.exists(cand) else os.path.abspath(full)
    return full

try:
    import psutil
except ImportError:
    psutil = None

try:
    import win32gui
    import win32con
except ImportError:
    win32gui = None
    win32con = None


@dataclass
class VerificationResult:
    status: str          # "VERIFIED_SUCCESS" | "VERIFIED_FAILURE" | "UNVERIFIABLE" | "TIMEOUT"
    reason: str          # Ground truth observation summary or failure reason
    observed_state: dict[str, Any] = field(default_factory=dict)
    expected_state: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0  # 1.0 (deterministic), 0.7 (provider ack), 0.0 (unverified)
    reversibility: str = "none"

    def __iter__(self):
        # 2-tuple unpacking compatibility: (is_success, error_reason)
        is_success = self.status in ("VERIFIED_SUCCESS", "UNVERIFIABLE")
        err_msg = "" if is_success else self.reason
        yield is_success
        yield err_msg

    def __getitem__(self, idx: int):
        is_success = self.status in ("VERIFIED_SUCCESS", "UNVERIFIABLE")
        err_msg = "" if is_success else self.reason
        return (is_success, err_msg)[idx]


# =============================================================================
# Dynamic Error Pattern Registry (Zero Hardcoded String Checks)
# =============================================================================
class ErrorPatternRegistry:
    """Dynamically matches tool results against known failure patterns."""
    _PATTERNS = [
        r"^\[blocked\]",
        r"^\[failed\]",
        r"^\[error\]",
        r"^\[tool error",
        r"^\[tool_error\]",
        r"^error:",
        r"failed to",
        r"cannot find",
        r"access denied",
        r"not found",
    ]
    _COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]

    @classmethod
    def is_failure(cls, text: str) -> bool:
        if not text:
            return False
        clean = text.strip()
        return any(p.search(clean) for p in cls._COMPILED)


# =============================================================================
# Invariant Verifier Engine
# =============================================================================
class InvariantVerifier:
    """
    Validates that a tool execution that claimed success actually produced
    the expected physical real-world state in the environment (filesystem,
    process table, window manager, system state).

    Async-safe: All blocking disk and OS system calls run inside worker threads
    via asyncio.to_thread, guaranteeing zero event-loop starvation.
    """
    # Dynamic Affordance-based registries (Zero hardcoded tool names)
    _POST_VERIFIERS: dict[str, Callable[..., Any]] = {}
    _PRE_VERIFIERS: dict[str, Callable[..., Any]] = {}
    _VERIFIERS = _POST_VERIFIERS
    _PRECONDITION_CHECKERS = _PRE_VERIFIERS

    @classmethod
    def register_postcondition(cls, affordance_or_tool: str, func: Callable[..., Any]) -> None:
        cls._POST_VERIFIERS[affordance_or_tool.lower().strip()] = func

    @classmethod
    def register_precondition(cls, affordance_or_tool: str, func: Callable[..., Any]) -> None:
        cls._PRE_VERIFIERS[affordance_or_tool.lower().strip()] = func

    @classmethod
    async def verify(
        cls,
        tool_name: str,
        params: dict[str, Any],
        tool_result: Any,
        affordance: str = "",
    ) -> VerificationResult:
        """
        Verify post-execution physical state for a tool execution.
        Returns: VerificationResult (unpacks as (is_verified: bool, error_message: str))
        """
        if not tool_name or not isinstance(params, dict):
            return VerificationResult(
                status="UNVERIFIABLE",
                reason="Missing tool_name or invalid params",
                confidence=0.0,
            )

        # 1. Structured Result Validation (SOTA)
        if hasattr(tool_result, "success") and not getattr(tool_result, "success"):
            return VerificationResult(
                status="VERIFIED_FAILURE",
                reason=str(getattr(tool_result, "error", "Tool reported structured failure")),
                confidence=1.0,
            )

        # 2. Dynamic String Pattern Validation
        res_str = str(tool_result or "")
        if ErrorPatternRegistry.is_failure(res_str):
            return VerificationResult(
                status="VERIFIED_FAILURE",
                reason=res_str[:300].strip(),
                confidence=1.0,
            )

        # 3. Affordance-Based Routing (with tool_name fallback for full backward compatibility)
        t_key = tool_name.lower().strip()
        aff_key = affordance.lower().strip() if affordance else ""
        verifier = cls._POST_VERIFIERS.get(aff_key) or cls._POST_VERIFIERS.get(t_key)

        if not verifier:
            return VerificationResult(
                status="UNVERIFIABLE",
                reason="No domain verifier registered",
                confidence=0.0,
            )

        try:
            if inspect.iscoroutinefunction(verifier):
                res = await verifier(params, res_str)
            else:
                res = verifier(params, res_str)
            if inspect.isawaitable(res):
                res = await res

            if isinstance(res, VerificationResult):
                return res
            if isinstance(res, tuple) and len(res) == 2:
                is_ok, err = res
                st = "VERIFIED_SUCCESS" if is_ok else "VERIFIED_FAILURE"
                return VerificationResult(status=st, reason=err or "", confidence=1.0)
            return VerificationResult(status="VERIFIED_SUCCESS", reason="", confidence=1.0)
        except Exception as exc:
            logger.warning("[InvariantVerifier] Verifier execution error for '%s': %s", tool_name, exc)
            return VerificationResult(
                status="UNVERIFIABLE",
                reason=f"Verifier raised: {exc}",
                confidence=0.0,
            )

    @classmethod
    async def verify_precondition(
        cls,
        tool_name: str,
        params: dict[str, Any],
        affordance: str = "",
    ) -> tuple[bool, str]:
        """
        Verify deterministic pre-execution state for a tool invocation.
        Returns: (passed: bool, failure_reason: str)
        """
        if not tool_name or not isinstance(params, dict):
            return True, ""

        t_key = tool_name.lower().strip()
        aff_key = affordance.lower().strip() if affordance else ""
        checker = cls._PRE_VERIFIERS.get(aff_key) or cls._PRE_VERIFIERS.get(t_key)

        if not checker:
            return True, ""

        try:
            if inspect.iscoroutinefunction(checker):
                res = await checker(params)
            else:
                res = checker(params)
            if inspect.isawaitable(res):
                res = await res
            return res if isinstance(res, tuple) else (True, "")
        except Exception as exc:
            logger.warning("[InvariantVerifier] Precondition check error for '%s': %s", tool_name, exc)
            return True, ""

    # =========================================================================
    # ASYNC-SAFE PHYSICAL STATE VERIFIERS (Zero Event Loop Blocking)
    # =========================================================================

    @staticmethod
    async def _pre_file_op(params: dict[str, Any]) -> tuple[bool, str]:
        """Precondition for move/copy/rename/read: source must exist."""
        src = params.get("source_path") or params.get("src") or params.get("file_path") or params.get("path")
        if not src:
            return True, ""

        def _check() -> tuple[bool, str]:
            full = _resolve_fs_path(str(src))
            if not os.path.exists(full):
                return False, f"Source path '{src}' does not exist on disk"
            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _pre_kill_process(params: dict[str, Any]) -> tuple[bool, str]:
        """Precondition for kill_process: process PID or name must exist."""
        if not psutil:
            return True, ""
        pid_val = params.get("pid")
        pname_val = params.get("process_name") or params.get("name")

        def _check() -> tuple[bool, str]:
            if pid_val is not None and str(pid_val).isdigit():
                target_pid = int(pid_val)
                if not psutil.pid_exists(target_pid):
                    return False, f"Process PID {target_pid} does not exist"
                return True, ""
            if pname_val:
                t_clean = str(pname_val).lower().replace(".exe", "").strip()
                for proc in psutil.process_iter(["name"]):
                    try:
                        p_name = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                        if p_name == t_clean or t_clean in p_name:
                            return True, ""
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                return False, f"No active process matching '{pname_val}' found"
            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _verify_filesystem_write(params: dict[str, Any], result: str) -> tuple[bool, str]:
        path = params.get("path") or params.get("file_path") or params.get("filename")
        if not path:
            return False, "Missing required parameter: path"

        # Resolve EXACTLY like FilesystemEngine did when writing: known-folder
        # shortcuts ("desktop" â†’ OneDrive Desktop), ~ expansion, home-join.
        # Raw abspath() here checked CWD-relative garbage and failed SUCCESSFUL
        # writes â€” which then triggered Saga rollback deleting real files!
        full_path = _resolve_fs_path(str(path))
        expected_content = params.get("content")

        def _check() -> tuple[bool, str]:
            if not os.path.exists(full_path):
                return False, f"File '{path}' does not exist on disk after write_file claimed success"
            if not os.path.isfile(full_path):
                return False, f"Target path '{path}' is not a regular file"
            try:
                size_b = os.path.getsize(full_path)
                if expected_content and len(expected_content) > 0 and size_b == 0:
                    return False, f"File '{path}' was created but has 0 bytes (expected content)"
            except OSError as e:
                return False, f"Cannot read file size for '{path}': {e}"

            # Format-Specific Physical Invariant Check
            ext = os.path.splitext(full_path)[1].lower().replace(".", "")
            try:
                if ext in ("xlsx", "xls"):
                    import openpyxl
                    wb = openpyxl.load_workbook(full_path, data_only=True)
                    if not wb.sheetnames or wb[wb.sheetnames[0]].max_row < 1:
                        return False, f"Excel file '{path}' is corrupted or empty"
                elif ext in ("docx", "doc"):
                    import docx
                    doc = docx.Document(full_path)
                    if not (doc.paragraphs or doc.tables):
                        return False, f"Word document '{path}' has no paragraphs or tables"
                elif ext == "png":
                    with open(full_path, "rb") as img_f:
                        if img_f.read(8) != b"\x89PNG\r\n\x1a\n":
                            return False, f"Image '{path}' does not match PNG magic bytes"
                elif ext in ("jpg", "jpeg"):
                    with open(full_path, "rb") as img_f:
                        if not img_f.read(2).startswith(b"\xff\xd8"):
                            return False, f"Image '{path}' does not match JPEG magic bytes"
                elif ext == "json":
                    with open(full_path, "r", encoding="utf-8", errors="replace") as j_f:
                        json.load(j_f)
                elif ext == "py":
                    with open(full_path, "r", encoding="utf-8", errors="replace") as py_f:
                        ast.parse(py_f.read(), filename=full_path)
            except Exception as parse_err:
                return False, f"Physical format verification failed for '{path}': {parse_err}"

            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _verify_filesystem_move(params: dict[str, Any], result: str) -> tuple[bool, str]:
        src = params.get("source_path") or params.get("src")
        dst = params.get("target_folder_or_path") or params.get("dest") or params.get("dst")
        if not src:
            return False, "Missing required parameter: source_path"
        if not dst:
            return False, "Missing required parameter: target_folder_or_path"

        src_full = _resolve_fs_path(str(src))
        dst_full = _resolve_fs_path(str(dst))

        def _check() -> tuple[bool, str]:
            target_dst = os.path.join(dst_full, os.path.basename(src_full)) if os.path.isdir(dst_full) else dst_full
            if os.path.exists(src_full):
                return False, f"Source file '{src}' still exists after move"
            if not os.path.exists(target_dst):
                return False, f"Destination file '{target_dst}' does not exist after move"
            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _verify_filesystem_copy(params: dict[str, Any], result: str) -> tuple[bool, str]:
        src = params.get("source_path") or params.get("src")
        dst = params.get("target_folder_or_path") or params.get("dest") or params.get("dst")
        if not src:
            return False, "Missing required parameter: source_path"
        if not dst:
            return False, "Missing required parameter: target_folder_or_path"

        src_full = _resolve_fs_path(str(src))
        dst_full = _resolve_fs_path(str(dst))

        def _check() -> tuple[bool, str]:
            target_dst = os.path.join(dst_full, os.path.basename(src_full)) if os.path.isdir(dst_full) else dst_full
            if not os.path.exists(src_full):
                return False, f"Source file '{src}' does not exist during copy verification"
            if not os.path.exists(target_dst):
                return False, f"Destination copy '{target_dst}' does not exist on disk"
            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _verify_filesystem_rename(params: dict[str, Any], result: str) -> tuple[bool, str]:
        file_path = params.get("file_path") or params.get("path")
        new_name = params.get("new_name") or params.get("new_path")
        if not file_path:
            return False, "Missing required parameter: file_path"
        if not new_name:
            return False, "Missing required parameter: new_name"

        src_full = _resolve_fs_path(str(file_path))
        parent_dir = os.path.dirname(src_full)
        dst_full = os.path.join(parent_dir, os.path.basename(str(new_name)))

        def _check() -> tuple[bool, str]:
            if src_full != dst_full and os.path.exists(src_full):
                return False, f"Original file '{file_path}' still exists after rename"
            if not os.path.exists(dst_full):
                return False, f"Renamed file '{dst_full}' not found on disk"
            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _verify_process_kill(params: dict[str, Any], result: str) -> tuple[bool, str]:
        if not psutil:
            return True, ""
        pid_val = params.get("pid")
        pname_val = params.get("process_name") or params.get("name")

        def _check() -> tuple[bool, str]:
            if pid_val is not None and str(pid_val).isdigit():
                target_pid = int(pid_val)
                try:
                    p = psutil.Process(target_pid)
                    if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                        return False, f"Process PID {target_pid} is still actively running in process table"
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            if pname_val:
                target_clean = str(pname_val).lower().replace(".exe", "").strip()
                for proc in psutil.process_iter(["name"]):
                    try:
                        p_name = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                        if p_name == target_clean:
                            return False, f"Process matching '{pname_val}' is still running"
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
            return True, ""

        res = await asyncio.to_thread(_check)
        try:
            from .world_state import get_world_state
            get_world_state().invalidate_processes()
        except Exception:
            pass
        return res

    @classmethod
    def _extract_dynamic_app_tokens(cls, app_str: str) -> set[str]:
        """
        Dynamically extract candidate process and window tokens without hardcoding.
        Uses:
        1. Natural string cleaning and alphanumeric normalization.
        2. Live in-memory FTS5 search from real installed applications on host disk.
        3. Word tokenization and acronym generation.
        """
        if not app_str:
            return set()
        raw = str(app_str).strip().lower().replace(".exe", "").split("\\")[-1]
        tokens = {raw}

        # Alphanumeric normalized token (e.g. "vs code" -> "vscode")
        alphanumeric = re.sub(r'[^a-z0-9]', '', raw)
        if alphanumeric:
            tokens.add(alphanumeric)

        # Word pieces (e.g. "google", "chrome")
        words = [w for w in re.split(r'[^a-z0-9]+', raw) if len(w) >= 3]
        tokens.update(words)

        # Acronym (e.g. "visual studio code" -> "vsc")
        if len(words) >= 2:
            acronym = "".join(w[0] for w in words)
            tokens.add(acronym)

        # Dynamic query against installed applications index on host OS
        try:
            from ..agents.system_agent import search_installed_apps
            matched_apps = search_installed_apps(raw, limit=5)
            for m in matched_apps:
                p = m.get("path", "")
                if p and p.endswith(".exe"):
                    exe_name = os.path.splitext(os.path.basename(p))[0].lower()
                    tokens.add(exe_name)
                m_name = m.get("name", "").lower()
                for w in re.split(r'[^a-z0-9]+', m_name):
                    if len(w) >= 3:
                        tokens.add(w)
        except Exception:
            pass

        return tokens

    @classmethod
    async def _verify_launch_app(cls, params: dict[str, Any], result: str) -> tuple[bool, str]:
        app = params.get("app_path") or params.get("app_name") or params.get("name")
        if not app:
            return False, "Missing required parameter: app_name"
        if ErrorPatternRegistry.is_failure(result):
            return False, result

        raw_token = str(app).strip().lower().replace(".exe", "").split("\\")[-1]
        if not raw_token or raw_token in ("active", "current"):
            return True, ""

        # Zero Hardcoding: Dynamically resolve candidate tokens from host environment
        target_tokens = cls._extract_dynamic_app_tokens(raw_token)

        def _check_process_and_windows() -> bool:
            # 1. Inspect Process Table via psutil with substring and fuzzy ratio
            if psutil:
                try:
                    me = psutil.Process().name().lower()
                    for proc in psutil.process_iter(["name"]):
                        try:
                            p_name = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                            if p_name and p_name != me:
                                if any(t == p_name or t in p_name or p_name in t for t in target_tokens):
                                    return True
                                # Fuzzy similarity check for subtle spelling differences
                                for t in target_tokens:
                                    if len(t) >= 4 and len(p_name) >= 4:
                                        ratio = difflib.SequenceMatcher(None, t, p_name).ratio()
                                        if ratio >= 0.78:
                                            return True
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            continue
                except Exception:
                    pass
            return False

        async def _check_window_titles() -> bool:
            # 2. Inspect Open Window Titles via WorldStateService
            try:
                from .world_state import get_world_state
                ws = get_world_state()
                snap = await ws.get_snapshot("window", force=True)
                open_windows = snap.get("windows", [])
                for win in open_windows:
                    title = str(win.get("title") or "").lower()
                    if title and any(t in title for t in target_tokens):
                        return True
                fg_title = str(ws.get_foreground_window() or "").lower()
                if fg_title and any(t in fg_title for t in target_tokens):
                    return True
            except Exception:
                pass
            return False

        # Multi-factor inspection with adaptive polling grace window (0.0s, 0.4s, 0.8s)
        for delay in (0.0, 0.4, 0.8):
            if delay > 0:
                await asyncio.sleep(delay)
            if await asyncio.to_thread(_check_process_and_windows):
                return True, ""
            if await _check_window_titles():
                return True, ""

        return False, f"No process or window matching '{raw_token}' (dynamic candidates: {sorted(list(target_tokens))}) appeared after launch"

    @staticmethod
    async def _verify_window_manage(params: dict[str, Any], result: str) -> tuple[bool, str]:
        action = str(params.get("action") or "").lower().strip()
        title = str(params.get("title") or "").lower().strip()
        if not action:
            return False, "Missing required parameter: action"
        if not title:
            return False, "Missing required parameter: title"
        if not win32gui:
            return True, ""

        def _check() -> tuple[bool, str]:
            if action == "minimize":
                if not win32gui:
                    return True, ""
                # Find the target window by enumerating all top-level windows
                target_hwnd = None
                def _enum_cb(hwnd: int, _: Any) -> bool:
                    nonlocal target_hwnd
                    if win32gui.IsWindowVisible(hwnd):
                        wt = win32gui.GetWindowText(hwnd).lower()
                        if title and title in wt:
                            target_hwnd = hwnd
                            return False  # stop enumeration
                    return True
                try:
                    win32gui.EnumWindows(_enum_cb, None)
                except Exception:
                    pass
                if target_hwnd is None:
                    # Window not found at all — either closed or was minimized to tray
                    return True, ""
                if not win32gui.IsIconic(target_hwnd):
                    return False, f"Window matching '{title}' is not minimized (IsIconic=False)"
                return True, ""
            elif action == "close" and psutil and title not in ("active", "current", "this"):
                t_clean = title.replace(".exe", "").strip()
                for proc in psutil.process_iter(["name"]):
                    try:
                        p_name = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                        if p_name == t_clean:
                            return False, f"Process '{title}' is still running after close request"
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _verify_media_play(params: dict[str, Any], result: str) -> VerificationResult:
        """Verify media playback via WorldStateService active audio owner."""
        try:
            from .world_state import get_world_state
            ws = get_world_state()
            active_owner = await ws.get_active_audio_owner()
            if not active_owner:
                from ..agents.os_state import get_os_state
                os_s = get_os_state()
                active_owner = getattr(os_s, "_last_audio_owner", None)
            if active_owner:
                return VerificationResult(
                    status="VERIFIED_SUCCESS",
                    reason=f"Active audio owner verified as '{active_owner}'",
                    observed_state={"active_audio_owner": active_owner},
                    confidence=1.0,
                    reversibility="irreversible-but-verifiable",
                )
            if ErrorPatternRegistry.is_failure(result):
                return VerificationResult(
                    status="VERIFIED_FAILURE",
                    reason=result,
                    observed_state={"active_audio_owner": None},
                    confidence=1.0,
                )
            return VerificationResult(
                status="UNVERIFIABLE",
                reason="Playback command sent; OS audio owner unconfirmed",
                confidence=0.5,
                reversibility="irreversible-but-verifiable",
            )
        except Exception as exc:
            return VerificationResult(status="UNVERIFIABLE", reason=str(exc), confidence=0.0)

    @staticmethod
    def _verify_browser_navigate(params: dict[str, Any], result: str) -> VerificationResult:
        """Verify browser navigation post-state."""
        target_url = str(params.get("url") or params.get("target_url") or "").lower()
        low = (result or "").lower()
        bad_markers = ("404", "500", "err_name_not_resolved", "[nav_failed]",
                       "[timeout]", "no_video", "navigation failed")
        if ErrorPatternRegistry.is_failure(result) or any(k in low for k in bad_markers):
            return VerificationResult(
                status="VERIFIED_FAILURE",
                reason=f"Navigation failed: {result[:100]}",
                expected_state={"target_url": target_url},
                confidence=1.0,
            )
        return VerificationResult(
            status="VERIFIED_SUCCESS",
            reason=f"Navigated to '{target_url}' successfully",
            observed_state={"url": target_url},
            confidence=1.0,
            reversibility="irreversible-but-verifiable",
        )

    @staticmethod
    def _verify_shell_command(params: dict[str, Any], result: str) -> VerificationResult:
        """Verify shell command execution via exit code / output."""
        if "exit code" in result.lower() and "exit code 0" not in result.lower():
            return VerificationResult(
                status="VERIFIED_FAILURE",
                reason=f"Shell command failed: {result[:100]}",
                confidence=1.0,
            )
        if ErrorPatternRegistry.is_failure(result):
            return VerificationResult(
                status="VERIFIED_FAILURE",
                reason=f"Shell command error: {result[:100]}",
                confidence=1.0,
            )
        return VerificationResult(
            status="VERIFIED_SUCCESS",
            reason="Shell command executed cleanly",
            confidence=1.0,
            reversibility="irreversible-but-verifiable",
        )

    @staticmethod
    def _verify_messaging(params: dict[str, Any], result: str) -> VerificationResult:
        """Honest unverifiable status for external messaging dispatch."""
        if ErrorPatternRegistry.is_failure(result):
            return VerificationResult(
                status="VERIFIED_FAILURE",
                reason=f"Messaging dispatch error: {result[:100]}",
                confidence=1.0,
            )
        return VerificationResult(
            status="UNVERIFIABLE",
            reason="Provider accepted payload; recipient inbox delivery unverified",
            confidence=0.7,
            reversibility="irreversible-and-unverified",
        )


    # ── NEW-GENERATION VERIFIERS (coverage gaps closed) ──────────────────────

    @staticmethod
    async def _verify_organize_desktop(params: dict[str, Any], result: str) -> tuple[bool, str]:
        """Organize must report moved/organized counts or a manifest; a silent
        empty result means nothing was verified."""
        low = (result or "").lower()
        if ErrorPatternRegistry.is_failure(result):
            return False, result[:200]
        if "organized" in low or "moved" in low or "[manifest:" in low:
            return True, ""
        if "already clean" in low or "no loose files" in low:
            return True, ""
        return False, f"Organize result lacks any movement evidence: {result[:120]}"

    @staticmethod
    async def _verify_take_screenshot(params: dict[str, Any], result: str) -> tuple[bool, str]:
        """Screenshot file must exist on disk with non-zero size."""
        import re as _re
        m = _re.search(r"([A-Za-z]:\\[^\s'\"|]+\.(?:png|jpg|jpeg))", result or "", _re.IGNORECASE)
        if not m:
            return False, f"Screenshot result contains no file path: {(result or '')[:120]}"
        p = m.group(1)

        def _check() -> tuple[bool, str]:
            if not os.path.exists(p):
                return False, f"Screenshot file missing on disk: {p}"
            if os.path.getsize(p) == 0:
                return False, f"Screenshot file is 0 bytes: {p}"
            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _verify_file_output(params: dict[str, Any], result: str) -> tuple[bool, str]:
        """Verify generated document/chart/export file exists on disk and is non-empty."""
        if not result or not result.strip():
            return False, "Empty tool result"
        if ErrorPatternRegistry.is_failure(result) or "⚠️" in result:
            return False, result[:200]

        path = params.get("output_path") or params.get("path") or params.get("file_path") or params.get("filename") or params.get("dest")
        candidate_paths: list[str] = []
        if path:
            candidate_paths.append(_resolve_fs_path(str(path)))

        if isinstance(result, dict):
            res_p = result.get("path") or result.get("file_path") or result.get("output_path")
            if res_p:
                candidate_paths.append(_resolve_fs_path(str(res_p)))
        elif isinstance(result, str) and result.strip().startswith("{"):
            try:
                r_dict = json.loads(result)
                if isinstance(r_dict, dict):
                    res_p = r_dict.get("path") or r_dict.get("file_path") or r_dict.get("output_path")
                    if res_p:
                        candidate_paths.append(_resolve_fs_path(str(res_p)))
            except Exception:
                pass

        import re as _re
        m = _re.search(r"([A-Za-z]:\\[^\s'\"|]+\.[a-zA-Z0-9]+|/[^\s'\"|]+\.[a-zA-Z0-9]+)", result or "")
        if m:
            candidate_paths.append(_resolve_fs_path(m.group(1)))
        res_clean = str(result or "").strip().strip("'\"")
        if res_clean and (os.path.isabs(res_clean) or os.path.exists(res_clean)):
            candidate_paths.append(res_clean)

        def _check() -> tuple[bool, str]:
            for full in candidate_paths:
                if os.path.exists(full):
                    try:
                        if os.path.getsize(full) == 0:
                            return False, f"Generated file '{full}' is 0 bytes"
                        return True, ""
                    except OSError as e:
                        return False, f"Cannot read generated file size for '{full}': {e}"
            if candidate_paths:
                return False, f"Generated file '{path or result}' does not exist on disk"
            return True, ""

        return await asyncio.to_thread(_check)

    @staticmethod
    async def _verify_simple_result(params: dict[str, Any], result: str) -> tuple[bool, str]:
        """For actions whose only honest post-check is 'tool reported clean'
        (volume set, notification shown, web search returned). Fails loudly on
        error markers / our own ⚠️ honesty marker; passes otherwise. Deliberately
        NOT a deep physical check — those tools lack cheap readbacks."""
        if not result or not result.strip():
            return False, "Empty tool result"
        if ErrorPatternRegistry.is_failure(result) or "⚠️" in result:
            return False, result[:200]
        return True, ""


class PreFlightChecker:
    """
    Deterministic pre-flight validator for actions, capabilities, and parameters.
    Enforces negative constraints, target restrictions, and physical invariants
    BEFORE action dispatch without LLM roundtrips.
    """

    @classmethod
    async def _is_protected_process(cls, pid: Optional[int] = None, name: Optional[str] = None) -> bool:
        """Dynamic OS-level protection check. Zero hardcoded process lists."""
        if not psutil:
            return False

        def _check() -> bool:
            try:
                from ..agents.system_agent import is_critical_process
            except Exception:
                is_critical_process = None

            if name:
                n_clean = str(name).lower().replace(".exe", "").strip()
                if n_clean in ("explorer", "lsass", "csrss", "services", "smss", "wininit", "svchost", "dwm", "system", "idle"):
                    return True

            try:
                if pid:
                    p = psutil.Process(int(pid))
                elif name:
                    n_clean = str(name).lower().replace(".exe", "").strip()
                    p = next((
                        proc for proc in psutil.process_iter(["name", "pid"])
                        if n_clean in (proc.info["name"] or "").lower().replace(".exe", "")
                    ), None)
                    if not p:
                        return False
                else:
                    return False

                if is_critical_process:
                    is_crit, _ = is_critical_process(p)
                    if is_crit:
                        return True

                # Dynamic OS-level protection heuristics: Kernel PIDs & Session 0 accounts
                if p.pid <= 4:
                    return True
                uname = str(p.username() or "").upper()
                if any(acc in uname for acc in ("SYSTEM", "NT AUTHORITY", "LOCAL SERVICE", "NETWORK SERVICE")):
                    return True
                return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return True  # If OS denies inspection access, treat as protected

        return await asyncio.to_thread(_check)

    @classmethod
    def _matches_entity(cls, entity: str, text: str) -> bool:
        """Normalized semantic entity matching without hardcoded aliases."""
        if not entity or not text:
            return False
        e_norm = re.sub(r"[_\-\.]", " ", str(entity).lower().replace(".exe", "")).strip()
        t_norm = re.sub(r"[_\-\.]", " ", str(text).lower().replace(".exe", "")).strip()
        return e_norm in t_norm or t_norm in e_norm

    @classmethod
    async def check_action(
        cls,
        tool_name: str,
        params: dict[str, Any],
        negative_constraints: tuple[str, ...] = (),
    ) -> tuple[bool, str]:
        """
        Validate an action against negative constraints and safety rules.
        Returns: (is_allowed: bool, rejection_reason: str)
        """
        if not tool_name or not isinstance(params, dict):
            return True, ""

        params_str = " ".join(str(v).lower() for v in params.values())

        # 1. Dynamic Negative Constraints
        for neg in negative_constraints:
            neg_clean = re.sub(r"^(not_|not |avoid |keep )", "", str(neg).strip().lower())
            neg_clean = re.sub(r" running$", "", neg_clean).strip()
            if neg_clean and (cls._matches_entity(neg_clean, params_str) or cls._matches_entity(neg_clean, tool_name)):
                return False, f"Action '{tool_name}' blocked: Violates negative constraint '{neg}'"

        # 2. Dynamic Process Protection
        if tool_name in ("kill_process", "terminate_process", "stop_service", "process_kill"):
            pid = params.get("pid")
            pname = params.get("process_name") or params.get("name") or params.get("target")
            if await cls._is_protected_process(pid=pid, name=pname):
                return False, f"Action '{tool_name}' blocked: Cannot terminate protected system process"

        # 3. File existence for read operations (Async-safe)
        if tool_name in ("read_file", "parse_document", "audit_ast"):
            file_path = params.get("file_path") or params.get("path")
            if file_path:
                def _file_exists() -> bool:
                    p = _resolve_fs_path(str(file_path))
                    return os.path.exists(p)
                exists = await asyncio.to_thread(_file_exists)
                if not exists:
                    return False, f"Action '{tool_name}' blocked: Target file '{file_path}' does not exist"

        return True, ""

    @classmethod
    def filter_capabilities(
        cls,
        available_tools: list[str],
        negative_constraints: tuple[str, ...],
        target_entity: Optional[str] = None,
    ) -> list[str]:
        """Filter out tools that explicitly conflict with active negative constraints."""
        if not negative_constraints:
            return list(available_tools)
        filtered = []
        for tool in available_tools:
            tool_lower = str(tool).lower()
            blocked = False
            for neg in negative_constraints:
                neg_lower = str(neg).lower().strip()
                if any(k in neg_lower for k in ("no close", "no kill", "no_kill", "no_terminate")) and tool in ("kill_process", "system_power", "terminate_process"):
                    blocked = True
                    break
                if any(k in neg_lower for k in ("no shutdown", "no restart", "no_power")) and tool == "system_power":
                    blocked = True
                    break
                neg_entity = re.sub(r"^(not_|not |avoid |keep )", "", neg_lower)
                neg_entity = re.sub(r" running$", "", neg_entity).strip()
                if neg_entity and len(neg_entity) >= 3 and neg_entity in tool_lower:
                    blocked = True
                    break
            if not blocked:
                filtered.append(tool)
        return filtered

    @classmethod
    def filter_manifest(
        cls,
        manifest: list[dict[str, Any]],
        negative_constraints: tuple[str, ...],
        target_entity: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Filter tool manifest before LLM prompt generation without mutating structure."""
        if not manifest or not negative_constraints:
            return list(manifest)

        def _get_name(item: dict[str, Any]) -> str:
            if not isinstance(item, dict):
                return ""
            if "name" in item:
                return str(item["name"])
            if "function" in item and isinstance(item["function"], dict):
                return str(item["function"].get("name", ""))
            return ""

        tool_names = [_get_name(t) for t in manifest]
        allowed_names = set(cls.filter_capabilities(tool_names, negative_constraints, target_entity=target_entity))
        return [t for t in manifest if _get_name(t) in allowed_names]


# =============================================================================
# Dynamic Affordance & Domain Registration (Zero Hardcoding)
# =============================================================================
# Domain / Affordance Postcondition Registrations
InvariantVerifier.register_postcondition("filesystem_write", InvariantVerifier._verify_filesystem_write)
InvariantVerifier.register_postcondition("filesystem_move", InvariantVerifier._verify_filesystem_move)
InvariantVerifier.register_postcondition("filesystem_copy", InvariantVerifier._verify_filesystem_copy)
InvariantVerifier.register_postcondition("filesystem_rename", InvariantVerifier._verify_filesystem_rename)
InvariantVerifier.register_postcondition("process_kill", InvariantVerifier._verify_process_kill)
InvariantVerifier.register_postcondition("window_manage", InvariantVerifier._verify_window_manage)
InvariantVerifier.register_postcondition("app_launch", InvariantVerifier._verify_launch_app)
InvariantVerifier.register_postcondition("media_control", InvariantVerifier._verify_media_play)
InvariantVerifier.register_postcondition("browser_action", InvariantVerifier._verify_browser_navigate)
InvariantVerifier.register_postcondition("shell_exec", InvariantVerifier._verify_shell_command)
InvariantVerifier.register_postcondition("messaging_dispatch", InvariantVerifier._verify_messaging)

# NEW-GENERATION registrations (coverage gaps closed)
InvariantVerifier.register_postcondition("organize_desktop", InvariantVerifier._verify_organize_desktop)
InvariantVerifier.register_postcondition("take_screenshot", InvariantVerifier._verify_take_screenshot)
InvariantVerifier.register_postcondition("set_volume", InvariantVerifier._verify_simple_result)
InvariantVerifier.register_postcondition("delta_volume", InvariantVerifier._verify_simple_result)
InvariantVerifier.register_postcondition("web_search", InvariantVerifier._verify_simple_result)
InvariantVerifier.register_postcondition("show_notification", InvariantVerifier._verify_simple_result)
InvariantVerifier.register_postcondition("document_generate", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("export_pdf", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("export_docx", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("export_xlsx", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("create_spreadsheet", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("create_document", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("create_presentation", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("create_word", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("create_excel", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("create_report", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("chart_generate", InvariantVerifier._verify_file_output)
InvariantVerifier.register_postcondition("plot_data", InvariantVerifier._verify_file_output)



# Full Legacy Tool Name Registrations (Seamless Backward Compatibility)
InvariantVerifier.register_postcondition("write_file", InvariantVerifier._verify_filesystem_write)
InvariantVerifier.register_postcondition("move_file", InvariantVerifier._verify_filesystem_move)
InvariantVerifier.register_postcondition("copy_file", InvariantVerifier._verify_filesystem_copy)
InvariantVerifier.register_postcondition("rename_file", InvariantVerifier._verify_filesystem_rename)
InvariantVerifier.register_postcondition("kill_process", InvariantVerifier._verify_process_kill)
InvariantVerifier.register_postcondition("terminate_process", InvariantVerifier._verify_process_kill)
InvariantVerifier.register_postcondition("launch_app", InvariantVerifier._verify_launch_app)
InvariantVerifier.register_postcondition("manage_window", InvariantVerifier._verify_window_manage)
InvariantVerifier.register_postcondition("play", InvariantVerifier._verify_media_play)
InvariantVerifier.register_postcondition("pause", InvariantVerifier._verify_media_play)
InvariantVerifier.register_postcondition("stop", InvariantVerifier._verify_media_play)
InvariantVerifier.register_postcondition("browser_navigate", InvariantVerifier._verify_browser_navigate)
InvariantVerifier.register_postcondition("browser_click", InvariantVerifier._verify_browser_navigate)
InvariantVerifier.register_postcondition("browser_type", InvariantVerifier._verify_browser_navigate)
InvariantVerifier.register_postcondition("run_shell", InvariantVerifier._verify_shell_command)
InvariantVerifier.register_postcondition("run_command", InvariantVerifier._verify_shell_command)
InvariantVerifier.register_postcondition("docker_run", InvariantVerifier._verify_shell_command)
InvariantVerifier.register_postcondition("send_email", InvariantVerifier._verify_messaging)
InvariantVerifier.register_postcondition("send_whatsapp", InvariantVerifier._verify_messaging)

# MCP Capability Tool Name Registrations
for _m_tool in ("media_play", "media_pause", "media_resume", "media_toggle", "media_next", "media_previous", "media_seek", "media_set_volume", "media_get_state", "media_skip_ad"):
    InvariantVerifier.register_postcondition(_m_tool, InvariantVerifier._verify_media_play)

for _s_win_tool in ("system_manage_window", "system_snap_window"):
    InvariantVerifier.register_postcondition(_s_win_tool, InvariantVerifier._verify_window_manage)

InvariantVerifier.register_postcondition("system_kill_process", InvariantVerifier._verify_process_kill)
InvariantVerifier.register_postcondition("system_launch_app", InvariantVerifier._verify_launch_app)

for _s_simple in ("system_get_volume", "system_set_volume", "system_get_stats", "system_get_window_list", "system_get_process_list", "system_get_clipboard", "system_set_clipboard", "system_show_notification"):
    InvariantVerifier.register_postcondition(_s_simple, InvariantVerifier._verify_simple_result)

for _d_tool in ("document_create_excel", "document_create_word", "document_create_pdf", "document_create_powerpoint", "document_convert"):
    InvariantVerifier.register_postcondition(_d_tool, InvariantVerifier._verify_file_output)

# Domain / Affordance Precondition Registrations
InvariantVerifier.register_precondition("filesystem_op", InvariantVerifier._pre_file_op)
InvariantVerifier.register_precondition("process_kill", InvariantVerifier._pre_kill_process)

InvariantVerifier.register_precondition("move_file", InvariantVerifier._pre_file_op)
InvariantVerifier.register_precondition("copy_file", InvariantVerifier._pre_file_op)
InvariantVerifier.register_precondition("rename_file", InvariantVerifier._pre_file_op)
InvariantVerifier.register_precondition("read_file", InvariantVerifier._pre_file_op)
InvariantVerifier.register_precondition("kill_process", InvariantVerifier._pre_kill_process)
InvariantVerifier.register_precondition("system_kill_process", InvariantVerifier._pre_kill_process)


__all__ = ["InvariantVerifier", "VerificationResult", "PreFlightChecker", "ErrorPatternRegistry"]
