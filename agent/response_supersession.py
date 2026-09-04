"""Response supersession at the delivery boundary — L6
(docs/VALUE_TRANSACTION_LOCK.md §7, owner terminology 2026-09-04).

Two responses must never play concurrently, and the one that plays must be
the most authoritative one available when audio starts. Authority rank:

    user reply (a turn answering the user's speech)  >  supervisor rescue  >  idle line

A newer, higher-or-equal-authority response supersedes an older in-flight
response BEFORE its delivery boundary (first audio frame). Once a response
has crossed the boundary it is playback-owned (barge-in handles it).

Pure logic — no asyncio, no livekit. main.py executes: it keeps the pending
rescue as a cancellable task, cancels it when a user turn is created, and
re-checks `should_stand_down` at the rescue's first-audio point.

Root cause this closes (session_20260903_103339 t17): the supervisor rescue
checked stand-down once at grace end; the user's reply was created AFTER
that check and both reached the speaker (99.2–105.1 s overlap).
"""
from __future__ import annotations

# authority rank (higher wins). Names match turn["turn_type"] values in main.py.
RANK = {"speech": 3, "user_reply": 3, "supervisor_rescue": 2, "idle": 1}


def rank_of(turn_type: str | None) -> int:
    return RANK.get(turn_type or "speech", 3)


def supersedes(new_type: str | None, old_type: str | None) -> bool:
    """Does a NEW response of `new_type` supersede an in-flight OLD response
    of `old_type` that has not crossed its delivery boundary? Equal rank
    supersedes (a newer user turn replaces an older user reply — the
    existing barge/prev_task rule); lower rank never does (an idle line
    never displaces a rescue; a rescue never displaces a user reply)."""
    return rank_of(new_type) >= rank_of(old_type)


def should_stand_down(*, agent_speaking: bool, user_turn_in_flight: bool,
                      newer_user_turn_since: bool) -> bool:
    """Evaluated by a supervisor rescue at BOTH gates — grace end AND first
    audio: stand down if the primary pipeline is audible, a user turn is
    being processed, or a user turn was created after the rescue was
    scheduled (the t17 race)."""
    return bool(agent_speaking or user_turn_in_flight or newer_user_turn_since)


def should_stand_down_at_boundary(*, user_turn_in_flight: bool,
                                  newer_user_turn_since: bool) -> bool:
    """The SAME predicate evaluated at the rescue's OWN first-audio boundary.
    `agent_speaking` is deliberately NOT an input here: main.py raises the
    speaking event at "Agent speaking..." — BEFORE the first audio frame — so
    at this point the event is held by the rescue itself and is not evidence
    of the primary pipeline. (Caught 2026-09-04 in the runtime-path review:
    with agent_speaking included, every rescue superseded itself.)"""
    return bool(user_turn_in_flight or newer_user_turn_since)


def decide_at_boundary(*, pending_type: str, agent_speaking: bool,
                       user_turn_in_flight: bool, newer_user_turn_since: bool) -> str:
    """One-word decision for the pending response at its delivery boundary:
    'play' or 'supersede' (cancel without audio). `agent_speaking` is accepted
    for symmetry with the grace-end call but ignored (see
    should_stand_down_at_boundary)."""
    if pending_type in ("supervisor_rescue", "idle"):
        return ("supersede" if should_stand_down_at_boundary(
                    user_turn_in_flight=user_turn_in_flight,
                    newer_user_turn_since=newer_user_turn_since)
                else "play")
    return "play"     # a user reply is never displaced by lower-rank responses
