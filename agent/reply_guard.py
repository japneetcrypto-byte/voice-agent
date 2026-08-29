"""Deterministic reply-side guards (Phase 5 behavioral tuning).

Two jobs, both ZERO-LLM and deterministic (same discipline as the turn
controller and validity gates):

1. trim_reply(text)        — hard safety net for reply length. The persona
   (C2) already asks for <=2 short sentences, but live sessions (2026-08-28,
   sessions 222656/224509) still produced 5-7.2s TTS replies. The prompt fix
   is the primary lever; this guard only catches pathological monologues
   (> REPLY_MAX_CHARS) by cutting at the last clean sentence boundary.

2. feminine_self_reference(text) — telemetry-only detector for the known
   persona violation (masculine self-reference). It does NOT rewrite the
   reply: a regex cannot reliably tell self-reference from third-person
   references to a female ("Rimmi so rahi thi" is correct speech), and an
   LLM rewrite pass is forbidden by the one-call contract. It only flags the
   turn in the session log so the prompt change can be measured.
"""
from __future__ import annotations

import re

# Safety-net cap: ~220 chars is roughly 12-15s of Hinglish TTS — far above
# the persona's target (4-12 words) but low enough to stop 20s monologues.
REPLY_MAX_CHARS = 220

# Sentence end: Latin . ! ? or Devanagari danda, optionally followed by a
# closing quote/bracket, then whitespace or end-of-string.
SENT_END_RE = re.compile(r"[.!?।]+[\"')\]]*(\s+|$)")


def trim_reply(text: str, max_chars: int = REPLY_MAX_CHARS) -> tuple[str, bool]:
    """Return (spoken_text, trimmed). Cuts at the last sentence boundary that
    fits under max_chars; if the text has no boundary at all and exceeds the
    cap, cuts at the last word boundary under the cap."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text, False

    kept = ""
    for m in SENT_END_RE.finditer(text):
        sentence = text[:m.end()].rstrip()
        if len(sentence) > max_chars:
            break
        kept = sentence
    if kept:
        return kept, True

    # No usable sentence boundary — hard cut at last space.
    cut = text[:max_chars]
    sp = cut.rfind(" ")
    if sp > 40:
        return cut[:sp].rstrip(), True
    return cut.rstrip(), True


# Feminine FIRST-PERSON forms, telemetry-only. Precision rules:
#   - "rahi hoon / hain", "gayi hoon", future "-ungi" (rahungi, jaungi,
#     bataungi...) are ALWAYS first person -> flag unconditionally.
#   - "rahi thi" is ambiguous ("Rimmi so rahi thi" is correct speech about a
#     third person) -> flag ONLY in a sentence that has a first-person cue
#     (main/mein/I) and NO third-person cue (woh/usne/relation noun/proper
#     noun subject).
FEMININE_FIRST_RE = re.compile(
    r"\brahi\s+(?:hoon|hun|hain|hein)\b"
    r"|\b(?:gayi|aayi)\s+(?:hoon|hun)\b"
    r"|\brahi\s+hui\b",
    re.IGNORECASE,
)
FEMININE_FUT_RE = re.compile(r"\b[a-z]{2,}ungi\b", re.IGNORECASE)
FEMININE_PAST_RE = re.compile(r"\brahi\s+thi\b", re.IGNORECASE)
FIRST_PERSON_CUE = re.compile(r"\b(?:main|mein|i)\b|मैं", re.IGNORECASE)
THIRD_PERSON_CUE = re.compile(
    r"\b(?:woh|wo|vo|wah|usne|usko|uska|uski|unhone|unka|unki|behen|bahen|maa|mummy|"
    r"wife|patni|didi|bhabhi|beti|ladki|girlfriend|bestie)\b"
    r"|वो|वह|उसने|उसका|उसकी|उन्होंने|उनकी|आपकी|तुम्हारी",
    re.IGNORECASE,
)
PROPER_NOUN = re.compile(r"\b[A-Z][a-z]{1,15}\b")


def feminine_self_reference(text: str) -> str | None:
    """Return the first matching feminine self-form, or None. Telemetry only —
    never used to alter the spoken reply."""
    t = text or ""
    m = FEMININE_FIRST_RE.search(t)
    if m:
        return m.group(0)
    m = FEMININE_FUT_RE.search(t)
    if m:
        return m.group(0)
    for sent in re.split(r"[.!?।\n]+", t):
        if not FEMININE_PAST_RE.search(sent):
            continue
        if not FIRST_PERSON_CUE.search(sent):
            continue
        if THIRD_PERSON_CUE.search(sent):
            continue
        # Proper-noun subject ("Rimmi intezaar kar rahi thi") — drop the
        # sentence-initial capitalized word before scanning.
        body = sent.split(None, 1)[1] if len(sent.split(None, 1)) > 1 else sent
        if PROPER_NOUN.search(body):
            continue
        return FEMININE_PAST_RE.search(sent).group(0)
    return None
