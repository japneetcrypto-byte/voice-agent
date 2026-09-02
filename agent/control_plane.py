"""Control Plane V1 — P1 shadow-only core (owner-approved design lock:
docs/CONTROL_PLANE_P1_LOCK.md).

P1 scope (locked): ONE pure, deterministic `control_turn()` producing the
unified Decision; the state-conditioned precedence table (G0..G7) over EXISTING
detectors; and a read-only Decision Safety / Invariant gate
(`validate_decision`). Wired in shadow mode only (telemetry, zero behavior
change).

Architecture (locked):
  detect_signals (Hindi+Hinglish adapter, this module)  emits SignalContract
  control_turn   (language-neutral CORE)                produces the Decision (ONLY authority)
  validate_decision (read-only gate, language-neutral)  returns (bool, violations)

Language-agnostic principle: the CORE (control_turn / validate_decision /
build_snapshot) contains no language tokens, no Devanagari, no regexes, no
imports. The ONLY language vocabulary in this module lives inside
`detect_signals` — the Hindi+Hinglish adapter seam (P1 deliberately keeps the
adapter here; a future language = a new adapter emitting the SAME
SignalContract, core unchanged). The adapter reuses the existing detectors
verbatim (precision_rail / turn_controller / stt_validation / fused_turn /
state_updater) — this module defines ZERO new patterns.

Structural pins (tested): zero pattern objects (no compile calls), no `import
re`, exactly one function returns a Decision, the validator returns a tuple
(never a Decision), and the validator has no store/LLM/detector access.

Fail-closed: an invalid Decision emits NO shadow decision and logs
INVARIANT_VIOLATION; production behavior is today's chain, which runs
unchanged (P1 shadow never influences it).
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Enums (locked §2 + §9 S1)
# ---------------------------------------------------------------------------
TURN_INTENTS = ("NORMAL", "CONTINUE", "STOP", "CORRECT", "CONFIRM", "REJECT", "REPEAT")
MEMORY_INTENTS = ("NONE", "POSSIBLE_SAVE", "SAVE", "UPDATE", "RECALL", "CORRECT", "FORGET")
CONV_STATES = ("NORMAL", "CONTINUING", "CONFIRMING", "SAVING", "RECALLING",
               "CORRECTING", "TASK_ACTIVE")
OWNERS = ("USER", "AGENT", "SYSTEM")
DELIVERY_MODES = ("NEW", "CONTINUE", "HOLD", "SILENT")
ACTIONS = ("llm", "greeting", "rail_echo", "rail_accumulate", "rail_confirm",
           "rail_recall", "rail_repair", "rail_arm", "suppress", "drop",
           "clarify", "idle")

# Locked llm_instruction directive-key set (enumerated §1 — no ellipsis).
# Each key is semantic + language-neutral; the response layer renders it into
# the session language. Delivery CONTENT lives in delivery_state, never here.
LLM_INSTRUCTIONS = frozenset({None, "CONTINUE", "RECALL_MEMORY",
                              "ACKNOWLEDGE_SAVE", "ACKNOWLEDGE_STOP",
                              "ACKNOWLEDGE_FORGET", "SUPERSEDE_MEMORY_HOOK",
                              "GREET"})

DECISION_FIELDS = ("turn_intent", "memory_intent", "conv_state", "turn_owner",
                   "delivery_mode", "action", "llm_instruction")


@dataclass(frozen=True)
class Decision:
    """The unified control-plane decision (locked §1). Frozen + deterministic."""
    turn_intent: str            # TURN_INTENTS (conversational axis only)
    memory_intent: str          # MEMORY_INTENTS (memory axis)
    conv_state: str             # CONV_STATES — state AFTER this turn
    turn_owner: str             # OWNERS
    delivery_mode: str          # DELIVERY_MODES
    action: str                 # ACTIONS — routing, not policy
    llm_instruction: str | None # LLM_INSTRUCTIONS (None = no LLM call)


# ---------------------------------------------------------------------------
# SignalContract (locked §4.1) — the language-neutral seam
# ---------------------------------------------------------------------------
def default_signals() -> dict:
    """A not-detected SignalContract (every canonical signal at its default).
    A language adapter never invents a new rule/signal/enum: an undetectable
    signal stays at its default — that is an adapter coverage gap, surfaced by
    the conformance tests, fixed in the adapter."""
    return {
        "digits_present": False, "digits_value": "", "digits_cluster": False,
        "confirm": False, "reject": False, "questionish": False,
        "claim": False, "complaint": False,
        "recall": False, "saved_number_query": False, "status_query": False,
        "query_stored": False, "only_this": False, "restart": False,
        "abandon": False, "dearm_detail": False, "continue_cue": False,
        "write_command": False, "announce": False, "topic_switch": False,
        "save_intent": False,
        "stop": False, "greeting_first_word": False, "continuation_fragment": False,
        "route": "normal", "turn_relation": "content", "mode": "VENT",
        "correction_spec": None,
    }


SIGNAL_KEYS = tuple(default_signals())


# ---------------------------------------------------------------------------
# Hindi+Hinglish adapter — detect_signals (the ONLY language seam in P1)
# ---------------------------------------------------------------------------
# STOP cues for the locked G5 row ("बस" NORMAL -> STOP). Exact-token, via the
# existing normalize_for_classify; the SAME word is a CONFIRM signal during
# dictation (the core resolves by state: G2 CONFIRM vs G5 STOP). This set is
# the Hindi+Hinglish adapter's vocabulary — a future English adapter would
# emit the SAME `stop` key with its own tokens ("stop", "enough", ...).
_STOP_TOKENS = frozenset({"bas", "ruko", "stop", "बस", "रुको"})


def detect_signals(text: str | None, turn_no: int, snapshot: dict | None = None) -> dict:
    """Emit the canonical SignalContract from the EXISTING detectors (the
    Hindi+Hinglish adapter). Pure + deterministic: no LLM, no I/O, no state
    mutation. A signal without an existing detector stays at its not-detected
    default (P1 adds zero new patterns)."""
    from agent.precision_rail import (
        dictation_value, normalize_span, _is_pure_digit_utterance, _is_confirm,
        _is_reject, _parse_correction, RECALL_RE, SAVED_NUMBER_QUERY_RE,
        STATUS_RE, QUERY_STORED_RE, ONLY_THIS_RE, RESTART_RE, ABANDON_RE,
        DEARM_DETAIL_RE, CONTINUE_CUE_RE, WRITE_COMMAND_RE,
        is_dictation_announcement, TOPIC_SWITCH_RE, _NUMBER_TOPIC_RE,
        CLAIM_RE, COMPLAINT_RE, QUESTIONISH_RE)
    from agent.turn_controller import (greeting_line_for,
                                       decide as turn_controller_decide)
    from agent.stt_validation import classify_turn_relation, normalize_for_classify
    from agent.fused_turn import _SAVE_INTENT_RE
    from agent.state_updater import classify_mode
    from agent.precision_rail import _CHANGE_FRAME_RE

    t = text or ""
    sig = default_signals()

    v_raw = dictation_value(t)
    digits_value = normalize_span(v_raw) if v_raw else ""
    if not digits_value and _is_pure_digit_utterance(t):
        digits_value = normalize_span(t)
    sig["digits_present"] = bool(digits_value)
    sig["digits_value"] = digits_value
    sig["digits_cluster"] = bool(v_raw and any(ch in v_raw for ch in " -.,"))

    # Mirrors the rail's M3 confirm-guard (correction-repair fix 2026-09-02):
    # a confirm word inside a change frame is an edit, not a confirm — keeps
    # the shadow's Decision consistent with the fixed chain.
    sig["confirm"] = _is_confirm(t) and not bool(_CHANGE_FRAME_RE.search(t))
    sig["reject"] = _is_reject(t)
    sig["questionish"] = bool(QUESTIONISH_RE.search(t))
    sig["claim"] = bool(CLAIM_RE.search(t))
    sig["complaint"] = bool(COMPLAINT_RE.search(t))
    sig["recall"] = bool(RECALL_RE.search(t))
    sig["saved_number_query"] = bool(SAVED_NUMBER_QUERY_RE.search(t))
    sig["status_query"] = bool(STATUS_RE.search(t))
    sig["query_stored"] = bool(QUERY_STORED_RE.search(t))
    sig["only_this"] = bool(ONLY_THIS_RE.search(t))
    sig["restart"] = bool(RESTART_RE.search(t))
    sig["abandon"] = bool(ABANDON_RE.search(t))
    sig["dearm_detail"] = bool(DEARM_DETAIL_RE.search(t))
    sig["continue_cue"] = bool(CONTINUE_CUE_RE.search(t))
    sig["write_command"] = bool(WRITE_COMMAND_RE.search(t))
    sig["announce"] = is_dictation_announcement(t)
    sig["topic_switch"] = bool(TOPIC_SWITCH_RE.search(t)) and not bool(
        _NUMBER_TOPIC_RE.search(t))
    sig["save_intent"] = bool(_SAVE_INTENT_RE.search(t))

    # STOP: exact normalized token (single-word "bas"/"ruko"/...). Multi-word
    # "roko mat" (keep going) and "bas itna" (only this) never match.
    norm = normalize_for_classify(t)
    sig["stop"] = norm in _STOP_TOKENS

    sig["greeting_first_word"] = greeting_line_for(t, turn_no) is not None
    sig["continuation_fragment"] = turn_controller_decide(t, 0)[0] == "suppress"
    sig["route"] = (snapshot or {}).get("route_action") or "normal"
    sig["turn_relation"] = classify_turn_relation(t)
    sig["mode"] = classify_mode(t)
    sig["correction_spec"] = _parse_correction(t)
    return sig


# ---------------------------------------------------------------------------
# Read-only state snapshot (locked §4: engine["conv"] / ["detail"] /
# ["wait_streak"]; the shadow never mutates them)
# ---------------------------------------------------------------------------
def _memory_has_record(engine: dict | None) -> bool:
    """Opaque adapter-side flag: does the session hold any long-term record?
    Read at the wiring boundary only; swallowed on any error (default False).
    The CORE never touches the store — this is the one place the snapshot
    builder reads a session object, and it returns a plain bool."""
    try:
        sess = (engine or {}).get("sess")
        if sess is None:
            return False
        return bool(sess.memory_view())
    except Exception:
        return False


def _phase(task: dict | None) -> str:
    """Input conversation phase from the dictation task (language-neutral)."""
    if not task:
        return "NORMAL"
    status = task.get("status")
    if status == "confirming":
        return "CONFIRMING"
    if status == "pending":
        return "TASK_ACTIVE"
    return "NORMAL"


def pre_state(engine: dict | None) -> dict:
    """Pre-chain state capture for the shadow: shallow copies of the engine
    keys the chain is about to MUTATE (the dictation/conv state is reassigned
    by the controller as a side effect of deciding). The shadow must decide on
    the SAME inputs the chain saw — never on the chain's side effects.
    Call this BEFORE precision_rail_decide / the turn-gate mutations."""
    eng = engine or {}
    return {
        "conv": dict(eng["conv"]) if isinstance(eng.get("conv"), dict) else None,
        "dictation": (dict(eng["dictation"]) if isinstance(eng.get("dictation"), dict)
                      else None),
        "detail": dict(eng["detail"]) if isinstance(eng.get("detail"), dict) else None,
        "wait_streak": eng.get("wait_streak"),
        "sess": eng.get("sess"),
    }


def build_snapshot(engine: dict | None = None, *, route_drop: bool = False,
                   route_action: str = "normal") -> dict:
    """Read-only snapshot of the runtime state the chain already consumed.
    Pure over the engine dict; never mutates it; no aliasing into it."""
    eng = engine or {}
    conv = eng.get("conv") or {}
    dictation = eng.get("dictation") or {}
    detail = eng.get("detail") or {}
    task = None
    if dictation:
        task = {
            "kind": dictation.get("kind", "dictation"),
            "value": str(dictation.get("value") or ""),
            "status": dictation.get("status") or "pending",
            "topic": conv.get("task_topic") or "",
        }
    try:
        wait_streak = int(eng.get("wait_streak") or 0)
    except Exception:
        wait_streak = 0
    return {
        "conv_state": _phase(task),
        "task": task,
        "task_value_present": bool(task and task["value"]),
        "delivery_active": bool(detail.get("active")),
        "wait_streak": wait_streak,
        "memory_has_record": _memory_has_record(eng),
        "route_drop": bool(route_drop),
        "route_action": route_action or "normal",
    }


# ---------------------------------------------------------------------------
# CORE — the precedence table (locked §3) and the ONLY Decision authority
# ---------------------------------------------------------------------------
# Guard ranks G0..G7. Higher rank = more specific / higher impact. ONE ordered
# list; the FIRST matching guard's Decision wins; there is no second
# decision-maker. The ordering itself is pinned by tests.
#
#   G0 transport/safety pre-filter (route_drop)          -> drop
#   G1 task repair/correct (TASK_ACTIVE/CONFIRMING)      -> CORRECT + rail_repair
#   G2 confirm/reject (REJECT > CONFIRM; dictation-owned)
#   G3 digits (accumulate/echo; system-owned)
#   G4 memory-explicit verbs (SAVE / memory-CORRECT / RECALL)
#   G5 conversational (STOP > REPEAT > CONTINUE)
#   G6 greeting (first-word)
#   G7 default (LLM)


def _g0(signals: dict, snapshot: dict):
    if snapshot.get("route_drop"):
        return Decision("NORMAL", "NONE", "NORMAL", "SYSTEM", "HOLD", "drop", None)
    return None


def _g1(signals: dict, snapshot: dict):
    if snapshot.get("conv_state") not in ("TASK_ACTIVE", "CONFIRMING"):
        return None
    repair = (signals.get("correction_spec") is not None
              or (signals.get("only_this") and signals.get("digits_present"))
              or (signals.get("restart") and signals.get("digits_present"))
              or (signals.get("reject") and signals.get("digits_present")))
    if not repair:
        return None
    return Decision("CORRECT", "POSSIBLE_SAVE", "CONFIRMING", "SYSTEM", "NEW",
                    "rail_repair", None)


def _g2(signals: dict, snapshot: dict):
    phase = snapshot.get("conv_state")
    value = bool(snapshot.get("task_value_present"))
    confirm = bool(signals.get("confirm"))
    reject = bool(signals.get("reject"))
    digits = bool(signals.get("digits_present"))
    if phase == "CONFIRMING":
        if reject:
            return Decision("REJECT", "NONE", "TASK_ACTIVE", "SYSTEM", "NEW",
                            "rail_repair", None)
        if confirm:
            return Decision("CONFIRM", "SAVE", "NORMAL", "SYSTEM", "NEW",
                            "rail_confirm", None)
    if phase == "TASK_ACTIVE":
        if reject and value:
            return Decision("REJECT", "NONE", "TASK_ACTIVE", "SYSTEM", "NEW",
                            "rail_repair", None)
        if confirm and value:
            return Decision("CONFIRM", "SAVE", "CONFIRMING", "SYSTEM", "NEW",
                            "rail_confirm", None)
        if confirm and not value and not digits:
            # backchannel "haan" while armed-empty: stay silent, task kept
            return Decision("NORMAL", "NONE", "TASK_ACTIVE", "SYSTEM", "SILENT",
                            "suppress", None)
    return None


def _g3(signals: dict, snapshot: dict):
    if not signals.get("digits_present"):
        return None
    if signals.get("query_stored"):
        return None  # recall-as-proof wins over silent append (ROW 45)
    phase = snapshot.get("conv_state")
    if phase == "TASK_ACTIVE":
        return Decision("NORMAL", "POSSIBLE_SAVE", "TASK_ACTIVE", "SYSTEM",
                        "SILENT", "rail_accumulate", None)
    if not signals.get("greeting_first_word"):
        return Decision("NORMAL", "POSSIBLE_SAVE", "TASK_ACTIVE", "SYSTEM",
                        "NEW", "rail_echo", None)
    return None  # "hello 9935..." unarmed -> G6 greeting, digits not fired


def _g4(signals: dict, snapshot: dict):
    # sub-order: SAVE > memory-CORRECT > RECALL (FORGET/UPDATE: no existing
    # detector in P1 — the missing signal stays not-detected by design)
    if signals.get("save_intent"):
        return Decision("NORMAL", "SAVE", "SAVING", "USER", "NEW", "llm",
                        "ACKNOWLEDGE_SAVE")
    phase = snapshot.get("conv_state")
    if signals.get("reject") and not signals.get("questionish") and phase == "NORMAL":
        if snapshot.get("memory_has_record"):
            return Decision("CORRECT", "CORRECT", "CORRECTING", "USER", "NEW",
                            "llm", "SUPERSEDE_MEMORY_HOOK")
        return Decision("CORRECT", "NONE", "NORMAL", "USER", "NEW", "llm", None)
    rec = (signals.get("recall") or signals.get("saved_number_query")
           or signals.get("status_query") or signals.get("query_stored"))
    if rec and not snapshot.get("delivery_active") and (
            snapshot.get("task_value_present") or snapshot.get("memory_has_record")
            or signals.get("saved_number_query")):
        if snapshot.get("memory_has_record") or snapshot.get("task_value_present"):
            return Decision("NORMAL", "RECALL", "RECALLING", "SYSTEM", "NEW",
                            "rail_recall", None)
        return Decision("NORMAL", "RECALL", "RECALLING", "USER", "NEW", "llm",
                        "RECALL_MEMORY")
    return None


def _g5(signals: dict, snapshot: dict):
    if signals.get("stop"):
        return Decision("STOP", "NONE", "NORMAL", "USER", "NEW", "llm",
                        "ACKNOWLEDGE_STOP")
    if signals.get("recall") and snapshot.get("delivery_active"):
        return Decision("REPEAT", "NONE", "CONTINUING", "USER", "CONTINUE",
                        "llm", "CONTINUE")
    if signals.get("continue_cue") and snapshot.get("delivery_active"):
        return Decision("CONTINUE", "NONE", "CONTINUING", "USER", "CONTINUE",
                        "llm", "CONTINUE")
    return None


def _g6(signals: dict, snapshot: dict):
    if signals.get("greeting_first_word"):
        return Decision("NORMAL", "NONE", "NORMAL", "SYSTEM", "NEW", "greeting",
                        None)
    return None


def _g7(signals: dict, snapshot: dict):
    return Decision("NORMAL", "NONE", "NORMAL", "USER", "NEW", "llm", None)


GUARDS = (_g0, _g1, _g2, _g3, _g4, _g5, _g6, _g7)


def control_turn(signals: dict | None, snapshot: dict | None) -> Decision:
    """THE single Decision authority (locked §3). Pure + deterministic over
    the canonical SignalContract and the state snapshot. The FIRST matching
    guard wins; the default (G7) is the LLM turn. Never re-derives signals."""
    signals = signals or {}
    snapshot = snapshot or {}
    for guard in GUARDS:
        d = guard(signals, snapshot)
        if d is not None:
            return d
    return _g7(signals, snapshot)


# ---------------------------------------------------------------------------
# Decision Safety / Invariant gate (locked §9) — validation, NOT a second
# authority: read-only, returns (bool, violations), never a Decision, never
# re-derives anything, no store/LLM access.
# ---------------------------------------------------------------------------
_DICTATION_ACTIONS = frozenset({"rail_accumulate", "rail_echo", "rail_confirm",
                                "rail_recall", "rail_repair", "rail_arm",
                                "suppress"})
_SYSTEM_OWNED_ACTIONS = frozenset(_DICTATION_ACTIONS | {"drop", "greeting",
                                                        "clarify"})
# Actions that can legitimately carry a memory intent in P1 (the LLM
# acknowledgment path + the deterministic rail paths).
_MEMORY_CAPABLE_ACTIONS = frozenset({"llm"} | set(_DICTATION_ACTIONS))


def validate_decision(decision, signals, state) -> tuple:
    """Read-only gate on the Decision's output. Returns
    (ok, [violated rule names]); NEVER returns or mutates a Decision. Fails
    closed on anything unknown/invalid (S1 + I1..I9, checked in order)."""
    if not isinstance(decision, Decision):
        return False, ["S1_type"]
    violations: list = []
    signals = signals or {}
    state = state or {}

    # S1 — schema (always first)
    if decision.turn_intent not in TURN_INTENTS:
        violations.append("S1_turn_intent")
    if decision.memory_intent not in MEMORY_INTENTS:
        violations.append("S1_memory_intent")
    if decision.conv_state not in CONV_STATES:
        violations.append("S1_conv_state")
    if decision.turn_owner not in OWNERS:
        violations.append("S1_turn_owner")
    if decision.delivery_mode not in DELIVERY_MODES:
        violations.append("S1_delivery_mode")
    if decision.action not in ACTIONS:
        violations.append("S1_action")
    if decision.llm_instruction not in LLM_INSTRUCTIONS:
        violations.append("I9_llm_instruction")

    phase = state.get("conv_state")
    digits = bool(signals.get("digits_present"))

    # I1 — TASK_ACTIVE + digits never routes to LLM
    if phase == "TASK_ACTIVE" and digits:
        if (decision.action == "llm"
                or decision.action not in _DICTATION_ACTIONS
                or decision.delivery_mode == "CONTINUE"):
            violations.append("I1")

    # I2 — CONFIRMING + confirm/reject only via the confirmation path
    if phase == "CONFIRMING" and decision.turn_intent in ("CONFIRM", "REJECT"):
        if decision.action not in ("rail_confirm", "rail_repair"):
            violations.append("I2")

    # I3 — REJECT never becomes CONFIRM
    if decision.turn_intent == "REJECT":
        if decision.action == "rail_confirm" or decision.memory_intent == "SAVE":
            violations.append("I3")

    # I4 — FORGET never executes in P1 (acknowledgment only)
    if decision.memory_intent == "FORGET":
        if decision.action != "llm" or decision.llm_instruction is None:
            violations.append("I4")

    # I5 — memory_intent alone can never cause a write (no write action exists
    # in the S1 action set; any non-NONE intent must pair with llm or rail_* —
    # a greeting/suppress/drop can never carry a memory intent)
    if decision.memory_intent != "NONE":
        if decision.action not in _MEMORY_CAPABLE_ACTIONS:
            violations.append("I5")

    # I6 — CONTINUE delivery requires active delivery + the LLM action
    if decision.delivery_mode == "CONTINUE":
        if not bool(state.get("delivery_active")) or decision.action != "llm":
            violations.append("I6")

    # I7 — llm never coexists with dictation-owned state (refined 2026-09-02:
    # memory-axis SAVING/RECALLING/CORRECTING DO pair with llm — the LLM
    # speaks the acknowledgment)
    if decision.action == "llm":
        if decision.conv_state == "CONFIRMING":
            violations.append("I7")
        if phase == "TASK_ACTIVE" and digits:
            violations.append("I7")
        if decision.delivery_mode == "SILENT":
            violations.append("I7")
    if decision.action in _SYSTEM_OWNED_ACTIONS and decision.turn_owner != "SYSTEM":
        violations.append("I7_owner")

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# Shadow wiring helpers (lock §7) — used by main.py and response_pipeline.run_turn
# ---------------------------------------------------------------------------
_RAIL_ACTION_MAP = {
    "echo_confirm": "rail_echo", "silent_accumulate": "rail_accumulate",
    "recall": "rail_recall", "retry": "rail_repair", "arm": "rail_arm",
    "status": "rail_recall", "echo_full": "rail_confirm",
    "confirm_ack": "rail_confirm", "silent": "suppress", "greet": "greeting",
    "hold": "clarify", "nudge": "clarify", "clarify": "clarify",
}


def chain_action(*, route_drop: bool = False, route_action: str = "normal",
                 rail: dict | None = None, greeting: str | None = None) -> str:
    """The action today's chain took, in Decision action vocabulary — for the
    shadow divergence telemetry. rail > greeting > clarify > llm; a dropped
    turn is drop (the chain returns before any response)."""
    if route_drop:
        return "drop"
    if rail is not None:
        a = rail.get("action") or "?"
        return _RAIL_ACTION_MAP.get(a, a)
    if greeting is not None:
        return "greeting"
    if route_action in ("acoustic_only", "clarify"):
        return "clarify"
    return "llm"


def shadow_turn(turn: dict, engine: dict | None, text: str | None, turn_no: int,
                *, route_drop: bool = False, route_action: str = "normal",
                rail: dict | None = None, greeting: str | None = None,
                emit=None) -> dict:
    """P1 SHADOW (lock §7): compute the control-plane Decision for THIS turn
    and archive it under turn['control_shadow'] (valid decisions only).

    Fail-closed: an invariant violation -> INVARIANT_VIOLATION event and NO
    shadow key; any exception -> CONTROL_SHADOW_ERROR and NO shadow key. The
    production chain already ran byte-identical above — this block only
    observes, never mutates engine state, and can never affect behavior.

    emit(name, details) is an optional telemetry hook (tmark / log_event)."""
    def _emit(name: str, details: dict) -> None:
        if emit is not None:
            try:
                emit(name, details)
            except Exception:
                pass
    try:
        snapshot = build_snapshot(engine, route_drop=route_drop,
                                  route_action=route_action)
        signals = detect_signals(text, turn_no, snapshot)
        decision = control_turn(signals, snapshot)
        ok, violations = validate_decision(decision, signals, snapshot)
        chain = chain_action(route_drop=route_drop, route_action=route_action,
                             rail=rail, greeting=greeting)
        if ok:
            shadow = {
                "valid": True,
                "turn_intent": decision.turn_intent,
                "memory_intent": decision.memory_intent,
                "conv_state": decision.conv_state,
                "turn_owner": decision.turn_owner,
                "delivery_mode": decision.delivery_mode,
                "action": decision.action,
                "llm_instruction": decision.llm_instruction,
                "chain_action": chain,
                "divergent": decision.action != chain,
            }
            turn["control_shadow"] = shadow
            _emit("DECISION_SHADOW", dict(shadow, turn=turn_no))
            if shadow["divergent"]:
                _emit("DECISION_SHADOW_DIVERGENCE",
                      {"turn": turn_no, "chain_action": chain,
                       "shadow_action": decision.action})
        else:
            _emit("INVARIANT_VIOLATION",
                  {"turn": turn_no, "rules": violations,
                   "decision": {f: getattr(decision, f) for f in DECISION_FIELDS}})
    except Exception as e:  # pragma: no cover - defensive fail-closed
        _emit("CONTROL_SHADOW_ERROR",
              {"turn": turn_no, "error": f"{type(e).__name__}: {e}"})
    return turn
