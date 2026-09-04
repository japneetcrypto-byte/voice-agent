"""Value Transaction Lock — L1/L2/L3/L4 primitives (docs/VALUE_TRANSACTION_LOCK.md,
owner-approved 2026-09-04).

This module owns the *lifecycle* of a dictated value; the Conversation
Controller owns *when* each primitive is called (the transition table) and
agent/precision_rail.py stays the signal layer (detectors, parsers, lines).
Nothing here parses user speech — every spec comes from the existing
`_parse_correction` / `_val_aware_correction` / `_apply_correction`.

  L1  two-phase mutation : propose() builds {base, spec, derived}; the base
                           is untouched until commit().
  L2  delivery gate      : mark_delivery() is written by the PLAYBACK layer
                           (main.py / run_turn) from the turn's response_state;
                           confirmable() is what the controller consults.
  L3  fragment coalescing: open_edit()/extend_edit()/close_edit() keep a
                           locally-parseable edit fragment from mutating state
                           while a continuation is expected.
  L4  addressability     : SILENT_STREAK_MAX is a POLICY constant (owner
                           ruling Q5: threshold is policy, not architecture).
"""
from __future__ import annotations

from agent.precision_rail import (
    _parse_correction, _val_aware_correction, _apply_correction,
    _is_change_frame, _is_reject, _is_plain_reject, _is_full_restatement,
    dictation_value, normalize_span, _is_pure_digit_utterance,
)
from agent.response_state import FULLY_PLAYED, PARTIALLY_PLAYED, UNHEARD

# ---------------------------------------------------------------------------
# Policy constants (tested, configurable — NOT invariants; lock §10)
# ---------------------------------------------------------------------------
SILENT_STREAK_MAX = 2        # L4: the (MAX+1)-th consecutive silent non-digit
                             # turn while a task is active speaks a status line
EDIT_BUFFER_MAX_TURNS = 2    # L3.3c: continuation turns before a forced close
RESUME_WINDOW_MS = 3000.0    # L3.2: mirrors providers/vad.py RESUME_WINDOW_MS
                             # (read-only — no VAD change)

# proposal.delivery states
UNSPOKEN, SPOKEN, UNHEARD_D = "unspoken", "spoken", "unheard"


# ---------------------------------------------------------------------------
# L1 — proposals
# ---------------------------------------------------------------------------
def propose(base: str, derived: str, *, spec, mode: str, turn_no: int) -> dict:
    """A candidate mutation. `mode`: 'correction' (spoken at creation — the
    echo) | 'fresh' (row 48: silent at creation, digits append to derived)."""
    return {"base": base, "spec": list(spec) if spec is not None else None,
            "derived": derived, "mode": mode, "created_turn": int(turn_no),
            "delivery": UNSPOKEN}


def confirmable(proposal: dict | None) -> bool:
    """L2: a proposal may commit only after its echo crossed the delivery
    boundary. Unspoken/unheard proposals re-echo instead."""
    return bool(proposal) and proposal.get("delivery") == SPOKEN


def delivery_from_turn(turn: dict, spoken_value: str | None) -> str:
    """L2 delivery classification from the turn's archived playback facts.
    Pure over (response_state, cancel_pre_audio, heard_text, tts audio).
    FULLY_PLAYED -> spoken; PARTIALLY_PLAYED -> spoken only if the digit
    span was actually heard (lock B3/B4); anything else -> unheard."""
    if turn.get("cancel_pre_audio"):
        return UNHEARD_D
    state = turn.get("response_state")
    tts = turn.get("tts") or {}
    if state == FULLY_PLAYED:
        if tts and "audio_duration_s" in tts and tts.get("audio_duration_s") in (None, 0):
            return UNHEARD_D            # B5: TTS produced no audio at all
        return SPOKEN
    if state == PARTIALLY_PLAYED:
        heard = (turn.get("heard_text") or "").lower()
        if spoken_value and spoken_value.lower() in heard:
            return SPOKEN
        return UNHEARD_D
    return UNHEARD_D                    # UNHEARD / unknown


