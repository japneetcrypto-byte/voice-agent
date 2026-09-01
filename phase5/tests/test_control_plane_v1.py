#!/usr/bin/env python3
"""Control Plane V1 — P1 shadow (owner-approved lock docs/CONTROL_PLANE_P1_LOCK.md).

Tests-FIRST (lock §8.1): the §5 16-row adversarial table, exact-match on all
7 Decision fields, through the REAL Hindi adapter (detect_signals) -> the
language-neutral core (control_turn). Plus the precedence conflict rows,
POSSIBLE_SAVE routing, axes independence, determinism, garbage-safety, and
the structural pins (no re.compile, no import re, language-free CORE).

Run: python3 phase5/tests/test_control_plane_v1.py
"""
import sys, os, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.control_plane import (Decision, detect_signals, control_turn,
                                 validate_decision, build_snapshot, shadow_turn,
                                 chain_action, default_signals, LLM_INSTRUCTIONS,
                                 ACTIONS)

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

# ---------------------------------------------------------------------------
# helpers — a snapshot mirroring build_snapshot()'s shape
# ---------------------------------------------------------------------------
def snap(phase="NORMAL", value="", delivery=False, memory=False):
    task = None
    if phase in ("TASK_ACTIVE", "CONFIRMING"):
        task = {"kind": "dictation", "value": value,
                "status": "confirming" if phase == "CONFIRMING" else "pending",
                "topic": "mobile"}
    return {"conv_state": phase, "task": task, "task_value_present": bool(value),
            "delivery_active": delivery, "wait_streak": 0,
            "memory_has_record": memory, "route_drop": False, "route_action": "normal"}

def D(ti, mi, cs, owner, dm, act, ins):
    return Decision(ti, mi, cs, owner, dm, act, ins)

def decide(text, phase="NORMAL", value="", delivery=False, memory=False, turn_no=1):
    s = snap(phase, value, delivery, memory)
    sig = detect_signals(text, turn_no, s)
    return control_turn(sig, s)

# ---------------------------------------------------------------------------
# §5 16-row adversarial table (lock-pinned, exact-match on all 7 fields)
# ---------------------------------------------------------------------------
print("== §5 16-row adversarial table ==")
ROWS = [
    # (no, text, phase, value, delivery, memory, expected Decision)
    (1,  "हाँ", "NORMAL", "", False, False, D("NORMAL","NONE","NORMAL","USER","NEW","llm",None)),
    (2,  "हाँ", "CONFIRMING", "9935", False, False, D("CONFIRM","SAVE","NORMAL","SYSTEM","NEW","rail_confirm",None)),
    (3,  "हाँ", "TASK_ACTIVE", "", False, False, D("NORMAL","NONE","TASK_ACTIVE","SYSTEM","SILENT","suppress",None)),
    (4,  "बस", "NORMAL", "", False, False, D("STOP","NONE","NORMAL","USER","NEW","llm","ACKNOWLEDGE_STOP")),
    (5,  "बस", "CONFIRMING", "9935", False, False, D("CONFIRM","SAVE","NORMAL","SYSTEM","NEW","rail_confirm",None)),
    (6,  "बस", "TASK_ACTIVE", "9935", False, False, D("CONFIRM","SAVE","CONFIRMING","SYSTEM","NEW","rail_confirm",None)),
    (7,  "आगे बताओ", "NORMAL", "", True, False, D("CONTINUE","NONE","CONTINUING","USER","CONTINUE","llm","CONTINUE")),
    (8,  "आगे बताओ", "NORMAL", "", False, False, D("NORMAL","NONE","NORMAL","USER","NEW","llm",None)),
    (9,  "नहीं, वो नैनीताल नहीं था", "NORMAL", "", False, True, D("CORRECT","CORRECT","CORRECTING","USER","NEW","llm","SUPERSEDE_MEMORY_HOOK")),
    (10, "नहीं, वो नैनीताल नहीं था", "NORMAL", "", False, False, D("CORRECT","NONE","NORMAL","USER","NEW","llm",None)),
    (11, "9935", "TASK_ACTIVE", "", False, False, D("NORMAL","POSSIBLE_SAVE","TASK_ACTIVE","SYSTEM","SILENT","rail_accumulate",None)),
    (12, "9935", "NORMAL", "", False, False, D("NORMAL","POSSIBLE_SAVE","TASK_ACTIVE","SYSTEM","NEW","rail_echo",None)),
    (13, "50-60 लोग", "NORMAL", "", False, False, D("NORMAL","POSSIBLE_SAVE","TASK_ACTIVE","SYSTEM","NEW","rail_echo",None)),
    (14, "याद रख लेना", "NORMAL", "", False, False, D("NORMAL","SAVE","SAVING","USER","NEW","llm","ACKNOWLEDGE_SAVE")),
    (15, "मैंने कौन सा नंबर बताया था?", "NORMAL", "", False, True, D("NORMAL","RECALL","RECALLING","SYSTEM","NEW","rail_recall",None)),
    (16, "हेलो", "NORMAL", "", False, False, D("NORMAL","NONE","NORMAL","SYSTEM","NEW","greeting",None)),
]
for no, text, phase, value, delivery, memory, want in ROWS:
    got = decide(text, phase, value, delivery, memory)
    check(f"row{no:02d} {text!r} [{phase}{'/' + value if value else ''}]",
          (got.turn_intent, got.memory_intent, got.conv_state, got.turn_owner,
           got.delivery_mode, got.action, got.llm_instruction),
          (want.turn_intent, want.memory_intent, want.conv_state, want.turn_owner,
           want.delivery_mode, want.action, want.llm_instruction))

