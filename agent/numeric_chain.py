"""Numeric audit CHAIN record — Phase 1 of docs/NUMERIC_OBSERVATION_LOCK.md §11.

Makes one turn's numeric handling inspectable end to end:

    STT (turn.stt_*)  ->  observation (turn.numeric_observation)
      ->  operation   (turn.numeric_audit.operation: what the rail DID,
                       derived from the decision + the task state before/after)
      ->  proposal    (turn.numeric_audit.proposal / precise_detail.proposal)
      ->  delivery    (precise_detail.delivery, written by the playback layer)
      ->  confirmation(turn.numeric_audit.confirm_evidence)
      ->  commit      (turn.numeric_audit.commit: base_before -> base_after)

READ-ONLY. Nothing here decides anything or mutates engine state: the record
is derived AFTER precision_rail.decide() ran, from (decision, pre-state
snapshot, post-state, observation, text). Fail-closed like the control-plane
shadow: an exception writes turn["numeric_audit_error"] and the production
path is untouched. The record is additive (a new turn key); the pinned
`precise_detail` shape is not changed.

`observation_vs_signal` is the Phase-1 measurement the lock asks for: does
the observation (what was numerically HEARD) agree with the legacy digit
signal (`seg`) the rows actually consumed? Phase 3 rewires the rows to the
observation; this field is the before/after delta oracle.
"""
from __future__ import annotations

import re

from agent.precision_rail import (
    dictation_value, normalize_span, _is_pure_digit_utterance, _is_change_frame,
    _is_confirm, _is_reject, _parse_correction, _digit_groups,
    CONFIRM_EN_RE, CONFIRM_DEV_WORDS, REJECT_EN_RE,
    REJECT_DEV_WORDS, QUESTIONISH_RE, RESTART_RE, CONTINUE_CUE_RE, _dev_words,
)
from agent.numeric_observation import digits_of, summary, COMPLETE, EMPTY

CHAIN_VERSION = "chain-1.0"

# Operation kinds under which the BASE may legitimately move (L1 lock):
# accumulation appends (owner Q1), a first span, an explicit commit, a task
# switch (row 40), a plain-reject wipe (retry) and a discard/close.
_BASE_MAY_CHANGE = frozenset({"start_base", "append_to_base", "commit", "task_switch_new_base",
                              "rearm_new_task", "retry_wipe", "discard_task", "close_task_confirmed"})


# ---------------------------------------------------------------------------
# legacy signal (what the rows consumed today) — recomputed read-only
# ---------------------------------------------------------------------------
def legacy_seg(text: str) -> tuple[str, str | None]:
    """Byte-for-byte the digit signal classify_turn builds today:
    seg = normalize_span(dictation_value(t)) with the pure-digit fallback."""
    t = text or ""
    v_raw = dictation_value(t)
    seg = normalize_span(v_raw) if v_raw else ""
    if not seg and _is_pure_digit_utterance(t):
        seg = normalize_span(t)
    return seg, v_raw


def observation_vs_signal(obs: dict | None, seg: str) -> str:
    """The observation against the legacy digit-span signal `seg` (the value
    the accumulate/replace rows consume). Values:
      none              neither has digits
      agree             COMPLETE and seg == the observed digits (all items or one item)
      observation_only  observation has COMPLETE digits, seg is empty — the rows
                        consumed no span (an edit-intent turn handled through the
                        correction parser, or a span the detectors declined)
      legacy_only       seg has digits the observation does not assert
      legacy_dropped    observation INCOMPLETE and seg empty (the span was lost)
      legacy_guessed    observation INCOMPLETE but seg holds a digit string (a
                        guess over UNKNOWN/AMBIGUOUS content)
      disagree          both COMPLETE-looking, different digits
    A per-turn first-wrong-layer oracle for Phase 3's before/after deltas; it
    grades nothing by itself."""
    cert = (obs or {}).get("certainty", EMPTY)
    items = (obs or {}).get("items") or []
    if cert == EMPTY and not seg:
        return "none"
    if cert == EMPTY and seg:
        return "legacy_only"                 # legacy saw digits the observation did not
    if cert == COMPLETE:
        if not seg:
            return "observation_only"        # legacy missed digits the observation has
        d_all = digits_of(obs)
        per_item = [digits_of(it) for it in items]
        if seg == d_all or seg in per_item:
            return "agree"
        return "disagree"
    # INCOMPLETE
    return "legacy_guessed" if seg else "legacy_dropped"


# ---------------------------------------------------------------------------
# operation — derived from decision + state transition
# ---------------------------------------------------------------------------
def _prop(d: dict | None) -> dict | None:
    p = (d or {}).get("proposal")
    return p if isinstance(p, dict) else None


