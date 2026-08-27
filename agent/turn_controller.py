"""Conversation Turn Controller (owner brief 2026-08-27) — deterministic gate
between VAD endpoint and response generation.

VAD speech-end only means "the user stopped making sound for the moment".
This controller decides whether that was a genuine handoff or a pause inside
a continuing thought.

Decision (pure function, no LLM, no interpretation beyond the listed
deterministic cues — same discipline as the hallucination/echo filters):

  WAIT (suppress response)  when the utterance looks unfinished:
    - trail-off connector at the end (ki/toh/aur/phir/matlab/kyunki/lekin…,
      Devanagari + Roman)
    - trailing ellipsis ("…")
    - very short fragment (<=2 words) while the previous turn was already
      suppressed as a continuation (user is stringing fragments together)

  RESPOND  otherwise.

Suppressed turns STILL enter conversation history (context is preserved so
the eventual response sees the complete thought) — only speech is withheld.
"""
from __future__ import annotations

import re

TRAIL_OFF_MARKERS = {
    # Devanagari connectors (checked against the final word)
    "कि", "की", "तो", "और", "फिर", "मतलब", "क्योंकि", "क्यूंकि", "लेकिन",
    "मगर", "असल", "वो", "वह", "ये", "यह",
    # Roman Hinglish
    "ki", "kie", "toh", "aur", "phir", "matlab", "kyunki", "kyu", "lekin",
    "magar", "actually", "asal",
}

# User handing the floor back ("haan bol", "ab bata") — always respond.
HANDOFF_MARKERS = {"bol", "bolo", "bata", "batao", "sun", "suno", "बोल",
                    "बता", "बताओ", "सुन", "सुनो"}


def decide(user_text: str, previous_turn_was_wait: bool = False) -> tuple[str, str]:
    """Returns (action, reason). action ∈ {"respond", "suppress"}."""
    text = (user_text or "").strip()
    if not text:
        return "suppress", "empty"

    if text.startswith("...") or text.startswith("…"):
        return "suppress", "leading_ellipsis"      # resuming their own thought

    if text.endswith("?") or text.endswith("？"):
        return "respond", "user_question"          # a question is a handoff

    if text.endswith("...") or text.endswith("…"):
        return "suppress", "trailing_ellipsis"

    words = re.findall(r"[\w\u0900-\u097F]+", text, re.UNICODE)
    last = words[-1].lower() if words else ""

    if last in HANDOFF_MARKERS:
        return "respond", "handoff"

    if last in TRAIL_OFF_MARKERS:
        return "suppress", "continuation_marker"

    if len(words) <= 3 and previous_turn_was_wait:
        return "suppress", "continuation_fragment"

    return "respond", "completed_or_unclear"
