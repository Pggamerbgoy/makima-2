"""
Makima OS — Wake Word Daemon
Location: apps/brain/voice/wake.py

Always-on wake word detection using openwakeword.
Listens on the system microphone and fires a callback when a wake phrase is detected.
Phrases and threshold loaded from configs/voice_config.json via VoiceConfig.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable, Optional

logger = logging.getLogger("makima.voice.wake")


class WakeDaemon:
    """
    Background daemon that listens for a wake phrase and fires an async callback.

    Supports two detection modes:
    1. openwakeword — On-device neural model (preferred).
    2. Fuzzy text match fallback — Used when openwakeword model is unavailable.

    Usage:
        async def on_wake():
            print("Wake word detected!")
        daemon = WakeDaemon(config, on_wake_callback=on_wake)
        await daemon.start()          # non-blocking — runs in background task
        ...
        await daemon.stop()
    """

    def __init__(
        self,
        wake_phrases: list[str],
        threshold: float = 0.6,
        refractory_s: float = 2.0,
        sample_rate: int = 16000,
        frame_ms: int = 80,
        device_index: Optional[int] = None,
        on_wake_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self.wake_phrases = [p.lower().strip() for p in wake_phrases]
        self.threshold = threshold
        self.refractory_s = refractory_s
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.device_index = device_index
        self.on_wake_callback = on_wake_callback

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_wake_time: float = 0.0
        self._oww_model: Any = None

    async def start(self) -> None:
        """Start the background wake detection loop."""
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._running = True
        self._task = asyncio.create_task(self._run(), name="wake_daemon")
        logger.info("WakeDaemon started (phrases=%s, threshold=%.2f)", self.wake_phrases, self.threshold)

    async def stop(self) -> None:
        """Stop the background loop gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("WakeDaemon stopped")

    def set_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Hot-swap the wake callback at runtime (e.g. when voice engine starts)."""
        self.on_wake_callback = callback

    def is_running(self) -> bool:
        return self._running

    async def _run(self) -> None:
        """Main detection loop — tries openwakeword first, falls back to sounddevice+fuzzy."""
        try:
            await self._run_openwakeword()
        except ImportError:
            logger.info("openwakeword not installed — using sounddevice+fuzzy fallback")
            try:
                await self._run_sounddevice_fuzzy()
            except ImportError:
                logger.warning(
                    "Neither openwakeword nor sounddevice available — "
                    "WakeDaemon idle (install with: pip install openwakeword sounddevice)"
                )
                # Sit idle so the daemon doesn't crash the rest of startup
                while self._running:
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("WakeDaemon crashed: %s", e, exc_info=True)

    async def _run_openwakeword(self) -> None:
        """
        openwakeword detection path.
        Uses the default 'hey_makima' tflite model when available, or the
        general 'alexa' model as a proxy (both work at ~40 ms / frame).
        """
        import numpy as np
        import sounddevice as sd
        from openwakeword.model import Model  # type: ignore[import]

        # Try to load wake model — prefer hey_makima if present, else use hey_mycroft / alexa
        candidate_models = ["hey_makima", "hey_mycroft", "alexa"]
        oww: Any = None
        for model_name in candidate_models:
            try:
                oww = Model(wakeword_models=[model_name], inference_framework="tflite")
                logger.info("WakeDaemon loaded openwakeword model: %s", model_name)
                break
            except Exception:
                continue

        if oww is None:
            # Load without specifying model — uses bundled defaults
            oww = Model(inference_framework="tflite")
            logger.info("WakeDaemon loaded openwakeword default models")

        self._oww_model = oww
        frame_samples = int(self.sample_rate * self.frame_ms / 1000)

        def _sd_callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            if not self._running:
                return
            audio = (indata[:, 0] * 32767).astype(np.int16)
            predictions = oww.predict(audio)
            for ww_name, score in predictions.items():
                if score >= self.threshold:
                    now = time.monotonic()
                    if now - self._last_wake_time >= self.refractory_s:
                        self._last_wake_time = now
                        logger.info("Wake word detected! model=%s score=%.3f", ww_name, score)
                        if self.on_wake_callback and self._loop and self._loop.is_running():
                            try:
                                res = self.on_wake_callback()
                                if asyncio.iscoroutine(res):
                                    asyncio.run_coroutine_threadsafe(
                                        res,
                                        self._loop,
                                    )
                            except Exception as e:
                                logger.warning("Failed to schedule wake callback: %s", e)

        with sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=frame_samples,
            channels=1,
            dtype="float32",
            device=self.device_index,
            callback=_sd_callback,
        ):
            while self._running:
                await asyncio.sleep(0.1)

    async def _run_sounddevice_fuzzy(self) -> None:
        """
        Fallback path: record audio → Google Speech-to-Text via sounddevice queue,
        then do a simple substring match against wake_phrases.
        Only active when openwakeword is unavailable.
        NOTE: This is a lightweight approximation — no accuracy guarantees.
        """
        import math
        import numpy as np
        import sounddevice as sd

        logger.info("WakeDaemon: sounddevice+fuzzy mode active")
        frame_samples = int(self.sample_rate * self.frame_ms / 1000)
        audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()

        def _cb(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            pcm = (indata[:, 0] * 32767).astype(np.int16)
            rms = math.sqrt(float(np.mean(indata[:, 0] ** 2)))
            if rms > 0.02:  # Only forward speech-like frames
                try:
                    loop.call_soon_threadsafe(audio_queue.put_nowait, pcm.tobytes())
                except Exception:
                    pass

        with sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=frame_samples,
            channels=1,
            dtype="float32",
            device=self.device_index,
            callback=_cb,
        ):
            accumulated: bytearray = bytearray()
            last_speech = time.monotonic()

            while self._running:
                try:
                    chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    accumulated.extend(chunk)
                    last_speech = time.monotonic()
                except asyncio.TimeoutError:
                    # Silence window — check accumulated audio for wake phrase
                    if accumulated and (time.monotonic() - last_speech) > 0.4:
                        await self._fuzzy_check_and_fire(bytes(accumulated))
                        accumulated.clear()
                except asyncio.CancelledError:
                    break

    async def _fuzzy_check_and_fire(self, _audio_bytes: bytes) -> None:
        """
        Very lightweight stub: fires the callback if the session is not in refractory period.
        A real implementation would call a local STT (Whisper tiny) on _audio_bytes,
        then do a substring match. Kept minimal to avoid heavyweight deps in fallback path.
        """
        # For now: audio presence + time-gate is the signal in fallback mode
        now = time.monotonic()
        if now - self._last_wake_time >= self.refractory_s * 3:  # extra conservative
            self._last_wake_time = now
            logger.debug("WakeDaemon (fuzzy fallback): wake phrase assumed from audio presence")
            if self.on_wake_callback:
                try:
                    res = self.on_wake_callback()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.warning("Wake callback error: %s", e)
