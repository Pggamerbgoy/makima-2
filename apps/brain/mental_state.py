"""
Makima OS v9.5 — User Mental State & Activity Sensing
Location: apps/brain/mental_state.py

Detects user cognitive load, work fatigue, stuck loops, and distraction states
from non-invasive OS telemetry (window switching velocity, session duration,
repeated file focus, idle periods, and battery levels). Zero keystroke logging.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("makima.mental_state")


class UserMentalState(str, Enum):
    """Canonical classification of user mental & activity state."""
    NORMAL = "normal"
    FOCUSED = "focused"
    OVERWHELMED = "overwhelmed"      # Rapid switching between apps/tabs (distraction/stress)
    STUCK = "stuck"                  # Repeatedly revisiting same file/window without progress
    FATIGUED = "fatigued"            # Continuous long-duration work or low battery exhaustion
    IDLE_BREAK = "idle_break"        # User away / idle for substantial period (>15-20 min)


@dataclass
class MentalStateSnapshot:
    """Immutable snapshot of the user's inferred state."""
    state: UserMentalState
    confidence: float
    rationale: str
    work_duration_m: float
    window_switches_last_min: int
    battery_percent: float
    is_plugged: bool
    recommended_action: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "confidence": round(self.confidence, 2),
            "rationale": self.rationale,
            "work_duration_m": round(self.work_duration_m, 1),
            "window_switches_last_min": self.window_switches_last_min,
            "battery_percent": self.battery_percent,
            "is_plugged": self.is_plugged,
            "recommended_action": self.recommended_action,
            "timestamp": self.timestamp,
        }


