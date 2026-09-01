#!/usr/bin/env python3
"""Control Plane V1 — Decision Safety / Invariant layer (lock §9, CTO addition).

One test per invariant S1 + I1-I9 (§9.4): craft a violating
(decision, signals, state) -> ok=False with the rule name present; a compliant
one -> ok=True. Plus: fail-closed wiring (I8 — invalid Decision must NOT emit a
shadow decision; INVARIANT_VIOLATION is logged), validator robustness,
determinism, language-neutrality (§4.1: equal canonical signals from
different-language adapters -> identical Decision), and the no-second-authority
structural pins (§9.3).

Run: python3 phase5/tests/test_control_plane_invariants.py
"""
import sys, os, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import agent.control_plane as cp
from agent.control_plane import (Decision, detect_signals, control_turn,
                                 validate_decision, shadow_turn, default_signals,
                                 LLM_INSTRUCTIONS)

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

def D(ti, mi, cs, owner, dm, act, ins=None):
    return Decision(ti, mi, cs, owner, dm, act, ins)

def state(phase="NORMAL", delivery=False):
    task = None
    if phase in ("TASK_ACTIVE", "CONFIRMING"):
        task = {"kind": "dictation", "value": "9935", "status":
                "confirming" if phase == "CONFIRMING" else "pending", "topic": "mobile"}
    return {"conv_state": phase, "task": task, "task_value_present": bool(task),
            "delivery_active": delivery, "wait_streak": 0, "memory_has_record": False,
            "route_drop": False, "route_action": "normal"}

def signals(**kw):
    s = default_signals()
    s.update(kw)
    return s

def must_contain(violations, name):
    return name in violations

# ---------------------------------------------------------------------------
# S1 — schema enums
# ---------------------------------------------------------------------------
print("== S1 schema ==")
st = state()
sg = signals()
bad = D("BOGUS", "NONE", "NORMAL", "USER", "NEW", "llm", None)
ok, v = validate_decision(bad, sg, st)
check("unknown turn_intent -> S1", ok, False)
check("S1_turn_intent present", must_contain(v, "S1_turn_intent"), True)
ok, v = validate_decision(D("NORMAL","BOGUS","NORMAL","USER","NEW","llm",None), sg, st)
check("unknown memory_intent -> S1", must_contain(v, "S1_memory_intent"), True)
ok, v = validate_decision(D("NORMAL","NONE","BOGUS","USER","NEW","llm",None), sg, st)
check("unknown conv_state -> S1", must_contain(v, "S1_conv_state"), True)
ok, v = validate_decision(D("NORMAL","NONE","NORMAL","BOGUS","NEW","llm",None), sg, st)
check("unknown turn_owner -> S1", must_contain(v, "S1_turn_owner"), True)
ok, v = validate_decision(D("NORMAL","NONE","NORMAL","USER","BOGUS","llm",None), sg, st)
check("unknown delivery_mode -> S1", must_contain(v, "S1_delivery_mode"), True)
ok, v = validate_decision(D("NORMAL","NONE","NORMAL","USER","NEW","invented_action",None), sg, st)
check("unknown action -> S1 (no invented action can ship)", must_contain(v, "S1_action"), True)
ok, v = validate_decision(D("NORMAL","NONE","NORMAL","USER","NEW","llm","free-form prose batao"), sg, st)
check("prose llm_instruction -> I9 (not in locked key set)", must_contain(v, "I9_llm_instruction"), True)
ok, v = validate_decision(D("NORMAL","NONE","NORMAL","USER","NEW","llm",None), sg, st)
check("compliant schema decision -> ok", ok, True)