print("== record-conditional row 15 (refinement) ==")
got = decide("मैंने कौन सा नंबर बताया था?", "NORMAL", "", False, False)
check("row15 no record -> llm + RECALL_MEMORY (rule-14 honest)",
      (got.turn_intent, got.memory_intent, got.conv_state, got.turn_owner,
       got.delivery_mode, got.action, got.llm_instruction),
      ("NORMAL","RECALL","RECALLING","USER","NEW","llm","RECALL_MEMORY"))

print("== precedence conflict rows (lock §8.1) ==")
got = decide("नहीं हाँ", "CONFIRMING", "9935")
check("REJECT > CONFIRM (both words, confirming)",
      (got.turn_intent, got.memory_intent, got.action), ("REJECT","NONE","rail_repair"))
got = decide("बस आगे", "NORMAL", "", delivery=True)
check("STOP word end-to-end with delivery (continue_cue fires, stop is exact)",
      (got.turn_intent, got.action, got.llm_instruction),
      ("CONTINUE", "llm", "CONTINUE"))
# Ordering STOP > CONTINUE (lock G5) is pinned at the signal level: both cues
# present, stop must win. (No single real utterance carries both an exact
# whole-word stop AND a continue cue, so this is the contract-level test.)
s = snap("NORMAL", "", delivery=True)
sig = default_signals(); sig["stop"] = True; sig["continue_cue"] = True
d = control_turn(sig, s)
check("G5 STOP > CONTINUE (both signals, delivery active)",
      (d.turn_intent, d.action, d.llm_instruction),
      ("STOP", "llm", "ACKNOWLEDGE_STOP"))
got = decide("नहीं 420 नहीं 0000 है", "TASK_ACTIVE", "9935")
check("G1 repair vs G2 reject (structured correction never a plain reject-wipe)",
      (got.turn_intent, got.memory_intent, got.action),
      ("CORRECT","POSSIBLE_SAVE","rail_repair"))
got = decide("मैंने बोला था 5 बट 0 उसका क्या किया तुमने", "TASK_ACTIVE", "9935")
check("digits vs recall-query in TASK_ACTIVE (query_stored recall-as-proof wins)",
      (got.memory_intent, got.action), ("RECALL","rail_recall"))
got = decide("हेलो 9935 6789", "TASK_ACTIVE", "")
check("greeting vs armed-state TASK_ACTIVE (G3 digits win)",
      (got.action, got.delivery_mode), ("rail_accumulate","SILENT"))
got = decide("हेलो 9935 6789", "NORMAL")
check("greeting vs armed-state NORMAL (G6 greeting wins, digits not fired)",
      (got.action, got.turn_owner), ("greeting","SYSTEM"))