class MentalStateDetector:
    """
    Non-invasive, privacy-preserving detector inferring cognitive load
    from OS-level window transitions, duration timers, and hardware battery telemetry.
    """

    _instance: Optional["MentalStateDetector"] = None
    _init_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "MentalStateDetector":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self._session_start_mono: float = time.monotonic()
        self._last_user_activity_mono: float = time.monotonic()
        self._window_history: deque[tuple[float, str]] = deque(maxlen=200)  # (timestamp, title)
        self._last_seen_title: str = ""

        # Thresholds
        self.THRASHING_SWITCHES_PER_MIN: int = 8
        self.STUCK_REPETITION_COUNT: int = 5
        self.FATIGUE_WORK_MINUTES: float = 120.0  # 2 hours continuous
        self.IDLE_BREAK_SECONDS: float = 1200.0   # 20 minutes idle
        self.LOW_BATTERY_THRESHOLD: float = 20.0

    def record_activity(self, title: str = "") -> None:
        """Record explicit user interaction or window focus event."""
        now = time.monotonic()
        self._last_user_activity_mono = now
        clean_title = (title or "").strip()
        if clean_title and clean_title != self._last_seen_title:
            self._last_seen_title = clean_title
            self._window_history.append((now, clean_title))

    def detect_state(self) -> MentalStateSnapshot:
        """Infer current mental state from OS telemetry and timers."""
        now = time.monotonic()
        from .agents.os_state import get_os_state
        os_provider = get_os_state()

        # Update latest foreground window if available
        fg_win = os_provider.get_foreground_window()
        if fg_win and fg_win != self._last_seen_title:
            self.record_activity(fg_win)

        # Retrieve real battery status and session duration once for all branches
        bat = os_provider.get_battery_status()
        work_m = (now - self._session_start_mono) / 60.0
        bat_pct = float(bat.get("percent", 100.0))
        is_plugged = bool(bat.get("power_plugged", True))

        # 1. Idle break detection
        idle_duration = now - self._last_user_activity_mono
        if idle_duration >= self.IDLE_BREAK_SECONDS:
            return MentalStateSnapshot(
                state=UserMentalState.IDLE_BREAK,
                confidence=0.88,
                rationale=f"User inactive for {int(idle_duration // 60)} minutes.",
                work_duration_m=work_m,
                window_switches_last_min=0,
                battery_percent=bat_pct,
                is_plugged=is_plugged,
                recommended_action="Break detected. Suppress non-critical proactive interrupts.",
            )

        # 2. Window switching velocity (thrashing / overwhelmed)
        transitions = os_provider.get_window_transitions(window_s=60.0)
        switches_last_min = len(transitions)

        if switches_last_min >= self.THRASHING_SWITCHES_PER_MIN:
            return MentalStateSnapshot(
                state=UserMentalState.OVERWHELMED,
                confidence=0.85,
                rationale=f"Rapid app switching detected: {switches_last_min} window switches in the last 60s.",
                work_duration_m=work_m,
                window_switches_last_min=switches_last_min,
                battery_percent=bat_pct,
                is_plugged=is_plugged,
                recommended_action="Proactively offer to organize open windows or clean workspace.",
            )

        # 3. Repeated window visits (stuck in loop)
        recent_5m = [win for t, win in self._window_history if now - t <= 300.0]
        if recent_5m:
            counts = Counter(recent_5m)
            most_common_win, freq = counts.most_common(1)[0]
            if freq >= self.STUCK_REPETITION_COUNT and len(counts) >= 2:
                return MentalStateSnapshot(
                    state=UserMentalState.STUCK,
                    confidence=0.82,
                    rationale=f"Window '{most_common_win[:40]}' focused {freq} times in 5 minutes.",
                    work_duration_m=work_m,
                    window_switches_last_min=switches_last_min,
                    battery_percent=bat_pct,
                    is_plugged=is_plugged,
                    recommended_action="Ask if the user is stuck on this task or wants relevant docs/logs pulled.",
                )

        # 4. Battery & Work Session Duration (Fatigue)
        if (work_m >= self.FATIGUE_WORK_MINUTES and bat_pct <= self.LOW_BATTERY_THRESHOLD and not is_plugged) or (work_m >= 180.0):
            return MentalStateSnapshot(
                state=UserMentalState.FATIGUED,
                confidence=0.90,
                rationale=f"Continuous work for {int(work_m)}m (battery: {bat_pct}%, plugged: {is_plugged}).",
                work_duration_m=work_m,
                window_switches_last_min=switches_last_min,
                battery_percent=bat_pct,
                is_plugged=is_plugged,
                recommended_action="Suggest plugging in charger, taking a pomodoro break, and saving backup.",
            )

        # 5. Deep focus
        if switches_last_min <= 2 and work_m >= 25.0:
            return MentalStateSnapshot(
                state=UserMentalState.FOCUSED,
                confidence=0.80,
                rationale=f"Deep work state: low app switching with {int(work_m)}m active session.",
                work_duration_m=work_m,
                window_switches_last_min=switches_last_min,
                battery_percent=bat_pct,
                is_plugged=is_plugged,
                recommended_action="Maintain zero distractions; suppress proactive notifications.",
            )

        # 6. Default normal state
        return MentalStateSnapshot(
            state=UserMentalState.NORMAL,
            confidence=0.75,
            rationale="Normal workstation activity.",
            work_duration_m=work_m,
            window_switches_last_min=switches_last_min,
            battery_percent=bat_pct,
            is_plugged=is_plugged,
            recommended_action=None,
        )

    def get_prompt_context(self) -> str:
        """Format mental state for agent system prompt context injection."""
        snapshot = self.detect_state()
        lines = [
            "[USER MENTAL & WORKSTATION STATE]",
            f"- Cognitive/Work State: {snapshot.state.value.upper()} (Confidence: {snapshot.confidence:.2f})",
            f"- Session Duration: {int(snapshot.work_duration_m)} minutes",
            f"- App Switching Velocity: {snapshot.window_switches_last_min} switches/min",
            f"- Battery: {snapshot.battery_percent}% ({'Plugged in' if snapshot.is_plugged else 'On Battery'})",
            f"- Assessment: {snapshot.rationale}",
        ]
        if snapshot.recommended_action:
            lines.append(f"- Recommended Makima Attitude: {snapshot.recommended_action}")
        return "\n".join(lines)


def get_mental_state_detector() -> MentalStateDetector:
    return MentalStateDetector()
