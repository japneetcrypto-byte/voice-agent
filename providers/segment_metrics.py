"""Segment-metric aggregation for Whisper transcripts — PURE module.

Root-cause fix (task 2026-08-30): reading only segments[0] meant a quiet
lead-in / pre-roll noise segment with no_speech_prob≈0.9 could reject a
clearly-spoken turn as `high_no_speech_prob`. Whisper emits per-segment
confidence; the TURN-level signal must aggregate:

  no_speech_prob   — MIN across segments: if ANY segment is clearly
                     speech, the utterance contained speech.
  avg_logprob      — duration-weighted mean: a short garbled segment must
                     not dominate a longer clean one.
  compression_ratio— duration-weighted mean.

Handles Groq verbose_json segments (dicts) and faster-whisper objects.
No heavy dependencies (numpy not required) so the acceptance gates are
unit-testable in any environment.
"""
from __future__ import annotations


def _seg_field(seg, name: str, default=None):
    if isinstance(seg, dict):
        return seg.get(name, default)
    return getattr(seg, name, default)


def aggregate_segments(segments) -> tuple[float | None, float | None, float | None]:
    """Return (min_no_speech_prob, weighted_avg_logprob, weighted_compression)."""
    if not segments:
        return None, None, None
    min_nsp: float | None = None
    w_lp, w_cr, total_w = 0.0, 0.0, 0.0
    for seg in segments:
        start = _seg_field(seg, "start", 0.0) or 0.0
        end = _seg_field(seg, "end", 0.0) or 0.0
        nsp = _seg_field(seg, "no_speech_prob", None)
        lp = _seg_field(seg, "avg_logprob", None)
        cr = _seg_field(seg, "compression_ratio", None)
        w = max(float(end) - float(start), 0.0)
        if nsp is not None:
            min_nsp = float(nsp) if min_nsp is None else min(min_nsp, float(nsp))
        if lp is not None:
            w_lp += float(lp) * w
            w_cr += (float(cr) if cr is not None else 0.0) * w
            total_w += w
    avg_lp = (w_lp / total_w) if total_w > 0 else None
    avg_cr = (w_cr / total_w) if total_w > 0 else None
    return min_nsp, avg_lp, avg_cr
