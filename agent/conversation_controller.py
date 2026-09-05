"""Conversation Controller — track ⑤, first slice: the DICTATION task as
explicit state.

Boundary (owner, 2026-08-31): the Controller DECIDES what is happening in
the conversation; the Rail ENFORCES the response. Patterns detect signals;
state determines behavior.

Why this slice: precision_rail.decide() (~600 lines) was a working state
machine whose state was IMPLICIT — engine["dictation"] = {value, status}
overloaded status="pending" for both "armed, awaiting first digits" and
"accumulating", and the if-chain ORDER was an invisible priority table.
Owner smoke-11 (session_20260831_171620, VERIFIED build 1500c29) showed
the cost, all traceable to missing explicit state:

  - t8  '...ab account number likho jara, 026-900-1262' APPENDED to the
        previous mobile number instead of starting a NEW task (no
        task-switch row)
  - t11 '...sirf itna number, theek hai?' was IGNORED (silent append)
  - t15/t16 'Hello'/'हेलो' were swallowed SILENT while a task was active
  - t17 long garbage RELEASED (discarded) the task, so t19 'mera mobile
        number mujhe bata' went to the LLM -> "number toh mujhe nahi pata
        tera"

This module makes the dictation task an explicit state object
(ConversationState + Task) and the transition an explicit table (rows
1-43 of docs/CONVERSATION_CONTROLLER_DESIGN.md section 4, expanded by the
smoke-11 rows below). precision_rail.decide() delegates here. Behavior is
byte-identical for rows 1-39 (all v1..v10 tests + replay identity);
rows 40-43 are the smoke-11 fixes. engine["dictation"] stays the compat
backing store (open-question #2 default) so replay archives need no change.

Rows added/CHANGED by smoke-11 (each tests-first):
  40  active × (announcement + write-command + digits)  -> NEW task, echo
  41  active × 'only this' + digits                    -> replace + echo
  42  active × greeting (first-word hello/hey)         -> greeting line,
                                                          task KEPT
  24  CHANGED: long garbage -> TURN to LLM, TASK KEPT (was: discard)
  43  recall-by-meaning ('नंबर क्या है', 'मुझे बता क्या') -> recall
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A digit span arriving after this many ACTUAL non-digit turns is a FRESH
# dictation, not a continuation (smoke-12 t26: ~14 real turns after the
# fumbled '012...' the user dictated '7398' — appending glued a new number
# onto the old one). Counted in decide() calls, NOT turn_no arithmetic:
# smoke-5's long-pause continuation (t25 -> t33, 8 turn-numbers apart, ZERO
# intervening turns) must still APPEND, and it does (gap counter = 0).
GAP_FRESH_TURNS = 4

from agent.precision_rail import (  # signal layer (patterns stay, demoted)
    dictation_value, normalize_span, speak_value,
    _is_pure_digit_utterance, _parse_correction, _apply_correction,
    _is_full_restatement, _is_reject, _is_confirm, _fragment_length,
    is_dictation_announcement,
    _is_plain_reject, _is_change_frame, _val_aware_correction,
    RECALL_RE, STATUS_RE, CONTINUE_CUE_RE, CLAIM_RE, COMPLAINT_RE,
    ABANDON_RE, DEARM_DETAIL_RE, WRITE_COMMAND_RE, ONLY_THIS_RE,
    QUERY_STORED_RE, TOPIC_SWITCH_RE, _NUMBER_TOPIC_RE, SAVED_NUMBER_QUERY_RE,
    ECHO_LINES, ACK_LINES, RETRY_LINES, FULL_LINES, RECALL_LINES,
    ARM_LINES, STATUS_LINES, HOLD_LINES, CORRECTION_LINES,
    COMPLAINT_EMPTY_LINES, CLARIFY_LINES, NUDGE_LINES,
    HOLD_EDIT_LINES, HOLD_REMOVAL_LINES, REECHO_LINES, PROPOSAL_RECALL_LINES,
    REVERT_LINES, STATUS_ACTIVE_LINES, EDIT_CLARIFY_LINES, MIXED_CLARIFY_LINES,
    _line, _correction_line, _already_correct_line,
)
from agent import value_transaction as vt  # L1-L4 lifecycle primitives (lock)


# ---------------------------------------------------------------------------
# Explicit conversation state (owner's model, first slice)
# ---------------------------------------------------------------------------
@dataclass
class Task:
    """The active structured-detail task (dictation for now).

    VALUE TRANSACTION LOCK (docs/VALUE_TRANSACTION_LOCK.md, owner-approved
    2026-09-04): `value` is the BASE — the last value the user accepted. A
    destructive row never writes it; it writes `proposal` (L1). The base
    moves only on commit = explicit confirm of a proposal whose echo crossed
    the delivery boundary (L2). `pending_edit` is the L3 instruction buffer.
    """
    kind: str = "dictation"
    value: str = ""               # BASE digits (LLM never the source of truth)
    status: str = "pending"       # pending | confirming | confirmed | discarded
    topic: str = ""               # e.g. "mobile_number" | "account_number" | ""
    proposal: dict | None = None  # L1 {base, spec, derived, mode, created_turn,
                                  #     delivery: unspoken|spoken|unheard}
    pending_edit: dict | None = None  # L3 {fragments, since_turn, base}

    def to_compat(self) -> dict:
        # engine["dictation"] compat shape (replay archives unchanged): the
        # L1/L3 keys are ADDITIVE and only present while open.
        d = {"value": self.value, "status": self.status}
        if self.proposal:
            d["proposal"] = dict(self.proposal)
        if self.pending_edit:
            d["pending_edit"] = dict(self.pending_edit)
        return d

    @classmethod
    def from_compat(cls, d: dict | None) -> "Task | None":
        if not d:
            return None
        prop = d.get("proposal")
        pe = d.get("pending_edit")
        return cls(value=d.get("value") or "", status=d.get("status") or "pending",
                   proposal=dict(prop) if isinstance(prop, dict) else None,
                   pending_edit=dict(pe) if isinstance(pe, dict) else None)


@dataclass
class ConversationState:
    """What is happening in the conversation right now (owner's list)."""
    user_state: str = "idle"            # announcing|dictating|correcting|querying|
                                        # complaining|confirming|continuing|
                                        # changing_task|abandoning|conversing|idle
    agent_state: str = "idle"           # listening|echoing|asking|answering|holding|
                                        # processing|speaking|awaiting_confirmation|idle
    task: Task | None = None            # active_task
    topic: str = ""                     # active_topic
    waiting_confirmation: bool = False  # echoed a value, awaiting yes/no
    interrupted: bool = False           # last response was cancelled (Generated != Delivered)
    next_action: str | None = None      # derived per turn (incl. "llm")
    armed_streak: int = 0               # consecutive armed-empty turns with no
                                        # digits/intent -> nudge (smoke-12 t18-24)
    accum_gap: int = 0                  # non-digit turns since the last digit
                                        # span (a span after a COLD GAP is a
                                        # fresh number — smoke-12 t26)
    silent_streak: int = 0              # L4: consecutive SILENT decisions on
                                        # non-digit turns while a task is active
                                        # (bounded by SILENT_STREAK_MAX policy)
    echo_delivery: str | None = None    # L2: playback-owned delivery of the last
                                        # base echo (rows 2/3/17): spoken|unheard


# ---------------------------------------------------------------------------
# Signals — patterns DETECT, state DECIDES
# ---------------------------------------------------------------------------
@dataclass
class Signals:
    seg: str = ""                       # normalized digits this turn ("" = none)
    raw: str | None = None              # verbatim dictation span
    correction: tuple | None = None     # (anchor, wrong, correct)
    reject: bool = False
    confirm: bool = False
    recall: bool = False
    status: bool = False
    complaint: bool = False
    cont: bool = False
    abandon: bool = False
    dearm: bool = False
    announcement: bool = False
    write_command: bool = False
    only_this: bool = False
    full_restatement: bool = False
    greeting_line: str | None = None    # deterministic greeting line when active
    topic_switch: bool = False          # explicit topic-switch marker ('के बारे में')
    query_stored: bool = False          # query about the stored value + digit-ish words
    number_talk: bool = False           # speech ABOUT the number, no digits
                                        # (T10: 'काशिड नंबर आ गया' -> clarify)
    filler_len: int = 0


def classify_turn(text: str, state: ConversationState, turn_no: int) -> Signals:
    """Pure signal extraction — reuses every existing rail detector verbatim."""
    t = text or ""
    v_raw = dictation_value(t)
    sig = Signals(seg=normalize_span(v_raw) if v_raw else "", raw=v_raw)
    if not sig.seg and _is_pure_digit_utterance(t):
        sig.seg = normalize_span(t)
    sig.reject = _is_reject(t)
    sig.confirm = _is_confirm(t)
    # M3 confirm-guard (owner session 20260902_184247 t11): a confirm word
    # inside a CHANGE frame ('...900...बस एक जीरो कम हो ज') is an EDIT, not
    # a confirmation of the pending value — never let "बस" hijack the turn
    # into a full-value echo. Plain confirmations ("बस, यही है") have no
    # frame and are untouched.
    if sig.confirm and _is_change_frame(t):
        sig.confirm = False
    # Row 44 (smoke-12 t29/t32): explicit topic-switch marker — unless the
    # topic IS the number ('अकाउंट नंबर के बारे में' stays a recall query).
    sig.topic_switch = bool(TOPIC_SWITCH_RE.search(t)) and not bool(
        _NUMBER_TOPIC_RE.search(t))
    if sig.seg:
        sig.correction = _parse_correction(t)
        if sig.correction is None:
            sig.full_restatement = _is_full_restatement(t, len(sig.seg))
        sig.only_this = bool(ONLY_THIS_RE.search(t))
        sig.announcement = is_dictation_announcement(t)
        sig.write_command = bool(WRITE_COMMAND_RE.search(t))
        # Smoke-12 t14: 'मैंने बोला था 5 बट 0 उसका क्या किया तुमने' — a query
        # about the stored value that ALSO has digit-ish words. Must recall as
        # proof, NEVER silently append the span.
        sig.query_stored = bool(QUERY_STORED_RE.search(t))
    else:
        sig.correction = _parse_correction(t)
        sig.recall = bool(RECALL_RE.search(t))
        sig.status = bool(STATUS_RE.search(t))
        sig.complaint = bool(CLAIM_RE.search(t) or COMPLAINT_RE.search(t))
        sig.cont = bool(CONTINUE_CUE_RE.search(t))
        sig.abandon = bool(ABANDON_RE.search(t))
        sig.dearm = bool(DEARM_DETAIL_RE.search(t))
        sig.announcement = is_dictation_announcement(t)
        sig.write_command = bool(WRITE_COMMAND_RE.search(t))
        sig.number_talk = bool(re.search(
            r"नंबर|नमबर|number|अकाउंट|खाता|मोबाइल|mobile", t, re.IGNORECASE))
        sig.filler_len = _fragment_length(t)
        # Row 42: a first-word greeting while a task is active -> the user is
        # checking the agent is still there; answer with a greeting line and
        # KEEP the task (smoke-11 t15/t16 were swallowed silent).
        if state.task is not None and state.task.status in ("pending", "confirming"):
            from agent.turn_controller import greeting_line_for
            sig.greeting_line = greeting_line_for(t, turn_no)
    return sig


# ---------------------------------------------------------------------------
# Transition — the explicit table (rows 1-43, docs/CONVERSATION_CONTROLLER_DESIGN.md)
# ---------------------------------------------------------------------------
def controller_decide(user_text: str, engine: dict | None, turn_no: int,
                      turn_meta: dict | None = None) -> dict | None:
    """One turn's controller decision. Pure + deterministic over
    (user_text, engine, turn_no, turn_meta). Returns None (normal LLM flow)
    or the decision dict — the SAME shape decide() always returned, so the
    harness/replay identity holds. Mutates engine["conv"] (explicit state)
    and engine["dictation"] (legacy compat backing store).

    turn_meta (optional, read-only): endpoint evidence the transport already
    archives per turn — {"premature_resume": {...}} — consumed by L3 only."""
    eng = engine if engine is not None else {}
    conv = eng.get("conv") or {}
    state = ConversationState(
        user_state=conv.get("user_state", "idle"),
        agent_state=conv.get("agent_state", "idle"),
        topic=conv.get("topic", ""),
        waiting_confirmation=bool(conv.get("waiting_confirmation", False)),
        interrupted=bool(conv.get("interrupted", False)),
        armed_streak=int(conv.get("armed_streak", 0)),
        accum_gap=int(conv.get("accum_gap", 0)),
        silent_streak=int(conv.get("silent_streak", 0)),
    )
    state.task = Task.from_compat(eng.get("dictation"))
    if isinstance(eng.get("dictation"), dict):
        state.echo_delivery = eng["dictation"].get("echo_delivery")
    if state.task is not None and conv.get("task_topic"):
        state.task.topic = conv["task_topic"]
    if state.task is not None and state.task.status in ("pending", "confirming"):
        state.accum_gap += 1   # one more non-digit turn since the last span
    sig = classify_turn(user_text, state, turn_no)
    decision = _transition(state, sig, user_text, turn_no, eng,
                           premature=vt.premature_from_turn_meta(turn_meta))
    # L4 (lock §5): bounded silence while a task is active. Digit turns
    # (genuine dictation) reset the streak and are never forced to speak;
    # a silent decision on a non-digit turn counts; reaching the policy bound
    # turns the NEXT silent non-digit decision into a deterministic status.
    decision = _l4_addressability(state, sig, decision, turn_no)
    # Topic continuity: _with_digits/_val_present recreate the Task without a
    # topic (arm-time kind was 'mobile'/'account'); restore it from conv so
    # the persisted saved-number keeps the right kind.
    if state.task is not None and not state.task.topic:
        state.task.topic = conv.get("task_topic") or _detect_kind(user_text)
    # ROW 51: a CONFIRMED number is written to the long-term store (the user
    # explicitly confirmed it: 'हां/ठीक है/हो गया'). This is what makes
    # 'मैंने तुझे अपना नंबर सेव करवाया था' answerable in a NEW session —
    # numbers were previously session-state-only, so a fresh session had
    # nothing to recall and the LLM said "no memory system".
    if decision and decision.get("status") == "confirmed" and state.task \
            and state.task.value:
        _persist_number(eng, state.task)
    # persist explicit state
    eng["conv"] = {
        "user_state": state.user_state,
        "agent_state": state.agent_state,
        "topic": state.topic,
        "waiting_confirmation": state.waiting_confirmation,
        "interrupted": state.interrupted,
        "armed_streak": state.armed_streak,
        "accum_gap": state.accum_gap,
        "silent_streak": state.silent_streak,
        "task_topic": state.task.topic if state.task else "",
    }
    if state.task is not None:
        prev = eng.get("dictation") if isinstance(eng.get("dictation"), dict) else {}
        compat = state.task.to_compat()
        # L2: the base-echo delivery mark (rows 2/3/17) is PLAYBACK-owned
        # state on the compat dict. It is carried only while the same base is
        # still awaiting confirmation and this turn did not echo it again (a
        # new echo gets a fresh mark from the playback layer).
        if decision is not None and decision.get("action") in ("echo_full", "echo_confirm") \
                and not compat.get("proposal") and compat.get("status") == "confirming" \
                and decision.get("value") == compat.get("value"):
            # L2: a fresh echo of the base has not crossed the delivery
            # boundary yet. The PLAYBACK layer (main.py / run_turn via
            # value_transaction.mark_delivery) upgrades it to spoken/unheard
            # once the response actually played (or was cancelled).
            compat["echo_delivery"] = vt.UNSPOKEN
        elif prev.get("echo_delivery") and compat.get("status") == "confirming" \
                and compat.get("value") == prev.get("value"):
            compat["echo_delivery"] = prev["echo_delivery"]
        eng["dictation"] = compat
    return decision


def _l4_addressability(state: ConversationState, sig: Signals, decision: dict | None,
                       turn_no: int) -> dict | None:
    """L4 (lock §5): an armed task must remain addressable — consecutive
    silent decisions on non-digit turns are bounded by SILENT_STREAK_MAX
    (policy). At the bound the turn speaks a status: the open proposal is
    re-echoed if one exists (L2 rule 2), else the base with the escape offer.
    Never touches digit turns (smoke-7: the agent listens while the user
    dictates) and never touches the armed-empty phase (row 47 owns it)."""
    task = state.task
    active = task is not None and task.status in ("pending", "confirming")
    if not active:
        state.silent_streak = 0
        return decision
    if sig.seg:
        state.silent_streak = 0
        return decision
    silent = decision is not None and decision.get("action") == "silent" \
        and decision.get("line") is None
    if not silent:
        state.silent_streak = 0
        return decision
    if not (task.value or task.proposal):
        return decision                 # armed-empty: row 47 nudge owns it
    state.silent_streak += 1
    if state.silent_streak <= vt.SILENT_STREAK_MAX:
        return decision
    state.silent_streak = 0
    state.user_state, state.agent_state = "dictating", "answering"
    if task.proposal:
        state.next_action = "reecho"
        return _reecho_decision(task, turn_no, trigger="status")
    state.next_action = "status"
    return {"action": "status", "value": task.value, "status": task.status,
            "line": _line(STATUS_ACTIVE_LINES, turn_no).format(spoken=speak_value(task.value)),
            "trigger": "l4_status"}


def eng_echo_unheard(state: ConversationState) -> bool:
    """L2 rule 4: the last echo of the BASE (rows 2/3/17) has not crossed the
    delivery boundary (unspoken: the playback layer has not marked it yet;
    unheard: it marked it cancelled/UNHEARD) -> a confirm re-echoes instead
    of committing. No record at all (states seeded from archives that predate
    the lock) = legacy, treated as heard."""
    return state.echo_delivery is not None and state.echo_delivery != vt.SPOKEN


def _reecho_decision(task: Task, turn_no: int, *, trigger: str) -> dict:
    """Speak the open proposal (L1/L2). The decision carries the DERIVED value
    (what is being proposed) while the base stays in task.value."""
    prop = task.proposal or {}
    derived = prop.get("derived") or task.value
    return {"action": "echo_confirm", "value": derived, "status": "confirming",
            "line": _line(REECHO_LINES, turn_no).format(spoken=speak_value(derived)),
            "trigger": trigger, "proposal": dict(prop)}


def _transition(state: ConversationState, sig: Signals, text: str, turn_no: int,
                 eng: dict | None = None, *, premature: bool = False) -> dict | None:
    task = state.task
    if task is None or task.status not in ("pending", "confirming"):
        return _idle(state, sig, text, turn_no, eng)
    val = task.value or ""
    # L3 (lock §4): an OPEN instruction buffer absorbs continuations before
    # any row can act on a fragment; it closes into one L1 proposal (or a
    # clarify) — never a mutation of the base.
    if task.pending_edit:
        d = _edit_buffer_turn(state, sig, text, turn_no, val, premature)
        if d is not _FALLTHROUGH:
            return d
    if sig.seg:
        return _with_digits(state, sig, text, turn_no, val)
    if val:
        return _val_present(state, sig, text, turn_no, val)
    return _armed_empty(state, sig, text, turn_no)


_FALLTHROUGH = object()


# ---------------------------------------------------------------------------
# L1 helpers — proposals never touch the base
# ---------------------------------------------------------------------------
def _propose_correction(state: ConversationState, val: str, derived: str, spec,
                        turn_no: int, *, user_state: str = "correcting",
                        line_pool=ECHO_LINES, raw=None) -> dict:
    """Destructive row -> PROPOSAL + echo of the derived value. task.value
    (the base) is byte-identical after this call (L1.1)."""
    task = state.task
    task.proposal = vt.propose(val, derived, spec=spec, mode="correction", turn_no=turn_no)
    task.pending_edit = None
    task.status = "confirming"
    state.user_state, state.agent_state = user_state, "echoing"
    state.next_action = "echo_confirm"
    state.waiting_confirmation = True
    d = {"action": "echo_confirm", "value": derived, "status": "confirming",
         "line": _line(line_pool, turn_no).format(spoken=speak_value(derived)),
         "proposal": dict(task.proposal)}
    if raw:
        d["raw"] = raw
    return d


def _commit(state: ConversationState, turn_no: int) -> dict:
    """L1 commit: base <- derived, proposal cleared, status confirmed (row 25
    semantics). Only reachable through a confirm of a SPOKEN proposal (L2)."""
    task = state.task
    derived = (task.proposal or {}).get("derived") or task.value
    task.value = derived
    task.proposal = None
    task.pending_edit = None
    task.status = "confirmed"
    state.user_state, state.agent_state = "confirming", "speaking"
    state.next_action = "confirm_ack"
    state.waiting_confirmation = False
    return {"action": "confirm_ack", "value": derived, "status": "confirmed",
            "line": _line(ACK_LINES, turn_no).format(spoken=speak_value(derived))}


def _revert(state: ConversationState, turn_no: int) -> dict:
    """L1.4: plain reject of an open proposal -> back to the base, spoken.
    Never a wipe."""
    task = state.task
    task.proposal = None
    task.pending_edit = None
    task.status = "pending"
    state.user_state, state.agent_state = "correcting", "asking"
    state.next_action = "revert"
    state.waiting_confirmation = False
    return {"action": "retry", "value": task.value, "status": "pending",
            "line": _line(REVERT_LINES, turn_no).format(spoken=speak_value(task.value)),
            "trigger": "proposal_reverted"}


# ---------------------------------------------------------------------------
# L3 — instruction buffer rows (hold_edit / continuation / close)
# ---------------------------------------------------------------------------
def _open_edit(state: ConversationState, text: str, turn_no: int, val: str,
               *, removal_wrong: str | None = None, spec: tuple | None = None) -> dict:
    """First edit fragment: open the buffer, HOLD (spoken), mutate nothing.
    A parsed-but-unapplicable spec keeps the existing correction-ack wording
    (smoke-7 t12 pin: the line names the correct digits)."""
    task = state.task
    task.pending_edit = vt.open_edit(text, val, turn_no)
    state.user_state, state.agent_state = "correcting", "holding"
    state.next_action = "hold_edit"
    action = "hold_edit"
    if removal_wrong:
        line = _line(HOLD_REMOVAL_LINES, turn_no).format(wrong=removal_wrong)
    elif spec is not None and spec[2]:
        # parsed spec that cannot be applied: the existing correction-ack
        # wording + action name (smoke-7 t12 pin) — but the value is KEPT
        # and the instruction stays open for the continuation (L3), instead
        # of the old retry-WIPE.
        line = _correction_line(turn_no, spec[1] or "", spec[2] or "")
        action = "retry"
    else:
        line = _line(HOLD_EDIT_LINES, turn_no)
    return {"action": action, "value": val, "status": task.status,
            "line": line, "pending_edit": dict(task.pending_edit)}


def _close_edit(state: ConversationState, sig: Signals, text: str, turn_no: int,
                val: str) -> dict:
    """Close the buffer: parse the JOINED text ONCE against the base -> L1
    proposal, or a clarify that names what was understood. The base is never
    written here."""
    task = state.task
    buf = task.pending_edit or vt.open_edit(text, val, turn_no)
    joined_text = vt.joined(buf)
    # C5: a fresh whole number inside the joined text (removal fragment + a
    # full re-dictation) is a row-14 REPLACE proposal, not a replace-spec.
    v_raw = dictation_value(joined_text)
    seg = normalize_span(v_raw) if v_raw else ""
    spec, derived = vt.resolve_edit(buf, val)
    if spec is not None and spec[2] is not None and spec[2].isdigit() \
            and spec[2] in val and not (spec[1] and spec[1] in val):
        # 46b already-correct guard on the joined instruction (smoke-13 t30)
        task.pending_edit = None
        task.status = "confirming"
        state.user_state, state.agent_state = "correcting", "echoing"
        state.next_action = "echo_confirm"
        state.waiting_confirmation = True
        return {"action": "echo_confirm", "value": val, "status": "confirming",
                "line": _already_correct_line(turn_no, spec[2], speak_value(val))}
    if seg and spec is not None and spec[2] == seg and len(seg) >= len(val) and seg != val:
        # C5 precedence (controller, not a parser rule): a "replace X with
        # <whole number>" whose replacement IS a whole-number restatement is
        # the restatement itself (row 14) — never X spliced into the old
        # value (the 30-digit glue). Only reachable at a buffer close.
        return _propose_correction(state, val, seg, ("restate", None, seg), turn_no,
                                   user_state="dictating", raw=v_raw)
    if derived is not None and derived != val:
        return _propose_correction(state, val, derived, spec, turn_no, raw=v_raw)
    if seg and (_is_full_restatement(joined_text, len(seg)) or len(seg) >= len(val)) \
            and seg != val:
        return _propose_correction(state, val, seg, ("restate", None, seg), turn_no,
                                   user_state="dictating", raw=v_raw)
    # nothing usable -> clarify naming the base (M1: never wipe)
    task.pending_edit = None
    state.user_state, state.agent_state = "correcting", "asking"
    state.next_action = "clarify"
    if task.proposal:
        # an older proposal stays open (lock L1.4): clarify by re-speaking it
        d = _reecho_decision(task, turn_no, trigger="edit_unresolved")
        d["action"] = "clarify"
        return d
    return {"action": "clarify", "value": val, "status": task.status,
            "line": _line(EDIT_CLARIFY_LINES, turn_no).format(spoken=speak_value(val)),
            "trigger": "edit_unresolved"}


def _edit_buffer_turn(state: ConversationState, sig: Signals, text: str, turn_no: int,
                      val: str, premature: bool):
    """A turn while the L3 buffer is open.
      continuation (edit-intent / digits / premature resume) -> extend; close
        into ONE proposal when the joined instruction is complete, a whole
        number was given, or the policy bound is reached;
      confirm-handoff ('बस'/'हाँ')  -> close: proposal if resolvable, else a
        clarify naming what was understood (the user thinks they finished);
      other handoff (recall/status/complaint/plain reject) -> close silently
        (proposal echo if resolvable) else drop the buffer and let the turn's
        own intent run;
      task switch / abandon / topic switch -> buffer discarded (C10);
      greeting -> answered, buffer stays open (C8);
      anything else -> buffer stays open until the bound, turn runs normally."""
    task = state.task
    buf = dict(task.pending_edit)
    buf["turns"] = int(buf.get("turns") or 0) + 1
    task.pending_edit = buf
    if sig.announcement and sig.write_command:
        task.pending_edit = None            # C10: task switch discards the buffer
        return _FALLTHROUGH
    if sig.abandon or sig.dearm or sig.topic_switch:
        task.pending_edit = None
        return _FALLTHROUGH
    if sig.greeting_line and not sig.seg:
        return _FALLTHROUGH                 # C8: greeting answered, buffer stays open
    plain_reject = sig.reject and _is_plain_reject(text) and not sig.seg
    if vt.is_continuation(text, val, premature) and not plain_reject:
        prop = task.proposal
        if prop and sig.seg and sig.seg == prop.get("derived"):
            # the continuation restates the OPEN proposal ('मतलब 00000 एड
            # होगा' with 00000 proposed) -> the instruction is already
            # satisfied by the proposal: close the buffer, re-echo it (46b
            # against derived; lock §8 t19).
            task.pending_edit = None
            state.user_state, state.agent_state = "correcting", "echoing"
            state.waiting_confirmation = True
            return _reecho_decision(task, turn_no, trigger="already_in_proposal")
        task.pending_edit = vt.extend_edit(buf, text)
        spec, derived = vt.resolve_edit(task.pending_edit, val)
        complete = derived is not None
        v_raw = dictation_value(vt.joined(task.pending_edit))
        full = bool(v_raw) and len(normalize_span(v_raw)) >= max(len(val), 1) and not premature
        if (complete and not premature) or full \
                or len(task.pending_edit["fragments"]) - 1 >= vt.EDIT_BUFFER_MAX_TURNS:
            return _close_edit(state, sig, text, turn_no, val)
        state.user_state, state.agent_state = "correcting", "listening"
        state.next_action = "hold_edit"
        return {"action": "silent", "value": val, "status": task.status, "line": None,
                "trigger": "edit_continuation", "pending_edit": dict(task.pending_edit)}
    if sig.confirm:
        return _close_edit(state, sig, text, turn_no, val)
    if sig.recall or sig.status or sig.complaint or plain_reject:
        d = _close_edit_silent(state, sig, text, turn_no, val)
        if d is not None:
            return d
        task.pending_edit = None
        return _FALLTHROUGH
    if buf["turns"] >= vt.EDIT_BUFFER_MAX_TURNS + 1:
        return _close_edit(state, sig, text, turn_no, val)
    return _FALLTHROUGH


def _close_edit_silent(state: ConversationState, sig: Signals, text: str, turn_no: int,
                       val: str) -> dict | None:
    """Close the buffer only if it resolves into a proposal (spoken echo);
    otherwise None (caller drops the buffer and runs the turn's own intent)."""
    task = state.task
    buf = task.pending_edit or {}
    spec, derived = vt.resolve_edit(buf, val)
    joined_text = vt.joined(buf)
    v_raw = dictation_value(joined_text)
    seg = normalize_span(v_raw) if v_raw else ""
    if seg and spec is not None and spec[2] == seg and len(seg) >= len(val) and seg != val:
        return _propose_correction(state, val, seg, ("restate", None, seg), turn_no,
                                   user_state="dictating", raw=v_raw)   # C5 precedence
    if derived is not None and derived != val:
        return _propose_correction(state, val, derived, spec, turn_no)
    if seg and seg != val and (_is_full_restatement(joined_text, len(seg)) or len(seg) >= len(val)):
        return _propose_correction(state, val, seg, ("restate", None, seg), turn_no,
                                   user_state="dictating", raw=v_raw)
    return None


def _idle(state: ConversationState, sig: Signals, text: str, turn_no: int,
           eng: dict | None = None) -> dict | None:
    """No active task. Rows 1-3, 34-35, 51."""
    if sig.seg:
        # Row 2/3: fresh dictation -> echo (checked BEFORE announcement so a
        # turn that announces AND dictates in one go captures the number,
        # smoke-8 t5).
        state.user_state, state.agent_state = "dictating", "echoing"
        state.next_action = "echo_confirm"
        state.task = Task(kind="dictation", value=sig.seg, status="confirming",
                          topic=_detect_kind(text))
        state.waiting_confirmation = True
        return {"action": "echo_confirm", "value": sig.seg, "status": "confirming",
                "line": _line(ECHO_LINES, turn_no).format(spoken=speak_value(sig.seg)),
                "raw": sig.raw}
    # ROW 51 (owner session_20260831_192745 "memory is not saving"): a FRESH
    # session with NO task + a saved-number query ('मैंने तुझे अपना नंबर शेप
    # करवाया था' / 'क्या लिखा था तुने') -> recall the stored number
    # DETERMINISTICALLY from the long-term store, digit-by-digit. Never the
    # LLM, never a fabrication ('number thodi save kar sakta hoon').
    if eng and (sig.recall or sig.status
                or bool(SAVED_NUMBER_QUERY_RE.search(text or ""))):
        prefer = "mobile" if re.search(r"मोबाइल|mobile|phone", text or "", re.IGNORECASE) else (
                 "account" if re.search(r"अकाउंट|account|खाता", text or "", re.IGNORECASE) else "")
        nums = _saved_numbers(eng, prefer)
        if nums:
            _kind, digits = nums[0]
            state.user_state, state.agent_state = "querying", "answering"
            state.next_action = "recall_memory"
            return {"action": "recall", "value": digits, "status": "confirmed",
                    "line": _line(RECALL_LINES, turn_no).format(spoken=speak_value(digits))}
        if SAVED_NUMBER_QUERY_RE.search(text or ""):
            # the user insists a number was saved, but none is stored ->
            # honest deterministic line (rule 14 discipline, no LLM)
            state.user_state, state.agent_state = "querying", "answering"
            state.next_action = "status"
            return {"action": "status", "value": "", "status": "none",
                    "line": "abhi koi number save nahi hai — bolo, main note kar loon."}

    if sig.announcement:
        # Row 1: "number likh le" -> arm the rail
        state.user_state, state.agent_state = "announcing", "listening"
        state.next_action = "arm"
        state.armed_streak = 0
        state.task = Task(kind="dictation", value="", status="pending",
                          topic=_detect_kind(text))
        return {"action": "arm", "value": "", "status": "pending",
                "line": _line(ARM_LINES, turn_no)}
    if sig.topic_switch:
        # ROW 44 (smoke-12 t29/t32): explicit topic-switch while the last
        # value is confirmed ('वॉइस एजेंट के बारे में बताओ') -> the LLM
        # answers the new topic; the number stays as last-known.
        state.user_state, state.agent_state = "changing_task", "processing"
        state.next_action = "llm"
        return None
    if state.task is not None and state.task.status == "confirmed" \
            and (sig.recall or sig.status):
        # Row 34: confirmed value -> recall on query (status queries too —
        # smoke-12 t31 'बताओ जरा' matches STATUS_RE but not RECALL_RE).
        val = state.task.value or ""
        state.user_state, state.agent_state = "querying", "answering"
        state.next_action = "recall"
        return {"action": "recall", "value": val, "status": "confirmed",
                "line": _line(RECALL_LINES, turn_no).format(spoken=speak_value(val))}
    state.next_action = "llm"
    return None


def _with_digits(state: ConversationState, sig: Signals, text: str, turn_no: int, val: str) -> dict | None:
    """A digit span was heard while a task is active. Rows 4, 8, 13-16, 29-31, 36-38, 40-41, 48."""
    seg = sig.seg
    task = state.task
    state.armed_streak = 0       # digits arrived -> the armed-empty phase is over
    cold = state.accum_gap >= GAP_FRESH_TURNS  # fresh-after-gap span? (ROW 48)
    state.accum_gap = 0          # any digit turn ends the cold phase
    prop = task.proposal
    if not val:
        # Row 4: first real digits after an announcement arm -> accumulate
        # SILENTLY (state-aware: while the user dictates, the agent listens,
        # it does not talk). Confirmed at 'bas' or on a query.
        state.user_state, state.agent_state = "dictating", "listening"
        state.next_action = "silent_accumulate"
        state.task = Task(kind="dictation", value=seg, status="pending")
        return {"action": "silent_accumulate", "value": seg, "status": "pending",
                "line": None, "raw": sig.raw}
    if sig.announcement and sig.write_command:
        # ROW 40 (smoke-11 t8): a NEW dictation announced with a WRITE
        # COMMAND + digits while a value is stored -> the user SWITCHED
        # tasks ('...ab account number likho jara, 026-900-1262'). A NEW
        # task — never append a new task's digits onto the old one. The old
        # task (and any open proposal) is discarded with the switch (L1 A4).
        state.user_state, state.agent_state = "changing_task", "echoing"
        state.next_action = "echo_confirm"
        state.task = Task(kind="dictation", value=seg, status="confirming",
                          topic=_detect_kind(text))
        state.waiting_confirmation = True
        return {"action": "echo_confirm", "value": seg, "status": "confirming",
                "line": _line(ECHO_LINES, turn_no).format(spoken=speak_value(seg)),
                "raw": sig.raw}
    if sig.correction is not None:
        # Rows 8/16/29/36/37: structured correction -> PROPOSE the repaired
        # value (L1: the base is never written here; L3: a correction that
        # carries digits AND resolves completely closes in one turn — lock
        # C3 — so the one-turn corrections of smokes 7/10/13 are unchanged).
        # The already-correct guard comes FIRST (smoke-13 t30: '6 को replace
        # करना है 6 बार 0 से' repeats an already-applied instruction; applying
        # wrong='6' would replace EVERY 6 and mangle the value).
        against = (prop.get("derived") if prop and prop.get("mode") == "correction" else None)
        # M5: a guard for REPEATED instructions only — never when the wrong
        # group is still in the value (that is a repair; see _val_present).
        wrong_present = bool(sig.correction[1]) and sig.correction[1] in val
        if (not wrong_present and sig.correction[2] is not None and sig.correction[2].isdigit()
                and (sig.correction[2] in val
                     or (against and sig.correction[2] in against))):
            # ROW 46b (smoke-12 t15 / smoke-13 t30): the 'correct' digits are
            # ALREADY in the value ('5 बार 0' when 00000 is there) -> confirm
            # the whole number, NEVER wipe it (was: retry-wipe). With an open
            # proposal that already satisfies it -> re-echo the proposal.
            if against and sig.correction[2] in against and sig.correction[2] not in val:
                state.user_state, state.agent_state = "correcting", "echoing"
                state.next_action = "echo_confirm"
                state.waiting_confirmation = True
                task.pending_edit = None
                return _reecho_decision(task, turn_no, trigger="already_in_proposal")
            state.user_state, state.agent_state = "correcting", "echoing"
            state.next_action = "echo_confirm"
            task.status = "confirming"
            task.pending_edit = None
            state.waiting_confirmation = True
            return {"action": "echo_confirm", "value": val, "status": "confirming",
                    "line": _already_correct_line(turn_no, sig.correction[2],
                                                  speak_value(val))}
        applied = _apply_correction(val, sig.correction)
        if applied is not None:
            return _propose_correction(state, val, applied, sig.correction, turn_no,
                                       raw=sig.raw)
        # cannot apply deterministically -> L3: hold the instruction open
        # (was: retry-WIPE of the value). The base survives (M1 + L1).
        return _open_edit(state, text, turn_no, val, spec=sig.correction)
    if sig.full_restatement:
        # Row 14: whole-number re-dictation -> REPLACE proposal + echo
        return _propose_correction(state, val, seg, ("restate", None, seg), turn_no,
                                   user_state="dictating", raw=sig.raw)
    if sig.only_this:
        # ROW 41 (smoke-11 t11): '...sirf itna number, theek hai?' — the
        # user states THE number and trims everything else -> replace proposal
        return _propose_correction(state, val, seg, ("only_this", None, seg), turn_no,
                                   raw=sig.raw)
    if seg == val or (prop and seg == prop.get("derived")):
        # Rows 15/31: exact re-statement of the stored value -> confirm or keep
        if sig.confirm:
            if prop:
                return _confirm_proposal(state, turn_no)
            state.user_state, state.agent_state = "confirming", "echoing"
            state.next_action = "echo_full"
            task.status = "confirming"
            return {"action": "echo_full", "value": val, "status": "confirming",
                    "line": _line(FULL_LINES, turn_no).format(spoken=speak_value(val))}
        state.next_action = "silent"
        return {"action": "silent", "value": val, "status": state.task.status,
                "line": None}
    if sig.query_stored:
        # ROW 45 (smoke-12 t14): a query about the stored value that ALSO has
        # digit-ish words ('मैंने बोला था 5 बट 0 उसका क्या किया तुमने') ->
        # recall as proof, NEVER silently append the span.
        return _recall_decision(state, turn_no)
    if _is_change_frame(text) and not (sig.announcement and sig.write_command):
        # L3: digits inside a change frame that parsed to no spec ('पाइप की
        # जगह 5 बार 0 लिखना है' alone) -> hold the instruction open. Never a
        # silent append/replace of an edit fragment (the t9 mechanism).
        return _open_edit(state, text, turn_no, val)
    if prop:
        # L1: digits while a proposal is open append to the PROPOSAL's
        # derived value (row 30 semantics, moved off the base): the user is
        # still dictating the proposed number. The appended derived has not
        # been heard -> delivery resets to unspoken, so a later confirm
        # speaks the full proposal before anything commits (L2).
        prop = dict(prop)
        prop["derived"] = (prop.get("derived") or "") + seg
        prop["delivery"] = vt.UNSPOKEN
        task.proposal = prop
        task.status = "pending"
        state.user_state, state.agent_state = "dictating", "listening"
        state.next_action = "silent_accumulate"
        return {"action": "silent_accumulate", "value": prop["derived"], "status": "pending",
                "line": None, "raw": sig.raw, "proposal": dict(prop)}
    if cold:
        # ROW 48 (smoke-12 t26): a digit span after a COLD GAP is a FRESH
        # number, not a continuation — the old value was abandoned (t12's
        # fumbled '012...' then 14 turns of grumbling, then '7398' afresh).
        # L1 (owner Q2, 2026-09-04): a silent FRESH PROPOSAL — the base is
        # kept until the new number is heard and confirmed; further digits
        # append to the proposal (row 4 silence semantics preserved).
        task.proposal = vt.propose(val, seg, spec=None, mode="fresh", turn_no=turn_no)
        task.pending_edit = None
        task.status = "pending"
        state.user_state, state.agent_state = "dictating", "listening"
        state.next_action = "silent_accumulate"
        return {"action": "silent_accumulate", "value": seg, "status": "pending",
                "line": None, "raw": sig.raw, "proposal": dict(task.proposal)}
    # Rows 13/30: continuation segment -> append SILENTLY (non-destructive,
    # reversible by rejection — stays immediate under L1, owner Q1)
    new_val = val + seg
    state.user_state, state.agent_state = "dictating", "listening"
    state.next_action = "silent_accumulate"
    task.value = new_val
    task.status = "pending"
    task.proposal = None
    return {"action": "silent_accumulate", "value": new_val, "status": "pending",
            "line": None, "raw": sig.raw}


def _recall_decision(state: ConversationState, turn_no: int) -> dict:
    """Recall distinguishes an open proposal from the base (L1.2)."""
    task = state.task
    val = task.value or ""
    state.user_state, state.agent_state = "querying", "answering"
    state.next_action = "recall"
    prop = task.proposal
    if prop and prop.get("derived") and prop.get("derived") != val:
        return {"action": "recall", "value": prop["derived"], "status": task.status,
                "line": _line(PROPOSAL_RECALL_LINES, turn_no).format(
                    spoken=speak_value(prop["derived"]), base=speak_value(val)),
                "proposal": dict(prop)}
    return {"action": "recall", "value": val, "status": task.status,
            "line": _line(RECALL_LINES, turn_no).format(spoken=speak_value(val))}


def _confirm_proposal(state: ConversationState, turn_no: int) -> dict:
    """Confirm while a proposal is open: COMMIT only if its echo was actually
    delivered (L2); otherwise speak the FULL proposed number (row 17
    semantics on the proposal) — never a silent commit."""
    task = state.task
    prop = task.proposal or {}
    if vt.confirmable(prop):
        return _commit(state, turn_no)
    state.user_state, state.agent_state = "confirming", "echoing"
    state.next_action = "echo_full"
    task.status = "confirming"
    derived = prop.get("derived") or task.value
    trigger = "proposal_echo" if prop.get("delivery") == vt.UNSPOKEN else "unheard_echo"
    return {"action": "echo_full", "value": derived, "status": "confirming",
            "line": _line(FULL_LINES, turn_no).format(spoken=speak_value(derived)),
            "trigger": trigger, "proposal": dict(prop)}


def _val_present(state: ConversationState, sig: Signals, text: str, turn_no: int, val: str) -> dict | None:
    """No digits, but a value is stored. Rows 17-28, 32-33, 42, 44, 46."""
    # M1/M2/M4 (owner session 20260902_184247): an EDIT-INTENT turn over a
    # stored value must repair, never wipe. When the text-only parse missed
    # it, try the val-aware spec (removal '1242 नहीं है'; change-frame pair
    # '9000...900...कम हो ज'). The existing correction branch below applies
    # it; a value is cleared only by a PLAIN whole-turn rejection.
    val_aware = False
    if (sig.correction is None and val
            and (sig.reject or sig.confirm or _is_change_frame(text))):
        sig.correction = _val_aware_correction(text, val)
        val_aware = sig.correction is not None
    # NOTE: val_aware replace-pairs (9000 -> 900) must SKIP the smoke-13
    # already-correct guard below — the 'new' digits are usually a substring
    # of the stored value (900 ⊂ 9000), and the guard would echo the old
    # value instead of applying the edit.
    if sig.topic_switch:
        # ROW 44 (smoke-12 t29/t32): explicit topic-switch while a value is
        # pending/confirming ('ज़िन के बारे में बताओ', 'वॉइस एजेंट के बारे
        # में बताओ') -> CLOSE the task to confirmed (last-known), let the LLM
        # answer the new topic. Was: stuck re-echoing the number (t29
        # echo_full, t32 recall).
        state.user_state, state.agent_state = "changing_task", "processing"
        state.next_action = "llm"
        state.task = Task(kind="dictation", value=val, status="confirmed")
        state.waiting_confirmation = False
        return None
    if sig.announcement and sig.write_command:
        # ROW 49 (smoke-12 t20): a NEW write-command while a value is stored
        # ('एक mobile number लिखो' — no digits yet) -> RE-ARM a fresh task
        # (was: silent fallthrough). With digits it is row 40 (replace+echo).
        state.user_state, state.agent_state = "announcing", "listening"
        state.next_action = "arm"
        state.armed_streak = 0
        state.task = Task(kind="dictation", value="", status="pending",
                          topic=_detect_kind(text))
        return {"action": "arm", "value": "", "status": "pending",
                "line": _line(ARM_LINES, turn_no)}
    task = state.task
    prop = task.proposal
    if sig.recall:
        # Rows 19/27/43: query/recall -> re-speak the stored value (an open
        # proposal is spoken AS a proposal, distinct from the base — L1.2)
        return _recall_decision(state, turn_no)
    if sig.correction is not None:
        # already-correct guard FIRST (smoke-13 t30): a repeated instruction
        # whose 'correct' is already in the value -> confirm, never re-apply
        # a wrong substring like '6' that would mangle every 6. SKIPPED for
        # val-aware replace pairs (9000->900 — the new digits 900 ⊂ stored
        # 9000 by construction; the guard must not echo the old value).
        against = (prop.get("derived") if prop and prop.get("mode") == "correction" else None)
        # M5 (owner session 20260905_102221 t9): the guard is for a REPEATED
        # instruction — its 'wrong' group is already gone from the value. When
        # the wrong group IS still in the value ('9000 नहीं है, 900 है' with
        # 9000 stored) the user is asking for a repair, and the 'correct'
        # digits being a substring of the value (900 ⊂ 9000) proves nothing.
        wrong_present = bool(sig.correction[1]) and sig.correction[1] in val
        if (not val_aware and not wrong_present and sig.correction[2] is not None
                and sig.correction[2].isdigit()
                and (sig.correction[2] in val or (against and sig.correction[2] in against))):
            if against and sig.correction[2] in against and sig.correction[2] not in val:
                state.user_state, state.agent_state = "correcting", "echoing"
                state.next_action = "echo_confirm"
                state.waiting_confirmation = True
                task.pending_edit = None
                return _reecho_decision(task, turn_no, trigger="already_in_proposal")
            # ROW 46b (smoke-12 t15 / smoke-13 t30): 'correct' digits already
            # in the value -> confirm, never wipe.
            state.user_state, state.agent_state = "correcting", "echoing"
            state.next_action = "echo_confirm"
            task.status = "confirming"
            task.pending_edit = None
            state.waiting_confirmation = True
            return {"action": "echo_confirm", "value": val, "status": "confirming",
                    "line": _already_correct_line(turn_no, sig.correction[2],
                                                  speak_value(val))}
        if sig.correction[2] is None:
            # L3 (lock §4, the t8 trace): a REMOVAL-only spec is by
            # construction the prefix of a replacement — hold the instruction
            # open instead of applying it; the continuation ('...की जगह 5 बार
            # 0') closes it into ONE proposal. The base is untouched.
            return _open_edit(state, text, turn_no, val, removal_wrong=sig.correction[1])
        applied = _apply_correction(val, sig.correction)
        if applied is not None:
            # complete single-turn correction -> proposal (L1), closes at once (C3)
            return _propose_correction(state, val, applied, sig.correction, turn_no)
        # M1 + L3: an edit-intent turn that cannot be applied keeps the value
        # and holds the instruction open (was: retry with correction line).
        return _open_edit(state, text, turn_no, val, spec=sig.correction)
    if sig.complaint:
        # Rows 20/28: writing-complaint -> RECALL as PROOF, never clear
        state.user_state, state.agent_state = "complaining", "answering"
        d = _recall_decision(state, turn_no)
        state.next_action = "recall"
        return d
    if sig.reject:
        # M6 (owner session 20260905_102221 t21): confirm AND reject words in
        # the same breath ('ठीक है, ओके, चलो कोई नहीं') is UNCLEAR — neither a
        # confirmation nor a rejection. The value stays; ask which it was.
        # (Was: _is_plain_reject -> retry wiped the base, then the retry line
        # was cancelled before audio — the number vanished in silence.)
        if sig.confirm:
            state.user_state, state.agent_state = "confirming", "asking"
            state.next_action = "clarify"
            task.pending_edit = None
            state.waiting_confirmation = True
            return {"action": "clarify", "value": val, "status": task.status,
                    "line": _line(MIXED_CLARIFY_LINES, turn_no).format(spoken=speak_value(val)),
                    "trigger": "mixed_confirm_reject"}
        # Rows 18/26: a PLAIN whole-turn rejection -> clear + retry (of an
        # accumulation) or REVERT to the base (of a proposal — L1.4). M1: an
        # edit-intent rejection (digits/digit-words/change frame present but
        # unresolved) never wipes — L3 holds the instruction open.
        if not _is_plain_reject(text):
            return _open_edit(state, text, turn_no, val)
        if prop:
            return _revert(state, turn_no)
        state.user_state, state.agent_state = "confirming", "asking"
        state.next_action = "retry"
        state.task = Task(kind="dictation", value="", status="pending")
        return {"action": "retry", "value": "", "status": "pending",
                "line": _line(RETRY_LINES, turn_no)}
    if sig.confirm:
        if prop:
            return _confirm_proposal(state, turn_no)
        if state.task.status == "pending":
            # Row 17: 'bas / haan / theek' while accumulating -> speak FULL value
            state.user_state, state.agent_state = "confirming", "echoing"
            state.next_action = "echo_full"
            task.status = "confirming"
            return {"action": "echo_full", "value": val, "status": "confirming",
                    "line": _line(FULL_LINES, turn_no).format(spoken=speak_value(val))}
        # Row 25: confirming -> confirm -> ack — L2: only if the echo of the
        # base was actually heard; an unheard echo re-speaks the full value.
        if eng_echo_unheard(state):
            state.user_state, state.agent_state = "confirming", "echoing"
            state.next_action = "echo_full"
            return {"action": "echo_full", "value": val, "status": "confirming",
                    "line": _line(FULL_LINES, turn_no).format(spoken=speak_value(val)),
                    "trigger": "unheard_echo"}
        state.user_state, state.agent_state = "confirming", "speaking"
        state.next_action = "confirm_ack"
        task.status = "confirmed"
        state.waiting_confirmation = False
        return {"action": "confirm_ack", "value": val, "status": "confirmed",
                "line": _line(ACK_LINES, turn_no).format(spoken=speak_value(val))}
    if sig.status:
        d = _recall_decision(state, turn_no)
        return d
    if sig.cont:
        # Rows 21/32: continuation cue -> hold line
        state.user_state, state.agent_state = "continuing", "holding"
        state.next_action = "hold"
        return {"action": "hold", "value": val, "status": state.task.status,
                "line": _line(HOLD_LINES, turn_no)}
    if sig.abandon or sig.dearm:
        # Rows 22/33: explicit abandon / new non-number detail -> release to LLM
        state.user_state, state.agent_state = "abandoning", "idle"
        state.next_action = "llm"
        state.task = Task(kind="dictation", value="", status="discarded")
        return None
    if sig.greeting_line:
        # ROW 42 (smoke-11 t15/t16): greeting while task active -> answer with
        # the greeting line, KEEP the task
        state.user_state, state.agent_state = "conversing", "holding"
        state.next_action = "greet"
        return {"action": "greet", "value": val, "status": state.task.status,
                "line": sig.greeting_line}
    if _is_change_frame(text):
        # L5 rule 1 / L3: an EDIT-INTENT turn (change frame, no resolvable
        # spec — the t15-t17 class) is never released to the LLM by word
        # count; it holds the instruction open (the LLM never sees it).
        return _open_edit(state, text, turn_no, val)
    if sig.filler_len <= 6:
        if sig.number_talk and sig.filler_len >= 3:
            # ROW 50 (owner T10): short speech ABOUT the stored number that
            # isn't a clean query/dictation ('कि यह काशिड नंबर आ गया') ->
            # deterministic "didn't catch that" line, NEVER silence. (The
            # acoustic corr was never a decision input — low corr simply
            # means NOT-an-echo = real speech; using it clarified every
            # clearly-heard turn, smoke-13 t23/t37.)
            state.user_state, state.agent_state = "dictating", "asking"
            state.next_action = "clarify"
            return {"action": "clarify", "value": val, "status": state.task.status,
                    "line": _line(CLARIFY_LINES, turn_no)}
        # Row 23: short filler while dictating -> silent, KEEP the value
        state.user_state, state.agent_state = "dictating", "listening"
        state.next_action = "silent"
        return {"action": "silent", "value": val, "status": state.task.status,
                "line": None}
    # ROW 24 CHANGED (smoke-11 t17): long non-dictation talk while a task is
    # active -> the TURN goes to the LLM (it answers the user) but the TASK
    # is KEPT (never discarded) — so a later recall ('mera mobile number
    # mujhe bata') still speaks the stored value instead of "mujhe nahi pata"
    # (that was smoke-11 t19, the worst failure).
    state.user_state, state.agent_state = "conversing", "processing"
    state.next_action = "llm"
    return None


def _armed_empty(state: ConversationState, sig: Signals, text: str, turn_no: int) -> dict | None:
    """Armed but no digits yet. Rows 5-12."""
    if sig.correction is not None:
        wrong = sig.correction[1] or ""
        correct = sig.correction[2] or ""
        state.user_state, state.agent_state = "correcting", "asking"
        state.next_action = "retry"
        return {"action": "retry", "value": "", "status": "pending",
                "line": _correction_line(turn_no, wrong, correct)}
    if sig.complaint:
        state.user_state, state.agent_state = "complaining", "asking"
        state.next_action = "retry"
        return {"action": "retry", "value": "", "status": "pending",
                "line": _line(COMPLAINT_EMPTY_LINES, turn_no)}
    if sig.reject:
        state.user_state, state.agent_state = "confirming", "asking"
        state.next_action = "retry"
        return {"action": "retry", "value": "", "status": "pending",
                "line": _line(RETRY_LINES, turn_no)}
    if sig.abandon:
        state.user_state, state.agent_state = "abandoning", "idle"
        state.next_action = "llm"
        state.task = Task(kind="dictation", value="", status="discarded")
        return None
    if sig.announcement and sig.write_command:
        # Row 9: re-announcement with a write command -> stay armed, silent
        # (pin); but after 2+ silent armed-empty turns the REPEATED command
        # gets a nudge (smoke-12 t20 'एक mobile number लिखो' was swallowed
        # silent into the black hole).
        if state.armed_streak >= 2:
            state.user_state, state.agent_state = "announcing", "asking"
            state.next_action = "nudge"
            return {"action": "nudge", "value": "", "status": "pending",
                    "line": _line(NUDGE_LINES, turn_no)}
        state.user_state, state.agent_state = "announcing", "listening"
        state.next_action = "silent"
        return {"action": "silent", "value": "", "status": "pending", "line": None}
    if sig.topic_switch:
        # ROW 44 (smoke-12): topic-switch while armed-empty ('वॉइस एजेंट के
        # बारे में बताओ' before any digits) -> LLM answers, task KEPT.
        state.user_state, state.agent_state = "changing_task", "processing"
        state.next_action = "llm"
        return None
    if sig.recall or sig.status:
        # Rows 5/10: status query while armed-empty -> say so (the LLM used to
        # FABRICATE a number here — smoke-6 t16)
        state.user_state, state.agent_state = "querying", "answering"
        state.next_action = "status"
        return {"action": "status", "value": "", "status": "pending",
                "line": _line(STATUS_LINES, turn_no)}
    if sig.cont:
        state.user_state, state.agent_state = "continuing", "holding"
        state.next_action = "hold"
        return {"action": "hold", "value": "", "status": "pending",
                "line": _line(HOLD_LINES, turn_no)}
    if sig.dearm:
        state.user_state, state.agent_state = "changing_task", "idle"
        state.next_action = "llm"
        state.task = Task(kind="dictation", value="", status="discarded")
        return None
    if sig.greeting_line:
        state.user_state, state.agent_state = "conversing", "holding"
        state.next_action = "greet"
        return {"action": "greet", "value": "", "status": "pending",
                "line": sig.greeting_line}
    # Fallback (rows 5-12 + 47): no intent matched. Task stays armed; the
    # turn decides between silence (interjection pin) and a nudge once the
    # user has waited 2+ turns (smoke-12 t18-t24 was a 9-turn silent black
    # hole; the smoke-6 pins hold because they are single-turn, streak=0).
    state.armed_streak += 1
    if state.armed_streak >= 2:
        # ROW 47 (smoke-12): 2+ armed-empty turns with no digits -> nudge
        state.user_state, state.agent_state = "dictating", "asking"
        state.next_action = "nudge"
        return {"action": "nudge", "value": "", "status": "pending",
                "line": _line(NUDGE_LINES, turn_no)}
    # any other filler -> silent, stay armed (smoke-6 t8 was 8 words)
    state.user_state, state.agent_state = "dictating", "listening"
    state.next_action = "silent"
    return {"action": "silent", "value": "", "status": "pending", "line": None}


# ---------------------------------------------------------------------------
# ROW 51 helpers — long-term saved-number memory (owner session_20260831_192745)
# ---------------------------------------------------------------------------
_NUMBER_KIND_RE = [
    (re.compile(r"मोबाइल|mobile|फोन|phone", re.IGNORECASE), "mobile"),
    (re.compile(r"अकाउंट|account|खाता", re.IGNORECASE), "account"),
]


def _detect_kind(text: str) -> str:
    """Best-effort kind of the number being dictated (mobile/account/saved)."""
    for rx, kind in _NUMBER_KIND_RE:
        if rx.search(text or ""):
            return kind
    return "saved"


def _persist_number(eng: dict, task) -> None:
    """Commit a user-confirmed number to the long-term store (explicit,
    immediate). Content keeps the kind so recall can prefer it."""
    try:
        store = eng.get("store")
        sess = eng.get("sess")
        if not store or not sess or not getattr(sess, "owner_id", None):
            return
        kind = task.topic or "saved"
        content = f"user's {kind} number: {task.value}"
        store.commit(sess.owner_id,
                     {"type": "saved_number", "content": content,
                      "criterion": "explicit"}, immediate=True)
        print(f"[Memory] saved-number committed ({kind}): {task.value}")
    except Exception as e:
        print(f"[Memory] number persist failed: {type(e).__name__}: {e}")


def _saved_numbers(eng: dict, prefer: str = "") -> list[tuple[str, str]]:
    """[(kind, digits)] of saved numbers in the long-term store, most
    recent first (view is last_seen DESC). Only lines that are actually
    saved numbers are parsed (digit runs in other facts are ignored)."""
    try:
        sess = eng.get("sess")
        if sess is None:
            return []
        lines = sess.memory_view() or []
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for line in lines:
        low = line.lower()
        if "number" not in low:
            continue
        m = re.search(r"(\d{4,})", line)
        if not m:
            continue
        if "mobile" in low:
            kind = "mobile"
        elif "account" in low:
            kind = "account"
        else:
            kind = "saved"
        out.append((kind, m.group(1)))
    if prefer:
        for k, d in out:
            if k == prefer:
                return [(k, d)]
    return out
