r"""
Location: apps/brain/core/known_folders.py
Purpose: Authoritative resolution of Windows known folders (Desktop,
Documents, Downloads, Pictures, Videos, Music) with ZERO hardcoded paths.

Method (in order of authority):
1. SHGetKnownFolderPath Win32 API — the same call Explorer itself uses.
   Reflects OneDrive Known Folder Move (KFM) redirection automatically,
   because the shell owns that mapping. No folder-name guessing.
2. Registry User Shell Folders — env-expanded, used only if the API call
   fails (kept as a diagnostic fallback, not a path guess).
3. Callers fall back to their own expanduser("~") logic if both fail
   (non-Windows dev machines).

Usage:
    from ..core.known_folders import resolve_known_folder
    desktop = resolve_known_folder("desktop")  # absolute path or None
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("makima.known_folders")

# Microsoft-documented KNOWNFOLDERID GUIDs (stable Win32 contract).
_KNOWNFOLDERIDS: dict[str, str] = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",   # FOLDERID_Desktop
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",  # FOLDERID_Documents
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",  # FOLDERID_Downloads
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",   # FOLDERID_Pictures
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",     # FOLDERID_Videos
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",      # FOLDERID_Music
}

_ALIASES: dict[str, str] = {
    "desktop": "desktop", "desk": "desktop",
    "downloads": "downloads", "download": "downloads",
    "documents": "documents", "document": "documents", "docs": "documents",
    "pictures": "pictures", "picture": "pictures", "photos": "pictures",
    "videos": "videos", "video": "videos",
    "music": "music",
}


def normalize_folder_name(name: str) -> str | None:
    """'Desktop', ' downloads ', 'Docs' → canonical key, or None."""
    key = (name or "").strip().strip("'\"").lower()
    return _ALIASES.get(key)


def _sh_get_known_folder(fid: str) -> str | None:
    """Call SHGetKnownFolderPath for a KNOWNFOLDERID. Returns None on failure."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        guid = _GUID()
        # CLSIDFromString parses '{XXXXXXXX-XXXX-...}' into a GUID struct
        hr = ctypes.windll.ole32.CLSIDFromString(
            ctypes.c_wchar_p(fid), ctypes.byref(guid)
        )
        if hr != 0:
            logger.debug("CLSIDFromString failed (hr=%s) for %s", hr, fid)
            return None

        buf_ptr = wintypes.LPWSTR()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(buf_ptr)
        )
        if hr != 0 or not buf_ptr.value:
            logger.debug("SHGetKnownFolderPath failed (hr=%s) for %s", hr, fid)
            return None
        value = str(buf_ptr.value)
        # Caller owns the CoTaskMem-allocated buffer — free the raw pointer, not the LPWSTR wrapper
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(buf_ptr, ctypes.c_void_p))
        return value
    except Exception as exc:
        logger.debug("SHGetKnownFolderPath unavailable: %s", exc)
        return None


def _registry_candidates(canonical: str) -> list[str]:
    """Env-expanded registry paths (fallback only). Never raises."""
    if sys.platform != "win32":
        return []
    reg_names = {
        "desktop": ["Desktop"],
        "documents": ["Personal"],
        "downloads": ["{374DE290-123F-4565-9164-39C4925E467B}"],
        "pictures": ["My Pictures"],
        "videos": ["My Video"],
        "music": ["My Music"],
    }.get(canonical, [])
    out: list[str] = []
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            for value_name in reg_names:
                try:
                    val, _ = winreg.QueryValueEx(k, value_name)
                except OSError:
                    continue
                p = os.path.expandvars(str(val).strip())
                if p:
                    out.append(p)
    except Exception as exc:
        logger.debug("Registry known-folder lookup failed: %s", exc)
    return out


def resolve_known_folder(name: str) -> str | None:
    """Resolve a friendly folder name to the user's REAL absolute path
    via the OS itself. Returns None for unknown names / resolution failure.
    Never raises."""
    canonical = normalize_folder_name(name)
    if not canonical:
        return None

    # 1. Shell API — authoritative, OneDrive-aware, zero guessing
    fid = _KNOWNFOLDERIDS.get(canonical)
    if fid:
        p = _sh_get_known_folder(fid)
        if p and os.path.isdir(p):
            return p

    # 2. Registry fallback (env-expanded), verified on disk
    for cand in _registry_candidates(canonical):
        if os.path.isdir(cand):
            return cand

    return None


__all__ = ["resolve_known_folder", "normalize_folder_name"]
