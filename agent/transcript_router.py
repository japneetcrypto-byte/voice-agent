"""Transcript routing contract (P0 fix, directive 2026-08-29).

Problem (session 181237 turn 7): a meaningful 14-word transcript
('ठीक है चलना कब शुरू होगा यार...') was simultaneously marked
valid=False (high_no_speech_prob) AND processed as a normal LLM turn —
with no log marker explaining why. The old code had a silent fall-through.

Contract (explicit, testable):

  empty transcript                      -> acoustic_only (presence turn)
  repetition loop / catastrophic conf   -> clarify (deterministic)
  valid                                 -> normal (full fused path)
  invalid BUT context-recoverable       -> contextual_recovery
      (meaningful: >=4 words AND decent confidence) — fused LLM attempts
      interpretation; turn is explicitly marked, never silently routed
  invalid AND unusable                  -> clarify

Pure function; no livekit/LLM dependencies. Consumed by main.py.
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[\w\u0900-\u097F]+", re.UNICODE)

# Context-recoverable bar: enough words to carry meaning, confidence not
# catastrophic. Tuned on evidence: session 181237 turn 7 (14 words,
# logprob -0.137, rejected only by no_speech_prob — clearly real speech).
MIN_RECOVERY_WORDS = 4
MIN_RECOVERY_LOGPROB = -0.5

ACTIONS = ("acoustic_only", "clarify", "contextual_recovery", "normal")


def route_transcript(text: str, is_valid: bool, rejection_reason: str | None,
                     avg_logprob: float | None, is_repetition: bool = False,
                     is_catastrophic: bool = False) -> tuple[str, str]:
    """Return (action, reason). action ∈ ACTIONS."""
    t = (text or "").strip()
    if not t:
        return "acoustic_only", "empty_transcript"

    if is_repetition:
        return "clarify", "repetition_loop"
    if is_catastrophic:
        return "clarify", "catastrophic_low_confidence"

    if is_valid:
        return "normal", "accepted"

    n_words = len(_WORD_RE.findall(t))
    lp_ok = avg_logprob is None or avg_logprob >= MIN_RECOVERY_LOGPROB
    if n_words >= MIN_RECOVERY_WORDS and lp_ok:
        return "contextual_recovery", (
            f"invalid ({rejection_reason}) but meaningful: {n_words} words, "
            f"logprob {avg_logprob}")

    return "clarify", f"unusable ({rejection_reason}; {n_words} words, logprob {avg_logprob})"
