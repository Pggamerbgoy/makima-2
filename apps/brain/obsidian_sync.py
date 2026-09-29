"""Makima — ObsidianSyncAdapter

Hybrid sync layer: writes every EternalMemory turn as a .md file into a
local Obsidian vault directory.  EternalMemory (SQLite + HNSW) stays the
source of truth; Obsidian is a human-readable mirror for browsing and
annotation.

Design decisions:
  - File I/O only — no Obsidian REST API dependency (vault need not be open).
    Works whether Obsidian is running or not.
  - One .md file per conversation-day, append-only.
    Format:  vault/conversations/YYYY-MM-DD/<conversation_id>.md
  - Graceful degradation: any write error is logged, never re-raised.
  - Background asyncio task: never blocks the save_turn() caller.
  - Vault path resolved from config key "obsidian.vault_path" or env var
    OBSIDIAN_VAULT_PATH.  If neither is set, sync is disabled (no-op).

Non-negotiable rules (same as EternalMemory):
  - Never crash the brain: catch/log, degrade gracefully.
  - asyncio-safe: no blocking file I/O on the event loop thread.
  - Structured %s-format logging.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("makima.obsidian_sync")

__all__ = ["ObsidianSyncAdapter"]

# ---------------------------------------------------------------------------
# Role → emoji for readable notes
# ---------------------------------------------------------------------------
_ROLE_ICON: dict[str, str] = {
    "user":      "🧑",
    "assistant": "🤖",
    "system":    "⚙️",
    "tool":      "🔧",
}


class ObsidianSyncAdapter:
    """
    Appends every Makima conversation turn as Markdown into an Obsidian vault.

    Usage (wired by AppBootstrap via EternalMemory):
        adapter = ObsidianSyncAdapter(config)
        adapter.sync_turn(role, message, conversation_id, ts)

    If vault_path is None or empty, every call is a no-op.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        obs_cfg: dict[str, Any] = cfg.get("obsidian", {}) if isinstance(cfg, dict) else {}

        raw_path_val = (
            obs_cfg.get("vault_path", "")
            or os.getenv("OBSIDIAN_VAULT_PATH", "")
        )
        raw_path: str = str(raw_path_val).strip() if raw_path_val else ""

        self._vault_path: Path | None = Path(os.path.expanduser(raw_path)) if raw_path else None
        self._enabled: bool = bool(self._vault_path) and bool(obs_cfg.get("enabled", True))
        self._background_tasks: set[asyncio.Task] = set()
        self._file_lock = threading.Lock()

        if self._enabled:
            logger.info("ObsidianSyncAdapter enabled — vault: %s", self._vault_path)
        else:
            logger.info(
                "ObsidianSyncAdapter disabled — set obsidian.vault_path in config "
                "or OBSIDIAN_VAULT_PATH env var to enable."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_turn(
        self,
        role: str,
        message: str,
        conversation_id: str | None = None,
        ts: float | None = None,
    ) -> None:
        """
        Fire-and-forget: schedule the markdown write as a background task.
        Returns immediately — never blocks save_turn().
        """
        if not self._enabled:
            return
        try:
            task = asyncio.create_task(
                self._write_turn(role, message, conversation_id, ts or time.time())
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            # No running event loop (e.g. during tests) — skip silently
            pass

    async def flush(self, timeout: float = 3.0) -> None:
        """Wait for any scheduled background writes to finish."""
        pending = [t for t in self._background_tasks if not t.done()]
        if not pending:
            return
        done, not_done = await asyncio.wait(pending, timeout=timeout)
        if not_done:
            logger.warning("ObsidianSyncAdapter flush timed out with %d tasks remaining", len(not_done))

    async def close(self) -> None:
        """Flush and cancel remaining tasks."""
        await self.flush(timeout=2.0)
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        self._background_tasks.clear()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def vault_path(self) -> Path | None:
        return self._vault_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _write_turn(
        self,
        role: str,
        message: str,
        conversation_id: str | None,
        ts: float,
    ) -> None:
        """Async wrapper — runs blocking file I/O in executor."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self._write_turn_sync,
                role,
                message,
                conversation_id,
                ts,
            )
        except Exception as exc:
            logger.error("ObsidianSyncAdapter write error: %s", exc)

    def _write_turn_sync(
        self,
        role: str,
        message: str,
        conversation_id: str | None,
        ts: float,
    ) -> None:
        """Blocking file write — runs in thread pool executor with file lock."""
        assert self._vault_path is not None  # guarded by self._enabled check

        dt = datetime.fromtimestamp(ts)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M:%S")

        # Directory: <vault>/conversations/YYYY-MM-DD/
        conv_dir = self._vault_path / "conversations" / date_str
        conv_dir.mkdir(parents=True, exist_ok=True)

        # File: <conv_dir>/<conversation_id>.md  or  <conv_dir>/general.md
        safe_cid = self._sanitize(conversation_id or "general")
        md_file = conv_dir / f"{safe_cid}.md"

        role_str = str(role).strip()
        icon = _ROLE_ICON.get(role_str.lower(), "💬")
        role_label = re.sub(r"[^\w\-]", "", role_str).capitalize() or "Note"

        with self._file_lock:
            # Create file with YAML frontmatter on first write
            is_new = not md_file.exists() or md_file.stat().st_size == 0
            with md_file.open("a", encoding="utf-8") as fh:
                if is_new:
                    fh.write(
                        f"---\n"
                        f"date: {date_str}\n"
                        f"conversation_id: {safe_cid}\n"
                        f"tags: [makima, conversation, memory]\n"
                        f"---\n\n"
                        f"# Conversation — {date_str}\n\n"
                    )
                # Append turn block using Obsidian callout syntax
                fh.write(f"> [!{role_label}]+ {icon} {role_label} · {time_str}\n")
                clean_msg = str(message).replace("\x00", "")
                for line in clean_msg.splitlines():
                    fh.write(f"> {line}\n")
                fh.write("\n")

        logger.debug(
            "ObsidianSync wrote %s turn → %s",
            role,
            md_file.relative_to(self._vault_path),
        )

    @staticmethod
    def _sanitize(name: str) -> str:
        """Strip chars illegal in filenames to produce a safe .md filename."""
        safe = re.sub(r"[^\w\-.]", "_", name).strip("._")
        return safe[:120] if safe else "general"
