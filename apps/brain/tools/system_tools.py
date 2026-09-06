"""
Makima v8.0 — System Capability Module (Standalone Tools)
Location: apps/brain/tools/system_tools.py

Zero-agent dependency standalone tool implementation for host OS management:
  - Window control & snapping (Win32 API)
  - Process management & scheduling priority (psutil / Win32 security tokens)
  - System master volume (Windows CoreAudio COM interface via PowerShell)
  - Clipboard read/write (win32clipboard / pyperclip)
  - Toast notifications (WinRT XML Toast Notifications)
  - System hardware & OS statistics (psutil)
  - Application discovery (in-memory SQLite FTS5 index) & verified launching
  - Temporary storage cleanup (dry-run & safe deletion)
  - Desktop file organization (dry-run & categorisation)
  - System power states (sleep, hibernate, restart, shutdown)
  - Network diagnostics & adapter discovery
  - Desktop screenshot & mouse interaction
  - Filesystem file operations with shadow snapshots

HARD RULE (Phase 1): No imports from apps.brain.agents.
"""
from __future__ import annotations

import asyncio
import ctypes
import difflib
import fnmatch
import glob
import json
import logging
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

logger = logging.getLogger("makima.tools.system")

# ==============================================================================
# OPTIONAL RESILIENCE IMPORTS
# ==============================================================================
try:
    import psutil
except ImportError:
    psutil = None
    logger.warning("psutil not found. Hardware/Process tools will operate in fallback mode.")

try:
    import win32gui
    import win32con
    import win32process
    import win32api
    import win32security
    import win32clipboard
    _HAS_WIN32 = True
except ImportError:
    win32gui = win32con = win32process = win32api = win32security = win32clipboard = None
    _HAS_WIN32 = False
    logger.warning("pywin32 not found. Advanced Win32 window and security features disabled.")

try:
    import pyperclip
    _HAS_PYPERCLIP = True
except ImportError:
    pyperclip = None
    _HAS_PYPERCLIP = False

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except ImportError:
    pyautogui = None
    _HAS_PYAUTOGUI = False

# ==============================================================================
# CONSTANTS & CACHES
# ==============================================================================
APPS_CACHE_TTL_SECONDS = 3600.0
REGISTRY_MAX_SCAN_LIMIT = 1500
FUZZY_MATCH_THRESHOLD = 0.70
MAX_APPS_SEARCH_LIMIT = 500
SCREENSHOT_FALLBACK_COLOR = (24, 24, 27)

_APPS_CACHE: list[dict[str, str]] = []
_APPS_CACHE_EXPIRES: float = 0.0
_APPS_FTS_CONN: Optional[sqlite3.Connection] = None
_APPS_FTS_LOCK = threading.Lock()

