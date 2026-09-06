"""
Makima OS — Native Audio Capture
Location: apps/brain/voice/audio.py

Low-latency microphone capture via sounddevice.
Streams 16 kHz mono PCM frames with silence detection and VAD-like energy gating.
Runs in its own asyncio background task — callers receive frames via an async queue.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import math
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger("makima.voice.audio")


class NativeAudioCapture:
    """
    Persistent microphone capture with real-time silence/speech detection.

    Usage:
        cap = NativeAudioCapture(sample_rate=16000, frame_ms=30)
        await cap.start()
        async for frame in cap.frames():
            ...
        await cap.stop()
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        device_index: Optional[int] = None,
        silence_threshold: float = 0.85,
        energy_multiplier: float = 1.5,
        calibration_window_s: float = 3.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.device_index = device_index
        self.silence_threshold = silence_threshold
        self.energy_multiplier = energy_multiplier
        self.calibration_window_s = calibration_window_s

        self._frame_samples = int(sample_rate * frame_ms / 1000)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._stream: Any = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Adaptive noise floor
        self._noise_floor: float = 0.0
        self._calibrating = True
        self._calibration_frames: collections.deque[float] = collections.deque(
            maxlen=int(calibration_window_s * 1000 / frame_ms)
        )

        # DSP Filter & AGC State
        self._current_gain: float = 1.0
        self._sos: Any = None
        self._zi: Any = None
        self._vad: Any = None
        self._hangover_frames_remaining: int = 0
        self._init_dsp()

    def _init_dsp(self) -> None:
        """Initialize high-pass filter and WebRTC VAD."""
        try:
            import scipy.signal as signal
            self._sos = signal.butter(4, 85, btype='highpass', fs=self.sample_rate, output='sos')
            self._zi = signal.sosfilt_zi(self._sos)
        except Exception as e:
            logger.debug("scipy highpass filter init failed: %s", e)
            self._sos = None

        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(2)  # Mode 2: Aggressive ambient noise suppression
        except Exception as e:
            logger.debug("webrtcvad init failed: %s", e)
            self._vad = None

    async def start(self) -> None:
        """Open the microphone stream and begin capturing frames."""
        if self._running:
            return

        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice is not installed — native audio capture unavailable")
            return

        self._loop = asyncio.get_running_loop()
        self._running = True
        self._calibrating = True
        self._calibration_frames.clear()
        self._current_gain = 1.0

        def _callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            if status:
                logger.debug("Audio stream status: %s", status)
            if not self._running or self._loop is None or self._loop.is_closed():
                return

            raw_samples = indata[:, 0].copy()

            # 1. High-Pass Filter (removes rumble, desk vibrations, 50/60Hz AC hum)
            if self._sos is not None and self._zi is not None:
                try:
                    import scipy.signal as signal
                    filtered, self._zi = signal.sosfilt(self._sos, raw_samples, zi=self._zi)
                except Exception:
                    filtered = raw_samples
            else:
                filtered = raw_samples

            # 2. Compute RMS & Track Background Noise Floor
            rms = math.sqrt(float(np.mean(filtered ** 2)))
            self._update_noise_floor(rms)

            # 3. Speech Activity Detection with Hangover Hysteresis
            threshold = max(0.08, self._noise_floor * 1.35)
            is_above_threshold = (not self._calibrating) and (rms >= threshold)

            if is_above_threshold:
                self._hangover_frames_remaining = 8  # ~240ms hangover to preserve word endings
                is_active_speech = True
            elif self._hangover_frames_remaining > 0:
                self._hangover_frames_remaining -= 1
                is_active_speech = True
            else:
                is_active_speech = False

            # 4. Adaptive AGC & Dynamic Noise Gating (-26 dB noise floor attenuation)
            target_rms = 0.22
            if is_active_speech:
                # Faint speech boost: smooth ramp up to 4.5x gain
                desired_gain = min(4.5, max(1.0, target_rms / max(rms, 0.02)))
                self._current_gain = 0.80 * self._current_gain + 0.20 * desired_gain
                # Apply gain with smooth tanh soft-limiter (zero digital clipping)
                processed = np.tanh(filtered * self._current_gain) * 0.95
            else:
                # Non-speech / background noise: decay gain and apply 95% attenuation (-26 dB)
                self._current_gain = 0.90 * self._current_gain + 0.10 * 1.0
                processed = filtered * 0.05

            # Convert to 16-bit PCM bytes
            pcm_int16 = (np.clip(processed, -1.0, 1.0) * 32767).astype(np.int16)
            pcm_bytes = pcm_int16.tobytes()

            def _push() -> None:
                try:
                    self._queue.put_nowait(pcm_bytes)
                except asyncio.QueueFull:
                    pass  # Drop frame if consumer is too slow

            try:
                self._loop.call_soon_threadsafe(_push)
            except RuntimeError:
                pass

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self._frame_samples,
            channels=1,
            dtype="float32",
            device=self.device_index,
            callback=_callback,
        )
        self._stream.start()
        logger.info(
            "NativeAudioCapture started: %d Hz, %d ms frames, device=%s (DSP Denoise + AGC Active)",
            self.sample_rate, self.frame_ms,
            self.device_index if self.device_index is not None else "default",
        )

    async def stop(self) -> None:
        """Stop the microphone stream and release resources."""
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.debug("Error closing audio stream: %s", e)
            self._stream = None
        # Drain the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        logger.info("NativeAudioCapture stopped")

    async def frames(self):
        """Async generator yielding raw PCM frames as bytes."""
        while self._running:
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield frame
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def is_speech(self, rms: float) -> bool:
        """Return True if the given RMS energy is above the adaptive speech threshold."""
        if self._calibrating:
            return rms > 0.018  # Fixed threshold during calibration
        return rms > (self._noise_floor * self.energy_multiplier)

    def compute_rms(self, pcm_bytes: bytes) -> float:
        """Compute RMS energy from raw 16-bit PCM bytes."""
        if not pcm_bytes:
            return 0.0
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32767.0
        return float(math.sqrt(np.mean(samples ** 2)))

    @property
    def is_calibrated(self) -> bool:
        return not self._calibrating

    @property
    def running(self) -> bool:
        return self._running

    def _update_noise_floor(self, rms: float) -> None:
        """Update the adaptive noise floor during calibration and steady state."""
        self._calibration_frames.append(rms)
        if self._calibrating:
            if len(self._calibration_frames) >= self._calibration_frames.maxlen:
                sorted_values = sorted(self._calibration_frames)
                percentile_idx = int(len(sorted_values) * 0.90)
                self._noise_floor = sorted_values[min(percentile_idx, len(sorted_values) - 1)]
                self._calibrating = False
                logger.info("Audio noise floor calibrated: %.5f", self._noise_floor)
        else:
            # Slow-moving exponential update in steady state
            sorted_values = sorted(self._calibration_frames)
            percentile_idx = int(len(sorted_values) * 0.90)
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * sorted_values[
                min(percentile_idx, len(sorted_values) - 1)
            ]