def operation_kind(rail: dict | None, pre: dict | None, post: dict | None, turn_no: int) -> str:
    pre = pre or {}
    post = post or {}
    base_b = pre.get("value") or ""
    base_a = post.get("value") or ""
    prop_b, prop_a = _prop(pre), _prop(post)
    if rail is None:
        if post and post.get("status") == "discarded" and pre.get("status") != "discarded":
            return "discard_task"
        if post and post.get("status") == "confirmed" and pre.get("status") in ("pending", "confirming"):
            return "close_task_confirmed"
        return "none"
    a = rail.get("action")
    trig = rail.get("trigger")
    if a == "silent_accumulate":
        if prop_a and not prop_b:
            return "fresh_proposal"                          # row 48 (owner Q2)
        if prop_a and prop_b:
            da, db = prop_a.get("derived") or "", prop_b.get("derived") or ""
            return "append_to_proposal" if da.startswith(db) and len(da) > len(db) else "proposal_changed"
        if not base_b:
            return "start_base"                              # row 4
        if base_a.startswith(base_b) and len(base_a) > len(base_b):
            return "append_to_base"                          # rows 13/30 (owner Q1)
        return "base_changed"
    if a == "echo_confirm":
        if prop_a and (not prop_b or prop_a.get("created_turn") == turn_no):
            spec = prop_a.get("spec") or []
            k = spec[0] if spec else None
            if k in ("restate", "only_this"):
                return f"replace_proposal:{k}"
            return "correction_proposal"
        if prop_a and trig == "already_in_proposal":
            return "reecho_proposal"
        if base_a != base_b:
            return "task_switch_new_base"                    # row 40
        return "echo_base"
    if a == "confirm_ack":
        return "commit" if base_a != base_b else "confirm_base"
    if a == "echo_full":
        return f"reecho:{trig}" if trig else "echo_full"
    if a == "retry":
        if trig == "proposal_reverted":
            return "revert"
        if post.get("pending_edit"):
            return "hold_instruction"
        if base_b and not base_a:
            return "retry_wipe"
        return "retry"
    if a == "hold_edit":
        return "hold_instruction"
    if a == "silent":
        return f"silent:{trig}" if trig else "silent"
    if a == "arm":
        return "rearm_new_task" if base_b else "arm"       # row 49 vs row 1
    return a or "none"


# ---------------------------------------------------------------------------
# confirmation evidence
# ---------------------------------------------------------------------------
def confirm_tokens(text: str) -> list[str]:
    t = text or ""
    out = [m.group(0) for m in CONFIRM_EN_RE.finditer(t)]
    out += sorted(_dev_words(t) & CONFIRM_DEV_WORDS)
    return out


def reject_tokens(text: str) -> list[str]:
    t = text or ""
    out = [m.group(0) for m in REJECT_EN_RE.finditer(t)]
    out += sorted(_dev_words(t) & REJECT_DEV_WORDS)
    return out


def confirm_evidence(text: str, rail: dict | None, pre: dict | None) -> dict | None:
    """Present whenever the turn carries a confirm/reject token — including
    turns the rail never saw (echo-dropped), so the diagnostic can show a
    confirmation that was heard and discarded before the rail (E1 input)."""
    c, r = confirm_tokens(text), reject_tokens(text)
    if not c and not r:
        return None
    pre = pre or {}
    prop = _prop(pre)
    t = text or ""
    return {
        "confirm_tokens": c,
        "reject_tokens": r,
        # after the rail's own guards: M3 (classify_turn) drops a confirm word
        # inside a change frame; _is_reject drops a 'नहीं' inside a question
        "is_confirm": _is_confirm(t) and not _is_change_frame(t),
        "is_reject": _is_reject(t),
        "change_frame": _is_change_frame(text or ""),
        "questionish": bool(QUESTIONISH_RE.search(text or "")),
        "proposal_delivery_before": prop.get("delivery") if prop else None,
        "echo_delivery_before": pre.get("echo_delivery"),
        "task_status_before": pre.get("status"),
        "rail_action": rail.get("action") if rail else None,
        "rail_trigger": rail.get("trigger") if rail else None,
    }


