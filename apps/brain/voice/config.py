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
from typing import Any, Dict, List, Optional

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
class NoiseGateConfig:
    """Adaptive noise gate calibration."""
    calibration_window_s: float = 3.0
    rolling_window_size: int = 50
    percentile: int = 90


@dataclass
class TTSConfig:
    """TTS engine settings."""
    engine: str = "gemini_live"
    priority_urgent: int = 0
    priority_normal: int = 5
    queue_maxsize: int = 20
    cancel_on_ptt: bool = True


@dataclass
class ConfidenceConfig:
    """STT confidence thresholds."""
    high_threshold: float = 0.7
    low_threshold: float = 0.3
    auto_confirm_timeout_s: float = 10.0
    auto_confirm_min_confidence: float = 0.5


@dataclass
class WakeDaemonConfig:
    """Always-on wake word daemon settings."""
    enabled: bool = True
    model: str = "hey_makima"
    threshold: float = 0.6
    refractory_s: float = 2.0
    device_index: Optional[int] = None


@dataclass
class LanguageHints:
    """Language detection hints."""
    default: str = "auto"
    devanagari_threshold: float = 0.3
    latin_only: str = "en"
    devanagari_dominant: str = "hi"
    mixed: str = "auto"


@dataclass
class VoiceConfig:
    """
    Complete voice subsystem configuration.
    Mirrors configs/voice_config.json with typed access.
    """
    wake_phrases: List[str] = field(default_factory=lambda: [
        "hey makima", "makima", "aye makima", "ok makima", "hello makima",
    ])
    wake_fuzzy_threshold: float = 0.75

    language_hints: LanguageHints = field(default_factory=LanguageHints)
    vad: VADConfig = field(default_factory=VADConfig)
    noise_gate: NoiseGateConfig = field(default_factory=NoiseGateConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    confidence: ConfidenceConfig = field(default_factory=ConfidenceConfig)
    wake_daemon: WakeDaemonConfig = field(default_factory=WakeDaemonConfig)
    gemini_model: str = "gemini-3.1-flash-live-preview"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "VoiceConfig":
        """Load configuration from JSON file, falling back to defaults for missing keys."""
        config_path = path or _DEFAULT_CONFIG_PATH
        raw: Dict[str, Any] = {}
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
            wake_fuzzy_threshold=float(raw.get("wake_fuzzy_threshold", 0.75)),
            language_hints=cls._load_sub(LanguageHints, raw.get("language_hints", {})),
            vad=cls._load_sub(VADConfig, raw.get("vad", {})),
            noise_gate=cls._load_sub(NoiseGateConfig, raw.get("noise_gate", {})),
            tts=cls._load_sub(TTSConfig, raw.get("tts", {})),
            confidence=cls._load_sub(ConfidenceConfig, raw.get("confidence", {})),
            wake_daemon=cls._load_sub(WakeDaemonConfig, raw.get("wake_daemon", {})),
            gemini_model=raw.get("gemini_model", "gemini-3.1-flash-live-preview"),
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

    def reload(self, path: Optional[Path] = None) -> "VoiceConfig":
        """Hot-reload from disk and return a fresh instance."""
        return self.load(path)
