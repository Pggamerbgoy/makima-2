"""Makima v8.0 - FilesystemEngine.

Domain-specific file-system tools extracted from the monolithic SystemAgent.
Added: preview_organize_desktop (dry-run), search_files, get_disk_health,
get_clipboard / set_clipboard.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import platform
import shutil
import time
from typing import Any, Optional

from ..core.known_folders import resolve_known_folder

logger = logging.getLogger("makima.filesystem_engine")

try:
    import pyperclip
    _HAS_CLIPBOARD = True
except ImportError:
    pyperclip = None
    _HAS_CLIPBOARD = False

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


class FilesystemEngine:
    """Stateless utility class. All methods are async and safe from any agent."""

    @staticmethod
    def _resolve(path_str: str) -> str:
        user_home = os.path.expanduser("~")
        p_clean = path_str.strip().lower().strip("'\"")

        # 1. Known-folder resolution via the OS itself (OneDrive-aware,
        #    SHGetKnownFolderPath — no hardcoded folder guesses)
        resolved = resolve_known_folder(p_clean)
        if resolved:
            return resolved

        # 2. Generic expansion for everything else
        full = os.path.expanduser(path_str.strip().strip("'\""))
        if not os.path.isabs(full):
            full = os.path.join(user_home, full)
        return full

    @staticmethod
    def _find_desktop() -> str | None:
        # OS-resolved (OneDrive-aware); expanduser fallback for non-Windows/dev
        resolved = resolve_known_folder("desktop")
        if resolved:
            return resolved
        fallback = os.path.join(os.path.expanduser("~"), "Desktop")
        return fallback if os.path.exists(fallback) else None

    @staticmethod
    def _classify_file(filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        for category, exts in _DESKTOP_CATEGORIES.items():
            if ext in exts:
                return category
        return "Others"

    async def copy_file(self, source_path: str, target_folder_or_path: str) -> str:
        src = self._resolve(source_path)
        dest = self._resolve(target_folder_or_path)
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

    async def read_file(self, path: str, max_chars: int = 6000) -> str:
        src = self._resolve(path)
        if not os.path.exists(src):
            return f"File not found: {path}"
        if os.path.isdir(src):
            return f"Path is a directory: {path}"
        try:
            def _read() -> str:
                with open(src, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read(max_chars + 1)
                if len(content) > max_chars:
                    return content[:max_chars] + "\n... [TRUNCATED]"
                return content
            return await asyncio.to_thread(_read)
        except PermissionError as exc:
            return f"Permission denied: {path} - {exc}"
        except Exception as exc:
            return f"Failed to read '{path}': {exc}"

    @staticmethod
    def _get_snapshot_dir() -> str:
        snap_dir = os.path.expanduser("~/.makima/snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        return snap_dir

    async def create_snapshot(self, path: str) -> Optional[str]:
        """Capture a shadow copy of a file before mutation. Returns snapshot_id."""
        src = self._resolve(path)
        if not os.path.exists(src) or not os.path.isfile(src):
            return None
        try:
            import uuid
            snap_dir = self._get_snapshot_dir()
            snap_id = f"snap_{uuid.uuid4().hex[:12]}_{os.path.basename(src)}"
            dst = os.path.join(snap_dir, snap_id)
            await asyncio.to_thread(shutil.copy2, src, dst)
            return snap_id
        except Exception as exc:
            logger.warning("[filesystem] Snapshot creation failed for %s: %s", path, exc)
            return None

    async def restore_snapshot(self, snapshot_id: str, target_path: str) -> tuple[bool, str]:
        """Restore a file from snapshot store to target path with physical post-restoration verification."""
        snap_dir = self._get_snapshot_dir()
        src = os.path.join(snap_dir, snapshot_id)
        dst = self._resolve(target_path)
        if not os.path.exists(src):
            return False, f"Snapshot file '{snapshot_id}' not found in store"
        try:
            parent_dir = os.path.dirname(dst)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, src, dst)
            # Physical Post-Restoration Verification
            if os.path.exists(dst) and os.path.isfile(dst):
                return True, ""
            return False, f"Post-restoration verification failed: file '{dst}' missing after copy"
        except Exception as exc:
            logger.error("[filesystem] Snapshot restore failed for %s -> %s: %s", snapshot_id, target_path, exc)
            return False, str(exc)

    async def restore_move_transaction(self, comp_params: dict[str, Any]) -> tuple[bool, str]:
        """
        Atomic move transaction compensation:
        1. Restore source file from pre-move snapshot.
        2. Restore destination file if it pre-existed, or delete destination file if it did not pre-exist.
        3. Physically verify final filesystem state.
        """
        src_path = self._resolve(comp_params.get("source_path", ""))
        dst_path = self._resolve(comp_params.get("destination_path", ""))
        src_snap = comp_params.get("src_snapshot_id")
        dst_snap = comp_params.get("dst_snapshot_id")
        dst_existed = comp_params.get("dst_existed_before", False)

        if not src_snap or not src_path:
            return False, "Missing source snapshot or path in compensation parameters"

        try:
            # 1. Restore source file
            ok_src, err_src = await self.restore_snapshot(src_snap, src_path)
            if not ok_src:
                return False, f"Failed to restore source file: {err_src}"

            # 2. Handle destination path residue / pre-existing state
            if dst_existed and dst_snap and dst_path:
                ok_dst, err_dst = await self.restore_snapshot(dst_snap, dst_path)
                if not ok_dst:
                    return False, f"Failed to restore original destination file: {err_dst}"
            elif not dst_existed and dst_path and os.path.exists(dst_path):
                # Clean up destination residue
                await asyncio.to_thread(os.remove, dst_path)

            # 3. Physical Post-Compensation Verification
            src_ok = os.path.exists(src_path) and os.path.isfile(src_path)
            dst_ok = (os.path.exists(dst_path) == dst_existed) if dst_path else True

            if src_ok and dst_ok:
                return True, ""
            return False, f"Move compensation verification failed (src_ok={src_ok}, dst_ok={dst_ok})"
        except Exception as exc:
            logger.error("[filesystem] restore_move_transaction failed: %s", exc)
            return False, str(exc)

    async def write_file(self, path: str, content: str) -> str:
        src = self._resolve(path)
        parent = os.path.dirname(src)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Create pre-mutation snapshot if file exists
        snap_id = await self.create_snapshot(src)
        try:
            def _write() -> None:
                with open(src, "w", encoding="utf-8") as fh:
                    fh.write(content)
            await asyncio.to_thread(_write)

            # Post-write physical verification — never claim success unverified
            size = os.path.getsize(src)
            if size == 0 and len(content) > 0:
                return f"❌ Write to '{src}' reported success but file is 0 bytes on disk."
            msg = f"Wrote {size} chars to '{os.path.abspath(src)}'."
            if snap_id:
                msg += f" [Snapshot: {snap_id}]"
            return msg
        except PermissionError as exc:
            return f"Permission denied writing to '{path}': {exc}"
        except Exception as exc:
            return f"Failed to write to '{path}': {exc}"

    async def organize_desktop(self, target_folder: str = "Desktop", confirmed: bool = False) -> str:
        """Organize Desktop or target folder. Shows preview unless confirmed=True."""
        if not confirmed:
            preview = await self.preview_organize_desktop(target_folder=target_folder)
            count = preview.get("count", 0)
            folder_label = "Desktop" if target_folder.lower() == "desktop" else f"'{target_folder}'"
            if count == 0:
                return f"{folder_label} is already clean - no loose files found."
            lines = "\n".join(
                f"  - {m['file']} -> {m['to']}/"
                for m in preview["would_move"][:15]
            )
            extra = f"\n  ...and {count - 15} more files." if count > 15 else ""
            return (
                f"PREVIEW - {count} file(s) would be organized in {folder_label}:\n{lines}{extra}\n\n"
                f"Call organize_desktop(confirmed=True) to execute."
            )
        desktop = self._find_desktop() if target_folder.lower() == "desktop" else self._resolve(target_folder)
        if not desktop or not os.path.exists(desktop):
            return f"Directory not found: {target_folder}"
        moved_count = 0
        summary: list[str] = []
        moves_manifest: list[dict[str, Any]] = []
        def _organize() -> None:
            nonlocal moved_count
            for filename in os.listdir(desktop):
                fp = os.path.join(desktop, filename)
                if filename in _CATEGORY_DIRS or filename.startswith(".") or filename.startswith("~$"):
                    continue
                if os.path.isdir(fp):
                    continue
                category = FilesystemEngine._classify_file(filename)
                target_dir = os.path.join(desktop, category)
                os.makedirs(target_dir, exist_ok=True)
                dest = os.path.join(target_dir, filename)
                dst_existed = os.path.exists(dest)
                if dst_existed:
                    base, ext = os.path.splitext(filename)
                    dest = os.path.join(target_dir, f"{base}_{int(time.time())}{ext}")
                try:
                    # Pre-mutation snapshot before moving
                    snap_id = None
                    try:
                        import uuid
                        snap_dir = self._get_snapshot_dir()
                        snap_id = f"snap_{uuid.uuid4().hex[:12]}_{filename}"
                        shutil.copy2(fp, os.path.join(snap_dir, snap_id))
                    except Exception:
                        pass
                    shutil.move(fp, dest)
                    moved_count += 1
                    summary.append(f"{filename} -> {category}/")
                    moves_manifest.append({
                        "source_path": fp,
                        "destination_path": dest,
                        "src_snapshot_id": snap_id,
                        "dst_existed_before": False,
                    })
                except Exception as exc:
                    logger.error("[filesystem] Failed to move %s: %s", filename, exc)
        await asyncio.to_thread(_organize)
        if platform.system() == "Windows":
            try:
                import ctypes
                ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)
            except Exception as exc:
                logger.debug("[filesystem] Desktop visual refresh failed: %s", exc)
        if moved_count == 0:
            return "Desktop is already clean - no loose files found."
        result = f"Organized {moved_count} file(s):\n" + "\n".join(summary[:15])
        if len(summary) > 15:
            result += f"\n...and {len(summary) - 15} more."
        
        # Embed manifest data for structured transaction compensation
        import json
        result += f"\n[Manifest: {json.dumps({'moves': moves_manifest})}]"
        return result

    async def rollback_organize_desktop(self, manifest: dict[str, Any]) -> tuple[bool, str]:
        """Roll back a composite organize_desktop transaction in reverse order with physical verification."""
        moves = manifest.get("moves", []) if isinstance(manifest, dict) else []
        if not moves:
            return True, ""
        
        all_ok = True
        errs = []
        for m in reversed(moves):
            ok, err = await self.restore_move_transaction(m)
            if not ok:
                all_ok = False
                errs.append(err)
                
        if all_ok:
            return True, ""
        return False, f"Composite organize_desktop rollback failed: {'; '.join(errs)}"

    async def move_file(self, source_path: str, target_folder_or_path: str) -> str:
        """Move a file (or a wildcard pattern like *.exe within one folder) to
        the target location, with pre-mutation snapshots of source and destination."""
        src = self._resolve(source_path)
        dst = self._resolve(target_folder_or_path)

        # ── Wildcard batch-move support ──────────────────────────────────────
        # The LLM naturally emits patterns like 'D:/Games/*.exe'. Instead of
        # failing (which pushed it to simulate!), expand the pattern ourselves.
        if any(ch in src for ch in "*?["):
            parent = os.path.dirname(src)
            pattern = os.path.basename(src)
            if not os.path.isdir(parent):
                return f"Source folder '{parent}' does not exist."
            matches = [
                f for f in os.listdir(parent)
                if fnmatch.fnmatch(f.lower(), pattern.lower())
                and os.path.isfile(os.path.join(parent, f))
            ]
            if not matches:
                return (
                    f"No files matching '{pattern}' inside '{parent}'. "
                    "Tip: search_files tool se pehle actual names list karo."
                )
            moved = failed = 0
            for fname in matches:
                res = await self.move_file(os.path.join(parent, fname), dst)
                if res.startswith("Moved"):
                    moved += 1
                else:
                    failed += 1
            return (
                f"Pattern '{pattern}': {moved} file(s) moved to '{dst}'"
                + (f", {failed} failed." if failed else ".")
            )

        if not os.path.exists(src):
            return (
                f"Source path '{source_path}' does not exist. "
                "Agar folder organize karna tha to organize_desktop(target_folder=...) use karo; "
                "file dhundni ho to search_files chalao."
            )
        
        dst_target = os.path.join(dst, os.path.basename(src)) if os.path.isdir(dst) else dst
        dst_existed = os.path.exists(dst_target)
        
        src_snap = await self.create_snapshot(src)
        dst_snap = await self.create_snapshot(dst_target) if dst_existed else None
        
        try:
            parent_dir = os.path.dirname(dst_target)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            await asyncio.to_thread(shutil.move, src, dst_target)
            msg = f"Moved '{src}' to '{dst_target}'."
            if src_snap:
                msg += f" [Snapshot: {src_snap}]"
            return msg
        except Exception as exc:
            return f"Failed to move '{source_path}': {exc}"

    async def rename_file(self, file_path: str, new_name: str) -> str:
        """Rename a file with pre-mutation snapshot."""
        src = self._resolve(file_path)
        if not os.path.exists(src):
            return f"File '{file_path}' does not exist."
        dst = os.path.join(os.path.dirname(src), new_name)
        snap_id = await self.create_snapshot(src)
        try:
            await asyncio.to_thread(os.rename, src, dst)
            msg = f"Renamed '{src}' to '{new_name}'."
            if snap_id:
                msg += f" [Snapshot: {snap_id}]"
            return msg
        except Exception as exc:
            return f"Failed to rename '{file_path}': {exc}"

    async def delete_file(self, path: str) -> str:
        """Delete a file with pre-mutation snapshot."""
        src = self._resolve(path)
        if not os.path.exists(src):
            return f"File '{path}' does not exist."
        snap_id = await self.create_snapshot(src)
        try:
            await asyncio.to_thread(os.remove, src)
            msg = f"Deleted '{src}'."
            if snap_id:
                msg += f" [Snapshot: {snap_id}]"
            return msg
        except Exception as exc:
            return f"Failed to delete '{path}': {exc}"

    async def preview_organize_desktop(self, target_folder: str = "Desktop") -> dict[str, Any]:
        """DRY-RUN: returns what would happen, no files moved."""
        desktop = self._find_desktop() if target_folder.lower() == "desktop" else self._resolve(target_folder)
        if not desktop or not os.path.exists(desktop):
            return {"count": 0, "would_move": [], "error": f"Directory not found: {target_folder}"}
        def _preview() -> list[dict[str, str]]:
            plan: list[dict[str, str]] = []
            for filename in os.listdir(desktop):
                fp = os.path.join(desktop, filename)
                if filename in _CATEGORY_DIRS or filename.startswith(".") or filename.startswith("~$"):
                    continue
                if os.path.isdir(fp):
                    continue
                category = FilesystemEngine._classify_file(filename)
                plan.append({"file": filename, "to": category})
            return plan
        plan = await asyncio.to_thread(_preview)
        return {"count": len(plan), "would_move": plan}

    async def search_files(
        self,
        query: str,
        path: str = "~",
        pattern: str = "*",
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Find files by name substring or glob across a directory tree."""
        root = self._resolve(path)
        if not os.path.exists(root):
            return [{"error": f"Search root not found: {path}"}]
        query_l = query.lower()
        def _search() -> list[dict[str, Any]]:
            results: list[dict[str, Any]] = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames + dirnames:
                    if len(results) >= max_results:
                        return results
                    if query_l not in name.lower():
                        continue
                    if not fnmatch.fnmatch(name.lower(), pattern.lower()):
                        continue
                    full_path = os.path.join(dirpath, name)
                    try:
                        size = os.path.getsize(full_path) if os.path.isfile(full_path) else 0
                    except OSError:
                        size = 0
                    results.append({
                        "path":       full_path,
                        "name":       name,
                        "size_bytes": size,
                        "is_dir":     os.path.isdir(full_path),
                    })
            return results
        return await asyncio.to_thread(_search)

    async def get_disk_health(self, scan_path: str = "~", top_n: int = 10) -> dict[str, Any]:
        """Disk space breakdown plus largest files."""
        root = self._resolve(scan_path)
        def _health() -> dict[str, Any]:
            usage = shutil.disk_usage(root)
            total_gb = round(usage.total / 1e9, 2)
            used_gb  = round(usage.used  / 1e9, 2)
            free_gb  = round(usage.free  / 1e9, 2)
            pct      = round(usage.used / usage.total * 100, 1)
            warning: str | None = None
            if free_gb < 10:
                warning = f"Low disk space: only {free_gb} GB free on {root}"
            large: list[tuple[int, str]] = []
            visited = 0
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    try:
                        sz = os.path.getsize(fp)
                        large.append((sz, fp))
                    except OSError:
                        pass
                    visited += 1
                    if visited >= 2000:
                        break
                if visited >= 2000:
                    break
            large.sort(reverse=True)
            largest = [{"path": fp, "size_mb": round(sz / 1e6, 2)} for sz, fp in large[:top_n]]
            return {
                "disk_usage": {"total_gb": total_gb, "used_gb": used_gb, "free_gb": free_gb, "percent": pct},
                "warning":       warning,
                "largest_files": largest,
            }
        try:
            return await asyncio.to_thread(_health)
        except Exception as exc:
            return {"error": f"Disk health scan failed: {exc}"}

    async def get_clipboard(self) -> str:
        """Read text from clipboard with win32clipboard / pyperclip support."""
        def _read():
            try:
                import win32clipboard
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
            if _HAS_CLIPBOARD:
                try:
                    res = pyperclip.paste()
                    return res if res else "[Clipboard is empty]"
                except Exception as exc:
                    return f"Failed to read clipboard: {exc}"
            return "Clipboard text format unavailable."

        return await asyncio.to_thread(_read)

    async def set_clipboard(self, text: str) -> str:
        """Copy text to clipboard with win32clipboard / pyperclip support."""
        if text is None:
            return "No text provided to copy to clipboard."
        
        def _write():
            text_str = str(text)
            try:
                import win32clipboard
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text_str)
                    return f"Copied {len(text_str)} chars to clipboard."
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass
            if _HAS_CLIPBOARD:
                try:
                    pyperclip.copy(text_str)
                    return f"Copied {len(text_str)} chars to clipboard."
                except Exception as exc:
                    return f"Failed to set clipboard: {exc}"
            return "Failed to set clipboard."

        return await asyncio.to_thread(_write)

    async def clean_temp_files(self, confirmed: bool = False) -> str:
        """Scan and clean Windows temporary junk files with dry-run preview and safety."""
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


_engine: FilesystemEngine | None = None


def get_filesystem_engine() -> FilesystemEngine:
    global _engine
    if _engine is None:
        _engine = FilesystemEngine()
    return _engine
