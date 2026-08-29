"""Endpointing hangover — natural speech decay vs hard cut (directive 2026-08-29).

Problem: speech termination was binary — energy goes high → zero and
SPEECH_ENDED fires after the (adaptive) silence window. Two very different
real-world profiles hit the same window:

  - NATURAL DECAY: trailing syllable fades gradually → normal window is right
  - HARD CUT: energy drops from near-peak to zero instantly (mic/codec glitch,
    plosive clipping, BT hiccup) → utterance may actually continue; ending at
    the base window fragments it

This module classifies the profile at the speech→silence transition and, for
hard cuts only, extends the silence window by a bounded hangover. If speech
resumes during the hangover, the utterance simply continues (no SPEECH_ENDED
was emitted, no premature-resume penalty). Latency for natural speech is
unchanged — the extension applies only to the hard-cut profile.

Pure numpy, deterministic. Consumed by providers/vad.py.
"""
from __future__ import annotations

from collections import deque

GRADUAL = "gradual"
HARD_CUT = "hard_cut"


class HangoverTracker:
    """Tracks per-frame speech energy across the current utterance and
    classifies the speech→silence transition."""

    def __init__(self, hangover_ms: int = 250, hard_cut_ratio: float = 0.6,
                 lookback_frames: int = 4):
        self.hangover_ms = int(hangover_ms)
        self.hard_cut_ratio = float(hard_cut_ratio)
        self._recent = deque(maxlen=lookback_frames)
        self._utt_peak = 0

    def reset(self) -> None:
        """New utterance starts."""
        self._recent.clear()
        self._utt_peak = 0

    def note_speech_frame(self, frame_peak: int) -> None:
        """Call for every frame classified as speech."""
        p = int(abs(frame_peak))
        self._recent.append(p)
        if p > self._utt_peak:
            self._utt_peak = p

    def evaluate(self) -> tuple[str, int]:
        """Call once, at the speech→silence transition.

        Returns (profile, hangover_ms). Hard cut = recent speech energy was
        still >= hard_cut_ratio of the utterance peak when it vanished."""
        if not self._recent or self._utt_peak <= 0:
            return GRADUAL, 0
        recent_peak = max(self._recent)
        ratio = recent_peak / self._utt_peak
        if ratio >= self.hard_cut_ratio:
            return HARD_CUT, self.hangover_ms
        return GRADUAL, 0