# ---------------------------------------------------------------------------
# I1 — TASK_ACTIVE + digits never routes to LLM
# ---------------------------------------------------------------------------
print("== I1 ==")
st = state("TASK_ACTIVE"); sg = signals(digits_present=True, digits_value="9935")
ok, v = validate_decision(D("NORMAL","POSSIBLE_SAVE","TASK_ACTIVE","SYSTEM","SILENT","llm",None), sg, st)
check("digits in TASK_ACTIVE with action=llm -> I1", must_contain(v, "I1"), True)
ok, v = validate_decision(D("NORMAL","POSSIBLE_SAVE","TASK_ACTIVE","SYSTEM","CONTINUE","rail_accumulate",None), sg, st)
check("digits in TASK_ACTIVE with CONTINUE delivery -> I1", must_contain(v, "I1"), True)
ok, v = validate_decision(D("NORMAL","POSSIBLE_SAVE","TASK_ACTIVE","SYSTEM","SILENT","rail_accumulate",None), sg, st)
check("digits in TASK_ACTIVE -> rail_accumulate compliant", ok, True)

# ---------------------------------------------------------------------------
# I2 — CONFIRMING + confirm/reject uses only the confirmation path
# ---------------------------------------------------------------------------
print("== I2 ==")
st = state("CONFIRMING"); sg = signals(confirm=True)
ok, v = validate_decision(D("CONFIRM","SAVE","NORMAL","SYSTEM","NEW","llm",None), sg, st)
check("CONFIRMING + confirm with action=llm -> I2", must_contain(v, "I2"), True)
ok, v = validate_decision(D("CONFIRM","SAVE","NORMAL","SYSTEM","NEW","greeting",None), sg, st)
check("CONFIRMING + confirm with action=greeting -> I2", must_contain(v, "I2"), True)
ok, v = validate_decision(D("CONFIRM","SAVE","NORMAL","SYSTEM","NEW","rail_confirm",None), sg, st)
check("CONFIRMING + confirm -> rail_confirm compliant", ok, True)

# ---------------------------------------------------------------------------
# I3 — REJECT never becomes CONFIRM (no rail_confirm, no SAVE)
# ---------------------------------------------------------------------------
print("== I3 ==")
ok, v = validate_decision(D("REJECT","NONE","NORMAL","SYSTEM","NEW","rail_confirm",None), signals(), state("CONFIRMING"))
check("REJECT with action=rail_confirm -> I3", must_contain(v, "I3"), True)
ok, v = validate_decision(D("REJECT","SAVE","NORMAL","SYSTEM","NEW","rail_repair",None), signals(), state("CONFIRMING"))
check("REJECT with memory_intent=SAVE -> I3", must_contain(v, "I3"), True)
ok, v = validate_decision(D("REJECT","NONE","TASK_ACTIVE","SYSTEM","NEW","rail_repair",None), signals(), state("CONFIRMING"))
check("REJECT -> rail_repair / NONE compliant", ok, True)

# ---------------------------------------------------------------------------
# I4 — FORGET never executes in P1 (action=llm + instruction present)
# ---------------------------------------------------------------------------
print("== I4 ==")
ok, v = validate_decision(D("NORMAL","FORGET","NORMAL","USER","NEW","rail_repair",None), signals(), state())
check("FORGET with a write-capable action -> I4", must_contain(v, "I4"), True)
ok, v = validate_decision(D("NORMAL","FORGET","NORMAL","USER","NEW","llm",None), signals(), state())
check("FORGET with action=llm but no instruction -> I4", must_contain(v, "I4"), True)
ok, v = validate_decision(D("NORMAL","FORGET","NORMAL","USER","NEW","llm","ACKNOWLEDGE_FORGET"), signals(), state())
check("FORGET -> llm + ACKNOWLEDGE_FORGET compliant", ok, True)

# ---------------------------------------------------------------------------
# I5 — memory_intent alone can never cause a write (no write action exists)
# ---------------------------------------------------------------------------
print("== I5 ==")
ok, v = validate_decision(D("NORMAL","SAVE","NORMAL","SYSTEM","NEW","greeting",None), signals(), state())
check("SAVE with action=greeting (no write-capable path) -> I5", must_contain(v, "I5"), True)
ok, v = validate_decision(D("NORMAL","SAVE","SAVING","USER","NEW","llm","ACKNOWLEDGE_SAVE"), signals(), state())
check("SAVE -> llm ack compliant", ok, True)
ok, v = validate_decision(D("NORMAL","POSSIBLE_SAVE","TASK_ACTIVE","SYSTEM","SILENT","rail_accumulate",None),
                          signals(digits_present=True), state("TASK_ACTIVE"))
