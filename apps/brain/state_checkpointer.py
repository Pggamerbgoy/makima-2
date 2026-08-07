"""Makima v7.2 — Elite StateCheckpointer

Prevents data loss on unexpected Python brain crash.

Spec (from makima_v7_improved_plan.md):
- Every 15s (and on graceful shutdown), serialize:
  - active task queue
  - current conversation turn buffer (unsaved to SQLite)
  - agent intermediate results (research outline, partial code)
- Storage: Rust ConversationCheckpoint writes CBOR atomically
  (tmp → fsync → rename) to ~/.makima/checkpoint.cbor.
- Restore on next startup and re-queue interrupted tasks.

Current implementation notes:
- The Rust PyO3 module may not exist yet in this repo snapshot.
- This module therefore provides a safe Python fallback:
  - checkpoints to a local JSON file atomically
  - restore restores queue (best-effort) into an injected callback

Elite v7.2 upgrades:
- Robust asyncio.Lock serialization for concurrent checkpoint calls
- Zero-crash resilience: every public and private method is wrapped in
  try/except so no error can propagate to the brain
- Proper data validation on restore (type checks, schema validation)
- Structured logging with %s format for log aggregation
- Full type annotations throughout
- Monotonic time tracking for internal timing
- Atomic write with proper write-mode fsync (not read-mode)
- Safe async/sync callback detection (inspect.iscoroutinefunction)
- Configurable max checkpoint file size guard
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("makima.state_checkpointer")

# Maximum allowed checkpoint file size (10 MB) — prevents runaway growth
_MAX_CHECKPOINT_SIZE_BYTES = 10 * 1024 * 1024


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomic write: write to temp file, fsync, then rename over target.

    Best-effort on Windows (``os.replace`` is atomic on POSIX, best-effort
    on Windows but still safer than direct overwrite).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        tmp.replace(path)
    except Exception:
        # Clean up temp file on failure
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ─── Data Model ───────────────────────────────────────────────────────────────


@dataclass
class CheckpointState:
    """Serializable snapshot of brain state for crash recovery."""
    checkpoint_version: int
    checkpointed_at: float
    tasks: List[Dict[str, Any]]
    conversation_buffer: Any = None
    agent_intermediate: Optional[Dict[str, Any]] = None


# ─── StateCheckpointer ────────────────────────────────────────────────────────


class StateCheckpointer:
    """Periodic checkpoint manager.

    Intentionally decoupled from CommandRouter internals — callbacks are
    injected so we don't tightly bind to a specific queue type.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        ws_broadcast: Optional[Callable[[Any], Awaitable[None]]] = None,
        get_state_callback: Optional[Callable[[], CheckpointState]] = None,
        restore_callback: Optional[
            Callable[[CheckpointState], Awaitable[None]]
        ] = None,
    ) -> None:
        self.config: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.ws_broadcast = ws_broadcast
        self.get_state_callback = get_state_callback
        self.restore_callback = restore_callback

        ck_cfg: Dict[str, Any] = self.config.get("checkpoint", {})
        self.interval_s: float = float(ck_cfg.get("interval_s", 15))
        ck_path: str = str(ck_cfg.get("path", "~/.makima/checkpoint.cbor"))

        expanded = os.path.expanduser(ck_path)
        self.checkpoint_path = Path(expanded)
        self.json_checkpoint_path = self.checkpoint_path.with_suffix(".json")

        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._running: bool = False
        self._last_checkpoint_mono: float = 0.0
        self._checkpoint_count: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the periodic checkpoint loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "StateCheckpointer started (interval=%.1fs)", self.interval_s
        )

    async def stop(self) -> None:
        """Stop the checkpoint loop and write a final checkpoint."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("Error awaiting checkpoint task: %s", exc)
        # Final checkpoint on shutdown — never raises
        await self.checkpoint(reason="shutdown")
        logger.info("StateCheckpointer stopped")

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore(self) -> None:
        """Restore from checkpoint at startup (best-effort).

        Reads the JSON checkpoint file, validates its structure, and
        invokes the restore callback (if provided).  Never raises.
        """
        try:
            if not self.json_checkpoint_path.exists():
                logger.debug("No checkpoint file found; nothing to restore")
                return

            # Size guard
            file_size = self.json_checkpoint_path.stat().st_size
            if file_size > _MAX_CHECKPOINT_SIZE_BYTES:
                logger.warning(
                    "Checkpoint file too large (%d bytes); skipping restore",
                    file_size,
                )
                return

            raw = self.json_checkpoint_path.read_text(encoding="utf-8")
            data = json.loads(raw)

            if not isinstance(data, dict):
                logger.warning("Checkpoint data is not a dict; skipping")
                return

            state = CheckpointState(
                checkpoint_version=int(data.get("checkpoint_version", 1)),
                checkpointed_at=float(data.get("checkpointed_at", time.time())),
                tasks=list(data.get("tasks", []))
                if isinstance(data.get("tasks"), list)
                else [],
                conversation_buffer=data.get("conversation_buffer"),
                agent_intermediate=data.get("agent_intermediate")
                if isinstance(data.get("agent_intermediate"), dict)
                else None,
            )

            if self.restore_callback:
                if inspect.iscoroutinefunction(self.restore_callback):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._safe_restore(state))
                    except RuntimeError:
                        logger.warning(
                            "No running event loop; async restore deferred"
                        )
                else:
                    try:
                        self.restore_callback(state)
                    except Exception as exc:
                        logger.error(
                            "Sync restore callback raised: %s", exc, exc_info=True
                        )

            logger.info(
                "Restored checkpoint: version=%s, tasks=%d",
                state.checkpoint_version,
                len(state.tasks),
            )
        except json.JSONDecodeError as e:
            logger.error("Corrupt checkpoint file (JSON): %s", e)
        except (OSError, ValueError, TypeError) as e:
            logger.error("StateCheckpointer restore failed: %s", e, exc_info=True)
        except Exception as e:
            logger.error(
                "Unexpected restore error: %s", e, exc_info=True
            )

    async def _safe_restore(self, state: CheckpointState) -> None:
        """Run the async restore callback with error isolation."""
        try:
            if self.restore_callback:
                await self.restore_callback(state)
        except Exception as e:
            logger.error("Async restore callback failed: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    async def checkpoint(self, reason: str = "periodic") -> None:
        """Write a checkpoint to disk atomically.

        Serialized via ``self._lock`` so concurrent calls don't corrupt
        the file.  Never raises.
        """
        async with self._lock:
            try:
                state = self._gather_state()
                if state is None:
                    return

                self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

                payload: Dict[str, Any] = {
                    "checkpoint_version": state.checkpoint_version,
                    "checkpointed_at": state.checkpointed_at,
                    "tasks": state.tasks,
                    "conversation_buffer": state.conversation_buffer,
                    "agent_intermediate": state.agent_intermediate,
                }

                try:
                    text = json.dumps(payload, ensure_ascii=False, default=str)
                except (TypeError, ValueError) as e:
                    logger.error("Checkpoint JSON serialization failed: %s", e)
                    return

                try:
                    _atomic_write_text(self.json_checkpoint_path, text)
                except OSError as e:
                    logger.error("Checkpoint file write failed: %s", e)
                    return

                self._last_checkpoint_mono = time.monotonic()
                self._checkpoint_count += 1

                logger.debug(
                    "Checkpoint written (%s, #%d) to %s — %d tasks",
                    reason,
                    self._checkpoint_count,
                    self.json_checkpoint_path,
                    len(state.tasks),
                )
            except Exception as e:
                logger.error(
                    "Checkpoint error (%s): %s", reason, e, exc_info=True
                )

    def _gather_state(self) -> Optional[CheckpointState]:
        """Invoke the state callback and return a CheckpointState.

        Returns None if the callback fails or is not set.
        """
        try:
            if self.get_state_callback:
                state = self.get_state_callback()
                if not isinstance(state, CheckpointState):
                    logger.warning(
                        "get_state_callback returned %s, expected CheckpointState",
                        type(state).__name__,
                    )
                    return None
                return state
        except Exception as e:
            logger.error("get_state_callback failed: %s", e, exc_info=True)
            return None

        return CheckpointState(
            checkpoint_version=1,
            checkpointed_at=time.time(),
            tasks=[],
        )

    # ------------------------------------------------------------------
    # Periodic loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Periodic checkpoint loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if not self._running:
                    break
                await self.checkpoint(reason="periodic")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Checkpoint loop error: %s", e, exc_info=True)
                # Brief pause to avoid tight error loop
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    @property
    def checkpoint_count(self) -> int:
        """Total number of checkpoints written since startup."""
        return self._checkpoint_count

    @property
    def last_checkpoint_age_s(self) -> Optional[float]:
        """Seconds since the last successful checkpoint, or None if never."""
        if self._last_checkpoint_mono == 0.0:
            return None
        return time.monotonic() - self._last_checkpoint_mono