got = decide("9935 बस", "TASK_ACTIVE", "")
# The real detector finds NO digit span when the confirm word trails the
# number ('9935 बस' is not a pure digit utterance) — so G2's armed-empty
# suppress fires, exactly like today's chain (silent wait). Shadow agrees.
check("digits + trailing confirm word (armed-empty -> suppress, chain-consistent)",
      (got.action, got.delivery_mode), ("suppress", "SILENT"))

print("== POSSIBLE_SAVE declared + routes pending (never explicit) ==")
for t in ["9935", "50-60 लोग", "9935 6789"]:
    got = decide(t, "NORMAL")
    check(f"{t!r} -> POSSIBLE_SAVE (inferred, pending), not SAVE",
          (got.memory_intent, got.action), ("POSSIBLE_SAVE","rail_echo"))

print("== axes independence (row 9: BOTH axes CORRECT) ==")
got = decide("नहीं, वो नैनीताल नहीं था", "NORMAL", memory=True)
check("memory correction sets turn_intent AND memory_intent CORRECT",
      (got.turn_intent, got.memory_intent), ("CORRECT","CORRECT"))

print("== determinism (same input -> same Decision) ==")
s = snap("TASK_ACTIVE", "9935")
a = control_turn(detect_signals("9935", 7, s), s)
b = control_turn(detect_signals("9935", 7, s), s)
check("twice-run identical", (a.turn_intent, a.action), (b.turn_intent, b.action))

print("== garbage / empty / long input never crash ==")
for t in ["", None, "!!!", "…", "हाँ?" * 50, "x" * 5000, " \t ", "9935" * 200]:
    s = snap()
    d = control_turn(detect_signals(t, 1, s), s)
    ok, v = validate_decision(d, detect_signals(t, 1, s), s)
    check(f"no-crash on {str(t)[:24]!r}", ok, True)

print("== structural pins (lock §8.1) ==")
src = inspect.getsource(sys.modules["agent.control_plane"])
check("control_plane.py contains no re.compile", "re.compile" in src, False)
check("control_plane.py contains no 'import re'", "\nimport re\n" in "\n" + src, False)
for fn in (control_turn, validate_decision, build_snapshot):
    fsrc = inspect.getsource(fn)
    dev = any(0x0900 <= ord(ch) <= 0x097F for ch in fsrc)
    check(f"CORE {fn.__name__} has no Devanagari characters", dev, False)
    check(f"CORE {fn.__name__} has no regex literal", "re." in fsrc, False)
    check(f"CORE {fn.__name__} has no import statement", "import" in fsrc, False)

print("== build_snapshot: engine state -> read-only snapshot ==")
eng = {"conv": {"user_state": "dictating", "task_topic": "mobile"},
       "dictation": {"value": "9935", "status": "pending"},
       "detail": {"active": True}, "wait_streak": 2}
sn = build_snapshot(eng)
check("phase TASK_ACTIVE from pending task", sn["conv_state"], "TASK_ACTIVE")
check("task_value_present", sn["task_value_present"], True)
check("delivery_active", sn["delivery_active"], True)
check("wait_streak", sn["wait_streak"], 2)
check("snapshot is a fresh dict (read-only, no aliasing)",
      sn is not eng and sn["task"] is not eng["dictation"], True)
sn2 = build_snapshot({"conv": {}, "dictation": {}, "detail": {}})
check("empty engine -> NORMAL phase, no crash", sn2["conv_state"], "NORMAL")

print("== chain_action mapping (wiring helper) ==")
check("rail echo_confirm -> rail_echo",
      chain_action(route_action="normal", rail={"action": "echo_confirm"}, greeting=None),
      "rail_echo")
check("rail silent_accumulate -> rail_accumulate",
      chain_action(route_action="normal", rail={"action": "silent_accumulate"}, greeting=None),
      "rail_accumulate")
check("greeting wins over llm",
      chain_action(route_action="normal", rail=None, greeting="hello!"), "greeting")
check("plain turn -> llm",
      chain_action(route_action="normal", rail=None, greeting=None), "llm")
check("drop route -> drop",
      chain_action(route_drop=True, route_action="normal", rail=None, greeting=None), "drop")

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")