_ECHO_ACTIONS = ("echo_confirm", "echo_full", "recall", "clarify", "status")


def mark_delivery(engine: dict | None, turn: dict) -> str | None:
    """L2 write side — called by the PLAYBACK layer (main.py completion +
    CancelledError paths; run_turn mirrors it) right after response_state is
    classified. If this turn spoke an open proposal, or the pending echo of
    the base (rows 2/3/17), record whether the user actually heard it. Never
    decides; only records. Returns the delivery recorded (None = nothing
    echoed this turn)."""
    if not engine:
        return None
    dic = engine.get("dictation")
    if not isinstance(dic, dict):
        return None
    pd = turn.get("precise_detail") or {}
    if not pd or pd.get("action") not in _ECHO_ACTIONS:
        return None
    heard_turn = turn
    lr = engine.get("last_response")
    if isinstance(lr, dict) and lr.get("turn") == turn.get("turn") and lr.get("heard_text"):
        heard_turn = dict(turn)
        heard_turn["heard_text"] = lr["heard_text"]       # untruncated form
    prop = dic.get("proposal")
    if isinstance(prop, dict) and prop.get("derived") and pd.get("value") == prop.get("derived"):
        d = delivery_from_turn(heard_turn, _spoken_form(prop["derived"]))
        prop = dict(prop)
        prop["delivery"] = d
        dic["proposal"] = prop
        pd["delivery"] = d
        return d
    if pd.get("action") in ("echo_confirm", "echo_full") and not prop \
            and dic.get("status") == "confirming" and pd.get("value") == dic.get("value"):
        d = delivery_from_turn(heard_turn, _spoken_form(dic.get("value") or ""))
        dic["echo_delivery"] = d
        pd["delivery"] = d
        return d
    return None


def _spoken_form(digits: str) -> str:
    """The digit span as it appears inside heard_text (speak_value output)."""
    from agent.precision_rail import speak_value
    return speak_value(digits) if digits else ""


# ---------------------------------------------------------------------------
# L3 — instruction buffer
# ---------------------------------------------------------------------------
def is_edit_intent(text: str, base: str) -> bool:
    """An edit-intent turn: a correction spec, a change frame, or a
    non-plain rejection. Existing detectors only (lock §1)."""
    t = text or ""
    if not t.strip():
        return False
    if _parse_correction(t) is not None:
        return True
    if base and _val_aware_correction(t, base) is not None:
        return True
    if _is_change_frame(t):
        return True
    if _is_reject(t) and not _is_plain_reject(t):
        return True
    return False


def open_edit(text: str, base: str, turn_no: int) -> dict:
    return {"fragments": [text or ""], "since_turn": int(turn_no), "base": base}


def extend_edit(buf: dict, text: str) -> dict:
    buf = dict(buf)
    buf["fragments"] = list(buf.get("fragments") or []) + [text or ""]
    return buf


def joined(buf: dict) -> str:
    return " ".join(f for f in (buf.get("fragments") or []) if f)


def resolve_edit(buf: dict, base: str) -> tuple:
    """Parse the instruction ONCE against the base: the JOINED text first;
    if that yields nothing applicable, the NEWEST fragment alone (the user
    may have restated the whole instruction). Returns (spec, derived):
    spec None = nothing parsed; derived None = incomplete (a removal-only
    spec is the prefix of a replace — lock §4) or not applicable."""
    frags = [f for f in (buf.get("fragments") or []) if f]
    candidates = [" ".join(frags)]
    if len(frags) > 1:
        candidates.append(frags[-1])
    best = (None, None)
    for text in candidates:
        spec = _parse_correction(text)
        if spec is None and base:
            spec = _val_aware_correction(text, base)
        if spec is None:
            continue
        if spec[2] is None:
            if best[0] is None:
                best = (spec, None)         # removal-only: incomplete
            continue
        derived = _apply_correction(base, spec)
        if derived is not None:
            return spec, derived
        if best[0] is None:
            best = (spec, None)
    return best


