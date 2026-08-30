"""TTS warm-up policy — PURE module (task 2026-08-30, CA3-approved add-on).

Why: Fish Audio's server warms the cloned-voice model per request. A tiny
background warmup request shortly AFTER a completed reply keeps the model
hot, so the NEXT turn's time-to-first-audio drops (Solana audit #4:
-200..-500ms on next-reply start). This is the non-provider lever for the
"next reply wait" — the stop path is fixed separately by barge-in reorder.

Conservative by design:
  - warm only after an idle gap (never during rapid exchange)
  - bounded calls per session (Fish free-tier quota!)
  - a warmup is only "fresh" for a limited window
Deterministic, no I/O. Consumed by providers/tts.py.
"""
from __future__ import annotations


class WarmupPolicy:
    def __init__(self, idle_gap_s: float = 15.0, max_per_session: int = 4,
                 warm_fresh_s: float = 60.0):
        self.idle_gap_s = idle_gap_s
        self.max_per_session = max_per_session
        self.warm_fresh_s = warm_fresh_s
        self._last_synth_end: float | None = None
        self._last_warm_at: float | None = None
        self._warm_calls = 0

    def note_synthesis_end(self, now: float) -> None:
        """Call after every completed (or cancelled) reply synthesis."""
        self._last_synth_end = now

    def should_warm(self, now: float) -> bool:
        """True when a background warmup is worthwhile right now."""
        if self._warm_calls >= self.max_per_session:
            return False
        if self._last_synth_end is None:
            return False
        # Warm only after an idle gap — no point mid-exchange.
        if now - self._last_synth_end < self.idle_gap_s:
            return False
        # Skip if a warmup within the freshness window already exists.
        if self.is_warm(now):
            return False
        return True

    def on_warmup_done(self, now: float) -> None:
        self._last_warm_at = now
        self._warm_calls += 1

    def is_warm(self, now: float) -> bool:
        """True when a warmup happened within the freshness window."""
        return (self._last_warm_at is not None
                and now - self._last_warm_at <= self.warm_fresh_s)
