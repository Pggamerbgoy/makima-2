"""
Makima v7.2 — STT Post-Processor (Elite Advanced)

Zero-latency text normalizer that runs AFTER Whisper transcription,
BEFORE intent classification. Strictly fixes known Whisper STT errors.
Does NOT perform general vocabulary fuzzy matching or intent classification.

Architecture & Design Rules:
  1. Hot-reload `hard_map` and `number_words` from configs/stt_corrections.json
     using os.stat() mtime checking. No fuzzy vocab. No general word matching.
  2. Apply exact substring replacements (longest-first, case-insensitive).
  3. Convert number words to digits based strictly on config.
  4. Sliding window N-gram deduplication (removes Whisper stutter/repeats).
  5. Hallucination detection (flags empty/repeated single-token outputs).
  6. Hinglish code-switch detection (Devanagari + Latin script flag).
  7. Returns an immutable NormalizedResult dataclass.
  
Performance: Thread-safe, zero I/O on hot path (metadata cached), pure functions.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("makima.stt_postprocessor")

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NormalizedResult:
    """
    Immutable result object returned by the post-processor.
    
    Attributes:
        text: The cleaned, normalized transcript.
        is_hallucination: True if the input was detected as Whisper garbage.
        is_hinglish: True if both Devanagari and Latin scripts are present.
        corrections_applied: List of (wrong, correct) tuples from hard_map.
    """
    text: str
    is_hallucination: bool
    is_hinglish: bool
    corrections_applied: list[tuple[str, str]] = field(default_factory=list)

# ---------------------------------------------------------------------------
# Configuration Hot-Reload Cache
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path("configs/stt_corrections.json")

class _ConfigCache:
    """Thread-safe, mtime-based hot-reload cache for STT corrections."""
    
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mtime: float = 0.0
        self._hard_map: list[tuple[str, str]] = []
        self._number_words: dict[str, str] = {}
        self._number_pattern: re.Pattern[str] | None = None
        self._fallback_logged = False

    def _load_from_disk(self) -> None:
        """Reads and parses the JSON config only if mtime has changed."""
        try:
            if not _CONFIG_PATH.exists():
                if not self._fallback_logged:
                    logger.warning(
                        "STT corrections config not found at %s. Using empty fallback.", 
                        _CONFIG_PATH
                    )
                    self._fallback_logged = True
                return

            # Zero I/O on hot path: os.stat is a lightweight VFS metadata call
            current_mtime = _CONFIG_PATH.stat().st_mtime
            if current_mtime == self._mtime:
                return

            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)

            # Parse hard_map (sorted longest-first for greedy substring matching)
            raw_map = data.get("hard_map", [])
            self._hard_map = sorted(
                [(str(k).lower(), str(v).lower()) for k, v in raw_map],
                key=lambda x: -len(x[0])
            )

            # Parse number_words and compile a single regex pattern
            raw_nums = data.get("number_words", {})
            self._number_words = {str(k).lower(): str(v) for k, v in raw_nums.items()}
            
            if self._number_words:
                words = sorted(self._number_words.keys(), key=len, reverse=True)
                pattern_str = r"\b(" + "|".join(re.escape(w) for w in words) + r")\b"
                self._number_pattern = re.compile(pattern_str, re.IGNORECASE)
            else:
                self._number_pattern = None

            self._mtime = current_mtime
            logger.debug("Reloaded STT corrections config (mtime: %.2f)", current_mtime)

        except Exception as e:
            logger.error("Failed to parse STT corrections config: %s", e)

    def get_config(self) -> tuple[list[tuple[str, str]], dict[str, str], re.Pattern[str] | None]:
        """Thread-safe accessor for the cached configuration."""
        with self._lock:
            self._load_from_disk()
            return self._hard_map, self._number_words, self._number_pattern

_cache = _ConfigCache()

# ---------------------------------------------------------------------------
# Hallucination & Script Detection Constants
# ---------------------------------------------------------------------------

_HALLUCINATION_STRINGS: frozenset[str] = frozenset({
    "thank you.", "thanks for watching.", "thank you for watching.",
    "please subscribe.", "like and subscribe.", "...", "you", "the", "i", "a",
    "thank you", "thanks for watching", "please subscribe", "like and subscribe",
    "byee", "bye bye", "hmm", "um"
})

# Unicode ranges for script detection
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_LATIN_RE = re.compile(r"[a-zA-Z]")

# ---------------------------------------------------------------------------
# Pure Pipeline Functions
# ---------------------------------------------------------------------------

def _detect_hallucination(text: str) -> bool:
    """Flags empty, pure punctuation, or repeated single-token outputs."""
    stripped = text.strip().lower()
    if not stripped or len(stripped) < 2:
        return True
    if stripped in _HALLUCINATION_STRINGS:
        return True
    
    # Check for repeated single short tokens (e.g., "the the the", "you you")
    words = stripped.split()
    if len(words) >= 2 and len(set(words)) == 1 and len(words[0]) <= 4:
        return True
        
    return False

def _detect_hinglish(text: str) -> bool:
    """Detects code-switching between Devanagari and Latin scripts."""
    return bool(_DEVANAGARI_RE.search(text)) and bool(_LATIN_RE.search(text))

def _apply_hard_map(
    text: str, 
    hard_map: list[tuple[str, str]]
) -> tuple[str, list[tuple[str, str]]]:
    """Applies exact substring replacements (case-insensitive)."""
    corrections: list[tuple[str, str]] = []
    text_lower = text.lower()
    
    for wrong, correct in hard_map:
        if wrong in text_lower:
            text_lower = text_lower.replace(wrong, correct)
            corrections.append((wrong, correct))
            
    return text_lower, corrections

def _apply_number_words(
    text: str, 
    pattern: re.Pattern[str] | None, 
    num_map: dict[str, str]
) -> str:
    """Converts number words to digits based on configured mapping."""
    if not pattern:
        return text
        
    def _replacer(match: re.Match[str]) -> str:
        return num_map.get(match.group(1).lower(), match.group(0))
        
    return pattern.sub(_replacer, text)

def _deduplicate_ngrams(text: str) -> str:
    """
    Sliding window N-gram deduplication.
    Removes repeated consecutive words/phrases (e.g., 'play play play' -> 'play',
    'turn on turn on' -> 'turn on').
    """
    words = text.split()
    if len(words) < 2:
        return text
        
    # Check N-grams from size 3 down to 1.
    # Checking larger N first prevents partial overlaps from breaking larger repeats.
    for n in range(3, 0, -1):
        i = 0
        while i <= len(words) - 2 * n:
            chunk_a = words[i:i+n]
            chunk_b = words[i+n:i+2*n]
            if chunk_a == chunk_b:
                # Found a duplicate chunk, remove the second one in-place
                del words[i+n:i+2*n]
                # Don't increment i, check the same position again for triple repeats
            else:
                i += 1
                
    return " ".join(words)

def _strip_trailing_punctuation(text: str) -> str:
    """Removes trailing periods, commas, etc., that Whisper hallucinates."""
    return re.sub(r"[.,!?;:…]+$", "", text).strip()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(raw_transcript: str) -> NormalizedResult:
    """
    Full STT post-processing pipeline.
    
    Args:
        raw_transcript: Raw text output from Whisper STT.
        
    Returns:
        NormalizedResult containing cleaned text and metadata flags.
    """
    # 1. Early exit for empty input
    if not raw_transcript or not raw_transcript.strip():
        return NormalizedResult(
            text="", 
            is_hallucination=True, 
            is_hinglish=False, 
            corrections_applied=[]
        )

    text = raw_transcript.strip()
    
    # 2. Hallucination Detection (pre-transform)
    is_hallucination = _detect_hallucination(text)
    if is_hallucination:
        logger.debug("Discarded hallucination: %r", text[:60])
        return NormalizedResult(
            text="", 
            is_hallucination=True, 
            is_hinglish=False, 
            corrections_applied=[]
        )

    # 3. Hinglish Detection (pre-transform, needs original casing/scripts)
    is_hinglish = _detect_hinglish(text)

    # 4. Load Config (Zero I/O on hot path if mtime unchanged)
    hard_map, num_map, num_pattern = _cache.get_config()

    # 5. Strip trailing punctuation
    text = _strip_trailing_punctuation(text)

    # 6. Hard Map Replacements
    text, corrections = _apply_hard_map(text, hard_map)

    # 7. Number Words Conversion
    text = _apply_number_words(text, num_pattern, num_map)

    # 8. N-gram Deduplication (fixes Whisper stuttering)
    text = _deduplicate_ngrams(text)

    # 9. Final whitespace collapse
    text = re.sub(r"\s+", " ", text).strip()

    # 10. Post-transform hallucination check (if dedup reduced it to nothing)
    if not text or _detect_hallucination(text):
        is_hallucination = True
        text = ""

    return NormalizedResult(
        text=text,
        is_hallucination=is_hallucination,
        is_hinglish=is_hinglish,
        corrections_applied=corrections
    )
