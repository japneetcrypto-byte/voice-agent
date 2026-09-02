"""memory_units — pure, deterministic episode-membership + fact-annotation rules.

Episode-memory foundation slice (owner-approved direction 2026-09-02,
docs/EPISODE_MEMORY_SLICE_LOCK.md). Design contract:
  - Atomic confirmed facts (the committed `memory` rows) stay the source of
    truth; this module NEVER stores a fact, NEVER touches the DB, NEVER calls
    the LLM. It is the pure decision layer the store calls AFTER a fact is
    committed.
  - Episodes are containers (grouping), not semantic records. 5W1H is not a
    storage lens. Mention keys are a recall accelerator, never a gate.
  - Corrections become NEW facts; this module only picks a supersede target.

All functions are pure over their inputs (strings / dataclasses / ints). The
only imports are the existing deterministic extractors + two existing signal
regexes (imported lazily to keep this module light and cycle-free). Time
values are UTC ISO strings or None; no clocks are read here.

Independence (owner decision #3): nothing here imports or depends on any
disclosure-frame detector (capture-confirm v2, the future feeder). Tests
drive these rules from synthetic fixtures.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.entity_extractor import (extract_place_mentions,
                                    extract_entities_from_user_text)

# Kinds that may join an episode container. Everything else (preference,
# relationship, saved_number, name/job semantic) is ALWAYS standalone (R1).
GROUPABLE_KINDS = frozenset({"episodic"})
SINGLETON_KINDS = frozenset({"preference", "relationship", "saved_number",
                             "semantic"})

# Default cross-session attach window (calendar days). Configurable policy:
# the wiring layer passes its configured value; tests override freely.
W_CROSS_DAYS_DEFAULT = 30


@dataclass(frozen=True)
class EpisodeInfo:
    """Non-archived episode snapshot for one owner (built by the store)."""
    episode_id: int
    session_id: int | None
    created_at: str
    last_touched_at: str
    time_marks: frozenset[str] = frozenset()      # distinct marks of members
    keys: frozenset[tuple[str, str]] = frozenset()  # (kind, key) of members


@dataclass(frozen=True)
class FactInfo:
    """Committed-fact snapshot for supersede candidate selection."""
    fact_id: int
    kind: str
    content: str
    keys: frozenset[tuple[str, str]] = frozenset()
    last_seen: str = ""
    status: str = "committed"


# ---------------------------------------------------------------------------
# Key + time-mark derivation (deterministic, over the verbatim clause)
# ---------------------------------------------------------------------------
def mention_keys(text: str | None) -> list[tuple[str, str]]:
    """[(kind, key)] — 'p' person keys from the existing relationship
    extractor (relatives only: name+relation pairs), 'l' place keys from the
    place alias vocabulary. Empty when nothing deterministic matches."""
    t = text or ""
    if not t.strip():
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ent in extract_entities_from_user_text(t):
        key = (ent.get("name") or "").lower().strip()
        if key:
            k = ("p", key)
            if k not in seen:
                seen.add(k)
                out.append(k)
    for tok in extract_place_mentions(t):
        k = ("l", tok)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# Canonical coarse time buckets. Deliberately NOT calendar dates: the token is
# a conflict-comparison anchor + future read-time hint, never an interpreted
# date (design: "raw token, not interpreted").
_TIME_BUCKET_RES = [
    (re.compile(r"अगले?\s*महीने?|अगले?\s*महिने?|agle\s*mahine?", re.IGNORECASE), "next_month"),
    (re.compile(r"अगले?\s*हफ्ते?|अगले?\s*हप्ते?|अगले?\s*सप्ताह|agle\s*hafte?", re.IGNORECASE), "next_week"),
    (re.compile(r"अगले?\s*(?:साल|वर्ष)|agle\s*(?:saal|saal)", re.IGNORECASE), "next_year"),
    (re.compile(r"इस\s*महीने|is\s*mahine", re.IGNORECASE), "this_month"),
    (re.compile(r"इस\s*(?:हफ्ते|हप्ते|सप्ताह)|is\s*hafte", re.IGNORECASE), "this_week"),
    (re.compile(r"(?:कल|kal|parson|परसों|aaj|आज)\b", re.IGNORECASE), "near"),
]


def time_mark_of(text: str | None) -> str | None:
    """Coarse canonical time bucket of the clause, or None. Buckets are
    fixed and few; used for conflict checks only (never stored as a date)."""
    t = text or ""
    for rx, bucket in _TIME_BUCKET_RES:
        if rx.search(t):
            return bucket
    return None


# ADDITIVE/CONTINUATION cue — general DISCOURSE continuers ("aur Rahul bhi
# aa raha hai", "और जयपुर भी जाएंगे", "wahan bhi"). Deliberately NOT the
# dictation rail's CONTINUE_CUE_RE ("aage"-style, continuation of a digit
# task) — that cue is about the number, not about the topic thread. Bounded
# vocabulary; used ONLY to decide episode attach (never capture).
_ADDITIVE_CUE_RE = re.compile(
    r"(?:^|[^\w])(?:और|aur|फिर|phir|वहां|वहाँ|wahan|उसके?\s*बाद|uske?\s*baad)\b",
    re.IGNORECASE)


# ---------------------------------------------------------------------------
# Same-utterance signals (topic-switch uses the existing detector regex via a
# lazy import — keeps this module light and cycle-free at load time).
# ---------------------------------------------------------------------------
def has_topic_switch(text: str | None) -> bool:
    from agent.precision_rail import TOPIC_SWITCH_RE, _NUMBER_TOPIC_RE
    t = text or ""
    return bool(TOPIC_SWITCH_RE.search(t)) and not bool(_NUMBER_TOPIC_RE.search(t))


def has_continuation(text: str | None) -> bool:
    return bool(_ADDITIVE_CUE_RE.search(text or ""))


# ---------------------------------------------------------------------------
# Membership decision (R1-R5, ordered)
# ---------------------------------------------------------------------------
def decide_membership(*, owner_id: str, session_id: int | None,
                      session_start: str, kind: str, text: str,
                      episodes: list[EpisodeInfo],
                      w_cross_days: int = W_CROSS_DAYS_DEFAULT
                      ) -> tuple[str, int | None]:
    """Where a just-committed fact belongs.

    Returns ("standalone", None) | ("new", None) | ("attach", episode_id).

    R1  singleton kind                    -> standalone (always)
    R2  explicit topic switch OR same-session active thread with a
        conflicting time bucket and no continuation cue
                                          -> new episode
    R3  active same-session thread AND (continuation cue OR key overlap)
                                          -> attach active
    R4  no active thread; exactly ONE cross-session episode (touched within
        w_cross_days, key overlap, non-conflicting time bucket)
                                          -> attach that episode
    R5  otherwise: keys/cue present       -> new episode
        no key and no cue                 -> standalone

    Conservative by design: any ambiguity lands in R5 (new/standalone), never
    a forced merge. Wrong grouping is non-destructive (recall never depends
    on membership).
    """
    if kind not in GROUPABLE_KINDS:
        return ("standalone", None)
    if session_id is None or not session_start:
        return ("standalone", None)   # no session context -> never guess
    tm = time_mark_of(text)
    if has_topic_switch(text):
        return ("new", None)
    keys = set(mention_keys(text))
    cont = has_continuation(text)

    # Active thread: the owner's most recently touched non-archived episode
    # that was touched during THIS session (last_touched >= session_start).
    touched_here = [e for e in episodes if e.last_touched_at >= session_start]
    active = max(touched_here, key=lambda e: e.last_touched_at) if touched_here else None
    if active is not None:
        conflict = bool(tm and active.time_marks and tm not in active.time_marks)
        if conflict and not cont:
            return ("new", None)
        if cont or (keys and (keys & active.keys)):
            return ("attach", active.episode_id)
        # Second distinct same-session plan with no tie -> new container.
        if keys:
            return ("new", None)
        return ("standalone", None)

    # Cross-session attach (R4): episodes NOT touched this session, touched
    # within w_cross_days of session_start, sharing a key, and not holding a
    # conflicting time bucket.
    cross = []
    for e in episodes:
        if e.last_touched_at >= session_start:
            continue
        if not _within_days(e.last_touched_at, session_start, w_cross_days):
            continue
        if keys and not (keys & e.keys):
            continue
        if not keys:
            continue
        if tm and e.time_marks and tm not in e.time_marks:
            continue
        cross.append(e)
    if len(cross) == 1:
        return ("attach", cross[0].episode_id)

    # R5.
    if keys or cont:
        return ("new", None)
    return ("standalone", None)


def _within_days(last_touched: str, session_start: str, days: int) -> bool:
    """True when last_touched is within `days` calendar days BEFORE
    session_start (or later). Both are UTC ISO strings."""
    if days < 0:
        return False
    try:
        from datetime import datetime
        a = datetime.fromisoformat(last_touched.replace("Z", "+00:00"))
        b = datetime.fromisoformat(session_start.replace("Z", "+00:00"))
        return (b - a).days <= days and a <= b
    except Exception:
        # Unparseable stamps never force a merge (R5 handles it downstream).
        return False


# ---------------------------------------------------------------------------
# Supersede decision — corrections become NEW facts; here we pick the target.
# ---------------------------------------------------------------------------
_CORRECTION_RE = re.compile(
    r"नहीं|nahi|nhi|गलत|galat|बदल|badal|change|correct|रिप्लेस|replace|सुधार",
    re.IGNORECASE)


def decide_supersede_target(*, kind: str, text: str,
                            keys: set[tuple[str, str]],
                            facts: list[FactInfo],
                            exclude_id: int) -> int | None:
    """Pick the committed fact this correction supersedes, or None.

    Conservative: fires only when the utterance carries a correction frame
    AND the fact shares a mention key with the new content AND is of the same
    kind. A contradicting fact WITHOUT a shared key (e.g. 'nahi, Kanpur nahi,
    Jaipur' when only the Kanpur fact is stored) is NOT superseded here —
    both coexist and recency resolves at read time (design case C4)."""
    if kind not in GROUPABLE_KINDS:
        return None
    if not _CORRECTION_RE.search(text or ""):
        return None
    cands = [f for f in facts
             if f.fact_id != exclude_id and f.status == "committed"
             and f.kind == kind and f.keys and keys and (f.keys & keys)]
    if not cands:
        return None
    newest = max(cands, key=lambda f: f.last_seen or "")
    return newest.fact_id