check("POSSIBLE_SAVE -> rail_accumulate compliant", ok, True)

# ---------------------------------------------------------------------------
# I6 — delivery_mode == CONTINUE requires active delivery + llm
# ---------------------------------------------------------------------------
print("== I6 ==")
ok, v = validate_decision(D("CONTINUE","NONE","CONTINUING","USER","CONTINUE","llm","CONTINUE"),
                          signals(continue_cue=True), state(delivery=False))
check("CONTINUE without active delivery -> I6", must_contain(v, "I6"), True)
ok, v = validate_decision(D("CONTINUE","NONE","CONTINUING","SYSTEM","CONTINUE","rail_recall",None),
                          signals(continue_cue=True), state(delivery=True))
check("CONTINUE with non-llm action -> I6", must_contain(v, "I6"), True)
ok, v = validate_decision(D("CONTINUE","NONE","CONTINUING","USER","CONTINUE","llm","CONTINUE"),
                          signals(continue_cue=True), state(delivery=True))
check("CONTINUE with active delivery + llm compliant", ok, True)

# ---------------------------------------------------------------------------
# I7 — llm never coexists with dictation-owned state
# ---------------------------------------------------------------------------
print("== I7 ==")
ok, v = validate_decision(D("NORMAL","NONE","CONFIRMING","USER","NEW","llm",None), signals(), state())
check("llm with conv_state=CONFIRMING -> I7", must_contain(v, "I7"), True)
ok, v = validate_decision(D("NORMAL","NONE","TASK_ACTIVE","USER","SILENT","llm",None),
                          signals(digits_present=True), state("TASK_ACTIVE"))
check("llm with TASK_ACTIVE + digits -> I7", must_contain(v, "I7"), True)
ok, v = validate_decision(D("NORMAL","NONE","NORMAL","USER","SILENT","llm",None), signals(), state())
check("llm with SILENT delivery -> I7", must_contain(v, "I7"), True)
ok, v = validate_decision(D("NORMAL","NONE","NORMAL","USER","NEW","rail_echo",None), signals(), state())
check("rail_echo with owner=USER -> I7 (system-owned action)", must_contain(v, "I7_owner"), True)
ok, v = validate_decision(D("NORMAL","SAVE","SAVING","USER","NEW","llm","ACKNOWLEDGE_SAVE"), signals(), state())
check("llm + SAVING (memory-ack state) compliant after I7 refinement", ok, True)
ok, v = validate_decision(D("CONTINUE","NONE","CONTINUING","USER","CONTINUE","llm","CONTINUE"),
                          signals(continue_cue=True), state(delivery=True))
check("llm + CONTINUING compliant", ok, True)

# ---------------------------------------------------------------------------
# I8 — fail-closed wiring: invalid Decision -> NO shadow, INVARIANT_VIOLATION
# ---------------------------------------------------------------------------
print("== I8 fail-closed emission ==")
def bad_control_turn(signals, snapshot):
    # an ill-formed decision that still fails S1/I1..I7: llm in CONFIRMING
    return D("CONFIRM", "SAVE", "CONFIRMING", "USER", "NEW", "llm", None)
orig = cp.control_turn
cp.control_turn = bad_control_turn
try:
    events = []
    turn = {}
    shadow_turn(turn, {}, "हाँ", 1, route_drop=False, route_action="normal",
                rail=None, greeting=None,
                emit=lambda ev, details: events.append((ev, details)))
    check("invalid decision emits NO control_shadow key", "control_shadow" in turn, False)
    check("INVARIANT_VIOLATION was logged",
          any(ev == "INVARIANT_VIOLATION" for ev, _ in events), True)
    rules = [d.get("rules") for ev, d in events if ev == "INVARIANT_VIOLATION"][0]
    check("violation rules reported", "I7" in rules, True)
finally:
    cp.control_turn = orig

print("== I8 valid emission ==")
events = []
turn = {}
shadow_turn(turn, {}, "aaj ka din kaisa raha", 3, route_drop=False, route_action="normal",
            rail=None, greeting=None,
            emit=lambda ev, details: events.append((ev, details)))
