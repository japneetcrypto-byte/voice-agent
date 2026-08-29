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
    # NOTE (evidence session 133659 t19): वो/वह/ये/यह REMOVED — at the END of
    # an utterance they are demonstrative PRONOUNS ("kya karta hai ye" = a
    # completed question), not connectors; suppressing on them made Aiva go
    # silent mid-conversation. Only true conjunctions remain.
    "कि", "की", "तो", "और", "फिर", "मतलब", "क्योंकि", "क्यूंकि", "लेकिन",
    "मगर", "असल", "बाद",
    # Roman Hinglish
    "ki", "kie", "toh", "aur", "phir", "matlab", "kyunki", "kyu", "lekin",
    "magar", "actually", "asal", "baad",
}

# User handing the floor back ("haan bol", "ab bata", "bolo bhai") — always
# respond. Checked against ANY word, not just the last (evidence t20:
# "बोलो भाई" was suppressed because only the last word was checked).
HANDOFF_MARKERS = {"bol", "bolo", "bata", "batao", "sun", "suno", "बोल",
                    "बोलो", "बता", "बताओ", "बताए", "सुन", "सुनो", "सुनों",
                    "बताओं", "सुनाओ"}

# Reaching-out tokens ("hello?" after silence) — ALWAYS respond. Never a
# continuation. (Evidence t21: "हेलो" was suppressed as a fragment while the
# user was visibly calling Aiva back.)
# NOTE: greetings must be the FIRST word (checked below) — English "hi"
# collides with the Hindi particle "hi" ('aise hi' = 'just like that').
GREETING_MARKERS = {"hello", "helo", "hallo", "halo", "hey", "hi",
                    "हेलो", "हलो", "हैलो", "नमस्ते", "namaste"}

# Hard cap on consecutive WAITs: after 2 suppressed continuations, the next
# turn RESPONDS regardless — an infinite WAIT latch reads as the agent
# disappearing (evidence t16-t21 chain: user said "hello hello" into silence).
WAIT_STREAK_CAP = 2


def decide(user_text: str, previous_turn_was_wait=False) -> tuple[str, str]:
    """Returns (action, reason). action ∈ {"respond", "suppress"}.

    previous_turn_was_wait accepts bool (legacy) or int streak count — the
    streak form enables WAIT_STREAK_CAP."""
    text = (user_text or "").strip()
    if not text:
        return "suppress", "empty"

    streak = previous_turn_was_wait if isinstance(previous_turn_was_wait, int) \
        else (1 if previous_turn_was_wait else 0)

    if text.startswith("...") or text.startswith("…"):
        return "suppress", "leading_ellipsis"      # resuming their own thought

    if text.endswith("?") or text.endswith("？"):
        return "respond", "user_question"          # a question is a handoff

    if text.endswith("...") or text.endswith("…"):
        return "suppress", "trailing_ellipsis"

    words = re.findall(r"[\w\u0900-\u097F]+", text, re.UNICODE)
    last = words[-1].lower() if words else ""

    if words and words[0].lower() in GREETING_MARKERS:
        return "respond", "greeting_or_reachout"

    if any(w.lower() in HANDOFF_MARKERS for w in words):
        return "respond", "handoff"

    if last in TRAIL_OFF_MARKERS:
        return "suppress", "continuation_marker"

    if len(words) <= 3 and streak >= 1:
        if streak >= WAIT_STREAK_CAP:
            return "respond", "wait_streak_cap"    # never vanish on the user
        return "suppress", "continuation_fragment"

    return "respond", "completed_or_unclear"
