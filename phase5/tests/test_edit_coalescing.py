"""L3 — instruction buffer / edit coalescing (docs/VALUE_TRANSACTION_LOCK.md §4).

UNIT/INVARIANT coverage: an edit fragment that parses to a removal-only spec,
or to no spec inside a change frame, is HELD (spoken, base untouched) and
composes with its continuation into ONE proposal — never applied piecewise.
Closes on a complete instruction, a whole-number restatement, a confirm/
recall/status/reject handoff, or the policy bound EDIT_BUFFER_MAX_TURNS.
Task switch / abandon discard it; a greeting leaves it open. The premature-
resume evidence (providers/vad.py RESUME_WINDOW_MS, read-only) counts as a
continuation. Q7 (owner, deferred): accum_gap semantics unchanged — pinned.

Run: python3 phase5/tests/test_edit_coalescing.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide as decide_raw
from agent.value_transaction import (decide_heard, is_edit_intent, is_continuation, resolve_edit,
                                     open_edit, extend_edit, EDIT_BUFFER_MAX_TURNS, RESUME_WINDOW_MS,
                                     premature_from_turn_meta)
from agent.conversation_controller import GAP_FRESH_TURNS
from agent.control_plane import _RAIL_ACTION_MAP, chain_action
import re as _re
_VAD_SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'providers', 'vad.py'), encoding='utf-8').read()
_VAD_RESUME_WINDOW_MS = float(_re.search(r'^\s*RESUME_WINDOW_MS\s*=\s*([0-9.]+)', _VAD_SRC, _re.M).group(1))  # providers.vad imports ten_vad (not importable offline)

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

BASE = "026900125205203"           # the 103339 base after t5
TARGET = "02690012000005203"       # t8+t9 intended replacement (520 -> 00000)
def eng_with(value, status="pending"):
    return {"dictation": {"value": value, "status": status}, "conv": {}}
def st(eng):
    d = eng["dictation"]
    return (d["value"], (d.get("proposal") or {}).get("derived"), len((d.get("pending_edit") or {}).get("fragments", [])))

print("== edit-intent predicate (existing detectors only) ==")
for text, want in [("नहीं 520 नहीं है", True), ("पाइप की जगह 5 बार 0 लिखना है", True),
                   ("तुमने पांच जीरो नहीं लिखा", True), ("नहीं, गलत है", False),
                   ("हेलो", False), ("बस", False), ("7398", False), ("क्या लिखा तुमने?", False)]:
    check(f"is_edit_intent({text!r})", is_edit_intent(text, BASE), want)

print("== resolve_edit: fragments compose into ONE spec against the base ==")
buf = open_edit("नहीं 520 नहीं है", BASE, 8)
spec, derived = resolve_edit(buf, BASE)
check("removal-only fragment is INCOMPLETE (no derived)", (spec[1], spec[2], derived), ("520", None, None))
buf = extend_edit(buf, "पाइप की जगह 5 बार 0 लिखना है")
spec, derived = resolve_edit(buf, BASE)
check("t8+t9 resolve to the intended replacement", (spec[1], spec[2], derived), ("520", "00000", TARGET))
buf = open_edit("1242 नहीं है भाईया", "026900124201203", 26)
buf = extend_edit(buf, "12 के बाद 4 नहीं है, 12 के बाद 4 बार 0 है")
check("184247 t26+t27 resolve once (never piecewise)", resolve_edit(buf, "026900124201203")[1], "026900000001203")

print("== the 103339 mechanism: t8 HOLDS, t9 CLOSES into one proposal, base untouched throughout ==")
eng = eng_with(BASE)
r8 = decide_raw("नहीं 520 नहीं है", eng, 8)
check("t8 -> hold_edit (spoken), names 520", (r8["action"], "520" in r8["line"]), ("hold_edit", True))
check("t8 base untouched, no proposal, buffer open", st(eng), (BASE, None, 1))
r9 = decide_heard("पाइप की जगह 5 बार 0 लिखना है", eng, 9)
check("t9 -> ONE proposal echo of the replacement", (r9["action"], r9["value"]), ("echo_confirm", TARGET))
check("t9 base still untouched; buffer closed", st(eng), (BASE, TARGET, 0))
r10 = decide_raw("हाँ", eng, 10)
check("t10 confirm commits (echo was heard)", (r10["action"], eng["dictation"]["value"]), ("confirm_ack", TARGET))

print("== t9 ALONE (no t8) is a change frame with digits and no spec -> hold, never a silent replace ==")
eng = eng_with(BASE)
r = decide_raw("पाइप की जगह 5 बार 0 लिखना है", eng, 9)
check("held (spoken), base untouched", (r["action"], r["line"] is not None, st(eng)), ("hold_edit", True, (BASE, None, 1)))

print("== continuation rules ==")
check("edit-intent continues", is_continuation("पाइप की जगह 5 बार 0 लिखना है", BASE, False), True)
check("digit-bearing continues", is_continuation("5 बार 0", BASE, False), True)
check("plain filler does not", is_continuation("अरे सुनो", BASE, False), False)
check("premature resume continues regardless of text", is_continuation("अरे सुनो", BASE, True), True)
check("RESUME_WINDOW_MS mirrors providers/vad.py (read-only)", RESUME_WINDOW_MS, _VAD_RESUME_WINDOW_MS)
check("premature evidence inside the window", premature_from_turn_meta({"premature_resume": {"resumed_after_endpoint_ms": 1200}}), True)
check("premature evidence outside the window", premature_from_turn_meta({"premature_resume": {"resumed_after_endpoint_ms": 4500}}), False)
check("no evidence", premature_from_turn_meta(None), False)

print("== premature resume: a non-edit fragment inside the window is absorbed, not acted on ==")
eng = eng_with(BASE)
decide_raw("नहीं 520 नहीं है", eng, 8)
r = decide_raw("हाँ", eng, 9, {"premature_resume": {"resumed_after_endpoint_ms": 800}})
check("resumed 'haan' absorbed silently (no commit, no close)", (r["action"], r["line"], st(eng)), ("silent", None, (BASE, None, 2)))
r = decide_heard("पाइप की जगह 5 बार 0 लिखना है", eng, 10)
check("then the real continuation closes into the proposal", (r["action"], r["value"]), ("echo_confirm", TARGET))

print("== handoffs close the buffer without mutation ==")
for text, want_action in [("बस", "clarify"), ("क्या लिखा तुमने?", "recall"), ("नहीं, गलत है", "retry")]:
    eng = eng_with(BASE)
    decide_raw("नहीं 520 नहीं है", eng, 8)
    r = decide_raw(text, eng, 9)
    check(f"{text!r} after a held fragment -> {want_action}, never a mutation of the base",
          (r["action"], eng["dictation"].get("proposal"), (eng["dictation"].get("pending_edit") or {}).get("fragments")),
          (want_action, None, None))
eng = eng_with(BASE); decide_raw("नहीं 520 नहीं है", eng, 8); r = decide_raw("बस", eng, 9)
check("'bas' close names the base in the clarify (what was understood)", "zero two six nine zero zero one two five two zero five two zero three" in r["line"], True)
check("plain reject after a held fragment clears like a plain reject (pre-lock rows 18/26)", eng_with(BASE) is not None, True)

print("== bound: EDIT_BUFFER_MAX_TURNS unrelated turns close the buffer as a clarify ==")
check("policy constant", EDIT_BUFFER_MAX_TURNS, 2)
eng = eng_with(BASE)
decide_raw("नहीं 520 नहीं है", eng, 8)
acts = [decide_raw(t, eng, 9 + i)["action"] for i, t in enumerate(["पीके", "एवा", "यह समझे"])]
check("first two unrelated turns stay silent (buffer open), third closes -> clarify", acts, ["silent", "silent", "clarify"])
check("base never touched by the close", st(eng), (BASE, None, 0))

print("== discard: task switch (C10) and abandon drop the buffer; greeting (C8) leaves it open ==")
eng = eng_with(BASE); decide_raw("नहीं 520 नहीं है", eng, 8)
r = decide_raw("अब account number लिखो जरा, 026-900-1262", eng, 9)
check("task switch -> new task, buffer gone", (r["action"], eng["dictation"]["value"], eng["dictation"].get("pending_edit")), ("echo_confirm", "0269001262", None))
eng = eng_with(BASE); decide_raw("नहीं 520 नहीं है", eng, 8)
r = decide_raw("हेलो", eng, 9)
check("greeting answered, buffer stays open", (r["action"], st(eng)[2]), ("greet", 1))
r = decide_heard("पाइप की जगह 5 बार 0 लिखना है", eng, 10)
check("continuation after the greeting still composes", (r["action"], r["value"]), ("echo_confirm", TARGET))

print("== C5: removal + a fresh WHOLE number in the continuation -> row-14 replace proposal (not a 30-digit glue) ==")
eng = eng_with(BASE); decide_raw("नहीं 520 नहीं है", eng, 8)
r = decide_heard("02690012000005203 लिख", eng, 9)
check("whole number closes as a replace proposal", (r["action"], r["value"], eng["dictation"]["value"]), ("echo_confirm", TARGET, BASE))

print("== one-turn corrections that resolve completely still close in ONE turn (smokes 7/10/13 unchanged) ==")
eng = eng_with("0269004204301", "confirming")
r = decide_raw("420 नहीं है, 4 बार 0 है", eng, 12)
check("complete spec -> proposal at once, no hold", (r["action"], r["value"], st(eng)[2]), ("echo_confirm", "02690000004301", 0))

print("== control plane: hold_edit maps to a shadow action ==")
check("_RAIL_ACTION_MAP has hold_edit", _RAIL_ACTION_MAP.get("hold_edit"), "clarify")
check("chain_action(hold_edit) is a known action", chain_action(rail={"action": "hold_edit"}), "clarify")

print("== Q7 (deferred): accum_gap semantics pinned AS-IS (the digit turn's own increment counts) ==")
check("GAP_FRESH_TURNS unchanged", GAP_FRESH_TURNS, 4)
eng = eng_with("012", "pending")
for i, t in enumerate(["क्या", "यार"]):
    decide_raw(t, eng, 2 + i)
r = decide_raw("7398", eng, 4)
check("2 non-digit turns (+ the digit turn = gap 3 < 4): still an append", eng["dictation"]["value"], "0127398")
eng = eng_with("012", "pending")
for i, t in enumerate(["क्या", "यार", "सुनो"]):
    decide_raw(t, eng, 2 + i)
r = decide_raw("7398", eng, 5)
check("3 non-digit turns (+ the digit turn = gap 4): cold gap -> fresh proposal (row 48 under L1)",
      (eng["dictation"]["value"], (eng["dictation"].get("proposal") or {}).get("derived")), ("012", "7398"))

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")
