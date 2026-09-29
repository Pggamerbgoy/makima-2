"""
Makima OS — Voice Configuration Loader
Location: apps/brain/voice/config.py

Strongly-typed dataclass mirroring configs/voice_config.json.
Hot-reloaded at runtime; single source of truth for all voice subsystem settings.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("makima.voice.config")

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "voice_config.json"


@dataclass
class VADConfig:
    """Voice Activity Detection parameters."""
    mode: int = 3
    frame_duration_ms: int = 30
    sample_rate: int = 16000
    silence_ratio_threshold: float = 0.85
    energy_fallback_multiplier: float = 1.5


@dataclass
class TTSConfig:
    """TTS engine settings — engine id only.

    NOTE 2026-09-26: queue/priority knobs removed (nothing consumed them;
    live audio streams straight through). Synthesis is Gemini-native.
    """
    engine: str = "gemini_live"


@dataclass
class WakeDaemonConfig:
    """Always-on wake word daemon settings."""
    enabled: bool = True
    model: str = "hey_makima"
    threshold: float = 0.6
    refractory_s: float = 2.0
    device_index: int | None = None


@dataclass
class LiveSessionConfig:
    """Gemini Live server-native features (no local re-implementation).

    These map 1:1 onto LiveConnectConfig fields — the model does the work,
    we only configure + consume. See engine._connect_and_stream.
    """
    vad_start_sensitivity: str = "high"   # high|low → server start-of-speech
    vad_end_sensitivity: str = "high"     # high|low → server end-of-speech
    prefix_padding_ms: int = 300
    silence_duration_ms: int = 600
    input_transcription: bool = True      # native STT (replaces local Whisper path)
    output_transcription: bool = True     # native model-output transcripts
    context_compression: bool = True      # sliding-window (audio ~25 tok/s)
    compression_trigger_tokens: int = 12000
    session_resumption: bool = True       # server resumption protocol
    reconnect_max_attempts: int = 10      # 0 = unlimited; explicit stop always exits
    reconnect_base_delay_s: float = 2.0   # exponential backoff base
    reconnect_max_delay_s: float = 30.0   # backoff cap
    receive_max_errors: int = 5           # consecutive transport errors before reconnect


# NOTE 2026-09-26: LanguageHints removed — zero consumers; multilingual
# handling is Gemini-native (model matches user language per system prompt).


@dataclass
class VoiceConfig:
    """
    Complete voice subsystem configuration.
    Mirrors configs/voice_config.json with typed access.
    """
    wake_phrases: list[str] = field(default_factory=lambda: [
        "hey makima", "makima", "aye makima", "ok makima", "hello makima",
    ])
    # NOTE 2026-09-26: wake_fuzzy_threshold/language_hints/noise_gate/
    # confidence removed — zero consumers (fuzzy path uses wake_daemon
    # threshold; language+denoise+STT are Gemini-native).
    vad: VADConfig = field(default_factory=VADConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    wake_daemon: WakeDaemonConfig = field(default_factory=WakeDaemonConfig)
    live: LiveSessionConfig = field(default_factory=LiveSessionConfig)
    gemini_model: str = "gemini-3.8-live"

    @classmethod
    def load(cls, path: Path | None = None) -> VoiceConfig:
        """Load configuration from JSON file, falling back to defaults for missing keys."""
        config_path = path or _DEFAULT_CONFIG_PATH
        raw: dict[str, Any] = {}
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    raw = json.load(f)
                logger.info("Loaded voice config from %s", config_path)
            except Exception as e:
                logger.warning("Failed to load voice config from %s: %s — using defaults", config_path, e)
        else:
            logger.info("No voice config found at %s — using defaults", config_path)

        return cls(
            wake_phrases=raw.get("wake_phrases") or [
                "hey makima", "makima", "aye makima", "ok makima", "hello makima",
            ],
            vad=cls._load_sub(VADConfig, raw.get("vad", {})),
            tts=cls._load_sub(TTSConfig, raw.get("tts", {})),
            wake_daemon=cls._load_sub(WakeDaemonConfig, raw.get("wake_daemon", {})),
            live=cls._load_sub(LiveSessionConfig, raw.get("live", {})),
            gemini_model=raw.get("gemini_model", "gemini-3.8-live"),
        )

    @staticmethod
    def _load_sub(cls_type: type, data: Any) -> Any:
        """Instantiate a sub-config dataclass from a dict, ignoring unknown keys."""
        if not isinstance(data, dict):
            return cls_type()
        import dataclasses
        valid_fields = {f.name for f in dataclasses.fields(cls_type)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls_type(**filtered)

    def reload(self, path: Path | None = None) -> VoiceConfig:
        """Hot-reload from disk and return a fresh instance."""
        return self.load(path)