check("valid decision IS emitted", "control_shadow" in turn, True)
check("shadow carries action=llm", turn["control_shadow"]["action"], "llm")
check("DECISION_SHADOW logged", any(ev == "DECISION_SHADOW" for ev, _ in events), True)

print("== shadow fail-closed on ANY error ==")
events = []
turn = {}
shadow_turn(turn, {"sess": object()}, "हाँ", 1, rail=None, greeting=None,
            emit=lambda ev, details: events.append((ev, details)))
# build_snapshot's _memory_has_record swallows the object() sess -> no crash;
# the turn simply carries a shadow decision or none — never raises.
check("shadow_turn never raises on odd engine", True, True)

# ---------------------------------------------------------------------------
# validator robustness + determinism
# ---------------------------------------------------------------------------
print("== robustness ==")
for bad in (None, {}, [], "garbage", 42):
    ok, v = validate_decision(bad, None, None)
    check(f"validate_decision({bad!r}) never raises -> False", (ok, isinstance(v, list)), (False, True))
ok, v = validate_decision(None, {"digits_present": "junk"}, {"conv_state": None})
check("garbage signals/state never raises", isinstance(v, list), True)
a = validate_decision(D("NORMAL","NONE","NORMAL","USER","NEW","llm",None), signals(), state())
b = validate_decision(D("NORMAL","NONE","NORMAL","USER","NEW","llm",None), signals(), state())
check("validator deterministic", a, b)

# ---------------------------------------------------------------------------
# language-neutrality (§4.1 / §9.4)
# ---------------------------------------------------------------------------
print("== language-neutrality ==")
st_c = state("CONFIRMING", delivery=False)
hi = detect_signals("हाँ", 1, st_c)                    # Hindi adapter (real detectors)
en = default_signals(); en["confirm"] = True; en["stop"] = False  # English adapter (synthetic)
check("both adapters emit confirm=True for 'haan'/'yes'", hi["confirm"] == en["confirm"] == True, True)
d_hi = control_turn(hi, st_c)
d_en = control_turn(en, st_c)
check("identical canonical signals -> identical Decision (core is language-agnostic)",
      (d_hi.action, d_hi.turn_intent), (d_en.action, d_en.turn_intent))
st_r = state("NORMAL")
hi2 = detect_signals("याद रख लेना", 1, st_r)
en2 = default_signals(); en2["save_intent"] = True
check("Hindi save_intent vs English save_intent -> same Decision",
      control_turn(hi2, st_r).action, control_turn(en2, st_r).action)

# ---------------------------------------------------------------------------
# no-second-authority structural pins (§9.3)
# ---------------------------------------------------------------------------
print("== no-second-authority pins ==")
res = validate_decision(D("NORMAL","NONE","NORMAL","USER","NEW","llm",None), signals(), state())
check("validate_decision returns a tuple (never a Decision)",
      isinstance(res, tuple), True)
check("validator result type is (bool, list)",
      (isinstance(res[0], bool), isinstance(res[1], list)), (True, True))
src = inspect.getsource(cp)
check("exactly ONE Decision-producing function in control_plane.py",
      src.count("-> Decision"), 1)
vsrc = inspect.getsource(cp.validate_decision)
check("validator has no re.compile", "re.compile" in vsrc, False)
check("validator has no pattern matching", "match" in vsrc, False)
for forbidden in ("memory_store", "memory_gate", "fused_turn", "detect_signals", "control_turn"):
    check(f"validator does not reference {forbidden}", forbidden in vsrc, False)
mod_imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
check("module-level imports exclude memory_store/memory_gate/fused_turn",
      all(f not in "".join(mod_imports) for f in ("memory_store", "memory_gate", "fused_turn")), True)
check("LLM_INSTRUCTIONS is the enumerated §1 set (no ellipsis)",
      LLM_INSTRUCTIONS, frozenset({None, "CONTINUE", "RECALL_MEMORY", "ACKNOWLEDGE_SAVE",
                                   "ACKNOWLEDGE_STOP", "ACKNOWLEDGE_FORGET",
                                   "SUPERSEDE_MEMORY_HOOK", "GREET"}))

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")
