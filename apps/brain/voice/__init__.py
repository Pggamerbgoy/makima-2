"""
Makima OS — Voice Subsystem Package
Location: apps/brain/voice/__init__.py

Public exports for the voice subsystem clean rebuild.
"""

from .config import VoiceConfig
from .engine import VoiceEngine
# NOTE: NativeAudioCapture (audio.py) removed 2026-09-26 — orphaned server-mic
# DSP that nothing consumed; mic audio arrives from the browser over WS and
# VAD/STT are Gemini-native. WakeDaemon keeps its own capture stream.
from .wake import WakeDaemon

__all__ = [
    "VoiceConfig",
    "VoiceEngine",
    "WakeDaemon",
]
