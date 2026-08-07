"""Makima v7.1 — FocusProfiles

Spec:
- Load Work/Quiet/Meeting/Gaming presets from configs/focus_profiles.yaml
- Apply toggles quickly (<200ms)
- Switch applies next task only if interactive task running

This snapshot provides:
- loader
- apply_profile(profile_name) returning a dict of toggles
- does not integrate with native services yet (safe fallback)
"""

from __future__ import annotations

import os
import yaml
import time
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_PROFILE = {
    "wake_word_enabled": True,
    "screen_capture_enabled": True,
    "proactive_suggestions_enabled": True,
    "tts_speak_unrequested": True,
    "hud_level": "full",
    "cpu_priority": "normal",
    "notification_outbound": True,
    "notification_inbound": True,
    "clipboard_events": True,
}

FALLBACK_PROFILES: dict[str, dict] = {
    "work": dict(DEFAULT_PROFILE),
    "quiet": dict(DEFAULT_PROFILE, **{
        "wake_word_enabled": True,
        "proactive_suggestions_enabled": False,
        "tts_speak_unrequested": False,
        "hud_level": "minimal",
        "notification_outbound": False,
        "notification_inbound": False,
        "clipboard_events": False,
    }),
    "meeting": dict(DEFAULT_PROFILE, **{
        "wake_word_enabled": False,
        "screen_capture_enabled": False,
        "proactive_suggestions_enabled": False,
        "tts_speak_unrequested": False,
        "hud_level": "hidden",
        "cpu_priority": "low",
        "notification_outbound": False,
        "notification_inbound": False,
        "clipboard_events": False,
    }),
    "gaming": dict(DEFAULT_PROFILE, **{
        "wake_word_enabled": False,
        "screen_capture_enabled": False,
        "proactive_suggestions_enabled": False,
        "tts_speak_unrequested": False,
        "hud_level": "hidden",
        "cpu_priority": "low",
        "notification_outbound": False,
        "notification_inbound": False,
        "clipboard_events": False,
    }),
}


class FocusProfiles:
    def __init__(self, configs_dir: str | None = None):
        self.configs_dir = Path(configs_dir or Path(__file__).resolve().parents[2] / "configs")
        self._profiles: Dict[str, Any] = {}
        self.active_profile: str = "work"
        self.load()

    def load(self) -> None:
        path = self.configs_dir / "focus_profiles.yaml"
        if not path.exists():
            self._profiles = dict(FALLBACK_PROFILES)
            return

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        loaded = data.get("profiles", data) if isinstance(data, dict) else {}
        # Lowercase all profile names for case-insensitive lookup
        self._profiles = {k.lower(): v for k, v in loaded.items()}

    def _merge_profile(self, profile: dict) -> dict:
        """Merge a profile dict against DEFAULT_PROFILE so missing fields get defaults."""
        merged = dict(DEFAULT_PROFILE)
        merged.update(profile)
        return merged

    def apply_profile(self, name: str) -> Dict[str, Any]:
        profile = self._profiles.get(name.lower())
        if not profile:
            profile = self._profiles.get("work", {})
            self.active_profile = "work"
        else:
            self.active_profile = name.lower()
        return self._merge_profile(profile)