_DESKTOP_CATEGORIES: dict[str, list[str]] = {
    "Documents":   [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx", ".csv", ".epub", ".odt"],
    "Images":      [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico"],
    "Videos":      [".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv"],
    "Audio":       [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"],
    "Archives":    [".zip", ".rar", ".7z", ".tar", ".gz"],
    "Executables": [".exe", ".msi", ".bat", ".cmd"],
    "Code":        [".py", ".js", ".ts", ".html", ".css", ".json", ".cpp", ".c", ".rs", ".java"],
    "Shortcuts":   [".lnk", ".url"],
}
_CATEGORY_DIRS = set(_DESKTOP_CATEGORIES.keys()) | {"Others"}


# ==============================================================================
# CORE AUDIO SCRIPT (C# P/Invoke for CoreAudio COM Interface)
# ==============================================================================
CORE_AUDIO_CS = r"""
using System;
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
    int R1(); int R2(); int R3();
    int SetMasterVolumeLevel(float fLevelDB, IntPtr pguidEventContext);
    int SetMasterVolumeLevelScalar(float fLevel, IntPtr pguidEventContext);
    int GetMasterVolumeLevel(out float pfLevelDB);
    int GetMasterVolumeLevelScalar(out float pfLevel);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid id, uint clsCtx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object obj); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int R1(); int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice dev); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject {}
public class Audio {
    private static IAudioEndpointVolume GetCtrl() {
        var e = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject()); IMMDevice d;
        e.GetDefaultAudioEndpoint(0, 1, out d); Guid g = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
        object o; d.Activate(ref g, 23, IntPtr.Zero, out o); return (IAudioEndpointVolume)o;
    }
    public static void SetVol(float l) { GetCtrl().SetMasterVolumeLevelScalar(l, IntPtr.Zero); }
    public static float GetVol() { float f; GetCtrl().GetMasterVolumeLevelScalar(out f); return f; }
}
"""


# ==============================================================================
# IN-MEMORY FTS5 APPLICATION DISCOVERY
# ==============================================================================
def _ensure_apps_fts_index(force_refresh: bool = False) -> None:
    """Pre-compute OS applications index into an in-memory SQLite FTS5 database."""
    global _APPS_CACHE, _APPS_CACHE_EXPIRES, _APPS_FTS_CONN
    with _APPS_FTS_LOCK:
        now = time.time()
        if _APPS_FTS_CONN is not None and now < _APPS_CACHE_EXPIRES and not force_refresh:
            return

        results: list[dict[str, str]] = []
        seen: set[str] = set()

        def add_result(name: str, path: str, source: str) -> None:
            if not path:
                return
            clean_path = path.strip().strip('"').strip("'")
            if clean_path.startswith("ms-") or (":" in clean_path and not os.path.splitdrive(clean_path)[0]):
                canonical = clean_path.lower()
            else:
                if not os.path.exists(clean_path):
                    return
                canonical = os.path.normpath(clean_path).lower()
            if canonical in seen:
                return
            seen.add(canonical)
            results.append({"name": name.strip(), "path": clean_path, "source": source})

        # 1. Start Menu Shortcuts (.lnk) - User & All Users & Desktop
        start_dirs = [
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%USERPROFILE%\Desktop"),
            os.path.expandvars(r"%PUBLIC%\Desktop"),
        ]
        for sdir in start_dirs:
            if os.path.isdir(sdir):
                for lnk in glob.glob(os.path.join(sdir, "**", "*.lnk"), recursive=True):
                    lnk_name = os.path.splitext(os.path.basename(lnk))[0]
                    add_result(lnk_name, lnk, "start_menu")

        # 2. WindowsApps (App Execution Aliases & Package Directories)
        win_apps_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps")
        if os.path.isdir(win_apps_dir):
            try:
                for entry in os.listdir(win_apps_dir):
                    full_entry = os.path.join(win_apps_dir, entry)
                    if entry.lower().endswith(".exe"):
                        name = os.path.splitext(entry)[0]
                        add_result(name, full_entry, "windows_apps")
                    elif os.path.isdir(full_entry):
                        clean_pkg_name = entry.split("_")[0].replace("Microsoft.", "")
                        for sub_exe in glob.glob(os.path.join(full_entry, "*.exe")):
                            add_result(f"{clean_pkg_name} ({os.path.basename(sub_exe)})", sub_exe, "windows_apps")
            except OSError:
                pass

        # 3. Windows Registry App Paths (HKLM & HKCU)
        try:
            import winreg
            for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(root_key, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", 0, winreg.KEY_READ) as parent:
                        num_subkeys, _, _ = winreg.QueryInfoKey(parent)
                        for i in range(num_subkeys):
                            try:
                                subkey_name = winreg.EnumKey(parent, i)
                                with winreg.OpenKey(parent, subkey_name, 0, winreg.KEY_READ) as k:
                                    val, _ = winreg.QueryValueEx(k, "")
                                    if val:
                                        name = os.path.splitext(subkey_name)[0]
                                        add_result(name, str(val), "registry_app_paths")
                            except OSError:
                                continue
                except OSError:
                    pass

            # 4. Windows Registry Uninstall Keys (Installed Software)
            uninstall_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            for root_key, upath in uninstall_paths:
                try:
                    with winreg.OpenKey(root_key, upath, 0, winreg.KEY_READ) as parent:
                        num_subkeys, _, _ = winreg.QueryInfoKey(parent)
                        for i in range(num_subkeys):
                            try:
                                subkey_name = winreg.EnumKey(parent, i)
                                with winreg.OpenKey(parent, subkey_name, 0, winreg.KEY_READ) as k:
                                    try:
                                        disp_name, _ = winreg.QueryValueEx(k, "DisplayName")
                                    except OSError:
                                        disp_name = None
                                    try:
                                        disp_icon, _ = winreg.QueryValueEx(k, "DisplayIcon")
                                    except OSError:
                                        disp_icon = None
                                    try:
                                        inst_loc, _ = winreg.QueryValueEx(k, "InstallLocation")
                                    except OSError:
                                        inst_loc = None

                                    if disp_name:
                                        if disp_icon:
                                            icon_path = str(disp_icon).split(",")[0].strip('"')
                                            if icon_path.lower().endswith(".exe") and os.path.exists(icon_path):
                                                add_result(str(disp_name), icon_path, "installed_programs")
                                        if inst_loc and os.path.isdir(str(inst_loc)):
                                            for exe in glob.glob(os.path.join(str(inst_loc), "*.exe")):
                                                add_result(str(disp_name), exe, "installed_programs")
                            except OSError:
                                continue
                except OSError:
                    pass
        except ImportError:
            pass

        # 5. Windows Dynamic URI Protocols via Registry Discovery
        try:
            import winreg
            root_locations = [
                (winreg.HKEY_CURRENT_USER, r"Software\Classes"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Classes"),
            ]
            for root_hkey, classes_path in root_locations:
                try:
                    with winreg.OpenKey(root_hkey, classes_path, 0, winreg.KEY_READ) as classes_key:
                        num_subkeys, _, _ = winreg.QueryInfoKey(classes_key)
                        for i in range(min(num_subkeys, REGISTRY_MAX_SCAN_LIMIT)):
                            try:
                                scheme_name = winreg.EnumKey(classes_key, i)
                                if scheme_name.startswith(".") or scheme_name.startswith("{"):
                                    continue
                                with winreg.OpenKey(classes_key, scheme_name, 0, winreg.KEY_READ) as scheme_key:
                                    try:
                                        winreg.QueryValueEx(scheme_key, "URL Protocol")
                                        is_protocol = True
                                    except OSError:
                                        is_protocol = False

                                    if not is_protocol:
                                        continue

                                    display_name = ""
                                    try:
                                        disp, _ = winreg.QueryValueEx(scheme_key, "FriendlyTypeName")
                                        if disp and isinstance(disp, str):
                                            display_name = disp.split(",")[-1].strip("@").strip()
                                    except OSError:
                                        pass

                                    if not display_name:
                                        try:
                                            def_val, _ = winreg.QueryValueEx(scheme_key, "")
                                            if def_val and isinstance(def_val, str):
                                                display_name = def_val.replace("URL:", "").replace("Protocol", "").strip()
                                        except OSError:
                                            pass

                                    if not display_name:
                                        display_name = scheme_name.capitalize()

                                    add_result(f"Windows {display_name} (Protocol)", f"{scheme_name.lower()}:", "protocol")
                            except OSError:
                                continue
                except OSError:
                    pass
        except ImportError:
            pass

        # Build in-memory FTS5 SQLite table
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS apps_fts USING fts5(
                    name, path, source, tokenize='porter unicode61'
                )
            """)
            conn.executemany(
                "INSERT INTO apps_fts (name, path, source) VALUES (?, ?, ?)",
                [(r["name"], r["path"], r["source"]) for r in results]
            )
            conn.commit()
            _APPS_FTS_CONN = conn
        except Exception as _fts_err:
            logger.debug("FTS5 creation fallback: %s", _fts_err)
            _APPS_FTS_CONN = None

        _APPS_CACHE = results
        _APPS_CACHE_EXPIRES = time.time() + APPS_CACHE_TTL_SECONDS


# Background pre-warm
try:
    threading.Thread(target=_ensure_apps_fts_index, daemon=True).start()
except Exception:
    pass


def search_installed_apps(query: str = "", limit: int = 20, force_refresh: bool = False) -> list[dict[str, str]]:
    """Sub-millisecond application discovery via in-memory SQLite FTS5 index."""
    global _APPS_CACHE, _APPS_CACHE_EXPIRES, _APPS_FTS_CONN
    if _APPS_FTS_CONN is None or force_refresh or time.time() >= _APPS_CACHE_EXPIRES:
        _ensure_apps_fts_index(force_refresh=force_refresh)

    query_clean = (query or "").strip()
    if not query_clean:
        return _APPS_CACHE[:limit]

    # Query in-memory FTS5 index
    if _APPS_FTS_CONN is not None:
        try:
            fts_query = re.sub(r'["*^:]', '', query_clean)
            if fts_query:
                cursor = _APPS_FTS_CONN.cursor()
                cursor.execute(
                    "SELECT name, path, source FROM apps_fts WHERE name MATCH ? ORDER BY rank LIMIT ?",
                    (f"{fts_query}*", limit)
                )
                rows = cursor.fetchall()
                if rows:
                    return [{"name": r[0], "path": r[1], "source": r[2]} for r in rows]
        except Exception:
            pass

    # High-speed in-memory fallback filter
    query_lower = query_clean.lower()
    filtered = [
        item for item in _APPS_CACHE
        if query_lower in item["name"].lower() or query_lower in item["path"].lower()
    ]
    return filtered[:limit]


# ==============================================================================
# PROCESS & SECURITY UTILITIES
# ==============================================================================
def is_critical_process(proc_or_pid: int | Any) -> tuple[bool, str]:
    """
    Determine if a process is a critical system process dynamically using
    Win32 Process Security Tokens, Session 0 Isolation, and MIC Integrity Levels.
    """
    if not psutil:
        return False, "psutil not available"

    if isinstance(proc_or_pid, str):
        target_name = proc_or_pid.lower().replace(".exe", "").strip()
        protected = {"explorer", "lsass", "csrss", "services", "wininit", "svchost", "dwm", "system", "idle", "smss", "winlogon", "fontdrvhost", "sihost", "taskmgr"}
        if target_name in protected:
            return True, f"Core OS Protected Process ({target_name})"
        try:
            proc = psutil.Process(int(proc_or_pid))
        except (ValueError, TypeError):
            # Non-critical process name string
            return False, "Non-critical process target"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True, "Protected OS Kernel Process (Access Denied)"
    elif isinstance(proc_or_pid, psutil.Process):
        proc = proc_or_pid
    else:
        try:
            proc = psutil.Process(int(proc_or_pid))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True, "Protected OS Kernel Process (Access Denied)"
        except Exception:
            return False, "Invalid process identifier"

    pid = proc.pid

    # 1. Kernel / Idle PIDs & Core OS Process Names
    if pid <= 4:
        return True, "Core Windows Kernel Subsystem (PID <= 4)"

    try:
        p_name = (proc.name() or "").lower().replace(".exe", "").strip()
        if p_name in ("explorer", "lsass", "csrss", "services", "wininit", "svchost", "dwm", "system", "idle"):
            return True, f"Core OS Protected Process ({p_name})"
    except Exception:
        pass

    # 2. Dynamic Desktop Shell Process Identification
    try:
        if _HAS_WIN32:
            shell_hwnd = ctypes.windll.user32.GetShellWindow()
            if shell_hwnd and win32process:
                _, shell_pid = win32process.GetWindowThreadProcessId(shell_hwnd)
                if pid == shell_pid:
                    return True, "Interactive Desktop Shell Process"
    except Exception:
        pass

    # 3. Session 0 Isolation & Service Account Checks
    session_id = None
    try:
        session_id = proc.as_dict(attrs=["session_id"]).get("session_id")
        username = (proc.username() or "").upper()
        if session_id == 0 and any(sys_acc in username for sys_acc in ("NT AUTHORITY", "SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE")):
            return True, f"Session 0 System Service ({username})"
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return True, "Session 0 Protected System Service"
    except Exception:
        pass

    # 4. Win32 Token Integrity & SID Verification
    if _HAS_WIN32 and sys.platform == "win32":
        handle = None
        token = None
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            token = win32security.OpenProcessToken(handle, win32security.TOKEN_QUERY)

            user_sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            account_name, domain, _ = win32security.LookupAccountSid(None, user_sid)
            if account_name.upper() in ("SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE") and session_id == 0:
                return True, f"System Security Principal ({domain}\\{account_name})"

            integrity_info = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
            if integrity_info:
                integrity_sid = integrity_info[0]
                integrity_rid = win32security.GetSubAuthority(integrity_sid, win32security.GetSubAuthorityCount(integrity_sid) - 1)
                if integrity_rid >= 0x4000:
                    return True, f"System Integrity Level (RID: {hex(integrity_rid)})"
        except Exception:
            pass
        finally:
            if token is not None:
                try:
                    token.Close()
                except Exception:
                    pass
            if handle is not None:
                try:
                    handle.Close()
                except Exception:
                    pass

    return False, "User Space Application"


def _process_running(probe: str) -> bool:
    """Check if any running process matches probe dynamically."""
    if not psutil or not probe:
        return False
    probe_clean = probe.lower().replace(".exe", "").strip()
    target_names = {probe_clean, probe_clean + ".exe", f"{probe_clean}app", f"{probe_clean}app.exe"}
    try:
        discovered = search_installed_apps(probe_clean)
        for a in discovered:
            c_clean = a.get("name", "").lower()
            c_base = re.sub(r'^(windows|microsoft)\s+|\s*\(protocol\)', '', os.path.splitext(os.path.basename(c_clean))[0], flags=re.I).split("(")[0].strip()
            if c_base:
                target_names.add(c_base)
                target_names.add(c_base + ".exe")
                target_names.add(f"{c_base}app")
                target_names.add(f"{c_base}app.exe")
    except Exception:
        pass

    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower().replace(".exe", "")
            if name in target_names:
                return True
    except Exception:
        pass
    return False


def _count_processes(probe: str) -> int:
    """Count running processes whose name matches probe."""
    if not psutil or not probe:
        return 0
    probe_l = probe.lower().replace(".exe", "").strip()
    count = 0
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name in (probe_l, probe_l + ".exe") or probe_l in name:
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass
    return count


def _extract_app_fuzzy(query: str, extra_candidates: list[str] | None = None) -> tuple[Optional[str], float]:
    """Candidate-grounded fuzzy extraction of target application name."""
    if not query:
        return None, 0.0

    apps = search_installed_apps(limit=MAX_APPS_SEARCH_LIMIT)
    candidate_names = [a["name"] for a in apps]
    if extra_candidates:
        candidate_names.extend(extra_candidates)

    q_clean = query.lower().strip()
    q_words = [w for w in re.split(r'[\s,;:?!\.\-_/\\()]+', q_clean) if len(w) >= 2]
    if not q_words:
        return None, 0.0

    spans: list[str] = []
    n_tokens = len(q_words)
    for span_len in range(1, min(5, n_tokens + 1)):
        for i in range(n_tokens - span_len + 1):
            spans.append(" ".join(q_words[i : i + span_len]))

    best_match: Optional[str] = None
    best_score: float = 0.0

    for candidate in candidate_names:
        c_clean = candidate.lower()
        c_base = re.sub(r'^(windows|microsoft)\s+|\s*\(protocol\)', '', os.path.splitext(os.path.basename(c_clean))[0], flags=re.I).split("(")[0].strip()
        if not c_base:
            c_base = c_clean.split("(")[0].strip()
        c_subtokens = [t for t in re.split(r'[\s\-_]+', c_base) if len(t) >= 2]

        for span in spans:
            if span == c_base and len(span) >= 3:
                score = 0.99
                if score > best_score:
                    best_score, best_match = score, candidate
                continue

            for sub in c_subtokens:
                coverage = len(sub) / max(len(c_base), 1)
                if span == sub:
                    sim = 1.0
                elif len(span) >= 4 and len(sub) >= 4 and (span in sub or sub in span):
                    sim = min(len(span), len(sub)) / max(len(span), len(sub))
                elif len(span) >= 4 and len(sub) >= 4:
                    sim = difflib.SequenceMatcher(None, span, sub).ratio()
                else:
                    sim = 0.0

                if sim >= 0.72:
                    score = sim * (0.60 + 0.40 * coverage)
                    if score > best_score:
                        best_score, best_match = score, candidate

    if best_match and best_score >= FUZZY_MATCH_THRESHOLD:
        return best_match, best_score

    return None, 0.0


def _resolve_app_path(name: str) -> str | None:
    """Resolve an app name to a launchable executable path or protocol URI."""
    if not name:
        return None

    raw = name.strip()
    if os.path.isabs(raw) and os.path.exists(raw):
        return os.path.abspath(raw)

    if raw.startswith("ms-") or (":" in raw and not os.path.splitdrive(raw)[0]):
        return raw

    matches = search_installed_apps(raw, limit=5)
    if matches:
        return matches[0]["path"]

    fuzzy_name, score = _extract_app_fuzzy(raw)
    if fuzzy_name and score >= 0.70:
        fuzzy_matches = search_installed_apps(fuzzy_name, limit=1)
        if fuzzy_matches:
            return fuzzy_matches[0]["path"]

    found = shutil.which(raw) or shutil.which(raw + ".exe")
    if found and os.path.exists(found):
        return found

    return None


async def _wait_process_running(app_clean: str, raw_app: str, max_retries: int = 4, delay: float = 0.1) -> bool:
    """Non-blocking fast process verification."""
    for i in range(max_retries):
        try:
            if await asyncio.to_thread(_process_running, app_clean) or await asyncio.to_thread(_process_running, raw_app):
                return True
        except Exception:
            pass
        if i < max_retries - 1:
            await asyncio.sleep(delay)
    return False


async def launch_app_verified(app_path: str, arguments: list[str] | None = None) -> str:
    """Launch an application asynchronously with dynamic resolution and verified desktop state."""
    raw_app = (app_path or "").strip()
    if not raw_app:
        return "App name or path was not provided."

    app_clean = os.path.basename(raw_app).replace(".exe", "").strip().lower()
    resolved = _resolve_app_path(raw_app)

    # 1. Handle Protocol URIs
    if resolved and (resolved.startswith("ms-") or (":" in resolved and not os.path.splitdrive(resolved)[0])):
        if sys.platform == "win32" and hasattr(os, "startfile"):
            try:
                os.startfile(resolved)
                logger.info("[system] Launched application via protocol URI: %s", resolved)
                verified = await _wait_process_running(app_clean, raw_app, max_retries=4, delay=0.3)
                if verified:
                    return f"Launched {app_clean.capitalize()} successfully."
                return f"Launched {app_clean.capitalize()} via protocol URI (background execution)."
            except Exception as e:
                logger.warning("[system] Protocol launch failed for %s: %s", resolved, e)

    # 2. Handle Start Menu Shortcuts (.lnk)
    if resolved and resolved.lower().endswith(".lnk"):
        if sys.platform == "win32" and hasattr(os, "startfile"):
            try:
                os.startfile(resolved)
                logger.info("[system] Launched application via Start Menu shortcut: %s", resolved)
                verified = await _wait_process_running(app_clean, raw_app, max_retries=4, delay=0.3)
                if verified:
                    return f"Launched {app_clean.capitalize()} successfully."
                return f"⚠️ Failed to verify launch of {app_clean.capitalize()}: process did not appear in active desktop session."
            except Exception as e:
                logger.warning("[system] Shortcut startfile failed for %s: %s", resolved, e)

    # 3. Handle Executables / Shell Binaries
    target_cmd = resolved if resolved else raw_app
    args = list(arguments) if arguments else []

    # CDP remote debugging port for browser inspection
    if app_clean in ("brave", "chrome", "msedge", "edge", "browser") or "brave.exe" in target_cmd.lower() or "chrome.exe" in target_cmd.lower():
        if "--remote-debugging-port=9222" not in args:
            args.append("--remote-debugging-port=9222")

    if sys.platform == "win32":
        try:
            target_file = resolved if (resolved and os.path.exists(resolved)) else raw_app
            args_str = subprocess.list2cmdline(args) if args else None
            # SW_SHOWNORMAL = 1
            ret = ctypes.windll.shell32.ShellExecuteW(None, "open", target_file, args_str, None, 1)
            if ret > 32:
                logger.info("[system] Launched application via Win32 ShellExecuteW (ret=%d): %s", ret, target_file)
                verified = await _wait_process_running(app_clean, raw_app, max_retries=4, delay=0.3)
                if verified:
                    await asyncio.sleep(0.7)
                    if win32gui and win32con:
                        try:
                            def _focus_new_window(hwnd: int, _: Any) -> None:
                                if win32gui.IsWindowVisible(hwnd):
                                    txt = win32gui.GetWindowText(hwnd)
                                    if app_clean in txt.lower() or (resolved and os.path.basename(resolved).lower().replace(".exe", "") in txt.lower()):
                                        if win32gui.IsIconic(hwnd):
                                            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                                        else:
                                            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                                        try:
                                            win32gui.SetForegroundWindow(hwnd)
                                            win32gui.BringWindowToTop(hwnd)
                                        except Exception:
                                            pass
                            win32gui.EnumWindows(_focus_new_window, None)
                        except Exception:
                            pass
                    return f"Launched {app_clean.capitalize()} successfully."
                return f"⚠️ Failed to verify launch of {app_clean.capitalize()}: process did not appear in active desktop session."
        except Exception as shell_err:
            logger.debug("[system] ShellExecuteW failed (%s), falling back to cmd start...", shell_err)

        try:
            full_cmd = subprocess.list2cmdline([target_cmd] + args)
            p_shell = await asyncio.create_subprocess_shell(
                f'cmd.exe /c start "" {full_cmd}',
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await p_shell.wait()
            verified = await _wait_process_running(app_clean, raw_app, max_retries=3, delay=0.3)
            if verified:
                return f"Launched {app_clean.capitalize()} successfully."
            return f"Launched {app_clean.capitalize()} via Windows command start."
        except Exception as e:
            return f"Failed to launch application '{raw_app}': {e}"

    # Non-Windows fallback
    try:
        proc = await asyncio.create_subprocess_exec(
            target_cmd, *args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return f"Launched {app_clean.capitalize()} (PID: {proc.pid})."
    except Exception as e:
        return f"Failed to launch application '{raw_app}': {e}"


# ==============================================================================
# FILESYSTEM HELPER & SHADOW SNAPSHOTS
# ==============================================================================
def _resolve_fs_path(path_str: str) -> str:
    """Resolve user-friendly path or known folder to absolute path."""
    p_clean = path_str.strip().strip("'\"")
    # Check known folders
    try:
        from ..core.known_folders import resolve_known_folder
        resolved = resolve_known_folder(p_clean.lower())
        if resolved:
            return resolved
    except Exception:
        pass

    user_home = os.path.expanduser("~")
    full = os.path.expanduser(p_clean)
    if not os.path.isabs(full):
        full = os.path.join(user_home, full)
    return full


def _find_desktop() -> str:
    """Find desktop path reliably."""
    try:
        from ..core.known_folders import resolve_known_folder
        resolved = resolve_known_folder("desktop")
        if resolved and os.path.isdir(resolved):
            return resolved
    except Exception:
        pass
    fallback = os.path.join(os.path.expanduser("~"), "Desktop")
    return fallback if os.path.isdir(fallback) else os.path.expanduser("~")


async def _create_file_snapshot(src_path: str) -> Optional[str]:
    """Capture a shadow snapshot of a file before mutation. Returns snapshot_id."""
    src = _resolve_fs_path(src_path)
    if not os.path.exists(src) or not os.path.isfile(src):
        return None
    try:
        import uuid
        snap_dir = os.path.expanduser("~/.makima/snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap_id = f"snap_{uuid.uuid4().hex[:12]}_{os.path.basename(src)}"
        dst = os.path.join(snap_dir, snap_id)
        await asyncio.to_thread(shutil.copy2, src, dst)
        return snap_id
    except Exception as exc:
        logger.warning("[filesystem] Snapshot creation failed for %s: %s", src_path, exc)
        return None


# ==============================================================================
# STANDALONE ASYNC TOOL HANDLERS
# ==============================================================================

async def get_window_list(
    filter_name: str = "",
    active_only: bool = True,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """
    Inspect and list open application windows on the desktop with HWND, title, and process metadata.
    Use when you need to inspect or choose which window to focus, minimize, or close.
    """
    if not win32gui:
        return [{"error": "win32gui is not available on this platform"}]

    def _scan_windows() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        fg_hwnd = win32gui.GetForegroundWindow() if win32gui else 0

        def _enum_cb(hwnd: int, _: Any) -> bool:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd).strip()
                if not title:
                    return True

                if active_only:
                    if title in ("Program Manager", "Default IME", "MSCTFIME UI"):
                        return True
                    if win32con and hasattr(win32con, "WS_EX_TOOLWINDOW") and isinstance(win32con.WS_EX_TOOLWINDOW, int):
                        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                        if isinstance(style, int) and (style & win32con.WS_EX_TOOLWINDOW):
                            return True

                p_name = ""
                pid = 0
                if win32process:
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if psutil and pid > 0:
                            try:
                                p_name = psutil.Process(pid).name()
                            except Exception:
                                p_name = ""
                    except Exception:
                        pass

                is_active = (hwnd == fg_hwnd)

                if filter_name:
                    f_lower = filter_name.lower().strip()
                    if f_lower not in title.lower() and f_lower not in p_name.lower():
                        return True

                results.append({
                    "hwnd": hwnd,
                    "title": title,
                    "process_name": p_name,
                    "pid": pid,
                    "is_active": is_active,
                })
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception as e:
            logger.debug("[system] EnumWindows scan error: %s", e)

        return results

    return await asyncio.to_thread(_scan_windows)


async def manage_window(
    action: str,
    title: str = "",
    hwnd: int | str | None = None,
    **kwargs: Any,
) -> str:
    """
    Control window state: focus (bring to front), minimize, maximize, restore, or close.
    Can target exact window handle (hwnd) from get_window_list or match by title substring.
    """
    if not win32gui:
        return "win32gui is not available. Window management requires pywin32."

    action = (action or "").lower().strip()

    # 1. Exact HWND Direct Targeting
    raw_hwnd = None
    if hwnd is not None:
        try:
            raw_hwnd = int(str(hwnd).strip())
        except (ValueError, TypeError):
            raw_hwnd = None

    if raw_hwnd is not None:
        if not win32gui.IsWindow(raw_hwnd):
            return f"Window handle {raw_hwnd} is no longer valid or has already closed."
        actual_title = win32gui.GetWindowText(raw_hwnd) or f"Window (HWND {raw_hwnd})"
        windows = [(raw_hwnd, actual_title)]
        title_lower = actual_title.lower().strip()
    else:
        title_raw = (title or kwargs.get("name") or kwargs.get("target") or "").strip()
        title_lower = title_raw.lower().strip()

        # Handle active / current window queries
        if title_lower in ("current window", "current", "active", "active window", "foreground", ""):
            try:
                hwnd_fg = win32gui.GetForegroundWindow()
                if hwnd_fg and win32gui.IsWindowVisible(hwnd_fg):
                    actual_title = win32gui.GetWindowText(hwnd_fg) or "Active Window"
                    if action == "focus":
                        if win32gui.IsIconic(hwnd_fg):
                            win32gui.ShowWindow(hwnd_fg, win32con.SW_RESTORE)
                        else:
                            win32gui.ShowWindow(hwnd_fg, win32con.SW_SHOW)
                        win32gui.SetForegroundWindow(hwnd_fg)
                        win32gui.BringWindowToTop(hwnd_fg)
                    elif action == "minimize":
                        win32gui.ShowWindow(hwnd_fg, win32con.SW_MINIMIZE)
                    elif action == "maximize":
                        win32gui.ShowWindow(hwnd_fg, win32con.SW_MAXIMIZE)
                    elif action == "restore":
                        win32gui.ShowWindow(hwnd_fg, win32con.SW_RESTORE)
                    elif action == "close":
                        win32gui.PostMessage(hwnd_fg, win32con.WM_CLOSE, 0, 0)
                        await asyncio.sleep(0.5)
                        is_closed = not win32gui.IsWindow(hwnd_fg)
                        if is_closed:
                            return f"Successfully closed active window '{actual_title}'."
                        return f"Close signal sent to active window '{actual_title}'."
                    else:
                        return f"Unknown window action: {action}. Valid: focus, minimize, maximize, restore, close."
                    return f"Successfully executed '{action}' on active window '{actual_title}'."
            except Exception as e:
                logger.debug("Failed active window action: %s", e)

        def enum_callback(h: int, results: list) -> bool:
            try:
                if win32gui.IsWindowVisible(h):
                    window_title = win32gui.GetWindowText(h)
                    if window_title and title_lower in window_title.lower():
                        results.append((h, window_title))
            except Exception:
                pass
            return True

        windows = []
        try:
            win32gui.EnumWindows(enum_callback, windows)
        except Exception:
            pass

        # If not found by title, try matching running process name
        if not windows and psutil:
            matching_pids: set[int] = set()
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    pname = (proc.info["name"] or "").lower().replace(".exe", "")
                    if title_lower in pname or pname in title_lower:
                        matching_pids.add(proc.info["pid"])
                except Exception:
                    pass

            if matching_pids and win32process:
                def enum_pid_callback(h: int, results: list) -> bool:
                    if win32gui.IsWindowVisible(h):
                        try:
                            _, pid = win32process.GetWindowThreadProcessId(h)
                            if pid in matching_pids:
                                window_title = win32gui.GetWindowText(h)
                                if window_title:
                                    results.append((h, window_title))
                        except Exception:
                            pass
                    return True

                try:
                    win32gui.EnumWindows(enum_pid_callback, windows)
                except Exception:
                    pass

        if not windows:
            if action == "focus":
                try:
                    p_sub = await asyncio.create_subprocess_exec(
                        "powershell", "-NoProfile", "-Command",
                        "param($t); $ws = New-Object -ComObject WScript.Shell; $res = $ws.AppActivate($t); exit ([int](-not $res))",
                        title_raw,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    exit_code = await p_sub.wait()
                    if exit_code == 0:
                        return f"Brought '{title_raw}' to front successfully."
                except Exception:
                    pass
            if action == "close":
                kill_res = await kill_process(process_name=title_raw)
                if "Successfully terminated" in kill_res or "Terminated" in kill_res:
                    return f"Closed '{title_raw}' (via process termination)."
                return f"No open window or active process found for '{title_raw}' (already closed)."
            return f"No visible windows found matching title '{title_raw}'."

        def _score_window(item: tuple[int, str]) -> float:
            w_title = item[1].lower()
            if w_title == title_lower:
                return 1.0
            if w_title.startswith(title_lower):
                return 0.95
            if f" {title_lower} " in f" {w_title} ":
                return 0.90
            return difflib.SequenceMatcher(None, title_lower, w_title).ratio()

        windows.sort(key=_score_window, reverse=True)

    target_hwnd, actual_title = windows[0]
    hwnd = target_hwnd

    try:
        if action == "focus":
            try:
                cur_tid = win32api.GetCurrentThreadId() if win32api else 0
                tgt_tid, _ = win32process.GetWindowThreadProcessId(hwnd) if win32process else (0, 0)
                if tgt_tid and win32process and cur_tid:
                    win32process.AttachThreadInput(cur_tid, tgt_tid, True)
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                else:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
                if tgt_tid and win32process and cur_tid:
                    try:
                        win32process.AttachThreadInput(cur_tid, tgt_tid, False)
                    except Exception:
                        pass
            except Exception:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                else:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass

            try:
                await asyncio.create_subprocess_exec(
                    "powershell", "-NoProfile", "-Command",
                    "param($t); $ws = New-Object -ComObject WScript.Shell; $ws.AppActivate($t)",
                    actual_title,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
        elif action == "minimize":
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        elif action == "maximize":
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        elif action == "restore":
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        elif action == "close":
            for w_hwnd, _ in windows:
                try:
                    if win32gui.IsWindow(w_hwnd):
                        win32gui.PostMessage(w_hwnd, win32con.WM_CLOSE, 0, 0)
                except Exception:
                    pass

            await asyncio.sleep(0.6)
            still_open = any(win32gui.IsWindow(h) for h, _ in windows) if win32gui else False
            if not still_open:
                return f"Successfully closed window '{actual_title}'."
            return f"Close signal sent to window '{actual_title}' (window still active/modal pending)."
        else:
            return f"Unknown window action: {action}. Valid: focus, minimize, maximize, restore, close."

        return f"Successfully executed '{action}' on window '{actual_title}'."
    except Exception as e:
        return f"Failed to {action} window '{actual_title}': {str(e)}"


async def snap_window(
    position: str = "left",
    title: str = "active",
    **kwargs: Any,
) -> str:
    """
    Snap or tile a window to a monitor region: 'left', 'right', 'top', 'bottom',
    'top_left', 'top_right', 'bottom_left', 'bottom_right', 'center', 'maximize', 'restore'.
    """
    if not win32gui:
        return "Window snapping requires pywin32."
    pos_clean = (position or "left").lower().strip()
    title_raw = (title or "active").strip()

    hwnd = None
    if title_raw.lower() in ("active", "current", "foreground", "current window", "active window"):
        hwnd = win32gui.GetForegroundWindow()
    else:
        windows: list[tuple[int, str]] = []
        def _enum(h: int, res: list) -> None:
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h)
                if t and title_raw.lower() in t.lower():
                    res.append((h, t))
        try:
            win32gui.EnumWindows(_enum, windows)
            if windows:
                hwnd = windows[0][0]
        except Exception:
            pass

    if not hwnd or not win32gui.IsWindow(hwnd):
        return f"Window '{title_raw}' not found for snapping."

    actual_title = win32gui.GetWindowText(hwnd) or title_raw

    def _apply_snap() -> str:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        elif win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        try:
            h_mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
            mon_info = win32api.GetMonitorInfo(h_mon)
            work_rect = mon_info.get("Work")
            if not work_rect:
                user32 = ctypes.windll.user32
                work_rect = (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        except Exception:
            try:
                user32 = ctypes.windll.user32
                work_rect = (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
            except Exception:
                work_rect = (0, 0, 1920, 1080)

        wx, wy, wr, wb = work_rect
        ww = wr - wx
        wh = wb - wy

        half_w = ww // 2
        half_h = wh // 2

        if pos_clean in ("left", "left_half", "split_left"):
            tx, ty, tw, th = wx, wy, half_w, wh
        elif pos_clean in ("right", "right_half", "split_right"):
            tx, ty, tw, th = wx + half_w, wy, half_w, wh
        elif pos_clean in ("top", "top_half"):
            tx, ty, tw, th = wx, wy, ww, half_h
        elif pos_clean in ("bottom", "bottom_half"):
            tx, ty, tw, th = wx, wy + half_h, ww, half_h
        elif pos_clean in ("top_left", "quad_top_left"):
            tx, ty, tw, th = wx, wy, half_w, half_h
        elif pos_clean in ("top_right", "quad_top_right"):
            tx, ty, tw, th = wx + half_w, wy, half_w, half_h
        elif pos_clean in ("bottom_left", "quad_bottom_left"):
            tx, ty, tw, th = wx, wy + half_h, half_w, half_h
        elif pos_clean in ("bottom_right", "quad_bottom_right"):
            tx, ty, tw, th = wx + half_w, wy + half_h, half_w, half_h
        elif pos_clean in ("center", "centered"):
            tw, th = int(ww * 0.7), int(wh * 0.7)
            tx, ty = wx + (ww - tw) // 2, wy + (wh - th) // 2
        elif pos_clean == "maximize":
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return f"Maximized window '{actual_title}'."
        elif pos_clean == "minimize":
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return f"Minimized window '{actual_title}'."
        elif pos_clean in ("restore", "unmaximize", "unminimize"):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return f"Restored window '{actual_title}'."
        else:
            tx, ty, tw, th = wx, wy, half_w, wh

        win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, tx, ty, tw, th, win32con.SWP_SHOWWINDOW)
        try:
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
        return f"Successfully snapped '{actual_title}' to '{pos_clean}' ({tw}x{th} at {tx},{ty})."

    return await asyncio.to_thread(_apply_snap)


async def get_process_list(
    filter_name: str = "",
    top_n: int = 10,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """List running processes, optionally filtered by name, sorted by memory usage."""
    if not psutil:
        return [{"error": "psutil not installed"}]

    def _scan():
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status']):
            try:
                pinfo = proc.info
                if filter_name and filter_name.lower() not in (pinfo['name'] or "").lower():
                    continue
                processes.append({
                    "pid": pinfo['pid'],
                    "name": pinfo['name'],
                    "cpu": pinfo['cpu_percent'],
                    "mem_mb": round(pinfo['memory_info'].rss / (1024 * 1024), 2) if pinfo['memory_info'] else 0,
                    "status": pinfo['status'],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        processes.sort(key=lambda x: x['mem_mb'], reverse=True)
        top = processes[:top_n]
        for entry in top:
            try:
                is_crit, _ = is_critical_process(entry["pid"])
                entry["danger"] = "critical" if is_crit else "normal"
            except Exception:
                entry["danger"] = "normal"
        return top

    return await asyncio.to_thread(_scan)


async def kill_process(
    pid: int | str | None = None,
    process_name: str | None = None,
    name: str | None = None,
    force: bool = True,
    **kwargs: Any,
) -> str:
    """Terminate an application or background process safely by name or PID."""
    target_name = process_name or name or (str(pid) if pid is not None and not str(pid).isdigit() else None)
    target_pid = int(pid) if pid is not None and str(pid).isdigit() else None

    if target_name:
        t_clean = target_name.lower().strip()
        base_name = t_clean.replace(".exe", "").lower()
        exe_name = base_name + ".exe"

        candidate_names = {exe_name, base_name, t_clean, f"{base_name}app", f"{base_name}app.exe"}
        try:
            discovered = search_installed_apps(base_name)
            for a in discovered:
                c_clean = a.get("name", "").lower()
                c_base = re.sub(r'^(windows|microsoft)\s+|\s*\(protocol\)', '', os.path.splitext(os.path.basename(c_clean))[0], flags=re.I).split("(")[0].strip()
                if c_base:
                    candidate_names.add(c_base)
                    candidate_names.add(c_base + ".exe")
                    candidate_names.add(f"{c_base}app")
                    candidate_names.add(f"{c_base}app.exe")
        except Exception:
            pass

        killed_count = 0
        if psutil:
            def _kill_scan():
                killed = 0
                target_procs = []

                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        p_name = (proc.info['name'] or "").lower()
                        p_name_clean = p_name.replace(".exe", "")
                        is_match = (p_name in candidate_names or p_name_clean in candidate_names)
                        if is_match:
                            is_crit, _ = is_critical_process(proc)
                            if is_crit:
                                continue
                            target_procs.append(proc)

                            if win32gui and win32con and win32process:
                                try:
                                    target_pid_val = proc.pid
                                    hwnds = []
                                    def _find_hwnd(hwnd: int, _: Any) -> None:
                                        if win32gui.IsWindowVisible(hwnd):
                                            _, pid_out = win32process.GetWindowThreadProcessId(hwnd)
                                            if pid_out == target_pid_val:
                                                hwnds.append(hwnd)
                                    win32gui.EnumWindows(_find_hwnd, None)
                                    for h in hwnds:
                                        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                                except Exception:
                                    pass
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                for proc in target_procs:
                    try:
                        proc.wait(timeout=1.5)
                        killed += 1
                    except (psutil.TimeoutExpired, Exception):
                        try:
                            proc.terminate()
                            proc.wait(timeout=1.0)
                            killed += 1
                        except Exception:
                            if force:
                                try:
                                    proc.kill()
                                    killed += 1
                                except Exception:
                                    pass
                            else:
                                killed += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                return killed

            killed_count = await asyncio.to_thread(_kill_scan)

        if killed_count > 0:
            await asyncio.sleep(0.5)
            still = _count_processes(base_name)
            if still == 0:
                return f"Successfully terminated {killed_count} instance(s) of '{target_name}' (verified)."
            return f"Terminated {killed_count} instance(s) of '{target_name}', but {still} still running (access denied or respawned)."

        if platform.system() == "Windows":
            try:
                proc = await asyncio.create_subprocess_exec(
                    "taskkill", "/f", "/im", exe_name,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0:
                    await asyncio.sleep(1.0)
                    still = _count_processes(base_name)
                    if still == 0:
                        return f"Successfully terminated '{exe_name}' via taskkill (verified)."
                    return f"taskkill reported success, but {still} instance(s) of '{exe_name}' still running."
            except Exception:
                pass
        return f"No running processes found matching '{target_name}'."

    if target_pid is not None and psutil:
        try:
            proc = psutil.Process(target_pid)
            pname = proc.name()
            is_crit, reason = is_critical_process(proc)
            if is_crit:
                return (f"REFUSED: process '{pname}' (PID: {target_pid}) is a protected system process ({reason}). "
                        f"Terminating it could destabilize the host OS.")
            if force:
                proc.kill()
            else:
                proc.terminate()
            return f"Successfully terminated process {pname} (PID: {target_pid})."
        except psutil.NoSuchProcess:
            return f"Process with PID {target_pid} not found."
        except Exception as e:
            return f"Failed to kill process: {str(e)}"

    return "Please specify a valid PID or process_name (e.g. 'msedge', 'chrome', 'notepad')."


async def set_process_priority(pid: int, priority: str, **kwargs: Any) -> str:
    """Adjust the scheduling priority of a running process (idle, below_normal, normal, above_normal, high, realtime)."""
    if not psutil:
        return "psutil not installed."

    priority_map = {
        "idle": psutil.IDLE_PRIORITY_CLASS,
        "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
        "normal": psutil.NORMAL_PRIORITY_CLASS,
        "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
        "high": psutil.HIGH_PRIORITY_CLASS,
        "realtime": psutil.REALTIME_PRIORITY_CLASS,
    }

    p_class = priority_map.get(priority.lower().strip())
    if p_class is None:
        return f"Invalid priority: {priority}. Valid: {list(priority_map.keys())}"

    try:
        proc = psutil.Process(pid)
        proc.nice(p_class)
        return f"Successfully set priority of PID {pid} ({proc.name()}) to {priority}."
    except psutil.NoSuchProcess:
        return f"Process {pid} not found."
    except psutil.AccessDenied:
        return f"Access denied to change priority for PID {pid}. Run as admin."
    except Exception as e:
        return f"Failed to set priority: {str(e)}"


async def _run_ps_command(script: str) -> tuple[int, str]:
    """Execute a PowerShell command asynchronously and return (returncode, stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out_txt = stdout.decode(errors="replace").strip()
        err_txt = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            logger.debug("[_run_ps_command] code=%d, err=%s, out=%s", proc.returncode, err_txt, out_txt)
        return proc.returncode, out_txt or err_txt
    except Exception as exc:
        return -1, str(exc)


async def get_volume(**kwargs: Any) -> str:
    """Get current Windows host master volume level percentage."""
    if sys.platform != "win32":
        return f"System volume adjustment not supported on {sys.platform}."

    get_script = "Add-Type -TypeDefinition @'\n" + CORE_AUDIO_CS.strip() + "\n'@\n[Audio]::GetVol()\n"
    rc, out = await _run_ps_command(get_script)
    if rc == 0 and out:
        try:
            vol = round(float(out.strip().replace(",", ".")) * 100)
            return f"🔊 Current Master Volume: {vol}%"
        except Exception:
            pass
    return "⚠️ Master volume could not be read."


async def set_volume(
    level: int | str | None = None,
    delta: int | str | None = None,
    action: str | None = None,
    **kwargs: Any,
) -> str:
    """
    Set or adjust host master volume (0-100) via Windows CoreAudio COM interface.
    Supports absolute level, delta (+10 / -10), or action ('mute' / 'unmute').
    """
    if sys.platform != "win32":
        return f"System volume adjustment not supported on {sys.platform}."

    raw_level = level if level is not None else (kwargs.get("volume") if kwargs.get("volume") is not None else kwargs.get("target"))
    raw_delta = delta if delta is not None else (kwargs.get("delta") if kwargs.get("delta") is not None else kwargs.get("change"))
    act = str(action or kwargs.get("act") or "").strip().lower()

    # Normalize directional string arguments passed in level
    if isinstance(raw_level, str):
        low_lvl = raw_level.strip().lower()
        if any(w in low_lvl for w in ("kam", "down", "lower", "decrease", "reduce", "thodi kam")):
            raw_delta = -10
            raw_level = None
        elif any(w in low_lvl for w in ("badhao", "up", "raise", "increase", "higher", "thodi badhao")):
            raw_delta = 10
            raw_level = None

    if act == "mute" or raw_level in (0, "0", "mute"):
        raw_level = 0
        raw_delta = None

    if act == "unmute":
        raw_level = 30
        raw_delta = None

    if raw_level is None and raw_delta is None:
        raw_delta = 10

    try:
        if raw_delta is not None:
            d_str = re.sub(r"[^\d\-+]", "", str(raw_delta))
            try:
                d_val = int(d_str) if d_str else 10
            except ValueError:
                d_val = 10
            get_script = "Add-Type -TypeDefinition @'\n" + CORE_AUDIO_CS.strip() + "\n'@\n[Audio]::GetVol()\n"
            rc, out = await _run_ps_command(get_script)
            try:
                current_pct = round(float(out.strip().replace(",", ".")) * 100)
            except Exception:
                current_pct = 50
            new_pct = max(0, min(100, current_pct + d_val))
            label = f"raised by {abs(d_val)}% → {new_pct}%" if d_val >= 0 else f"lowered by {abs(d_val)}% → {new_pct}%"
        else:
            try:
                pct_str = re.sub(r"[^\d]", "", str(raw_level))
                new_pct = max(0, min(100, int(pct_str))) if pct_str else (0 if raw_level == 0 else 50)
            except Exception:
                new_pct = 0 if raw_level == 0 else 50
            label = f"set to {new_pct}%"

        set_script = "Add-Type -TypeDefinition @'\n" + CORE_AUDIO_CS.strip() + f"\n'@\n[Audio]::SetVol({new_pct / 100.0})\n"
        rc, _ = await _run_ps_command(set_script)
        if rc == 0:
            if act == "mute" or raw_level == 0:
                return f"🔇 System Master Volume set to {new_pct}% (muted)."
            if act == "unmute":
                return f"🔊 System Master Volume set to {new_pct}% (unmuted)."
            return f"🔊 System Master Volume {label} (CoreAudio)."
        return f"⚠️ Volume command exited code {rc}."
    except Exception as e:
        return f"Failed to set volume: {e}"


async def get_clipboard(**kwargs: Any) -> str:
    """Read and return current text from the Windows system clipboard."""
    def _read():
        if _HAS_WIN32:
            try:
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                        val = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                        return val if val else "[Clipboard is empty]"
                    elif win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                        val = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                        return val.decode("utf-8", errors="replace") if val else "[Clipboard is empty]"
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass
        if _HAS_PYPERCLIP:
            try:
                res = pyperclip.paste()
                return res if res else "[Clipboard is empty]"
            except Exception as exc:
                return f"Failed to read clipboard: {exc}"
        return "Clipboard text format unavailable."

    return await asyncio.to_thread(_read)


async def set_clipboard(text: str, **kwargs: Any) -> str:
    """Copy text content to the Windows system clipboard."""
    if text is None:
        return "No text provided to copy to clipboard."

    def _write():
        text_str = str(text)
        if _HAS_WIN32:
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text_str)
                    return f"Copied {len(text_str)} chars to clipboard."
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass
        if _HAS_PYPERCLIP:
            try:
                pyperclip.copy(text_str)
                return f"Copied {len(text_str)} chars to clipboard."
            except Exception as exc:
                return f"Failed to set clipboard: {exc}"
        return "Failed to set clipboard."

    return await asyncio.to_thread(_write)


async def show_notification(
    title: str,
    message: str,
    app_id: str = "Makima",
    **kwargs: Any,
) -> str:
    """Display a native Windows desktop toast notification alert."""
    t_safe = (title or "Makima")[:256]
    m_safe = (message or "")[:512]
    app_safe = (app_id or "Makima")[:64]

    ps_script = (
        "param($t, $m, $aid);"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template);"
        "$text = $xml.GetElementsByTagName('text');"
        "$text.Item(0).AppendChild($xml.CreateTextNode($t)) > $null;"
        "$text.Item(1).AppendChild($xml.CreateTextNode($m)) > $null;"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aid).Show($toast)"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-Command", ps_script,
            t_safe, m_safe, app_safe,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode == 0:
            return f"Notification '{title}' sent to desktop."
        return f"Notification '{title}' sent (code {proc.returncode})."
    except Exception as e:
        return f"Failed to send notification: {e}"


async def get_system_stats(**kwargs: Any) -> dict[str, Any]:
    """Retrieve comprehensive CPU, RAM, Disk, and Network IO statistics."""
    if not psutil:
        return {"error": "psutil is not installed. Cannot retrieve system stats."}

    def _stats():
        stats = {
            "cpu": {
                "usage_percent": psutil.cpu_percent(interval=0.1),
                "cores_logical": psutil.cpu_count(logical=True),
                "cores_physical": psutil.cpu_count(logical=False),
                "freq": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None,
            },
            "memory": psutil.virtual_memory()._asdict(),
            "swap": psutil.swap_memory()._asdict(),
            "disks": [],
            "network_io": {},
        }

        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                stats["disks"].append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "usage": usage._asdict(),
                })
            except PermissionError:
                continue

        net_io = psutil.net_io_counters()
        if net_io:
            stats["network_io"] = net_io._asdict()

        return stats

    return await asyncio.to_thread(_stats)


async def search_installed_apps_tool(
    query: str = "",
    limit: int = 15,
    **kwargs: Any,
) -> list[dict[str, str]]:
    """Dynamically discover installed applications, shortcuts, executables, and protocols across the OS."""
    return await asyncio.to_thread(search_installed_apps, query, limit)


async def launch_app(
    app_path: str = "",
    arguments: list[str] | None = None,
    **kwargs: Any,
) -> str:
    """Open and start a NEW application that is not yet open."""
    target = app_path or kwargs.get("app_name") or kwargs.get("name") or kwargs.get("path") or kwargs.get("app") or ""
    args = arguments or kwargs.get("args")
    return await launch_app_verified(target, args)


async def clean_temp_files(confirmed: bool = False, **kwargs: Any) -> str:
    """Scan and clean temporary junk files with dry-run safety preview."""
    temp_dirs = [
        os.path.expandvars(r"%TEMP%"),
        os.path.expandvars(r"%LOCALAPPDATA%\Temp"),
    ]
    unique_dirs = list(dict.fromkeys(d for d in temp_dirs if os.path.isdir(d)))

    def _scan():
        total_bytes = 0
        candidate_files: list[str] = []
        for tdir in unique_dirs:
            try:
                for root, _, files in os.walk(tdir):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            sz = os.path.getsize(fp)
                            total_bytes += sz
                            candidate_files.append(fp)
                        except Exception:
                            pass
            except Exception:
                pass
        return len(candidate_files), total_bytes, candidate_files

    count, total_size, files = await asyncio.to_thread(_scan)
    size_mb = round(total_size / (1024 * 1024), 1)

    if not confirmed:
        if count == 0:
            return "Temporary storage is already clean (0 files found)."
        return (
            f"🧹 Temp Storage Preview: Found {count} temporary files occupying ~{size_mb} MB.\n"
            f"Call clean_temp_files(confirmed=True) to safely delete un-locked temp files."
        )

    def _clean():
        deleted = 0
        freed = 0
        for fp in files:
            try:
                sz = os.path.getsize(fp)
                os.remove(fp)
                deleted += 1
                freed += sz
            except Exception:
                pass
        return deleted, freed

    del_count, freed_bytes = await asyncio.to_thread(_clean)
    freed_mb = round(freed_bytes / (1024 * 1024), 1)
    return f"Cleaned {del_count} temp files, reclaiming {freed_mb} MB disk space."


async def organize_desktop(
    target_folder: str = "Desktop",
    confirmed: bool = False,
    **kwargs: Any,
) -> str:
    """Organize loose files on Desktop or target folder into categorized subfolders."""
    desktop = _find_desktop() if target_folder.lower() == "desktop" else _resolve_fs_path(target_folder)
    if not desktop or not os.path.exists(desktop):
        return f"Directory not found: {target_folder}"

    def _preview() -> list[dict[str, str]]:
        plan: list[dict[str, str]] = []
        try:
            for filename in os.listdir(desktop):
                fp = os.path.join(desktop, filename)
                if filename in _CATEGORY_DIRS or filename.startswith(".") or filename.startswith("~$"):
                    continue
                if os.path.isdir(fp):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                cat = "Others"
                for c_name, exts in _DESKTOP_CATEGORIES.items():
                    if ext in exts:
                        cat = c_name
                        break
                plan.append({"file": filename, "to": cat})
        except Exception:
            pass
        return plan

    plan = await asyncio.to_thread(_preview)
    count = len(plan)
    folder_label = "Desktop" if target_folder.lower() == "desktop" else f"'{target_folder}'"

    if not confirmed:
        if count == 0:
            return f"{folder_label} is already clean - no loose files found."
        lines = "\n".join(f"  - {m['file']} -> {m['to']}/" for m in plan[:15])
        extra = f"\n  ...and {count - 15} more files." if count > 15 else ""
        return (
            f"PREVIEW - {count} file(s) would be organized in {folder_label}:\n{lines}{extra}\n\n"
            f"Call organize_desktop(confirmed=True) to execute."
        )

    def _execute():
        moved = 0
        for item in plan:
            src = os.path.join(desktop, item["file"])
            dst_dir = os.path.join(desktop, item["to"])
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, item["file"])
            try:
                shutil.move(src, dst)
                moved += 1
            except Exception:
                pass
        return moved

    moved_count = await asyncio.to_thread(_execute)
    return f"Successfully organized {moved_count} file(s) in {folder_label}."


async def system_power(action: str, **kwargs: Any) -> str:
    """Execute system-level power state transitions: sleep, hibernate, restart, or shutdown."""
    action = action.lower().strip()
    is_win = platform.system() == "Windows"

    if is_win and action == "sleep":
        def _win_sleep() -> str:
            try:
                # PowrProf SetSuspendState(bHibernate=0, bForce=0, bWakeupEventsDisabled=0)
                res = ctypes.windll.PowrProf.SetSuspendState(0, 0, 0)
                if res:
                    return "System power action 'sleep' initiated successfully."
                return "System power action 'sleep' requested."
            except Exception as ex:
                return f"Failed to enter sleep: {ex}"
        return await asyncio.to_thread(_win_sleep)

    commands = {
        "sleep": "systemctl suspend",
        "hibernate": "rundll32.exe powrprof.dll,SetSuspendState 1,1,0" if is_win else "systemctl hibernate",
        "restart": 'shutdown /r /t 5 /c "Makima restart"' if is_win else "shutdown -r -t 5",
        "shutdown": 'shutdown /s /t 5 /c "Makima shutdown"' if is_win else "shutdown -h -t 5",
    }

    if action not in commands:
        return f"Unsupported power action: {action}. Supported: ['sleep', 'hibernate', 'restart', 'shutdown']"

    try:
        process = await asyncio.create_subprocess_shell(
            commands[action], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return f"System power action '{action}' initiated successfully."
        else:
            return f"Power action failed: {stderr.decode().strip()}"
    except Exception as e:
        return f"Failed to execute power action: {str(e)}"


async def network_diagnostics(target: str, action: str = "ping", **kwargs: Any) -> str:
    """Run network diagnostic commands: ping, tracert, or dns."""
    action = action.lower().strip()
    is_win = platform.system() == "Windows"

    if action == "ping":
        cmd = ["ping", "-n", "4", target] if is_win else ["ping", "-c", "4", target]
    elif action in ("tracert", "traceroute"):
        cmd = ["tracert", target] if is_win else ["traceroute", target]
    elif action == "dns":
        try:
            ip = socket.gethostbyname(target)
            return f"DNS resolution for {target}: {ip}"
        except socket.gaierror as e:
            return f"DNS resolution failed for {target}: {str(e)}"
    else:
        return f"Unsupported network action: {action}. Supported: ping, tracert, dns."

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
        output = stdout.decode('utf-8', errors='ignore') or stderr.decode('utf-8', errors='ignore')
        if len(output) > 1500:
            output = output[:1500] + "\n... [Output Truncated]"
        return output.strip()
    except asyncio.TimeoutError:
        return f"Network diagnostics timed out for {target}."
    except Exception as e:
        return f"Network diagnostics failed: {str(e)}"


async def manage_service(service_name: str, action: str, **kwargs: Any) -> str:
    """Start, stop, or restart background OS services."""
    action = action.lower().strip()
    if action not in ("start", "stop", "restart"):
        return f"Unsupported service action: {action}. Use start, stop, or restart."

    is_win = platform.system() == "Windows"

    if action == "restart":
        cmd_stop = ["net", "stop", service_name] if is_win else ["systemctl", "stop", service_name]
        cmd_start = ["net", "start", service_name] if is_win else ["systemctl", "start", service_name]
        try:
            await asyncio.create_subprocess_exec(*cmd_stop, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process = await asyncio.create_subprocess_exec(*cmd_start, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = await process.communicate()
            return f"Service {service_name} restarted. Output: {stdout.decode().strip()}"
        except Exception as e:
            return f"Failed to restart service: {str(e)}"
    else:
        cmd = ["net", action, service_name] if is_win else ["systemctl", action, service_name]
        try:
            process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = await process.communicate()
            output = stdout.decode().strip() or stderr.decode().strip()
            return f"Service {service_name} {action} executed. Output: {output}"
        except Exception as e:
            return f"Failed to {action} service: {str(e)}"


async def get_network_adapters(active_only: bool = True, **kwargs: Any) -> str:
    """Retrieve detailed network interface configurations and IP addresses."""
    if not psutil:
        return "psutil is not installed. Cannot retrieve network adapters."

    def _scan_net() -> str:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        lines = ["### 🌐 Network Interfaces:"]

        for iface, stats_info in stats.items():
            if active_only and not stats_info.isup:
                continue
            status = "🟢 UP" if stats_info.isup else "🔴 DOWN"
            speed = f" ({stats_info.speed} Mbps)" if stats_info.speed > 0 else ""
            lines.append(f"- **{iface}** [{status}{speed}]:")
            if iface in addrs:
                for addr in addrs[iface]:
                    fam_str = "IPv4" if str(addr.family) in ("2", "AddressFamily.AF_INET") else "IPv6" if "INET6" in str(addr.family) or str(addr.family) in ("23", "30") else str(addr.family)
                    if fam_str in ("IPv4", "IPv6"):
                        lines.append(f"  • {fam_str}: `{addr.address}`")
        if len(lines) == 1:
            lines.append("No active network interfaces found.")
        return "\n".join(lines)

    return await asyncio.to_thread(_scan_net)


async def take_screenshot(
    target: str = "fullscreen",
    title: str = "active",
    **kwargs: Any,
) -> dict[str, Any]:
    """Capture a desktop or active window screenshot and save to ~/.makima/screenshots/."""
    target_clean = (target or "fullscreen").lower().strip()

    def _capture() -> dict[str, Any]:
        from PIL import ImageGrab

        shot_dir = os.path.expanduser("~/.makima/screenshots")
        os.makedirs(shot_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"screen_{timestamp}.png"
        file_path = os.path.join(shot_dir, filename)

        bbox = None
        if target_clean in ("active", "window", "active_window") and win32gui:
            try:
                hwnd = win32gui.GetForegroundWindow()
                if hwnd and win32gui.IsWindowVisible(hwnd):
                    rect = win32gui.GetWindowRect(hwnd)
                    if rect and (rect[2] > rect[0]) and (rect[3] > rect[1]):
                        bbox = rect
            except Exception:
                bbox = None

        try:
            img = ImageGrab.grab(bbox=bbox)
        except Exception:
            from PIL import Image, ImageDraw
            try:
                user32 = ctypes.windll.user32
                width = user32.GetSystemMetrics(0)
                height = user32.GetSystemMetrics(1)
            except Exception:
                width, height = 1920, 1080

            img = Image.new("RGB", (width, height), color=SCREENSHOT_FALLBACK_COLOR)
            d = ImageDraw.Draw(img)
            d.text((40, 40), f"Makima Screenshot: {timestamp}\nTarget: {target_clean}", fill=(255, 255, 255))

        img.save(file_path, "PNG")
        return {
            "status": "success",
            "path": file_path,
            "width": img.width,
            "height": img.height,
            "target": target_clean if bbox else "fullscreen",
            "timestamp": timestamp,
        }

    try:
        return await asyncio.to_thread(_capture)
    except Exception as e:
        return {"status": "error", "error": f"Failed to capture screenshot: {e}"}


async def mouse_click(
    x: int,
    y: int,
    button: str = "left",
    double: bool = False,
    **kwargs: Any,
) -> str:
    """Click anywhere on the host desktop using PyAutoGUI."""
    if not pyautogui:
        return "pyautogui is not installed. Native mouse click unavailable."

    def _click() -> str:
        clicks = 2 if double else 1
        pyautogui.click(x=x, y=y, clicks=clicks, button=button.lower())
        return f"Clicked {button} button at ({x}, {y}) [clicks={clicks}]."

    return await asyncio.to_thread(_click)


async def mouse_draw(
    shape: str = "circle",
    start_x: int = 500,
    start_y: int = 500,
    size: int = 150,
    points: list[list[int]] | None = None,
    duration: float = 2.0,
    **kwargs: Any,
) -> str:
    """Draw shapes or freehand mouse paths on the desktop."""
    if not pyautogui:
        return "pyautogui is not installed. Native mouse drawing unavailable."

    def _draw() -> str:
        import math
        shape_clean = (shape or "circle").lower().strip()
        pyautogui.moveTo(start_x, start_y)
        pyautogui.mouseDown()

        try:
            if points and isinstance(points, list):
                for pt in points:
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        pyautogui.dragTo(pt[0], pt[1], duration=0.05, button="left")
                return f"Drew custom path with {len(points)} points starting at ({start_x}, {start_y})."

            if shape_clean == "square":
                pyautogui.drag(size, 0, duration=0.3, button="left")
                pyautogui.drag(0, size, duration=0.3, button="left")
                pyautogui.drag(-size, 0, duration=0.3, button="left")
                pyautogui.drag(0, -size, duration=0.3, button="left")
            elif shape_clean in ("circle", "ellipse"):
                steps = 36
                for i in range(steps + 1):
                    angle = (2 * math.pi * i) / steps
                    px = int(start_x + (size / 2) * math.cos(angle))
                    py = int(start_y + (size / 2) * math.sin(angle))
                    if i > 0:
                        pyautogui.dragTo(px, py, duration=0.03, button="left")
                    else:
                        pyautogui.moveTo(px, py)
            elif shape_clean in ("star", "5star"):
                outer = size / 2
                inner = size / 4
                for i in range(11):
                    angle = (math.pi * i) / 5 - (math.pi / 2)
                    r = outer if i % 2 == 0 else inner
                    px = int(start_x + r * math.cos(angle))
                    py = int(start_y + r * math.sin(angle))
                    if i > 0:
                        pyautogui.dragTo(px, py, duration=0.08, button="left")
                    else:
                        pyautogui.moveTo(px, py)
            elif shape_clean in ("heart", "dil"):
                steps = 40
                for i in range(steps + 1):
                    t = (2 * math.pi * i) / steps
                    x_val = 16 * (math.sin(t) ** 3)
                    y_val = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
                    px = int(start_x + (x_val * size / 32))
                    py = int(start_y + (y_val * size / 32))
                    if i > 0:
                        pyautogui.dragTo(px, py, duration=0.03, button="left")
                    else:
                        pyautogui.moveTo(px, py)
            elif shape_clean == "triangle":
                pyautogui.drag(size // 2, size, duration=0.3, button="left")
                pyautogui.drag(-size, 0, duration=0.3, button="left")
                pyautogui.drag(size // 2, -size, duration=0.3, button="left")
            else:
                pyautogui.drag(size, size, duration=0.5, button="left")

            return f"Successfully drew '{shape_clean}' at ({start_x}, {start_y}) with size={size}."
        finally:
            pyautogui.mouseUp()

    return await asyncio.to_thread(_draw)


system_mouse_click = mouse_click
system_mouse_draw = mouse_draw


async def keyboard_press(key: str, **kwargs: Any) -> str:
    """Press a keyboard key or hotkey combination (e.g. 'enter', 'esc', 'tab', 'ctrl+c', 'alt+f4')."""
    if not pyautogui:
        return "pyautogui is not installed. Keyboard action unavailable."

    def _press() -> str:
        clean_key = str(key or kwargs.get("keys") or "").strip().lower()
        if not clean_key:
            return "No key specified to press."
        if "+" in clean_key:
            parts = [p.strip() for p in clean_key.split("+") if p.strip()]
            pyautogui.hotkey(*parts)
            return f"Pressed hotkey combination: {' + '.join(parts)}"
        else:
            pyautogui.press(clean_key)
            return f"Pressed key: {clean_key}"

    return await asyncio.to_thread(_press)


async def keyboard_type(text: str, interval: float = 0.01, **kwargs: Any) -> str:
    """Type text into the currently focused window or application."""
    if not pyautogui:
        return "pyautogui is not installed. Keyboard action unavailable."

    def _type() -> str:
        content = str(text or kwargs.get("content") or "")
        if not content:
            return "No text specified to type."
        pyautogui.write(content, interval=float(interval))
        return f"Typed {len(content)} characters into active window."

    return await asyncio.to_thread(_type)


system_keyboard_press = keyboard_press
system_keyboard_type = keyboard_type


async def read_file(path: str, max_chars: int = 6000, **kwargs: Any) -> str:
    """Read the contents of a file from disk."""
    actual_path = (path or kwargs.get("file_path") or kwargs.get("filepath") or kwargs.get("filename") or "").strip()
    if not actual_path:
        return "Error: No file path provided to read."
    src = _resolve_fs_path(actual_path)
    if not os.path.exists(src):
        return f"File not found: {actual_path}"
    if os.path.isdir(src):
        return f"Path is a directory: {actual_path}"
    try:
        def _read() -> str:
            with open(src, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read(max_chars + 1)
            if len(content) > max_chars:
                return content[:max_chars] + "\n... [TRUNCATED]"
            return content
        return await asyncio.to_thread(_read)
    except PermissionError as exc:
        return f"Permission denied: {actual_path} - {exc}"
    except Exception as exc:
        return f"Failed to read '{actual_path}': {exc}"


async def write_file(path: str, content: str = "", **kwargs: Any) -> str:
    """Write content to a file on disk with shadow snapshot protection."""
    actual_path = (path or kwargs.get("file_path") or kwargs.get("filepath") or kwargs.get("filename") or "").strip()
    actual_content = content if content != "" else (kwargs.get("text") or kwargs.get("data") or "")
    if not actual_path:
        return "Error: No file path provided to write."
    src = _resolve_fs_path(actual_path)
    parent = os.path.dirname(src)
    if parent:
        os.makedirs(parent, exist_ok=True)

    snap_id = await _create_file_snapshot(src)
    try:
        def _write() -> None:
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(actual_content)
        await asyncio.to_thread(_write)

        size = os.path.getsize(src)
        if size == 0 and len(actual_content) > 0:
            return f"❌ Write to '{src}' reported success but file is 0 bytes on disk."
        msg = f"Wrote {size} chars to '{os.path.abspath(src)}'."
        if snap_id:
            msg += f" [Snapshot: {snap_id}]"
        return msg
    except PermissionError as exc:
        return f"Permission denied writing to '{actual_path}': {exc}"
    except Exception as exc:
        return f"Failed to write to '{actual_path}': {exc}"


async def copy_file(source_path: str, target_folder_or_path: str, **kwargs: Any) -> str:
    """Copy a file or folder to a destination path."""
    src = _resolve_fs_path(source_path)
    dest = _resolve_fs_path(target_folder_or_path)
    if not os.path.exists(src):
        return f"Source not found: {source_path}"
    def _copy() -> None:
        if os.path.isdir(src):
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            d = dest if not os.path.isdir(dest) else os.path.join(dest, os.path.basename(src))
            shutil.copy2(src, d)
    try:
        await asyncio.to_thread(_copy)
        return f"Copied '{os.path.basename(src)}' to '{dest}'."
    except Exception as exc:
        return f"Failed to copy '{source_path}': {exc}"


async def move_file(source_path: str, target_folder_or_path: str, **kwargs: Any) -> str:
    """Move a file or folder from source path to target folder or path with shadow snapshot."""
    src = _resolve_fs_path(source_path)
    dest = _resolve_fs_path(target_folder_or_path)
    if not os.path.exists(src):
        return f"Source not found: {source_path}"
    snap_id = await _create_file_snapshot(src)
    def _move() -> str:
        d = dest if not os.path.isdir(dest) else os.path.join(dest, os.path.basename(src))
        shutil.move(src, d)
        return d
    try:
        final_dest = await asyncio.to_thread(_move)
        msg = f"Moved '{os.path.basename(src)}' to '{final_dest}'."
        if snap_id:
            msg += f" [Snapshot: {snap_id}]"
        return msg
    except Exception as exc:
        return f"Failed to move '{source_path}': {exc}"


async def rename_file(file_path: str, new_name: str, **kwargs: Any) -> str:
    """Rename a file or folder in place with shadow snapshot."""
    src = _resolve_fs_path(file_path)
    if not os.path.exists(src):
        return f"File not found: {file_path}"
    parent = os.path.dirname(src)
    dest = os.path.join(parent, os.path.basename(new_name))
    snap_id = await _create_file_snapshot(src)
    try:
        await asyncio.to_thread(os.rename, src, dest)
        msg = f"Renamed '{os.path.basename(src)}' to '{os.path.basename(dest)}'."
        if snap_id:
            msg += f" [Snapshot: {snap_id}]"
        return msg
    except Exception as exc:
        return f"Failed to rename '{file_path}': {exc}"


# ==============================================================================
# TOOL REGISTRY WIRING
# ==============================================================================
SYSTEM_TOOLS_MANIFEST = [
    {
        "name": "get_window_list",
        "func": get_window_list,
        "description": "Inspect and list open application windows on the desktop with HWND, title, and process metadata. Use when you need to inspect or choose which window to focus, minimize, or close.",
        "schema": {
            "type": "object",
            "properties": {
                "filter_name": {"type": "string", "description": "Optional title or process name to filter windows"},
                "active_only": {"type": "boolean", "description": "If true, only return visible interactive application windows (default true)"},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "manage_window",
        "func": manage_window,
        "description": "Control an open window state: close (exit/band karna), minimize (hide from view), maximize, focus (bring to front), or restore. Can target exact window handle (hwnd) from get_window_list or match by title.",
        "schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["close", "minimize", "maximize", "focus", "restore"],
                    "description": "Window state action on ALREADY OPEN windows: 'close' to quit/exit; 'minimize' to hide; 'maximize' for full screen; 'focus' to bring to front; 'restore' to un-maximize.",
                },
                "title": {
                    "type": "string",
                    "description": "Window title or application name substring to match (e.g. 'notepad', 'calculator', 'calc', 'edge', 'chrome', or 'active' for current window). Ignored if hwnd is provided.",
                },
                "hwnd": {
                    "type": "integer",
                    "description": "Exact window handle (HWND) returned by get_window_list. If provided, targets that exact window directly.",
                },
            },
            "required": ["action"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "snap_window",
        "func": snap_window,
        "description": "Snap/tile window to a screen region ('left', 'right', 'top', 'bottom', 'top_left', 'top_right', 'bottom_left', 'bottom_right', 'center', 'maximize', 'restore').",
        "schema": {
            "type": "object",
            "properties": {
                "position": {
                    "type": "string",
                    "enum": ["left", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right", "center", "maximize", "restore"],
                    "description": "Snap target position",
                },
                "title": {
                    "type": "string",
                    "description": "Window title, application name, or 'active' for current window (default 'active')",
                },
            },
            "required": ["position"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "get_process_list",
        "func": get_process_list,
        "description": "List running processes, optionally filtered by name, sorted by memory usage.",
        "schema": {
            "type": "object",
            "properties": {
                "filter_name": {"type": "string", "description": "Optional process name filter"},
                "top_n": {"type": "integer", "description": "Number of top processes to return (default 10)"},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "kill_process",
        "func": kill_process,
        "description": "Terminate a process safely by name (e.g. 'msedge', 'chrome', 'notepad') or PID. Protected against critical OS kernel processes.",
        "schema": {
            "type": "object",
            "properties": {
                "process_name": {"type": "string", "description": "Process name, e.g. 'msedge', 'chrome', 'notepad'"},
                "pid": {"type": "integer", "description": "Process PID (alternative to process_name)"},
                "force": {"type": "boolean", "description": "Force kill (default true)"},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": True,
    },
    {
        "name": "set_process_priority",
        "func": set_process_priority,
        "description": "Set a process scheduling priority (idle, below_normal, normal, above_normal, high, realtime).",
        "schema": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "Target process PID"},
                "priority": {"type": "string", "enum": ["idle", "below_normal", "normal", "above_normal", "high", "realtime"]},
            },
            "required": ["pid", "priority"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "get_volume",
        "func": get_volume,
        "description": "Retrieve current host system master volume level percentage (0-100).",
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "set_volume",
        "func": set_volume,
        "description": "Set host system master volume level (0-100), adjust relatively with delta (+10/-10), or mute/unmute via Windows CoreAudio.",
        "schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Target volume percentage between 0 and 100"},
                "delta": {"type": "integer", "description": "Relative percentage change (e.g. 10 to increase, -10 to decrease)"},
                "action": {"type": "string", "description": "Semantic action: 'increase'/'decrease'/'mute'/'unmute'"},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "get_clipboard",
        "func": get_clipboard,
        "description": "Read and return current text from the Windows system clipboard.",
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "set_clipboard",
        "func": set_clipboard,
        "description": "Copy text content to the Windows system clipboard.",
        "schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text content to copy to clipboard"},
            },
            "required": ["text"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "show_notification",
        "func": show_notification,
        "description": "Display a native Windows desktop toast notification alert.",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title"},
                "message": {"type": "string", "description": "Notification body message"},
                "app_id": {"type": "string", "description": "Application sender identifier (default 'Makima')"},
            },
            "required": ["title", "message"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "get_system_stats",
        "func": get_system_stats,
        "description": "Retrieve comprehensive CPU, RAM, Disk, and Network IO statistics.",
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "search_installed_apps",
        "func": search_installed_apps_tool,
        "description": "Search, list, or discover what applications or software are installed on the computer (e.g. 'konse apps hain', 'check if blender is installed', 'list installed apps'). Use ONLY for discovery/listing. NEVER use to open or launch an app (use launch_app instead).",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional keyword or app name to search in the installed apps list (e.g. 'browser', 'media', 'code')"},
                "limit": {"type": "integer", "description": "Maximum number of search results to return (default 15)"},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "launch_app",
        "func": launch_app,
        "description": "Open, launch, or start an application on the desktop (e.g. 'notepad kholo', 'open chrome', 'calculator chalao', 'start spotify', 'open vs code'). Always use this tool when the user requests to open, start, or run any application.",
        "schema": {
            "type": "object",
            "properties": {
                "app_path": {"type": "string", "description": "Application name or executable to START, OPEN, or LAUNCH (e.g. 'notepad', 'chrome', 'calc', 'spotify', 'code')."},
            },
            "required": ["app_path"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "clean_temp_files",
        "func": clean_temp_files,
        "description": "Scan and clean temporary junk files with dry-run preview and safety.",
        "schema": {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean", "description": "Set to true to execute deletion. False returns dry-run preview."},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": True,
    },
    {
        "name": "organize_desktop",
        "func": organize_desktop,
        "description": "Organize loose files on the Desktop or target folder into categorized subfolders with dry-run safety.",
        "schema": {
            "type": "object",
            "properties": {
                "target_folder": {"type": "string", "description": "Folder to organize (default Desktop)"},
                "confirmed": {"type": "boolean", "description": "Set to true to execute moves. False returns dry-run preview."},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "system_power",
        "func": system_power,
        "description": "Control system power: sleep, hibernate, restart, or shutdown (HIGHLY DESTRUCTIVE).",
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["sleep", "hibernate", "restart", "shutdown"]},
            },
            "required": ["action"],
        },
        "category": "system",
        "is_destructive": True,
    },
    {
        "name": "network_diagnostics",
        "func": network_diagnostics,
        "description": "Run network diagnostics: ping, tracert, or DNS lookup.",
        "schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Hostname or IP to diagnose"},
                "action": {"type": "string", "enum": ["ping", "tracert", "dns"], "description": "Diagnostic action (default ping)"},
            },
            "required": ["target"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "manage_service",
        "func": manage_service,
        "description": "Start, stop, or restart a system service (DESTRUCTIVE).",
        "schema": {
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
                "action": {"type": "string", "enum": ["start", "stop", "restart"]},
            },
            "required": ["service_name", "action"],
        },
        "category": "system",
        "is_destructive": True,
    },
    {
        "name": "get_network_adapters",
        "func": get_network_adapters,
        "description": "List all network interfaces and their IP configurations.",
        "schema": {
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "default": True},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "take_screenshot",
        "func": take_screenshot,
        "description": "Capture fullscreen or active window screenshot saved to disk.",
        "schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["fullscreen", "active", "window"], "description": "Capture target ('fullscreen' or 'active' window)"},
                "title": {"type": "string", "description": "Optional window title if target is window (default 'active')"},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "mouse_click",
        "func": mouse_click,
        "description": "Native desktop mouse click at coordinates.",
        "schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X desktop coordinate"},
                "y": {"type": "integer", "description": "Y desktop coordinate"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "double": {"type": "boolean", "default": False},
            },
            "required": ["x", "y"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "mouse_draw",
        "func": mouse_draw,
        "description": "Draw shapes or paths with mouse.",
        "schema": {
            "type": "object",
            "properties": {
                "shape": {"type": "string", "description": "Shape: circle, square, star, heart, triangle, or custom"},
                "start_x": {"type": "integer", "default": 500},
                "start_y": {"type": "integer", "default": 500},
                "size": {"type": "integer", "default": 150},
                "points": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}, "description": "Custom path points [[x1,y1], [x2,y2], ...]"},
            },
            "required": [],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "keyboard_press",
        "func": keyboard_press,
        "description": "Press a keyboard key (enter, esc, tab, space, backspace, up, down) or hotkey combination (ctrl+c, ctrl+v, alt+f4, win).",
        "schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name or hotkey combination separated by '+' (e.g. 'enter', 'esc', 'ctrl+c', 'alt+f4', 'win')"},
            },
            "required": ["key"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "keyboard_type",
        "func": keyboard_type,
        "description": "Type text into the currently active desktop window at the current cursor/focus position.",
        "schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text content to type into the focused application"},
                "interval": {"type": "number", "default": 0.01, "description": "Delay between keystrokes in seconds"},
            },
            "required": ["text"],
        },
        "category": "system",
        "is_destructive": False,
    },
    {
        "name": "read_file",
        "func": read_file,
        "description": "Read the contents of a file from disk.",
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
        "category": "filesystem",
        "is_destructive": False,
    },
    {
        "name": "write_file",
        "func": write_file,
        "description": "Write content to a file on disk with snapshot backup.",
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path or known folder name."},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        "category": "filesystem",
        "is_destructive": False,
    },
    {
        "name": "copy_file",
        "func": copy_file,
        "description": "Copy a file or folder to a destination path.",
        "schema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string"},
                "target_folder_or_path": {"type": "string"},
            },
            "required": ["source_path", "target_folder_or_path"],
        },
        "category": "filesystem",
        "is_destructive": False,
    },
    {
        "name": "move_file",
        "func": move_file,
        "description": "Move a file or folder from source to target folder/path with snapshot backup.",
        "schema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string"},
                "target_folder_or_path": {"type": "string"},
            },
            "required": ["source_path", "target_folder_or_path"],
        },
        "category": "filesystem",
        "is_destructive": False,
    },
    {
        "name": "rename_file",
        "func": rename_file,
        "description": "Rename a file or folder in place with snapshot backup.",
        "schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["file_path", "new_name"],
        },
        "category": "filesystem",
        "is_destructive": False,
    },
]


def register_system_tools(registry: Any) -> None:
    """Register all system capability tools into Makima's ToolRegistry."""
    agent_hints = ["system", "commander", "automation", "general"]
    task_tags = ["system", "os", "file", "desktop", "apps", "window", "visibility", "screen", "hide", "minimize", "focus", "close", "clipboard", "screenshot", "notification", "snap", "volume", "process"]

    for tool_def in SYSTEM_TOOLS_MANIFEST:
        name = tool_def["name"]
        func = tool_def["func"]
        desc = tool_def["description"]
        schema = tool_def["schema"]
        cat = tool_def.get("category", "system")
        is_dest = tool_def.get("is_destructive", False)

        if hasattr(registry, "register_tool"):
            registry.register_tool(
                name=name,
                description=desc,
                func=func,
                schema=schema,
                category=cat,
                agent_hints=agent_hints,
                task_tags=task_tags,
                priority=1,
                is_destructive=is_dest,
            )
        elif hasattr(registry, "register"):
            registry.register(
                name=name,
                func=func,
                description=desc,
                schema=schema,
                category=cat,
            )
        elif hasattr(registry, "add_tool"):
            registry.add_tool(
                name=name,
                func=func,
                description=desc,
                schema=schema,
                category=cat,
            )
        else:
            registry[name] = func

        # Also register system_ prefixed aliases if not already prefixed
        if not name.startswith("system_") and name not in ("read_file", "write_file", "copy_file", "move_file", "rename_file"):
            alias_name = f"system_{name}"
            if hasattr(registry, "register_tool"):
                registry.register_tool(
                    name=alias_name,
                    description=desc,
                    func=func,
                    schema=schema,
                    category=cat,
                    agent_hints=agent_hints,
                    task_tags=task_tags,
                    priority=1,
                    is_destructive=is_dest,
                )
            elif hasattr(registry, "register"):
                registry.register(
                    name=alias_name,
                    func=func,
                    description=desc,
                    schema=schema,
                    category=cat,
                )
            elif hasattr(registry, "add_tool"):
                registry.add_tool(
                    name=alias_name,
                    func=func,
                    description=desc,
                    schema=schema,
                    category=cat,
                )
            else:
                registry[alias_name] = func

    # Special convenient alias: system_get_stats -> get_system_stats
    if hasattr(registry, "register_tool"):
        registry.register_tool(
            name="system_get_stats",
            description="Retrieve comprehensive CPU, RAM, Disk, and Network IO statistics.",
            func=get_system_stats,
            schema={"type": "object", "properties": {}, "required": []},
            category="system",
            agent_hints=agent_hints,
            task_tags=task_tags,
            priority=1,
            is_destructive=False,
        )

    logger.info("Successfully registered %d system tools into ToolRegistry.", len(SYSTEM_TOOLS_MANIFEST))