def cue_tokens(text: str) -> dict:
    """Instruction cues the operation layer may consult (N5) — recorded, not
    decided on here."""
    t = text or ""
    return {"restart": [m.group(0) for m in RESTART_RE.finditer(t)],
            "continue": [m.group(0) for m in CONTINUE_CUE_RE.finditer(t)],
            "change_frame": _is_change_frame(t)}


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------
def build_chain(*, text: str, turn_no: int, rail: dict | None, pre_dictation: dict | None,
                post_dictation: dict | None, observation: dict | None,
                premature_resume: dict | bool | None = None, stage: str = "rail") -> dict:
    pre = pre_dictation if isinstance(pre_dictation, dict) else {}
    post = post_dictation if isinstance(post_dictation, dict) else {}
    seg, raw = legacy_seg(text)
    try:
        corr = _parse_correction(text or "")
        groups = [g[0] for g in _digit_groups(text or "")]
    except Exception:
        corr, groups = None, []
    kind = operation_kind(rail, pre, post, turn_no)
    base_b = pre.get("value") or ""
    base_a = post.get("value") or ""
    prop_a = _prop(post)
    pr_ms = None
    if isinstance(premature_resume, dict):
        pr_ms = premature_resume.get("resumed_after_endpoint_ms")
    rec = {
        "version": CHAIN_VERSION,
        "stage": stage,                       # rail | route_dropped | echo_dropped
        "observation_ref": (observation or {}).get("turn", turn_no),
        "observation": summary(observation),
        "observation_certainty": (observation or {}).get("certainty", EMPTY),
        "observation_digits": digits_of(observation),
        "legacy_signal": {"seg": seg, "raw": raw,
                          "correction": list(corr) if corr else None,   # what the correction parser read
                          "groups": groups},                            # digit groups the parsers see
        "observation_vs_signal": observation_vs_signal(observation, seg),
        "operation": {
            "kind": kind,
            "action": rail.get("action") if rail else None,
            "trigger": rail.get("trigger") if rail else None,
            "value": rail.get("value") if rail else None,
            "spoken": bool(rail and rail.get("line")),
            "inputs": {
                "cues": cue_tokens(text),
                "premature_resume_ms": pr_ms,
                "base_before": base_b,
                "proposal_before": (_prop(pre) or {}).get("derived"),
                "pending_edit_before": bool(pre.get("pending_edit")),
                "task_status_before": pre.get("status"),
            },
        },
        "proposal": ({"base": prop_a.get("base"), "derived": prop_a.get("derived"),
                      "mode": prop_a.get("mode"), "spec": prop_a.get("spec"),
                      "created_turn": prop_a.get("created_turn"),
                      "delivery_at_decision": prop_a.get("delivery")} if prop_a else None),
        "pending_edit": bool(post.get("pending_edit")),
        "confirm_evidence": confirm_evidence(text, rail, pre),
        "commit": {"base_before": base_b, "base_after": base_a,
                   "changed": base_a != base_b,
                   "l1_check": ("ok" if base_a == base_b or kind in _BASE_MAY_CHANGE
                                else "UNEXPECTED_BASE_CHANGE")},
        "task_status_after": post.get("status"),
    }
    return rec


def attach_chain(turn: dict, *, text: str, turn_no: int, rail: dict | None,
                 pre_dictation: dict | None, engine: dict | None,
                 premature_resume=None, stage: str = "rail", emit=None) -> dict | None:
    """Write turn['numeric_audit'] ONCE (read-only over its inputs). Never
    raises: an error is archived under turn['numeric_audit_error']."""
    if isinstance(turn.get("numeric_audit"), dict):
        return turn["numeric_audit"]
    try:
        post = (engine or {}).get("dictation") if isinstance((engine or {}).get("dictation"), dict) else None
        rec = build_chain(text=text, turn_no=turn_no, rail=rail, pre_dictation=pre_dictation,
                          post_dictation=post, observation=turn.get("numeric_observation"),
                          premature_resume=premature_resume, stage=stage)
        turn["numeric_audit"] = rec
        if emit is not None:
            try:
                emit("NUMERIC_CHAIN", {"turn": turn_no, "stage": stage, "observation": rec["observation"],
                                       "vs_signal": rec["observation_vs_signal"],
                                       "op": rec["operation"]["kind"],
                                       "base": f"{rec['commit']['base_before']}->{rec['commit']['base_after']}",
                                       "l1": rec["commit"]["l1_check"]})
            except Exception:
                pass
        return rec
    except Exception as e:  # fail-closed: production path untouched
        turn["numeric_audit_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return None


_SHORT_RE = re.compile(r"\s+")


def chain_line(turn: dict) -> str:
    """One-line rendering for the stage diagnostic."""
    na = turn.get("numeric_audit")
    if not isinstance(na, dict):
        return ""
    op = na.get("operation") or {}
    c = na.get("commit") or {}
    p = na.get("proposal")
    pd = turn.get("precise_detail") or {}
    parts = [f"op={op.get('kind')}", f"base {c.get('base_before') or '∅'}→{c.get('base_after') or '∅'}"]
    if p:
        parts.append(f"proposal={p.get('derived')}({p.get('mode')}, delivery={pd.get('delivery') or p.get('delivery_at_decision')})")
    if na.get("pending_edit"):
        parts.append("edit=held")
    ce = na.get("confirm_evidence")
    if ce:
        parts.append(f"confirm={ce.get('confirm_tokens')} reject={ce.get('reject_tokens')}")
    if c.get("l1_check") != "ok":
        parts.append(f"!! {c.get('l1_check')}")
    return " | ".join(parts)
