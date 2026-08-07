"""Makima v7.1 — DiskGuard

Spec:
- Poll ~/.makima/ disk usage every 60s.
- thresholds at 80%/90%/95%.
- 80% stop HNSW snapshots.
- 90% stop all DB writes + TTS warn.
- 95% emergency: SQLite WAL checkpoint + compact/VACUUM, delete oldest
  conversation backups, re-enable writes when below 88%.

In this repo snapshot, we may not yet have HNSW snapshot controls.
So this module focuses on:
- computing thresholds
- emitting WS events via ws_broadcast
- exposing flags: allow_writes, allow_snapshots, emergency_compaction_needed

Other modules can consult these flags.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Callable, Awaitable

logger = logging.getLogger("makima.disk_guard")


@dataclass
class DiskStatus:
    path: str
    used_pct: float
    allow_writes: bool
    allow_snapshots: bool
    emergency: bool


class DiskGuard:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        ws_broadcast: Optional[Callable[[Any], Awaitable[None]]] = None,
    ):
        self.config = config or {}
        self.ws_broadcast = ws_broadcast

        dg_cfg = self.config.get("disk_guard", {}) if isinstance(self.config, dict) else {}
        self.base_dir = dg_cfg.get("path", os.path.expanduser("~/.makima"))

        self.warn_pct = float(dg_cfg.get("warn_pct", 80))
        self.critical_pct = float(dg_cfg.get("critical_pct", 90))
        self.emergency_pct = float(dg_cfg.get("emergency_pct", 95))
        self.reenable_below_pct = float(dg_cfg.get("reenable_below_pct", 88))

        self.poll_interval_s = float(dg_cfg.get("interval_s", 60))

        self._emergency_mode = False
        self._allow_writes = True
        self._allow_snapshots = True
        self._task: Any = None

    async def start(self) -> None:
        import asyncio

        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    def _compute(self) -> DiskStatus:
        import shutil
        try:
            usage = shutil.disk_usage(self.base_dir)
        except OSError:
            # Path doesn't exist yet — nothing to guard
            return DiskStatus(
                path=self.base_dir, used_pct=0.0,
                allow_writes=True, allow_snapshots=True, emergency=False,
            )
        used_pct = (usage.used / usage.total * 100.0) if usage.total > 0 else 0.0

        emergency = used_pct >= self.emergency_pct
        critical = used_pct >= self.critical_pct
        warn = used_pct >= self.warn_pct

        if emergency:
            allow_writes = False
            allow_snapshots = False
        elif critical:
            allow_writes = False
            allow_snapshots = False
        elif warn:
            allow_writes = True
            allow_snapshots = False
        else:
            # If we were in emergency mode previously, require re-enable below threshold
            if self._emergency_mode and used_pct < self.reenable_below_pct:
                self._emergency_mode = False
            allow_writes = True if not self._emergency_mode else False
            allow_snapshots = True if not self._emergency_mode else False

        if emergency:
            self._emergency_mode = True

        return DiskStatus(
            path=self.base_dir,
            used_pct=used_pct,
            allow_writes=allow_writes,
            allow_snapshots=allow_snapshots,
            emergency=emergency,
        )

    async def _loop(self) -> None:
        import asyncio

        while True:
            try:
                status = self._compute()
                self._allow_writes = status.allow_writes
                self._allow_snapshots = status.allow_snapshots

                if self.ws_broadcast:
                    from . import ws_protocol

                    if status.used_pct >= self.emergency_pct:
                        await self.ws_broadcast(ws_protocol.build_disk_critical(status.used_pct))
                    elif status.used_pct >= self.critical_pct:
                        await self.ws_broadcast(ws_protocol.build_disk_critical(status.used_pct))
                    elif status.used_pct >= self.warn_pct:
                        await self.ws_broadcast(ws_protocol.build_disk_warning(status.used_pct, status.path))

                await asyncio.sleep(self.poll_interval_s)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("DiskGuard error: %s", e)
                await asyncio.sleep(self.poll_interval_s)

    @property
    def allow_writes(self) -> bool:
        return self._allow_writes

    @property
    def allow_snapshots(self) -> bool:
        return self._allow_snapshots

    @property
    def emergency_compaction_needed(self) -> bool:
        return self._emergency_mode

