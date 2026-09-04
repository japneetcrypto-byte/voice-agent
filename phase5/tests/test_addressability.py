"""L4 — bounded silence / addressability (docs/VALUE_TRANSACTION_LOCK.md §5,
owner Q5: threshold is POLICY, name-call markers are opt-in and NOT here).

UNIT/INVARIANT coverage: while a task holds a value (or an open proposal),
consecutive SILENT decisions on non-digit turns are bounded by
SILENT_STREAK_MAX; the turn that crosses the bound speaks a deterministic
status (the open proposal is re-echoed; otherwise the base + the 'bas'
escape). Digit turns never count and are never forced to speak (smoke-7);
the armed-empty phase stays row 47's; any spoken decision resets the
streak; the bound is a constant the tests read, not a number they assume.

Run: python3 phase5/tests/test_addressability.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide as decide_raw, speak_value
from agent.value_transaction import decide_heard, SILENT_STREAK_MAX
import agent.value_transaction as vt

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

BASE = "026900125205203"
FILLERS = ["सुनातो ने?", "एवा", "एवा, सुना, कहाँ?", "अवाँ", "यह समझे", "अरे बे चुप", "बता समझा की नहीं समझा", "बोल"]
def eng_with(value, status="pending"):
    return {"dictation": {"value": value, "status": status}, "conv": {}}

print("== policy constant is read, not assumed ==")
check("SILENT_STREAK_MAX is a small positive int (policy)", isinstance(SILENT_STREAK_MAX, int) and 1 <= SILENT_STREAK_MAX <= 5, True)
N = SILENT_STREAK_MAX

print("== the 103339 t10-t13 silence: bounded by the policy, status names the value + escape ==")
eng = eng_with(BASE)
acts = []
for i, t in enumerate(FILLERS[:N + 1]):
    r = decide_raw(t, eng, 10 + i)
    acts.append((r["action"], r["line"] is not None))
check(f"first {N} silent, the {N + 1}th speaks", acts, [("silent", False)] * N + [("status", True)])
r = decide_raw(FILLERS[N], eng_with(BASE), 99)  # single filler: still silent (streak 1)
check("a single filler is still silent (no over-talking)", r["action"], "silent")
eng = eng_with(BASE)
for i, t in enumerate(FILLERS[:N + 1]):
    r = decide_raw(t, eng, 10 + i)
check("status speaks the base digit-by-digit", speak_value(BASE) in r["line"], True)
check("status offers the escape ('bas')", "bas" in r["line"], True)
check("status does not mutate", (eng["dictation"]["value"], eng["dictation"].get("proposal")), (BASE, None))
check("streak reset after speaking", eng["conv"]["silent_streak"], 0)

print("== with an OPEN proposal the bound re-echoes the proposal (L2 rule 2), never commits it ==")
eng = eng_with(BASE)
decide_raw("नहीं 520 नहीं है", eng, 8)
decide_heard("पाइप की जगह 5 बार 0 लिखना है", eng, 9)
derived = eng["dictation"]["proposal"]["derived"]
for i, t in enumerate(FILLERS[4:4 + N + 1]):
    r = decide_raw(t, eng, 20 + i)
check("bound turn re-echoes the proposal", (r["action"], r["value"], r.get("trigger")), ("echo_confirm", derived, "status"))
check("base still untouched, proposal still open", (eng["dictation"]["value"], eng["dictation"]["proposal"]["derived"]), (BASE, derived))

print("== digit turns never count and are never forced to speak (smoke-7) ==")
eng = {"dictation": None, "conv": {}}
decide_raw("एक अकाउंट नंबर लिखो", eng, 1)
acts = [decide_raw(t, eng, 2 + i)["action"] for i, t in enumerate(["012", "1, 2, 0,", "1, 2,", "5 x 0", "1 2 0 3", "4 4"])]
check("six consecutive digit turns: all silent_accumulate", set(acts), {"silent_accumulate"})
check("streak stays 0 across digit turns", eng["conv"]["silent_streak"], 0)
eng = eng_with(BASE)
decide_raw(FILLERS[0], eng, 10); decide_raw(FILLERS[1], eng, 11)
r = decide_raw("7398", eng, 12)
check("a digit turn after silence resets the streak instead of triggering a status", (r["action"], eng["conv"]["silent_streak"]), ("silent_accumulate", 0))

print("== armed-empty phase is row 47's (nudge), not L4's ==")
eng = {"dictation": None, "conv": {}}
decide_raw("एक अकाउंट नंबर लिखो", eng, 1)
acts = [(decide_raw(t, eng, 2 + i) or {}).get("action") for i, t in enumerate(["हम्म", "अच्छा", "हाँ तो", "ओके"])]
check("armed-empty fillers: row 47 nudges, L4 never fires", ("status" in acts, eng["conv"]["silent_streak"]), (False, 0))

print("== any spoken decision resets the streak ==")
eng = eng_with(BASE)
decide_raw(FILLERS[0], eng, 10)
decide_raw("क्या लिखा तुमने?", eng, 11)         # recall speaks
check("recall resets", eng["conv"]["silent_streak"], 0)
decide_raw(FILLERS[1], eng, 12)
decide_raw("हेलो", eng, 13)                      # greeting speaks
check("greeting resets", eng["conv"]["silent_streak"], 0)

print("== the bound is policy: raising it moves the status turn (no hard-coded '3rd turn') ==")
saved = vt.SILENT_STREAK_MAX
try:
    vt.SILENT_STREAK_MAX = N + 2
    eng = eng_with(BASE)
    acts = [decide_raw(t, eng, 10 + i)["action"] for i, t in enumerate(FILLERS[:N + 3])]
    check(f"with MAX={N + 2} the status comes on turn {N + 3}", acts, ["silent"] * (N + 2) + ["status"])
finally:
    vt.SILENT_STREAK_MAX = saved

print("== no name-call / imperative marker list in core (opt-in only, owner Q5) ==")
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent", "conversation_controller.py"), encoding="utf-8").read()
check("controller has no NAME_CALL / ADDRESS_MARKERS table", ("NAME_CALL" in src or "ADDRESS_MARKERS" in src), False)

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")
