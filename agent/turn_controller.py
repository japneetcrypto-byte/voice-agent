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

# Leading question words ("कहां करें?" without the '?') — a question is a
# handoff even when STT drops the '?' marker. Checked FIRST word only.
# Evidence session 182736 t6: 'कहां करें' suppressed as fragment; user then
# complained about the silence ('कहां गए भाई').
QUESTION_STARTERS = {"कहां", "कहाँ", "kahan", "क्या", "kya", "कब", "kab",
                     "कौन", "kaun", "क्यों", "kyon", "kyun", "कैसे", "kaise",
                     "कहाँ"}
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

# Greeting rail (owner smoke 3, 2026-08-31: "it started with acha- not
# hello"). A turn whose FIRST word is a greeting marker gets a deterministic
# greeting reply instead of the LLM's drift ('bas yahin hoon, bol kya scene
# hai?') and instead of a bare ack ('achha'). Persona-consistent, short,
# masculine, Roman — same discipline as the other rails.
GREETING_LINES = [
    "hello! kaise ho?",
    "hello! bol kya scene hai?",
    "namaste! kaise ho?",
]


def greeting_line_for(text: str, turn_no: int) -> str | None:
    """Deterministic greeting reply when the user's FIRST word is a greeting
    marker ('hello'/'hi'/'हेलो'/'नमस्ते'... — the same marker set as
    decide()). 'hi' as a non-first word is the Hindi particle ('aise hi') —
    only the first word is checked, matching turn_controller discipline.
    Returns None for non-greeting turns (normal LLM flow)."""
    t = (text or "").strip()
    if not t:
        return None
    words = re.findall(r"[\w\u0900-\u097F]+", t, re.UNICODE)
    if words and words[0].lower() in GREETING_MARKERS:
        return GREETING_LINES[turn_no % len(GREETING_LINES)]
    return None


# Cues that should EXTEND an ongoing detail conversation (directive
# 192439-synthesis session 203226: the 6-turn latch expired mid-explanation
# and 11-14s monologues returned). 'haan/aage' means "next chunk";
# a question means "more depth on this thread".
CONTINUATION_CUES = {"haan", "han", "aage", "aagay", "phir", "aur", "और",
                     "हाँ", "हां", "आगे", "फिर", "ok", "okay", "achha",
                     "अच्छा", "बताओ", "batao", "next", "continue"}
# Multi-word keep-going phrases (owner brief 2026-08-31, fix ②): 'bolte jao' /
# 'roko mat' mean CONTINUE the active explanation, never a fresh request.
# Checked as substrings — the single-word cue set above is separate.
CONTINUATION_PHRASES = ("bolte jao", "bolte ja", "roko mat", "rokna mat",
                        "aage bolo", "aage bata", "keep going",
                        "और बोलो", "रुको मत", "बोलते जाओ")
# DELIVERY-flag cues: the ORIGINAL verified 6-cue list (baseline semantics,
# 2026-08-30) — NOT the wider CONTINUATION_CUES. Widening would change the
# delivery flag on fresh detail requests ('poora plan batao' contains
# 'batao', which must stay chunked_detail) — a regression. The approved fix ②
# adds only the keep-going PHRASES to the delivery semantics.
DELIVERY_CUES = {"haan", "aage", "phir", "हाँ", "आगे", "और"}


def delivery_cue_present(text: str) -> bool:
    """True when the user text carries a DELIVERY continuation cue: one of
    the verified single-word cues or a keep-going phrase ('bolte jao' /
    'roko mat' / 'aage bolo'...). Deliberately excludes bare questions and
    the wider cue set — the delivery flag (continue_detail vs chunked_detail)
    follows the verified semantics, while continues_or_asks() (which includes
    questions) drives state extension. Single source for the prompt-delivery
    branch AND the replay gate's policy.delivery check."""
    t = (text or "").lower()
    if any(p in t for p in CONTINUATION_PHRASES):
        return True
    words = re.findall(r"[\w\u0900-\u097F]+", t, re.UNICODE)
    return any(w.lower() in DELIVERY_CUES for w in words)


def continues_or_asks(text: str) -> bool:
    """True when a user turn should EXTEND an ongoing detail conversation:
    any question (marker or question-word), a continuation cue, or a
    keep-going phrase ('bolte jao' / 'roko mat' — approved fix ②)."""
    t = (text or "").strip()
    if not t:
        return False
    if any(p in t.lower() for p in CONTINUATION_PHRASES):
        return True
    if "?" in t or "？" in t:
        return True
    words = re.findall(r"[\w\u0900-\u097F]+", t, re.UNICODE)
    if any(w.lower() in QUESTION_STARTERS for w in words):
        return True
    if any(w.lower() in CONTINUATION_CUES for w in words):
        return True
    return False


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

    if words and words[0].lower() in QUESTION_STARTERS:
        return "respond", "question_word"

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
