"""STT turn validation + turn classification — PURE module (no livekit deps).

Extracted from agent/main.py (2026-08-30) so the acceptance rules are
unit-testable in isolation. Behavior-preserving extraction EXCEPT the
task-signed changes:

  1. Suspicious no-speech band (sign-off CA6): Whisper ~50/50 on whether
     this is speech -> never reaches a substantive LLM answer. The
     transcript is REJECTED with reason `suspicious_no_speech_band`;
     route_transcript then bounds it (>=4 words -> contextual_recovery
     with checkpoint policy; otherwise -> clarify).
  2. Multi-token filler ("hm hm acha", "haan theek") now classifies as
     `backchannel` so the deterministic backchannel policy answers with a
     1-3 word line instead of a substantive LLM call on junk.
"""
from __future__ import annotations

import re
from collections import Counter

# Exact-token turn-taking flags (same class as the hallucination blacklist).
# Consumed by classify_turn_relation and the ack bridge as structured signals.
BACKCHANNEL_TOKENS = {"haan", "han", "hmm", "hm", "hmmm", "okay", "ok", "accha", "achha",
                      "acha", "phir", "bol", "yeah", "yes", "theek", "thik", "hai", "hain",
                      "हाँ", "हम्म", "अच्छा", "ठीक"}
LISTEN_REQUEST_TOKENS = {"chup", "chupchup", "suno", "suno_bas", "bassuno", "pehlemeribaatsun",
                         "beechmeinmatbolo", "chupraho", "meribaatsun", "pehlesunomera"}

# Whisper noise-hallucination exact matches (short confabulations Whisper
# emits on silence/noise). Exact match only — never fuzzy.
_HALLUCINATIONS = {"i am good.", "i am good", "thank you.", "thanks for watching.", "subscribe."}

_WORD_RE = re.compile(r"[\w\u0900-\u097F]+", re.UNICODE)
_NORM_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)


def normalize_for_classify(text: str) -> str:
    """Lowercase, strip punctuation, remove whitespace (legacy behavior)."""
    norm = _NORM_RE.sub("", (text or "").lower()).strip()
    return _WS_RE.sub("", norm)


def classify_turn_relation(transcript_text: str) -> str:
    """Exact-token classification of the normalized transcript.

    Returns one of: listen_request / backchannel / content / empty.

    Extension (task 2026-08-30): a multi-token string whose tokens are ALL
    backchannel fillers ("hm hm acha", "haan theek hai") is a backchannel —
    previously it fell through to 'content' and could trigger a substantive
    LLM answer on junk ("hm hm acha" with confident no_speech_prob).
    """
    norm = normalize_for_classify(transcript_text)
    if not norm:
        return "empty"
    if any(tok in norm for tok in LISTEN_REQUEST_TOKENS):
        return "listen_request"
    if norm in BACKCHANNEL_TOKENS or norm in {"bas", "hmmhaan", "haanhmm"}:
        return "backchannel"
    # Multi-token filler: every word is a backchannel token.
    words = _WORD_RE.findall(transcript_text or "")
    if words and all(w.lower() in BACKCHANNEL_TOKENS for w in words):
        return "backchannel"
    return "content"


def is_repetition_loop(transcript_text: str) -> bool:
    """Deterministic Whisper degeneration detector (evidence 2026-08-27):
    'ake ake ake ake' — same token repeated >=4x consecutively, or one
    token dominating the transcript."""
    words = _WORD_RE.findall(transcript_text or "")
    if len(words) >= 4:
        run, prev = 1, None
        for w in words:
            lw = w.lower()
            run = run + 1 if lw == prev else 1
            prev = lw
            if run >= 4 and len(prev) >= 2:
                return True
        top, n = Counter(w.lower() for w in words).most_common(1)[0]
        if n >= 3 and len(top) >= 2 and n / len(words) >= 0.5:
            return True
    return False


def validate_transcript(transcript, speech_duration_ms: float | None = None,
                        no_speech_threshold: float = 0.6,
                        suspicious_nsp_min: float = 0.5,
                        avg_logprob_threshold: float = -1.0,
                        catastrophic_logprob: float = -1.2) -> tuple[bool, str]:
    """Acceptance gate for a transcribed user turn.

    Returns (is_valid, reason). Reasons:
      empty_transcript / known_hallucination_pattern / punctuation_only /
      high_no_speech_prob / suspicious_no_speech_band / low_avg_logprob /
      catastrophic_low_confidence / accepted

    sign-off CA6: the suspicious band (suspicious_nsp_min <= nsp <
    no_speech_threshold) is a REJECTION — an uncertain transcript never
    reaches a substantive LLM answer; route_transcript bounds it.
    """
    text = (getattr(transcript, "text", "") or "").strip()
    if not text:
        return False, "empty_transcript"

    lower_text = text.lower()
    if any(lower_text == h for h in _HALLUCINATIONS):
        return False, "known_hallucination_pattern"

    # Punctuation/symbols only (common Whisper noise hallucination).
    if not re.search(r"[a-zA-Z0-9\u0900-\u097F]", text):
        return False, "punctuation_only"

    nsp = getattr(transcript, "no_speech_prob", None)
    if nsp is not None and nsp > no_speech_threshold:
        return False, "high_no_speech_prob"
    if nsp is not None and nsp >= suspicious_nsp_min:
        return False, "suspicious_no_speech_band"

    lp = getattr(transcript, "avg_logprob", None)
    # Order matters (bug found 2026-08-30): with catastrophic=-0.85 and
    # low=-1.0, the low branch was UNREACHABLE (any lp < -1.0 is also
    # < -0.85). Catastrophic is now the LOWEST bound: < -1.2 -> deterministic
    # clarify (no LLM); [-1.2, -1.0) -> low -> route_transcript bounds it
    # (contextual_recovery if meaningful, else clarify).
    if lp is not None and lp < catastrophic_logprob:
        return False, "catastrophic_low_confidence"
    if lp is not None and lp < avg_logprob_threshold:
        return False, "low_avg_logprob"

    return True, "accepted"