def is_continuation(text: str, base: str, premature_resume: bool) -> bool:
    """L3.2: the next turn continues the instruction if it is itself
    edit-intent, digit-bearing, or arrived as a premature resume."""
    if premature_resume:
        return True
    if is_edit_intent(text, base):
        return True
    t = text or ""
    v = dictation_value(t)
    if v and normalize_span(v):
        return True
    return _is_pure_digit_utterance(t)


def premature_from_turn_meta(meta: dict | None) -> bool:
    """Read-only endpoint evidence (main.py archives turn['premature_resume']
    = {resumed_after_endpoint_ms, ...} when the VAD saw a resume inside its
    window). Missing evidence -> False."""
    if not meta:
        return False
    pr = meta.get("premature_resume")
    if isinstance(pr, dict):
        ms = pr.get("resumed_after_endpoint_ms")
        try:
            return ms is not None and float(ms) <= RESUME_WINDOW_MS
        except (TypeError, ValueError):
            return False
    return bool(pr)


# ---------------------------------------------------------------------------
# Offline playback stand-in (tests / offline decide() loops)
# ---------------------------------------------------------------------------
def mark_heard(engine: dict | None, decision: dict | None, turn_no: int,
               *, heard: bool = True) -> str | None:
    """Play the PLAYBACK layer's role for a decision that has no transport
    behind it: record the echo as fully heard (or, with heard=False, as
    cancelled before audio — the t8 case). Builds the same turn shape
    main.py archives and calls mark_delivery, so offline loops exercise the
    identical write path."""
    if not engine or not decision:
        return None
    turn = {"turn": turn_no,
            "precise_detail": {"action": decision.get("action"),
                               "value": decision.get("value"),
                               "status": decision.get("status")}}
    if decision.get("line") is None:
        return None
    if heard:
        turn["response_state"] = FULLY_PLAYED
        turn["heard_text"] = decision.get("line") or ""
    else:
        turn["response_state"] = UNHEARD
        turn["cancel_pre_audio"] = True
        turn["heard_text"] = ""
    return mark_delivery(engine, turn)


def decide_heard(user_text: str, engine: dict | None, turn_no: int,
                 turn_meta: dict | None = None, *, heard: bool = True) -> dict | None:
    """decide() + the playback stand-in in one call: every spoken decision is
    marked heard (or unheard). The offline equivalent of live turn + playback."""
    from agent.precision_rail import decide
    d = decide(user_text, engine, turn_no, turn_meta)
    mark_heard(engine, d, turn_no, heard=heard)
    return d


def archive_precise_detail(rail: dict, engine: dict | None) -> dict:
    """The per-turn precise_detail archive record (main.py + run_turn use
    this SAME helper). Pre-lock keys unchanged: action/value/status(+raw).
    Lock keys (additive): base = the stored BASE after the decision;
    proposal / pending_edit when open. `delivery` is added later by
    mark_delivery once the response played."""
    pd = {"action": rail["action"], "value": rail["value"], "status": rail["status"]}
    if rail.get("raw"):
        pd["raw"] = rail["raw"]
    dic = (engine or {}).get("dictation") if isinstance((engine or {}).get("dictation"), dict) else None
    if dic is not None:
        pd["base"] = dic.get("value") or ""
        if dic.get("proposal"):
            pd["proposal"] = dict(dic["proposal"])
        if dic.get("pending_edit"):
            pd["pending_edit"] = dict(dic["pending_edit"])
    return pd


def task_state_view(engine: dict | None) -> dict | None:
    """L5: the ONLY task information the LLM may see — kind/status/has_value/
    proposal_open. No digits, no spec, no derived value, ever."""
    dic = (engine or {}).get("dictation") if isinstance((engine or {}).get("dictation"), dict) else None
    if not dic or dic.get("status") not in ("pending", "confirming"):
        return None
    return {"kind": "dictation", "status": dic.get("status"),
            "has_value": bool(dic.get("value")),
            "proposal_open": bool(dic.get("proposal"))}
